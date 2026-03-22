import os from 'os';
import path from 'path';

import { readEnvFile } from './env.js';

// Read config values from .env (falls back to process.env).
// Secrets (API keys, tokens) are NOT read here — they are loaded only
// by the credential proxy (credential-proxy.ts), never exposed to containers.
const envConfig = readEnvFile(['ASSISTANT_NAME', 'ASSISTANT_HAS_OWN_NUMBER']);

export const ASSISTANT_NAME =
  process.env.ASSISTANT_NAME || envConfig.ASSISTANT_NAME || 'Andy';
export const ASSISTANT_HAS_OWN_NUMBER =
  (process.env.ASSISTANT_HAS_OWN_NUMBER ||
    envConfig.ASSISTANT_HAS_OWN_NUMBER) === 'true';
export const POLL_INTERVAL = 2000;
export const SCHEDULER_POLL_INTERVAL = 60000;

// Absolute paths needed for container mounts
const PROJECT_ROOT = process.cwd();
const HOME_DIR = process.env.HOME || os.homedir();

// Mount security: allowlist stored OUTSIDE project root, never mounted into containers
export const MOUNT_ALLOWLIST_PATH = path.join(
  HOME_DIR,
  '.config',
  'nanoclaw',
  'mount-allowlist.json',
);
export const SENDER_ALLOWLIST_PATH = path.join(
  HOME_DIR,
  '.config',
  'nanoclaw',
  'sender-allowlist.json',
);
export const STORE_DIR = path.resolve(PROJECT_ROOT, 'store');
export const GROUPS_DIR = path.resolve(PROJECT_ROOT, 'groups');
export const DATA_DIR = path.resolve(PROJECT_ROOT, 'data');

export const CONTAINER_IMAGE =
  process.env.CONTAINER_IMAGE || 'nanoclaw-agent:latest';
export const CONTAINER_TIMEOUT = parseInt(
  process.env.CONTAINER_TIMEOUT || '1800000',
  10,
);
export const CONTAINER_MAX_OUTPUT_SIZE = parseInt(
  process.env.CONTAINER_MAX_OUTPUT_SIZE || '10485760',
  10,
); // 10MB default
export const CREDENTIAL_PROXY_PORT = parseInt(
  process.env.CREDENTIAL_PROXY_PORT || '3001',
  10,
);
export const IPC_POLL_INTERVAL = 1000;
export const IDLE_TIMEOUT = parseInt(process.env.IDLE_TIMEOUT || '1800000', 10); // 30min default — how long to keep container alive after last result
export const MAX_CONCURRENT_CONTAINERS = Math.max(
  1,
  parseInt(process.env.MAX_CONCURRENT_CONTAINERS || '5', 10) || 5,
);

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export const TRIGGER_PATTERN = new RegExp(
  `^@${escapeRegex(ASSISTANT_NAME)}\\b`,
  'i',
);

// SDR MCP sidecar data mounts (host paths → container /data/)
// Only starts MCP sidecar when at least SCORER_DIR and CRM_DIR are configured.
const sdrEnv = readEnvFile([
  'SDR_SCORER_DIR',
  'SDR_CRM_DIR',
  'SDR_ECOSYSTEM_PEOPLE_FILE',
  'SDR_SIGNALS_FILE',
  'SDR_CLAY_PROFILES',
]);

export interface SdrDataMount {
  hostPath: string;
  containerPath: string;
}

export const SDR_DATA_MOUNTS: SdrDataMount[] = (() => {
  const mounts: SdrDataMount[] = [];
  const scorerDir =
    process.env.SDR_SCORER_DIR || sdrEnv.SDR_SCORER_DIR;
  const crmDir = process.env.SDR_CRM_DIR || sdrEnv.SDR_CRM_DIR;
  const ecosystemFile =
    process.env.SDR_ECOSYSTEM_PEOPLE_FILE ||
    sdrEnv.SDR_ECOSYSTEM_PEOPLE_FILE;
  const signalsFile =
    process.env.SDR_SIGNALS_FILE || sdrEnv.SDR_SIGNALS_FILE;
  const clayProfiles =
    process.env.SDR_CLAY_PROFILES || sdrEnv.SDR_CLAY_PROFILES;

  if (scorerDir) mounts.push({ hostPath: scorerDir, containerPath: '/data/scorer' });
  if (crmDir) mounts.push({ hostPath: crmDir, containerPath: '/data/crm' });
  if (ecosystemFile)
    mounts.push({ hostPath: ecosystemFile, containerPath: '/data/ecosystem-people.json' });
  if (signalsFile)
    mounts.push({ hostPath: signalsFile, containerPath: '/data/signals.json' });
  if (clayProfiles)
    mounts.push({ hostPath: clayProfiles, containerPath: '/data/clay-profiles.json' });

  return mounts;
})();

// Timezone for scheduled tasks (cron expressions, etc.)
// Uses system timezone by default
export const TIMEZONE =
  process.env.TZ || Intl.DateTimeFormat().resolvedOptions().timeZone;
