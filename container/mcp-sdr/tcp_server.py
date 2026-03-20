"""
TCP server wrapper for the MCP SDR server.

Listens on a TCP port, reads a JSON metadata preface from the client,
spawns the MCP server subprocess with injected env vars, and raw-byte-pipes
the TCP socket to the subprocess stdin/stdout.

Protocol:
  1. Client connects
  2. Client sends one line of JSON (the "preface"), terminated by \n
  3. Server validates the preface, spawns MCP subprocess
  4. Raw bidirectional byte pipe until either side disconnects

Security constraints:
  - Max 2 concurrent connections
  - 2s timeout for preface read
  - 60s idle timeout (no data in either direction)
  - 1MB max buffer per read
  - Hard-fail on bad/missing/timeout preface
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mcp-tcp-server")

PORT = int(os.environ.get("MCP_TCP_PORT", "9000"))
MCP_SERVER_CMD = os.environ.get("MCP_SERVER_CMD", "python3 -u /app/mcp-server/server.py")
MAX_CONNECTIONS = 2
PREFACE_TIMEOUT_S = 2.0
PREFACE_MAX_BYTES = 4096
IDLE_TIMEOUT_S = 60.0
MAX_BUFFER = 1_048_576  # 1MB

REQUIRED_PREFACE_KEYS = {"version", "run_id", "actor_id", "channel"}

active_connections = 0


def validate_preface(data: bytes) -> dict:
    """Parse and validate the JSON preface line.

    Returns the parsed dict on success, raises ValueError on failure.
    """
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError("preface must be a JSON object")

    missing = REQUIRED_PREFACE_KEYS - set(obj.keys())
    if missing:
        raise ValueError(f"missing required keys: {', '.join(sorted(missing))}")

    if obj.get("version") != 1:
        raise ValueError(f"unsupported version: {obj.get('version')}")

    for key in ("run_id", "actor_id", "channel"):
        val = obj[key]
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"{key} must be a non-empty string")

    return obj


async def pipe_stream_to_writer(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter | asyncio.subprocess.Process,
    direction: str,
    idle_event: asyncio.Event,
) -> None:
    """Read from reader, write to writer. Set idle_event on each chunk."""
    try:
        while True:
            chunk = await reader.read(MAX_BUFFER)
            if not chunk:
                logger.debug("EOF on %s", direction)
                break
            idle_event.set()
            if isinstance(writer, asyncio.subprocess.Process):
                writer.stdin.write(chunk)
                await writer.stdin.drain()
            else:
                writer.write(chunk)
                await writer.drain()
    except (ConnectionError, BrokenPipeError, OSError) as exc:
        logger.debug("%s pipe error: %s", direction, exc)
    except asyncio.CancelledError:
        pass


async def handle_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """Handle one TCP connection: preface -> spawn MCP -> pipe."""
    global active_connections
    peer = client_writer.get_extra_info("peername", ("?", 0))
    peer_str = f"{peer[0]}:{peer[1]}"

    # Check connection limit
    if active_connections >= MAX_CONNECTIONS:
        logger.warning("connection rejected (limit %d): %s", MAX_CONNECTIONS, peer_str)
        try:
            client_writer.write(b'{"error":"connection limit reached"}\n')
            await client_writer.drain()
        except (ConnectionError, OSError):
            pass
        client_writer.close()
        return

    active_connections += 1
    logger.info("connection accepted: %s (active: %d)", peer_str, active_connections)

    proc = None
    try:
        # Step 1: Read preface (first line, max 4KB, 2s timeout)
        try:
            preface_data = await asyncio.wait_for(
                client_reader.readuntil(b"\n"),
                timeout=PREFACE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("preface timeout: %s", peer_str)
            client_writer.write(b'{"error":"preface timeout"}\n')
            await client_writer.drain()
            return
        except asyncio.IncompleteReadError:
            logger.warning("preface incomplete (client disconnected): %s", peer_str)
            return
        except asyncio.LimitOverrunError:
            logger.warning("preface too large: %s", peer_str)
            client_writer.write(b'{"error":"preface too large"}\n')
            await client_writer.drain()
            return

        if len(preface_data) > PREFACE_MAX_BYTES:
            logger.warning("preface exceeds %d bytes: %s", PREFACE_MAX_BYTES, peer_str)
            client_writer.write(b'{"error":"preface too large"}\n')
            await client_writer.drain()
            return

        # Step 2: Validate preface
        try:
            preface = validate_preface(preface_data)
        except ValueError as exc:
            logger.warning("bad preface from %s: %s", peer_str, exc)
            client_writer.write(f'{{"error":"bad preface: {exc}"}}\n'.encode())
            await client_writer.drain()
            return

        logger.info(
            "preface accepted from %s: run_id=%s actor_id=%s channel=%s",
            peer_str,
            preface["run_id"],
            preface["actor_id"],
            preface["channel"],
        )

        # Step 3: Spawn MCP server subprocess with injected env vars
        env = {**os.environ}
        env["SDR_RUN_ID"] = preface["run_id"]
        env["SDR_ACTOR_ID"] = preface["actor_id"]
        env["SDR_CHANNEL"] = preface["channel"]

        cmd_parts = MCP_SERVER_CMD.split()
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        logger.info("spawned MCP subprocess pid=%d for %s", proc.pid, peer_str)

        # Step 4: Raw byte pipe with idle timeout
        idle_event = asyncio.Event()

        # pipe client -> subprocess stdin
        client_to_proc = asyncio.create_task(
            pipe_stream_to_writer(client_reader, proc, "client->proc", idle_event),
            name=f"c2p-{peer_str}",
        )
        # pipe subprocess stdout -> client
        proc_to_client = asyncio.create_task(
            pipe_stream_to_writer(proc.stdout, client_writer, "proc->client", idle_event),
            name=f"p2c-{peer_str}",
        )
        # Forward subprocess stderr to our stderr
        async def forward_stderr():
            try:
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    sys.stderr.buffer.write(b"[mcp] " + line)
                    sys.stderr.buffer.flush()
            except asyncio.CancelledError:
                pass

        stderr_task = asyncio.create_task(forward_stderr(), name=f"err-{peer_str}")

        # Idle timeout loop
        pipe_tasks = {client_to_proc, proc_to_client}
        while pipe_tasks:
            idle_event.clear()
            done, pipe_tasks = await asyncio.wait(
                pipe_tasks,
                timeout=IDLE_TIMEOUT_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done and not idle_event.is_set():
                # Timeout with no data transfer
                logger.warning("idle timeout (%ds) for %s", IDLE_TIMEOUT_S, peer_str)
                break
            # If a pipe finished, the remaining one will finish soon
            if done:
                break

        # Cancel remaining tasks
        for task in pipe_tasks:
            task.cancel()
        stderr_task.cancel()
        await asyncio.gather(*pipe_tasks, stderr_task, return_exceptions=True)

    except asyncio.CancelledError:
        logger.info("connection cancelled: %s", peer_str)
    except Exception as exc:
        logger.error("unexpected error for %s: %s", peer_str, exc, exc_info=True)
    finally:
        # Kill subprocess if still running
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            logger.info("killed MCP subprocess pid=%d for %s", proc.pid, peer_str)

        # Close client connection
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except (ConnectionError, OSError):
            pass

        active_connections -= 1
        logger.info("connection closed: %s (active: %d)", peer_str, active_connections)


async def main() -> None:
    server = await asyncio.start_server(
        handle_connection,
        host="0.0.0.0",
        port=PORT,
        limit=PREFACE_MAX_BYTES,
    )
    addrs = [s.getsockname() for s in server.sockets]
    logger.info("MCP TCP server listening on %s (max %d connections)", addrs, MAX_CONNECTIONS)

    # Handle graceful shutdown
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def signal_handler():
        logger.info("shutdown signal received")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    async with server:
        await stop.wait()
        logger.info("shutting down")


if __name__ == "__main__":
    asyncio.run(main())
