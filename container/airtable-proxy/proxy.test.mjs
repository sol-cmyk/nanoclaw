/**
 * Tests for Airtable proxy (proxy.mjs).
 * Spawns the proxy with a dummy AIRTABLE_TOKEN, sends requests, checks responses.
 * No external dependencies — uses Node.js stdlib only.
 */
import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROXY_PATH = join(__dirname, "proxy.mjs");
const PORT = 13002; // high port to avoid conflicts
const TOKEN = "test_token_abc123";

let proxyProc = null;
let passed = 0;
let failed = 0;

function request(method, path, body = null) {
  return new Promise((resolve, reject) => {
    const opts = { hostname: "127.0.0.1", port: PORT, method, path };
    const req = http.request(opts, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () =>
        resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() })
      );
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function assert(label, condition) {
  if (condition) {
    console.log(`  ✓ ${label}`);
    passed++;
  } else {
    console.error(`  ✗ ${label}`);
    failed++;
  }
}

async function startProxy() {
  proxyProc = spawn("node", [PROXY_PATH], {
    env: { ...process.env, AIRTABLE_TOKEN: TOKEN, PROXY_PORT: String(PORT) },
    stdio: ["ignore", "pipe", "pipe"],
  });

  // Wait for proxy to be ready
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Proxy startup timeout")), 5000);
    proxyProc.stdout.on("data", (data) => {
      if (data.toString().includes("listening")) {
        clearTimeout(timeout);
        resolve();
      }
    });
    proxyProc.stderr.on("data", (data) => {
      const msg = data.toString().trim();
      if (msg) console.error(`[proxy stderr] ${msg}`);
    });
    proxyProc.on("error", (e) => { clearTimeout(timeout); reject(e); });
    proxyProc.on("exit", (code) => {
      if (code !== null) { clearTimeout(timeout); reject(new Error(`Proxy exited with ${code}`)); }
    });
  });
}

async function runTests() {
  console.log("Airtable Proxy Tests\n");

  // --- Allowed method tests (proxy will try to reach upstream, will get network error -> 502) ---
  // We can't reach api.airtable.com in test, but we can verify the proxy ACCEPTS the request
  // (returns 502 Bad Gateway from upstream failure, not 405/404 from our guards)

  console.log("GET /v0/app9snIQPsaND3WlM/SDR%20Outreach (allowed)");
  const getRes = await request("GET", "/v0/app9snIQPsaND3WlM/SDR%20Outreach");
  // 502 means it tried to proxy (allowed), not 404/405 (blocked)
  assert("GET accepted (status not 404/405)", getRes.status !== 404 && getRes.status !== 405);

  console.log("\nGET with query string (allowed)");
  const getQsRes = await request("GET", "/v0/app9snIQPsaND3WlM/SDR%20Outreach?filterByFormula=Name%3D%22test%22");
  assert("GET+query accepted (status not 404/405)", getQsRes.status !== 404 && getQsRes.status !== 405);

  console.log("\nPOST /v0/app9snIQPsaND3WlM/SDR%20Outreach (allowed)");
  const postRes = await request("POST", "/v0/app9snIQPsaND3WlM/SDR%20Outreach", '{"fields":{}}');
  assert("POST accepted (status not 404/405)", postRes.status !== 404 && postRes.status !== 405);

  console.log("\nPATCH /v0/app9snIQPsaND3WlM/SDR%20Outreach (allowed)");
  const patchRes = await request("PATCH", "/v0/app9snIQPsaND3WlM/SDR%20Outreach", '{"records":[]}');
  assert("PATCH accepted (status not 404/405)", patchRes.status !== 404 && patchRes.status !== 405);

  // --- Blocked method tests ---
  console.log("\nDELETE (blocked)");
  const delRes = await request("DELETE", "/v0/app9snIQPsaND3WlM/SDR%20Outreach");
  assert("DELETE returns 405", delRes.status === 405);

  console.log("\nPUT (blocked)");
  const putRes = await request("PUT", "/v0/app9snIQPsaND3WlM/SDR%20Outreach", '{}');
  assert("PUT returns 405", putRes.status === 405);

  console.log("\nOPTIONS (blocked)");
  const optRes = await request("OPTIONS", "/v0/app9snIQPsaND3WlM/SDR%20Outreach");
  assert("OPTIONS returns 405", optRes.status === 405);

  // --- Blocked path tests ---
  console.log("\nGET /v0/app9snIQPsaND3WlM/OtherTable (wrong table)");
  const wrongTable = await request("GET", "/v0/app9snIQPsaND3WlM/OtherTable");
  assert("wrong table returns 404", wrongTable.status === 404);

  console.log("\nGET /v0/appOTHERBASE/SDR%20Outreach (wrong base)");
  const wrongBase = await request("GET", "/v0/appOTHERBASE/SDR%20Outreach");
  assert("wrong base returns 404", wrongBase.status === 404);

  console.log("\nGET / (root path)");
  const rootPath = await request("GET", "/");
  assert("root path returns 404", rootPath.status === 404);

  console.log("\nPOST /v1/messages (anthropic path)");
  const anthPath = await request("POST", "/v1/messages", '{}');
  assert("anthropic path returns 404", anthPath.status === 404);

  // --- Path traversal attempt ---
  console.log("\nGET /v0/app9snIQPsaND3WlM/SDR%20Outreach/../Users (traversal)");
  const traversal = await request("GET", "/v0/app9snIQPsaND3WlM/SDR%20Outreach/../Users");
  assert("traversal returns 404", traversal.status === 404);

  // --- Path suffix attack ---
  console.log("\nGET /v0/app9snIQPsaND3WlM/SDR%20OutreachExtra (suffix attack)");
  const suffix = await request("GET", "/v0/app9snIQPsaND3WlM/SDR%20OutreachExtra");
  assert("suffix attack returns 404", suffix.status === 404);

  // --- CONNECT block (raw TCP, since Node http client handles CONNECT differently) ---
  console.log("\nCONNECT method");
  const connResult = await new Promise((resolve) => {
    const sock = net.createConnection({ host: "127.0.0.1", port: PORT }, () => {
      sock.write("CONNECT api.airtable.com:443 HTTP/1.1\r\nHost: api.airtable.com:443\r\n\r\n");
    });
    let data = "";
    sock.on("data", (d) => { data += d.toString(); });
    sock.on("end", () => resolve(data));
    sock.on("close", () => resolve(data));
    setTimeout(() => { sock.destroy(); resolve(data); }, 2000);
  });
  assert("CONNECT returns 405", connResult.includes("405"));

  // --- Summary ---
  console.log(`\n${"=".repeat(40)}`);
  console.log(`Results: ${passed} passed, ${failed} failed`);
  console.log(`${"=".repeat(40)}`);
}

// --- Main ---
try {
  await startProxy();
  await runTests();
} catch (err) {
  console.error("Test setup failed:", err.message);
  failed++;
} finally {
  if (proxyProc) proxyProc.kill("SIGTERM");
  process.exit(failed > 0 ? 1 : 0);
}
