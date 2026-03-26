/**
 * Network isolation for NanoClaw containers (Option C: 4-network topology).
 *
 * Four Docker networks:
 *   nanoclaw-control (--internal): agent + MCP sidecar communicate here.
 *   nanoclaw-agent-egress (--internal): agent + Anthropic proxy.
 *   nanoclaw-mcp-egress (--internal): MCP sidecar + Airtable proxy.
 *   nanoclaw-egress: both proxies have internet access here.
 *
 * Isolation guarantees:
 *   - Agent CANNOT reach Airtable proxy (no shared network).
 *   - MCP sidecar CANNOT reach Anthropic proxy (no shared network).
 */
import { exec } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { promisify } from 'util';

import {
  CONTAINER_RUNTIME_BIN,
  readonlyMountArgs,
} from './container-runtime.js';
import { SDR_DATA_MOUNTS } from './config.js';
import { logger } from './logger.js';
import { readEnvFile } from './env.js';

const execAsync = promisify(exec);
const docker = CONTAINER_RUNTIME_BIN;

// --- Networks ---
export const CONTROL_NETWORK = 'nanoclaw-control';
export const AGENT_EGRESS_NETWORK = 'nanoclaw-agent-egress';
export const MCP_EGRESS_NETWORK = 'nanoclaw-mcp-egress';
export const EGRESS_NETWORK = 'nanoclaw-egress';

// --- Anthropic proxy ---
export const ANTHROPIC_PROXY_CONTAINER_NAME = 'nanoclaw-anthropic-proxy';
export const ANTHROPIC_PROXY_IMAGE = 'nanoclaw-anthropic-proxy:latest';
export const ANTHROPIC_PROXY_PORT = 3001;

// --- Airtable proxy ---
export const AIRTABLE_PROXY_CONTAINER_NAME = 'nanoclaw-airtable-proxy';
export const AIRTABLE_PROXY_IMAGE = 'nanoclaw-airtable-proxy:latest';
export const AIRTABLE_PROXY_PORT = 3002;

// --- MCP SDR sidecar ---
export const MCP_CONTAINER_NAME = 'nanoclaw-mcp';
export const MCP_IMAGE = 'nanoclaw-mcp-sdr:latest';
export const MCP_PORT = 9000;

async function dockerExec(cmd: string): Promise<string> {
  const { stdout } = await execAsync(`${docker} ${cmd}`, { timeout: 15000 });
  return stdout.trim();
}

async function networkExists(name: string): Promise<boolean> {
  try {
    await dockerExec(`network inspect ${name}`);
    return true;
  } catch {
    return false;
  }
}

async function containerRunning(name: string): Promise<boolean> {
  try {
    const out = await dockerExec(`inspect -f "{{.State.Running}}" ${name}`);
    return out === 'true';
  } catch {
    return false;
  }
}

async function ensureNetwork(name: string, internal: boolean): Promise<void> {
  if (await networkExists(name)) return;
  const internalFlag = internal ? ' --internal' : '';
  await dockerExec(`network create${internalFlag} ${name}`);
  logger.info(
    { network: name, internal },
    `Created network${internal ? ' (internal, no internet)' : ''}`,
  );
}

export async function ensureNetworks(): Promise<void> {
  // Create egress (bridge) first — proxies start on this network
  await ensureNetwork(EGRESS_NETWORK, false);
  // Internal networks
  await ensureNetwork(CONTROL_NETWORK, true);
  await ensureNetwork(AGENT_EGRESS_NETWORK, true);
  await ensureNetwork(MCP_EGRESS_NETWORK, true);
}

// --- Proxy lifecycle helpers ---

async function startProxy(opts: {
  name: string;
  image: string;
  port: number;
  primaryNetwork: string;
  secondaryNetwork: string;
  envFileContent: string;
  envFileSuffix: string;
}): Promise<void> {
  if (await containerRunning(opts.name)) {
    logger.debug({ container: opts.name }, 'Proxy container already running');
    return;
  }

  // Remove stale container if exists but not running
  try {
    await dockerExec(`rm -f ${opts.name}`);
  } catch {
    /* ignore */
  }

  const envFilePath = path.join(
    os.tmpdir(),
    `.nanoclaw-${opts.envFileSuffix}-env`,
  );
  try {
    fs.writeFileSync(envFilePath, opts.envFileContent, { mode: 0o600 });

    // Start on egress network first (internet access)
    await dockerExec(
      `run -d --rm ` +
        `--name ${opts.name} ` +
        `--network ${opts.primaryNetwork} ` +
        `--env-file ${envFilePath} ` +
        `--user 9999:9999 ` +
        `--read-only ` +
        `--cap-drop=ALL ` +
        `--security-opt=no-new-privileges:true ` +
        `--memory=256m ` +
        `--cpus=0.5 ` +
        `--pids-limit=64 ` +
        opts.image,
    );
  } finally {
    try {
      fs.unlinkSync(envFilePath);
    } catch {
      /* ignore */
    }
  }

  // Connect to second network so internal containers can reach this proxy
  await dockerExec(`network connect ${opts.secondaryNetwork} ${opts.name}`);

  logger.info(
    { container: opts.name, port: opts.port },
    `Proxy started (${opts.primaryNetwork} + ${opts.secondaryNetwork})`,
  );
}

export async function ensureAnthropicProxyRunning(): Promise<void> {
  const secrets = readEnvFile(['ANTHROPIC_API_KEY']);
  if (!secrets.ANTHROPIC_API_KEY) {
    throw new Error(
      'ANTHROPIC_API_KEY not found in .env — cannot start Anthropic proxy',
    );
  }

  await startProxy({
    name: ANTHROPIC_PROXY_CONTAINER_NAME,
    image: ANTHROPIC_PROXY_IMAGE,
    port: ANTHROPIC_PROXY_PORT,
    primaryNetwork: EGRESS_NETWORK,
    secondaryNetwork: AGENT_EGRESS_NETWORK,
    envFileContent: `ANTHROPIC_API_KEY=${secrets.ANTHROPIC_API_KEY}\n`,
    envFileSuffix: 'anthropic-proxy',
  });
}

export async function ensureAirtableProxyRunning(): Promise<void> {
  const secrets = readEnvFile(['AIRTABLE_TOKEN']);
  if (!secrets.AIRTABLE_TOKEN) {
    throw new Error(
      'AIRTABLE_TOKEN not found in .env — cannot start Airtable proxy',
    );
  }

  await startProxy({
    name: AIRTABLE_PROXY_CONTAINER_NAME,
    image: AIRTABLE_PROXY_IMAGE,
    port: AIRTABLE_PROXY_PORT,
    primaryNetwork: EGRESS_NETWORK,
    secondaryNetwork: MCP_EGRESS_NETWORK,
    envFileContent: `AIRTABLE_TOKEN=${secrets.AIRTABLE_TOKEN}\n`,
    envFileSuffix: 'airtable-proxy',
  });
}

async function stopContainerByName(name: string): Promise<void> {
  try {
    await dockerExec(`stop ${name}`);
    logger.info({ container: name }, 'Proxy stopped');
  } catch {
    /* may not be running */
  }
}

export async function stopAnthropicProxy(): Promise<void> {
  await stopContainerByName(ANTHROPIC_PROXY_CONTAINER_NAME);
}

export async function stopAirtableProxy(): Promise<void> {
  await stopContainerByName(AIRTABLE_PROXY_CONTAINER_NAME);
}

// --- MCP SDR sidecar lifecycle ---

export async function ensureMcpRunning(): Promise<void> {
  if (SDR_DATA_MOUNTS.length === 0) {
    logger.warn(
      'SDR data mounts not configured (set SDR_SCORER_DIR, SDR_CRM_DIR in .env) — skipping MCP sidecar',
    );
    return;
  }

  // Fail fast: validate all required inputs exist before starting Docker.
  // The 4 required paths map to config.py's required env vars.
  const REQUIRED_CONTAINER_PATHS = [
    '/data/scorer',
    '/data/crm',
    '/data/ecosystem-people.csv',
    '/data/signals.jsonl',
  ];
  const configuredPaths = new Set(SDR_DATA_MOUNTS.map((m) => m.containerPath));
  const missing = REQUIRED_CONTAINER_PATHS.filter(
    (p) => !configuredPaths.has(p),
  );
  if (missing.length > 0) {
    logger.error(
      { missing },
      'MCP sidecar cannot start: required SDR data mounts are missing. ' +
        'Set SDR_SCORER_DIR, SDR_CRM_DIR, SDR_ECOSYSTEM_PEOPLE_FILE, and SDR_SIGNALS_FILE in .env',
    );
    return;
  }

  // Also validate host paths actually exist
  for (const m of SDR_DATA_MOUNTS) {
    if (!fs.existsSync(m.hostPath)) {
      logger.error(
        { hostPath: m.hostPath, containerPath: m.containerPath },
        'MCP sidecar cannot start: host data path does not exist',
      );
      return;
    }
  }

  if (await containerRunning(MCP_CONTAINER_NAME)) {
    logger.debug(
      { container: MCP_CONTAINER_NAME },
      'MCP sidecar already running',
    );
    return;
  }

  // Remove stale container if exists but not running
  try {
    await dockerExec(`rm -f ${MCP_CONTAINER_NAME}`);
  } catch {
    /* ignore */
  }

  // Build data mount args (all read-only)
  const mountArgs: string[] = [];
  for (const m of SDR_DATA_MOUNTS) {
    mountArgs.push(...readonlyMountArgs(m.hostPath, m.containerPath));
  }

  // Read Airtable + Postgres config from .env (tokens stay in proxies, not here)
  const airtableEnv = readEnvFile([
    'AIRTABLE_BASE_ID',
    'AIRTABLE_INTERACTIONS_TABLE',
    'POSTGRES_DSN',
  ]);
  const airtableBaseId =
    process.env.AIRTABLE_BASE_ID || airtableEnv.AIRTABLE_BASE_ID;
  const airtableTable =
    process.env.AIRTABLE_INTERACTIONS_TABLE ||
    airtableEnv.AIRTABLE_INTERACTIONS_TABLE ||
    'SDR Outreach';

  if (!airtableBaseId) {
    logger.warn(
      'AIRTABLE_BASE_ID not set — MCP sidecar will start without Airtable',
    );
  }

  // Env vars the MCP server's config.py expects (container /data/ paths).
  // Written to a temp env-file to avoid shell quoting issues with spaces in values.
  const envLines = [
    'SCORER_DIR=/data/scorer',
    'CRM_DIR=/data/crm',
    'ECOSYSTEM_PEOPLE_FILE=/data/ecosystem-people.csv',
    'SIGNALS_FILE=/data/signals.jsonl',
    'CLAY_PROFILES=/data/clay-profiles.jsonl',
    `AIRTABLE_BASE_URL=http://${AIRTABLE_PROXY_CONTAINER_NAME}:${AIRTABLE_PROXY_PORT}`,
  ];
  if (airtableBaseId) {
    envLines.push(`AIRTABLE_BASE_ID=${airtableBaseId}`);
  }
  envLines.push(`AIRTABLE_INTERACTIONS_TABLE=${airtableTable}`);

  // Postgres backbone: pass DSN so MCP sidecar can use Postgres-first reads/writes.
  // For local dev, DSN should use host.docker.internal (routed via --add-host below).
  // For Fargate, DSN points directly to EC2 private IP.
  const postgresDsn = process.env.POSTGRES_DSN || airtableEnv.POSTGRES_DSN;
  if (postgresDsn) {
    envLines.push(`POSTGRES_DSN=${postgresDsn}`);
    logger.info('Postgres DSN configured for MCP sidecar');
  } else {
    logger.info(
      'POSTGRES_DSN not set — MCP sidecar will use file fallback only',
    );
  }

  const mcpEnvFilePath = path.join(os.tmpdir(), '.nanoclaw-mcp-sdr-env');
  try {
    fs.writeFileSync(mcpEnvFilePath, envLines.join('\n') + '\n', {
      mode: 0o600,
    });

    // Start on CONTROL_NETWORK (agent can reach it), then connect to MCP_EGRESS_NETWORK.
    // --add-host lets the container reach the Docker host (for SSH-tunneled Postgres in local dev).
    // On Fargate, POSTGRES_DSN points directly to EC2 so this flag is harmless.
    await dockerExec(
      `run -d --rm ` +
        `--name ${MCP_CONTAINER_NAME} ` +
        `--network ${CONTROL_NETWORK} ` +
        `--env-file ${mcpEnvFilePath} ` +
        `--add-host=host.docker.internal:host-gateway ` +
        `--user 9999:9999 ` +
        `--read-only ` +
        `--cap-drop=ALL ` +
        `--security-opt=no-new-privileges:true ` +
        `--memory=512m ` +
        `--cpus=1.0 ` +
        `--pids-limit=128 ` +
        `--tmpfs /tmp:rw,nosuid,size=64m ` +
        mountArgs.join(' ') +
        (mountArgs.length > 0 ? ' ' : '') +
        MCP_IMAGE,
    );
  } finally {
    try {
      fs.unlinkSync(mcpEnvFilePath);
    } catch {
      /* ignore */
    }
  }

  // Connect to MCP egress network so sidecar can reach Airtable proxy
  await dockerExec(
    `network connect ${MCP_EGRESS_NETWORK} ${MCP_CONTAINER_NAME}`,
  );

  // Connect to egress network so sidecar can reach Docker host (Postgres via SSH tunnel).
  // The MCP sidecar is trusted code — the security boundary is agent→MCP, not MCP→host.
  if (postgresDsn) {
    await dockerExec(
      `network connect ${EGRESS_NETWORK} ${MCP_CONTAINER_NAME}`,
    );
  }

  logger.info(
    { container: MCP_CONTAINER_NAME, port: MCP_PORT },
    `MCP sidecar started (${CONTROL_NETWORK} + ${MCP_EGRESS_NETWORK}${postgresDsn ? ' + ' + EGRESS_NETWORK : ''})`,
  );
}

export async function stopMcp(): Promise<void> {
  await stopContainerByName(MCP_CONTAINER_NAME);
}

// --- Convenience wrappers ---

export async function ensureProxiesRunning(): Promise<void> {
  await ensureAnthropicProxyRunning();
  await ensureAirtableProxyRunning();
}

export async function stopProxies(): Promise<void> {
  await stopAnthropicProxy();
  await stopAirtableProxy();
}
