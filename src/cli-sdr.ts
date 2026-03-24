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

import { execSync } from 'child_process';
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
    'dry-run': { type: 'boolean', default: false },
  },
  strict: true,
});

const runId = args['run-id'];
const actorId = args['actor-id'];
const channel = args.channel;
const account = args.account;
const dryRun = args['dry-run'] ?? false;

if (!runId || !actorId || !channel || !account) {
  console.error(
    'Usage: node dist/cli-sdr.js --run-id <id> --actor-id <id> --channel <id> --account <name> [--dry-run]',
  );
  process.exit(2);
}

// --- SDR headless prompt ---

const logInstruction = dryRun
  ? 'Do NOT call log_outreach. Return the JSON result only.'
  : `If SKIP: call log_outreach(status="skipped") with the reason.
If PROCEED: call log_outreach(status="draft") with all fields (account_id, crm_contact_id, angle, why_now, draft_text).`;

const SDR_HEADLESS_PROMPT = `You are running in headless SDR mode for account: ${account}
${dryRun ? '\n** DRY RUN MODE ** Do not write to Airtable.\n' : ''}
Execute this workflow in order. Stop early if the gate fails.

Step 1: Call get_account_score for "${account}".
Step 2: Call get_timing_signals for the account. If ZERO signals, SKIP immediately. Do not proceed to later steps.
Step 3: Call get_best_contacts for the account.
Step 4: Pick the best contact and angle based on the data.
Step 5: Draft a 4-line email.
Step 6: ${logInstruction}

Your final message MUST be a single raw JSON object (no markdown fences):
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
}`;

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
    { runId, account, actorId, channel, dryRun },
    'Starting headless SDR run',
  );

  // Collect streaming outputs. The agent-runner emits a result marker when done.
  let lastOutput: ContainerOutput | undefined;
  let containerName: string | null = null;

  try {
    await runContainerAgent(
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
      (_proc, name) => {
        containerName = name;
        logger.info({ containerName: name }, 'SDR container started');
      },
      // Streaming callback: capture result and exit immediately.
      // For headless mode, we don't wait for the container to close.
      async (output) => {
        lastOutput = output;
        // Kill the container — agent already emitted its result
        if (containerName) {
          try {
            execSync(`docker stop ${containerName}`, {
              timeout: 10000,
              stdio: 'ignore',
            });
          } catch {
            /* container may already be stopped */
          }
        }
      },
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

  if (!lastOutput || lastOutput.status === 'error') {
    console.log(
      JSON.stringify({
        decision: 'ERROR',
        account,
        error:
          (lastOutput as ContainerOutput | undefined)?.error ||
          'No output from container',
      }),
    );
    process.exit(1);
  }

  // The agent's final message should be raw JSON. Try to extract it.
  const raw = (lastOutput as ContainerOutput).result || '';
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
