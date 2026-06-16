---
name: sdr
description: SDR outbound prep — orchestrates research, planning, drafting, and critique for one account at a time
---

# /sdr — SDR Outbound Workflow

Orchestrate outbound prep for one account. Dispatch specialized subagents for research, planning, drafting, and critique.

## Triggers

- `/sdr work <account>` — full workflow for one account
- `/sdr train` — training mode: draft emails for multiple accounts, collect human feedback

## Architecture

You are the orchestrator. You do NOT research, plan, draft, or critique emails yourself. You dispatch specialized agents:

1. **sdr-researcher** — Gathers account data, qualifies, picks contact, returns structured JSON
2. **sdr-email-planner** — Picks hook, pain, proof, CTA from research record
3. **sdr-email-drafter** — Writes one email from the plan
4. **sdr-email-critic** — Scores the draft, decides SHIP/REWRITE/KILL

## Tools Available

- `Agent` tool to dispatch subagents
- `mcp__nanoclaw__send_message` to post to Slack
- `mcp__flarion-sdr__log_outreach` to log to Airtable
- `Read` to load approved examples and proof points from `/workspace/group/email-data/`

## Standard Workflow (`/sdr work <account>`)

### Step 1: Dispatch researcher

```
Research account: <account name>
```

If decision = SKIP: post skip to Slack, log to Airtable, stop.
If decision = PROCEED: continue.

### Step 2: Enrich the research record

Before passing to the planner, add these fields to the research record:
- `sender`: "Sol" (default) or "Udi" if user specified
- `company_context`: read from `/workspace/group/company-context.md` — what Flarion is, what it is NOT, approved language, banned language, and qualitative patterns. Pass the full file contents.
- `approved_proof_points`: read from `/workspace/group/email-data/proof-points.md`
- `approved_examples`: read last 5 entries from `/workspace/group/email-data/approved-examples.jsonl` where `demo_only` is not `true`. Filter before passing — demo_only entries are contaminated training data and must not reach the drafter. If all entries are demo_only, pass an empty list.

### Step 3: Dispatch planner

Pass the enriched research record to `sdr-email-planner`. It returns a plan (hook, pain, proof, CTA, personalization guide, word target).

### Step 4: Dispatch drafter

Pass the plan + enriched research record + approved examples to `sdr-email-drafter`. It returns a draft with subject lines and word count.

The drafter must NOT put URLs in the email body. URLs flow to the human approval message in Step 7 instead, so the reviewer can verify claims without the prospect seeing source links.

### Step 5: Dispatch critic

Pass the draft + plan + research record + `company_context` to `sdr-email-critic`. It returns scores and a decision.

### Step 6: Handle critic decision

**SHIP**: Continue to Step 7.

**REWRITE** (max 2 cycles): Pass the critic's `rewrite_instructions` + original plan + research back to the drafter. Then re-run the critic on the new draft (include `company_context`). If it ships after rewrite, continue. If still REWRITE after 2 cycles, post both versions to Slack and let the human choose.

**KILL**: Post to Slack:
```
**Account:** [name]
**Decision:** KILL
**Reason:** [kill_reason from critic]
**Critic scores:** [summary]
```
Log as skipped. Stop.

### Step 7: Post for approval

```
**Account:** [name]
**Fit:** [fit_summary]
**Contact:** [contact_name], [contact_title]
**Why this person:** [why_this_person]
**Why now:** [trigger]
**Angle:** [angle from plan]
**Stage:** [outreach_stage] (touch [touch_number])

---

**Subject options:**
1. [subject 1]
2. [subject 2]

**Draft email:**

[email text]

---

**Sources to verify** (every specific claim in the email above traces back to one of these — click before approving):
- [evidence_url 1]
- [evidence_url 2]
- ...

**Until the claim registry exists: any account-specific specific (number, dollar figure, percentage, named event, direct quote, exec paraphrase) = REJECT immediately.** Source URLs are for verifying the signal type used as hook, not for unlocking specifics. An email that cites "$20K" with a valid source URL is still a reject. The only safe path is generic qualitative language.

---

**Critic scores:**
Specificity: [x]/5 | Relevance: [x]/5 | Brevity: [x]/5
Human-ness: [x]/5 | Stage fit: [x]/5 | Factual safety: [x]/5 | Positioning: [x]/5
Average: [x.x] | Rewrites: [0-2]

**Next:** Approve / Revise / Skip
```

### Step 8: Handle user response

**Approve:**
1. Call `log_outreach` with status `draft`, all fields including subject_line_1, subject_line_2, touch_number, outreach_stage
2. Decide which exemplar bucket the draft belongs to:
   - **signal-anchored** → `/workspace/group/email-data/approved-examples.jsonl`. Use this when the draft cites a verified specific anchor: a claim license, a verified signal with source URL, a public earnings/job-posting/blog quote that the researcher tagged as the trigger. Indicators: `plan.hook_type == "signal"` AND `plan.proof_source` references a verified source (e.g. `claim_license`, `verified_signal`, named public URL).
   - **qualitative-only** → `/workspace/group/email-data/generic-safe-examples.jsonl`. Use this when the draft relies on pattern language without a concrete verified anchor. Indicators: `plan.proof_source == "qualitative_pattern"`, no claim license referenced, the email talks in generalities (e.g. "teams running Spark at your scale"). Create the file on first write if it does not exist.
3. Append the entry to whichever file was chosen, including `anchor_type`:
```json
{"timestamp": "ISO", "account": "name", "contact": "name", "persona": "type", "stage": "stage", "touch": 1, "sender": "Sol", "subject_1": "...", "subject_2": "...", "email": "full text", "critic_scores": {}, "plan": {}, "anchor_type": "signal_anchored | qualitative_only"}
```
4. Confirm in Slack: "Logged and saved as `<signal-anchored|qualitative-only>` example."

The drafter only reads `approved-examples.jsonl` for voice training (Step 2). Qualitative-only exemplars are kept in a separate file so the drafter never learns it is safe to invent specifics from a pattern-only example.

**Revise (with reason):**
1. Ask for a reason code if not provided: `too vague | too long | unsupported claim | wrong persona | weak CTA | sounds AI | bad subject | other`
2. Append to `/workspace/group/email-data/revision-log.jsonl`:
```json
{"timestamp": "ISO", "account": "name", "reason": "code", "feedback": "user's exact words", "original_draft": "text", "critic_scores": {}}
```
3. Pass the feedback to the drafter as critic feedback. Re-run critic. Post new draft.

**Skip:**
1. Log as skipped with reason
2. If user gives feedback, log to revision-log.jsonl

## Training Mode (`/sdr train`)

Training mode generates drafts for human review to build the approved examples library.

The critic is the gate. It throws weak drafts away on its own — only drafts it scores as confidently great reach Slack. This mirrors the LinkedIn agent's pass/minor_edit/fail loop: a draft that the critic fails after the rewrite budget is exhausted is terminal and never surfaced. Sol's review time is spent only on drafts the critic already believes are good, not on rating crap.

### How it works

1. Read `/workspace/group/email-data/approved-examples.jsonl` to count current examples
2. Pick accounts that have timing signals. Use `get_timing_signals` to find 5-10 accounts with active signals.
3. For each account, run the full workflow (research → plan → draft → critic) but do NOT log to Airtable

### Auto-discard gate

The critic's decision is the threshold. Do not invent a separate numeric cutoff — `SHIP` already means "all dimensions 3+, factual safety 5, positioning 4+, average 4+", which is the confidently-great bar.

For each account, after the critic returns:

- **KILL** → discard silently. Append a discard entry to `/workspace/group/email-data/revision-log.jsonl` (schema below). Do NOT post to Slack. Move to next account.
- **REWRITE** → run up to 2 rewrite cycles (pass `rewrite_instructions` + plan + research back to the drafter, re-run the critic with `company_context`). If a cycle returns `SHIP`, treat it as SHIP below. If the draft is still `REWRITE` or `KILL` after 2 cycles, discard silently — append a discard entry to `revision-log.jsonl`, do NOT post to Slack, move to next account.
- **SHIP** → this is a confidently-great draft. Surface it (next section).

Discard entry schema (append one line per discarded account):

```json
{"timestamp": "ISO", "account": "name", "reason": "auto_discard_kill | auto_discard_rewrite_exhausted", "feedback": "critic kill_reason, or the last cycle's rewrite_instructions", "original_draft": "best/last draft text", "critic_scores": {}, "rewrite_count": 0-2, "discarded": true}
```

Discards are silent by design. Do not post them to Slack, not even a summary line per account — they only land in `revision-log.jsonl`.

### Surfacing a SHIP draft

Only `SHIP` drafts post to Slack. For each one:

```
**Training draft — SHIP** (account [k] of [total] · [j] surfaced · [m] auto-discarded so far)

[full draft output with critic scores]

Rate this draft:
:white_check_mark: Approve (saves as example)
:pencil2: Critique (tell me what to fix)
:x: Reject (tell me why)
```

- On approve: save to the bucket chosen by the same split rule as Step 8 — `approved-examples.jsonl` for signal-anchored drafts, `generic-safe-examples.jsonl` for qualitative-only drafts. Include `anchor_type` in the entry.
- On critique: save feedback to revision-log.jsonl, redraft, post again
- On reject: save feedback to revision-log.jsonl, move to next account

### End-of-run summary

After all accounts are processed, post one summary to Slack:

```
**Training run complete** — [total] accounts · [j] surfaced for review · [m] auto-discarded
Discards: [count] KILL · [count] rewrite-exhausted (see revision-log.jsonl)
```

Goal: build 10-20 approved examples that define Sol's voice.

### Training accounts

If the user says `/sdr train`, pick accounts with signals. If they say `/sdr train <account1>, <account2>, ...` (comma-separated), use those specific accounts. Split on commas and trim whitespace.

## Sender Identity

| Sender | Voice |
|--------|-------|
| Sol | Casual, direct. First name basis. Short sentences. |
| Udi (CEO) | Slightly more formal. References company vision. Still concise. |

Default is Sol. Always include sender in data passed to planner and drafter.

## What NOT to do

- Do not research, plan, draft, or critique yourself. Dispatch agents.
- Do not auto-send. Always wait for approval.
- Do not batch accounts in standard mode. One at a time.
- Do not call `enrich_contact`. Host-side action.
- Do not force a draft when the critic returns KILL.
- Do not skip the critic. Every draft gets scored.
- Do not log to Airtable in training mode.

## Headless Mode

When running headless (`SDR_HEADLESS=1`):

1. Run full workflow through critic
2. If SHIP: log to Airtable with status `draft`
3. If REWRITE after 2 cycles: log best draft with status `draft_needs_review`
4. If KILL: log with status `skipped`
5. Return JSON:

```json
{
  "decision": "SHIP | REWRITE | KILL | SKIP | NEEDS_INFO",
  "account": "name",
  "fit": "summary or null",
  "contact_name": "name or null",
  "contact_title": "title or null",
  "why_person": "reason or null",
  "why_now": "signal or null",
  "angle": "angle or null",
  "outreach_stage": "stage or null",
  "touch_number": "integer or null",
  "subject_line_1": "subject or null",
  "subject_line_2": "subject or null",
  "draft_email": "text or null",
  "critic_scores": "scores object or null",
  "rewrite_count": 0,
  "skip_reason": "reason or null",
  "needs_info": ["fields or null"]
}
```
