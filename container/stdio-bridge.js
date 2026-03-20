#!/usr/bin/env node
// stdio-bridge.js — Bridges Claude Agent SDK stdio MCP to the TCP-based MCP sidecar.
// Sends a JSON metadata preface, then raw-pipes stdin <-> TCP.

const net = require("net");

const host = process.env.MCP_SDR_HOST || "127.0.0.1";
const port = parseInt(process.env.MCP_SDR_PORT || "9000", 10);

const preface = JSON.stringify({
  version: 1,
  run_id: process.env.SDR_RUN_ID || "",
  actor_id: process.env.SDR_ACTOR_ID || "",
  channel: process.env.SDR_CHANNEL || "",
}) + "\n";

const socket = net.createConnection({ host, port, timeout: 10000 }, () => {
  socket.setTimeout(0); // clear connect timeout once connected
  socket.write(preface);
  process.stdin.pipe(socket);
  socket.pipe(process.stdout);
});

socket.on("timeout", () => { process.stderr.write("stdio-bridge: connect timeout\n"); process.exit(1); });
socket.on("error", (e) => { process.stderr.write(`stdio-bridge: ${e.message}\n`); process.exit(1); });
socket.on("close", () => process.exit(0));
process.stdin.on("error", () => process.exit(1));
process.stdout.on("error", () => process.exit(1));
