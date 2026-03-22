/**
 * cli-sdr.ts — Headless NanoClaw SDR entrypoint.
 *
 * Called by the Cockpit bot via subprocess. Runs a single SDR prep
 * for one account and prints structured JSON to stdout.
 *
 * Usage:
 *   node dist/cli-sdr.js \
 *     --run-id sdr-abc123-1711100000 \
 *     --actor-id U0AEPEZGCNB \
 *     --channel C0AN2SVEM41 \
 *     --account Armis
 */

import fs from 'fs';
import path from 'path';
import { parseArgs } from 'util';

import { DATA_DIR, GROUPS_DIR } from './config.js';
import { ContainerOutput, runContainerAgent } from './container-runner.js';
import {
  ensureContainerRuntimeRunning,
  cleanupOrphans,
} from './container-runtime.js';
import { logger } from './logger.js';
import {
  ensureNetworks,
  ensureProxiesRunning,
  ensureMcpRunning,
  stopMcp,
  stopProxies,
} from './network-isolation.js';
import { RegisteredGroup } from './types.js';

// --- Argument parsing ---

const { values: args } = parseArgs({
  options: {
    'run-id': { type: 'string' },
    'actor-id': { type: 'string' },
    channel: { type: 'string' },
    account: { type: 'string' },
  },
  strict: true,
});

const runId = args['run-id'];
const actorId = args['actor-id'];
const channel = args.channel;
const account = args.account;

if (!runId || !actorId || !channel || !account) {
  console.error(
    'Usage: node dist/cli-sdr.js --run-id <id> --actor-id <id> --channel <id> --account <name>',
  );
  process.exit(2);
}

// --- SDR headless prompt ---

const SDR_HEADLESS_PROMPT = `You are running in headless SDR mode for account: ${account}

Follow the /sdr skill workflow (Steps 1-6) exactly. Then:

1. If SKIP: call log_outreach(status="skipped") with the reason, then return ONLY a JSON object.
2. If PROCEED: call log_outreach(status="draft") with all fields (account_id, crm_contact_id, angle, why_now, draft_text), then return ONLY a JSON object.

Your final message MUST be a single JSON object with these keys:
{
  "decision": "PROCEED" or "SKIP",
  "account": "<account name>",
  "fit": "<tier summary>",
  "contact_name": "<name or null>",
  "contact_title": "<title or null>",
  "why_person": "<1 sentence or null>",
  "why_now": "<timing signal summary or null>",
  "angle": "<chosen angle or null>",
  "data_cited": "<exact fact backing the angle or null>",
  "draft_email": "<full email text or null>",
  "skip_reason": "<reason or null>"
}

Do NOT post to Slack (you have no send_message tool). Do NOT wrap the JSON in markdown code fences. Return ONLY the raw JSON object as your final message.`;

// --- Main ---

async function main(): Promise<void> {
  // Ensure Docker is running and sidecars are up
  ensureContainerRuntimeRunning();
  cleanupOrphans();
  await ensureNetworks();
  await ensureProxiesRunning();
  await ensureMcpRunning();

  // Ensure group folder exists
  const groupDir = path.resolve(GROUPS_DIR, 'slack_sdr');
  fs.mkdirSync(path.join(groupDir, 'logs'), { recursive: true });

  const group: RegisteredGroup = {
    name: 'sdr-headless',
    folder: 'slack_sdr',
    trigger: '',
    added_at: new Date().toISOString(),
    requiresTrigger: false,
    isMain: true,
  };

  logger.info(
    { runId, account, actorId, channel },
    'Starting headless SDR run',
  );

  let output: ContainerOutput;
  try {
    output = await runContainerAgent(
      group,
      {
        prompt: SDR_HEADLESS_PROMPT,
        groupFolder: 'slack_sdr',
        chatJid: `cli-sdr-${runId}`,
        isMain: true,
        actorId,
        channelName: channel,
        runId,
        headless: true,
      },
      (_proc, containerName) => {
        logger.info({ containerName }, 'SDR container started');
      },
      // No streaming callback — legacy mode, parse final stdout
    );
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    console.log(
      JSON.stringify({
        decision: 'ERROR',
        account,
        error: errorMsg,
      }),
    );
    process.exit(1);
  }

  if (output.status === 'error') {
    console.log(
      JSON.stringify({
        decision: 'ERROR',
        account,
        error: output.error || 'Container exited with error',
      }),
    );
    process.exit(1);
  }

  // The agent's final message should be raw JSON. Try to extract it.
  const raw = output.result || '';
  let parsed: Record<string, unknown> | null = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Agent may have wrapped it in text. Try to find JSON in the output.
    const jsonMatch = raw.match(/\{[\s\S]*"decision"[\s\S]*\}/);
    if (jsonMatch) {
      try {
        parsed = JSON.parse(jsonMatch[0]);
      } catch {
        // give up
      }
    }
  }

  if (parsed && typeof parsed === 'object' && 'decision' in parsed) {
    console.log(JSON.stringify(parsed));
  } else {
    // Couldn't parse structured output — return raw text as fallback
    console.log(
      JSON.stringify({
        decision: 'ERROR',
        account,
        error: 'Could not parse agent output as JSON',
        raw_output: raw.slice(0, 2000),
      }),
    );
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(`cli-sdr fatal: ${err}`);
  process.exit(1);
});
