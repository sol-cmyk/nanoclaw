#!/usr/bin/env node
// stdio-bridge.cjs — Bridges Claude Agent SDK stdio MCP to the TCP-based MCP sidecar.
// Sends a JSON metadata preface, then raw-pipes stdin <-> TCP.
//
// Must be .cjs because the agent container's package.json has "type": "module".
// The SDK immediately sends JSON-RPC requests on stdin after launching this process.
// We buffer stdin until the TCP connection is established and the preface is sent,
// then flush the buffer and start piping.

const net = require("net");

const host = process.env.MCP_SDR_HOST || "127.0.0.1";
const port = parseInt(process.env.MCP_SDR_PORT || "9000", 10);

const preface = JSON.stringify({
  version: 1,
  run_id: process.env.SDR_RUN_ID || "",
  actor_id: process.env.SDR_ACTOR_ID || "",
  channel: process.env.SDR_CHANNEL || "",
}) + "\n";

// Buffer stdin while connecting
const stdinBuffer = [];
let connected = false;
let socket = null;

process.stdin.on("data", (chunk) => {
  if (connected && socket) {
    socket.write(chunk);
  } else {
    stdinBuffer.push(chunk);
  }
});

process.stdin.on("error", () => process.exit(1));
process.stdout.on("error", () => process.exit(1));

socket = net.createConnection({ host, port, timeout: 10000 }, () => {
  socket.setTimeout(0); // clear connect timeout
  socket.write(preface);

  // Flush buffered stdin
  for (const chunk of stdinBuffer) {
    socket.write(chunk);
  }
  stdinBuffer.length = 0;
  connected = true;

  // Pipe TCP responses to stdout
  socket.pipe(process.stdout);
});

socket.on("timeout", () => { process.stderr.write("stdio-bridge: connect timeout\n"); process.exit(1); });
socket.on("error", (e) => { process.stderr.write(`stdio-bridge: ${e.message}\n`); process.exit(1); });
socket.on("close", () => process.exit(0));
