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
- `approved_examples`: read last 5 entries from `/workspace/group/email-data/approved-examples.jsonl` (may be empty early on)

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

If the draft contains specific numbers, quotes, named events, or dollar figures and there are no source URLs above, REJECT — that means the drafter invented something.

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
2. Append to `/workspace/group/email-data/approved-examples.jsonl`:
```json
{"timestamp": "ISO", "account": "name", "contact": "name", "persona": "type", "stage": "stage", "touch": 1, "sender": "Sol", "subject_1": "...", "subject_2": "...", "email": "full text", "critic_scores": {}, "plan": {}}
```
3. Confirm in Slack: "Logged and saved as approved example."

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

### How it works

1. Read `/workspace/group/email-data/approved-examples.jsonl` to count current examples
2. Pick accounts that have timing signals. Use `get_timing_signals` to find 5-10 accounts with active signals.
3. For each account, run the full workflow (research → plan → draft → critic) but do NOT log to Airtable
4. Post each draft to Slack with the critic scores, then ask:

```
**Training draft [n/total]**

[full draft output with critic scores]

Rate this draft:
:white_check_mark: Approve (saves as example)
:pencil2: Critique (tell me what to fix)
:x: Reject (tell me why)
```

5. On approve: save to approved-examples.jsonl
6. On critique: save feedback to revision-log.jsonl, redraft, post again
7. On reject: save feedback to revision-log.jsonl, move to next account

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
