/**
 * Standalone credential proxy for NanoClaw sidecar.
 * Runs in its own container on two networks:
 *   - nanoclaw-sandbox (internal, no internet) — SDR containers connect here
 *   - nanoclaw-egress (normal) — this proxy reaches api.anthropic.com
 *
 * Env vars:
 *   ANTHROPIC_API_KEY  — injected into every proxied request
 *   PROXY_PORT         — listen port (default 3001)
 *   UPSTREAM_URL       — Anthropic API base (default https://api.anthropic.com)
 */
import { createServer } from "node:http";
import { request as httpsRequest } from "node:https";
import { request as httpRequest } from "node:http";

const API_KEY = process.env.ANTHROPIC_API_KEY;
if (!API_KEY) {
  console.error("FATAL: ANTHROPIC_API_KEY not set");
  process.exit(1);
}

const PORT = parseInt(process.env.PROXY_PORT || "3001", 10);
const UPSTREAM = new URL(process.env.UPSTREAM_URL || "https://api.anthropic.com");
const isHttps = UPSTREAM.protocol === "https:";
const makeRequest = isHttps ? httpsRequest : httpRequest;

const server = createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    const headers = { ...req.headers, host: UPSTREAM.host, "content-length": body.length };

    // Strip hop-by-hop headers
    delete headers.connection;
    delete headers["keep-alive"];
    delete headers["transfer-encoding"];

    // Inject real API key (container sends "placeholder")
    delete headers["x-api-key"];
    headers["x-api-key"] = API_KEY;

    const upstream = makeRequest(
      {
        hostname: UPSTREAM.hostname,
        port: UPSTREAM.port || (isHttps ? 443 : 80),
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
  console.log(`Credential proxy listening on 0.0.0.0:${PORT} -> ${UPSTREAM.origin}`);
});

process.on("SIGTERM", () => {
  console.log("Shutting down proxy");
  server.close();
  process.exit(0);
});
