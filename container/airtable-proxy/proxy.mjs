/**
 * Airtable-only credential proxy for NanoClaw Option C.
 * Runs in its own container on two networks:
 *   - nanoclaw-mcp-egress (internal) — MCP sidecar connects here
 *   - nanoclaw-egress (bridge) — this proxy reaches api.airtable.com
 *
 * Security:
 *   - Allows ONLY GET/POST/PATCH to /v0/{base_id}/SDR%20Outreach
 *   - GET may include query string (e.g., ?filterByFormula=...)
 *   - Blocks CONNECT method
 *   - Does NOT follow upstream redirects (returns them as-is)
 *   - Injects Authorization Bearer token, strips hop-by-hop headers
 *   - Runs as non-root with --read-only and --cap-drop=ALL at runtime
 *
 * Env vars:
 *   AIRTABLE_TOKEN — injected into every proxied request
 *   PROXY_PORT     — listen port (default 3002)
 */
import { createServer } from "node:http";
import { request as httpsRequest } from "node:https";

const TOKEN = process.env.AIRTABLE_TOKEN;
if (!TOKEN) {
  console.error("FATAL: AIRTABLE_TOKEN not set");
  process.exit(1);
}

const PORT = parseInt(process.env.PROXY_PORT || "3002", 10);
const UPSTREAM_HOST = "api.airtable.com";
const UPSTREAM_PORT = 443;

const BASE_ID = "app9snIQPsaND3WlM";
const TABLE_NAME = "SDR%20Outreach";
const ALLOWED_PATH_PREFIX = `/v0/${BASE_ID}/${TABLE_NAME}`;

const ALLOWED_METHODS = new Set(["GET", "POST", "PATCH"]);

/** Check if the request URL matches the allowed table path. */
function isAllowedPath(url) {
  // Split off query string
  const qIdx = url.indexOf("?");
  const pathname = qIdx === -1 ? url : url.slice(0, qIdx);
  // Exact match on the table path (no traversal beyond it)
  return pathname === ALLOWED_PATH_PREFIX;
}

const server = createServer((req, res) => {
  // Block CONNECT
  if (req.method === "CONNECT") {
    res.writeHead(405, { "content-type": "text/plain" });
    res.end("Method Not Allowed");
    return;
  }

  // Reject disallowed methods
  if (!ALLOWED_METHODS.has(req.method)) {
    res.writeHead(405, { "content-type": "text/plain" });
    res.end("Method Not Allowed");
    return;
  }

  // Reject paths not matching the allowed table
  if (!isAllowedPath(req.url)) {
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("Not Found");
    return;
  }

  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    const headers = { ...req.headers, host: UPSTREAM_HOST };

    // Set content-length for methods with body
    if (req.method === "POST" || req.method === "PATCH") {
      headers["content-length"] = body.length;
    } else {
      delete headers["content-length"];
    }

    // Strip hop-by-hop headers
    delete headers.connection;
    delete headers["keep-alive"];
    delete headers["transfer-encoding"];
    delete headers["proxy-authorization"];
    delete headers["proxy-connection"];

    // Inject real Bearer token (container sends placeholder)
    delete headers.authorization;
    headers.authorization = `Bearer ${TOKEN}`;

    // Build upstream path: always use ALLOWED_PATH_PREFIX + original query string
    const qIdx = req.url.indexOf("?");
    const upstreamPath =
      qIdx === -1 ? ALLOWED_PATH_PREFIX : ALLOWED_PATH_PREFIX + req.url.slice(qIdx);

    const upstream = httpsRequest(
      {
        hostname: UPSTREAM_HOST,
        port: UPSTREAM_PORT,
        path: upstreamPath,
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

    if (req.method === "POST" || req.method === "PATCH") {
      upstream.write(body);
    }
    upstream.end();
  });
});

// Reject CONNECT at the server level (HTTP CONNECT tunneling)
server.on("connect", (req, socket) => {
  socket.write("HTTP/1.1 405 Method Not Allowed\r\n\r\n");
  socket.destroy();
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Airtable proxy listening on 0.0.0.0:${PORT} -> https://${UPSTREAM_HOST}`);
  console.log(`Allowed: ${[...ALLOWED_METHODS].join("/")} ${ALLOWED_PATH_PREFIX}`);
});

process.on("SIGTERM", () => {
  console.log("Shutting down proxy");
  server.close();
  process.exit(0);
});
