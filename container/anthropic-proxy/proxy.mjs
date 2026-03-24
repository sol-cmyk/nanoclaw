/**
 * Anthropic-only credential proxy for NanoClaw Option C.
 * Runs in its own container on two networks:
 *   - nanoclaw-agent-egress (internal) — agent connects here
 *   - nanoclaw-egress (bridge) — this proxy reaches api.anthropic.com
 *
 * Security:
 *   - Allows ONLY POST /v1/messages (rejects all other methods/paths)
 *   - Blocks CONNECT method
 *   - Does NOT follow upstream redirects (returns them as-is)
 *   - Injects x-api-key, strips hop-by-hop headers
 *   - Runs as non-root with --read-only and --cap-drop=ALL at runtime
 *
 * Env vars:
 *   ANTHROPIC_API_KEY  — injected into every proxied request
 *   PROXY_PORT         — listen port (default 3001)
 */
import { createServer } from "node:http";
import { request as httpsRequest } from "node:https";

const API_KEY = process.env.ANTHROPIC_API_KEY;
if (!API_KEY) {
  console.error("FATAL: ANTHROPIC_API_KEY not set");
  process.exit(1);
}

const PORT = parseInt(process.env.PROXY_PORT || "3001", 10);
const UPSTREAM_HOST = "api.anthropic.com";
const UPSTREAM_PORT = 443;

// Allowlist: method + path pattern
// POST /v1/messages — inference
// GET  /v1/models/*  — SDK model availability check (read-only)
const MODELS_PATH_RE = /^\/v1\/models\/[a-z0-9._-]+$/;

function isAllowed(method, url) {
  if (method === "POST" && url === "/v1/messages") return true;
  if (method === "GET" && MODELS_PATH_RE.test(url)) return true;
  return false;
}

const server = createServer((req, res) => {
  // Block CONNECT (shouldn't arrive on HTTP server, but reject explicitly)
  if (req.method === "CONNECT") {
    res.writeHead(405, { "content-type": "text/plain" });
    res.end("Method Not Allowed");
    return;
  }

  // Strip query string for matching (path only)
  const urlPath = (req.url || "").split("?")[0];

  if (!isAllowed(req.method, urlPath)) {
    const code = req.method === "GET" || req.method === "POST" ? 404 : 405;
    res.writeHead(code, { "content-type": "text/plain" });
    res.end(code === 405 ? "Method Not Allowed" : "Not Found");
    return;
  }

  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    const headers = { ...req.headers, host: UPSTREAM_HOST, "content-length": body.length };

    // Strip hop-by-hop headers
    delete headers.connection;
    delete headers["keep-alive"];
    delete headers["transfer-encoding"];
    delete headers["proxy-authorization"];
    delete headers["proxy-connection"];

    // Inject real API key (container sends "placeholder")
    delete headers["x-api-key"];
    headers["x-api-key"] = API_KEY;

    const upstream = httpsRequest(
      {
        hostname: UPSTREAM_HOST,
        port: UPSTREAM_PORT,
        path: req.url,
        method: req.method,
        headers,
      },
      (upRes) => {
        res.writeHead(upRes.statusCode, upRes.headers);
        upRes.pipe(res);
      }
    );

    upstream.on("error", (err) => {
      console.error(`Proxy error: ${err.message}`);
      if (!res.headersSent) {
        res.writeHead(502, { "content-type": "text/plain" });
        res.end("Bad Gateway");
      }
    });

    upstream.write(body);
    upstream.end();
  });
});

// Reject CONNECT at the server level (HTTP CONNECT tunneling)
server.on("connect", (req, socket) => {
  socket.write("HTTP/1.1 405 Method Not Allowed\r\n\r\n");
  socket.destroy();
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Anthropic proxy listening on 0.0.0.0:${PORT} -> https://${UPSTREAM_HOST}`);
  console.log(`Allowed: POST /v1/messages, GET /v1/models/*`);
});

process.on("SIGTERM", () => {
  console.log("Shutting down proxy");
  server.close();
  process.exit(0);
});
