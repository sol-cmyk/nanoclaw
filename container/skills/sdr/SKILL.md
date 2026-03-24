# /sdr — SDR Prep Workflow

Run outbound prep for one account at a time. Gather context, decide whether to proceed, pick the best contact, choose a data-backed angle, draft structured outreach, and post for approval.

## Trigger

User says `/sdr work <account>` or `/sdr prep <account>`.

## Tools Available

You have 6 MCP tools from the `flarion-sdr` server. Use them in this order:

1. `get_account_score(account_id)` — Account fit: tier, DE density, infrastructure, leader presence
2. `get_best_contacts(account_id, limit)` — Ranked contacts by seniority and relationship strength
3. `get_timing_signals(account_id, limit)` — Recent timing signals (repo activity, evaluations, hiring)
4. `get_recent_outreach(account_id, limit)` — Past outreach from Airtable (avoid re-contacting too soon)
5. `enrich_contact(crm_contact_id)` — Clay enrichment cache or queue enrichment
6. `log_outreach(payload)` — Write approved/skipped result to Airtable

## Workflow

### Step 1: Gather context

Call `get_account_score`, `get_best_contacts`, and `get_timing_signals` for the account.

### Step 2: Decide whether to proceed (SKIP gate)

Evaluate the data you have so far (score, contacts, signals). You MUST return SKIP if:
- Zero timing signals (this alone is sufficient to SKIP, regardless of fit or contacts)

If timing signals exist, PROCEED. The signal is the trigger. Fit data and warm paths improve the email but do not gate whether you write one.

If you SKIP, post this to Slack and log it:

```
**Account:** [name]
**Decision:** SKIP
**Reason:** [specific reason: no fit signal, no timing signal, no warm path]
```

Call `log_outreach` with status `skipped` and the reason. Do not draft an email. Stop. No override.

### Step 3: Check outreach history

Call `get_recent_outreach`. If the account was contacted in the last 14 days, flag it and ask whether to proceed.

### Step 4: Pick one contact

From the contacts returned, pick the single best person based on:
- Title seniority (VP/Head/Director > Manager > IC)
- Has email
- Has warm path or intro available
- Not contacted recently

Do NOT call `enrich_contact` yet. Enrichment can trigger a Clay webhook (external write). Only enrich after the user approves the draft in Step 7.

### Step 5: Choose one angle from verifiable timing data

Every angle MUST cite a **timing signal** from `get_timing_signals`. Fit data (scorer tier, leader presence) and enrichment bios are context, not triggers. They help you write a better email, but they cannot justify sending one.

| Required: timing signal | Angle | Context that strengthens it |
|-------------------------|-------|-----------------------------|
| Active Spark evaluation | Cost + performance comparison | Scorer tier, infrastructure field |
| Cloud cost initiative or FinOps hiring | Managed Spark cost reduction | Contact bio mentions cost optimization |
| Data platform migration | Platform-agnostic Spark (AWS, GCP, Azure) | Contact leads the migration team |
| Scaling pain or infrastructure growth | Runtime optimization without code changes | High DE density confirms scale |
| Databricks cost concern | Independent Spark, not locked to one vendor | Enrichment shows Databricks stack |

**If `get_timing_signals` returns zero signals for this account, return SKIP.** Do not write an email based only on fit score, leader presence, or enrichment bio. Those are not "why now."

### Step 6: Draft one email (fixed structure)

The email follows a rigid 4-line structure. Do not freestyle.

**Line 1: Specific observation.**
Reference one verifiable data point. A job post, a signal, a tech stack detail, a public announcement. No compliments. No assumptions. No opinions.

**Line 2: Implication or risk.**
What does that observation mean for their business? Be specific. Tie it to cost, speed, reliability, or team capacity.

**Line 3: What similar companies do.**
One sentence about how companies in a similar situation handle it. Keep it qualitative ("teams in a similar position" or "companies running Spark on [their infra]"). Do not cite specific percentages or benchmarks unless the tool output contains an approved proof point. Do not invent numbers.

**Line 4: Soft question.**
Must be a question. Must NOT ask for a meeting. Must NOT include a calendar link. Must NOT say "15 minutes." Ask whether this is something they are working on or thinking about.

**Example:**

> Noticed your team posted a Senior Data Engineer role focused on Spark pipeline optimization last week.
>
> Scaling Spark pipelines on EMR without a managed layer usually means the team spends more time on infrastructure than on the data work itself.
>
> A few teams running similar Spark workloads on AWS found that a managed acceleration layer cut their infrastructure overhead significantly.
>
> Is pipeline performance something your team is actively working on?

### Writing rules (hard bans)

The email MUST NOT contain:
- Em dashes (use commas, periods, or rewrite)
- Buzzwords ("synergy", "leverage", "unlock", "game-changing", "cutting-edge", "revolutionary")
- Marketing language ("we are excited", "we would love to", "I wanted to reach out")
- Exclamation points
- Emojis
- "Just following up" or "I hope this finds you well"
- "I noticed that" or "I came across" (just state the observation)
- Compliments ("impressive growth", "love what you are doing")
- Assumptions about pain ("I know you are struggling with")
- Paragraphs longer than 2 sentences
- Calendar links or specific meeting requests

If you cannot write a clean email that follows all rules, return SKIP with reason "could not draft a compliant email."

### Step 7: Post for approval

Post the result in this format:

```
**Account:** [name]
**Fit:** [tier] | DE score: [score] | Leader: [yes/no]
**Contact:** [name], [title]
**Why this person:** [1 sentence]
**Why now:** [specific data point that triggered this outreach]
**Angle:** [chosen angle]
**Data cited:** [the exact fact from tool output that backs the angle]

---

**Draft email:**

[email text]

---

**Next:** Approve / Revise / Skip
```

Wait for the user to respond before taking any action.

### Step 8: Log result

- On **Approve**: call `log_outreach` with status `draft` (not "approved", that's host-only), the `account_id`, `crm_contact_id`, `angle`, `why_now`, and `draft_text`. The host approval flow will promote to "approved" separately.
- On **Skip**: call `log_outreach` with status `skipped` and a note with the skip reason.
- On **Revise**: redraft based on feedback, post again, wait for approval.

Do NOT call `enrich_contact` from this workflow. Enrichment that triggers Clay webhooks is a host-side action, not an agent action.

## Sender Identity

| Sender | Voice |
|--------|-------|
| Sol | Casual, direct. First name basis. Short sentences. |
| Udi (CEO) | Slightly more formal. References company vision. Still concise. |

Default sender is Sol unless the user specifies otherwise.

## What NOT to do

- Do not auto-send. Always wait for approval.
- Do not build decks (that's v1.1).
- Do not batch multiple accounts. One at a time.
- Do not invent data. If a tool returns no result, say so.
- Do not write an email without a verifiable data point backing the angle.
- Do not send "generic DE leader intro" emails. If there is no signal, SKIP.
- Do not use any banned words or patterns from the writing rules.

## Headless Mode

When running headless (no `send_message` tool available, `SDR_HEADLESS=1`):

1. Complete Steps 1-6 as normal (gather, decide, check history, pick contact, choose angle, draft).
2. Call `log_outreach` with status `draft` immediately after drafting (do not wait for approval, there is no human in the loop yet). Include all fields: `account_id`, `crm_contact_id`, `angle`, `why_now`, `draft_text`.
3. For SKIP: call `log_outreach` with status `skipped` and the skip reason.
4. Skip Step 7 entirely (do not try to post to Slack).
5. Return ONLY a single JSON object as your final message with these keys:

```json
{
  "decision": "PROCEED or SKIP",
  "account": "account name",
  "fit": "tier summary",
  "contact_name": "name or null",
  "contact_title": "title or null",
  "why_person": "1 sentence or null",
  "why_now": "timing signal summary or null",
  "angle": "chosen angle or null",
  "data_cited": "exact fact backing the angle or null",
  "draft_email": "full email text or null",
  "skip_reason": "reason or null"
}
```

Do NOT wrap the JSON in markdown code fences. Return ONLY the raw JSON object.
