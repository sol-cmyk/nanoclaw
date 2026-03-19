/**
 * Network isolation for NanoClaw containers.
 *
 * Two Docker networks:
 *   nanoclaw-sandbox (--internal): agent containers live here. No internet.
 *   nanoclaw-egress: credential proxy lives here + has internet access.
 *
 * The proxy container joins BOTH networks so agents can reach it
 * but cannot reach anything else.
 */
import { exec } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { promisify } from 'util';

import { CONTAINER_RUNTIME_BIN } from './container-runtime.js';
import { logger } from './logger.js';
import { readEnvFile } from './env.js';

const execAsync = promisify(exec);
const docker = CONTAINER_RUNTIME_BIN;

export const SANDBOX_NETWORK = 'nanoclaw-sandbox';
export const EGRESS_NETWORK = 'nanoclaw-egress';
export const PROXY_CONTAINER_NAME = 'nanoclaw-proxy';
export const PROXY_IMAGE = 'nanoclaw-proxy:latest';
export const PROXY_PORT = 3001;

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

export async function ensureNetworks(): Promise<void> {
  if (!(await networkExists(EGRESS_NETWORK))) {
    await dockerExec(`network create ${EGRESS_NETWORK}`);
    logger.info({ network: EGRESS_NETWORK }, 'Created egress network');
  }
  if (!(await networkExists(SANDBOX_NETWORK))) {
    await dockerExec(`network create --internal ${SANDBOX_NETWORK}`);
    logger.info(
      { network: SANDBOX_NETWORK },
      'Created sandbox network (internal, no internet)',
    );
  }
}

export async function ensureProxyRunning(): Promise<void> {
  if (await containerRunning(PROXY_CONTAINER_NAME)) {
    logger.debug('Credential proxy container already running');
    return;
  }

  // Stop stale container if exists but not running
  try {
    await dockerExec(`rm -f ${PROXY_CONTAINER_NAME}`);
  } catch {
    /* ignore */
  }

  const secrets = readEnvFile(['ANTHROPIC_API_KEY']);
  if (!secrets.ANTHROPIC_API_KEY) {
    throw new Error('ANTHROPIC_API_KEY not found in .env — cannot start proxy');
  }

  // Write a temporary env file for the proxy (avoids secret in docker run args / docker inspect)
  const envFilePath = path.join(os.tmpdir(), '.nanoclaw-proxy-env');
  fs.writeFileSync(
    envFilePath,
    `ANTHROPIC_API_KEY=${secrets.ANTHROPIC_API_KEY}\n`,
    {
      mode: 0o600,
    },
  );

  // Start proxy on egress network
  // --user 65534:65534 = nobody (non-root, matches Dockerfile USER proxyuser fallback)
  await dockerExec(
    `run -d --rm ` +
      `--name ${PROXY_CONTAINER_NAME} ` +
      `--network ${EGRESS_NETWORK} ` +
      `--env-file ${envFilePath} ` +
      `--user 9999:9999 ` +
      `--read-only ` +
      `--cap-drop=ALL ` +
      `--security-opt=no-new-privileges:true ` +
      PROXY_IMAGE,
  );

  // Clean up env file immediately (container already read it)
  try {
    fs.unlinkSync(envFilePath);
  } catch {
    /* ignore */
  }

  // Connect proxy to sandbox so agent containers can reach it
  await dockerExec(
    `network connect ${SANDBOX_NETWORK} ${PROXY_CONTAINER_NAME}`,
  );

  logger.info(
    { container: PROXY_CONTAINER_NAME, port: PROXY_PORT },
    'Credential proxy sidecar started (sandbox + egress networks)',
  );
}

export async function stopProxy(): Promise<void> {
  try {
    await dockerExec(`stop ${PROXY_CONTAINER_NAME}`);
    logger.info('Credential proxy sidecar stopped');
  } catch {
    /* may not be running */
  }
}
