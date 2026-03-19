/**
 * Standalone credential proxy for NanoClaw sidecar.
 * Runs in its own container on two networks:
 *   - nanoclaw-sandbox (internal, no internet) — SDR containers connect here
 *   - nanoclaw-egress (normal) — this proxy reaches api.anthropic.com
 *
 * Env vars:
 *   ANTHROPIC_API_KEY  — injected into every proxied request
 *   PROXY_PORT         — listen port (default 3001)
 *
 * Upstream is hardcoded to https://api.anthropic.com (not configurable).
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

const server = createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    const headers = { ...req.headers, host: UPSTREAM_HOST, "content-length": body.length };

    // Strip hop-by-hop headers
    delete headers.connection;
    delete headers["keep-alive"];
    delete headers["transfer-encoding"];

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
        res.writeHead(502);
        res.end("Bad Gateway");
      }
    });

    upstream.write(body);
    upstream.end();
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Credential proxy listening on 0.0.0.0:${PORT} -> https://${UPSTREAM_HOST}`);
});

process.on("SIGTERM", () => {
  console.log("Shutting down proxy");
  server.close();
  process.exit(0);
});
