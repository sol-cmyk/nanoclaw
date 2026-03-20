from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from clients import AirtableClient
from config import ConfigError, Settings, load_settings
from data import (
    ACCOUNT_ID_KEYS,
    ACCOUNT_NAME_KEYS,
    SIGNAL_DATE_KEYS,
    SIGNAL_SCORE_KEYS,
    coerce_float,
    coerce_list,
    coerce_str,
    parse_isoish,
    read_records,
    safe_preview,
)
from models import (
    AccountScoreResult,
    BestContactsResult,
    ContactCandidate,
    EnrichmentResult,
    LogOutreachPayload,
    LogOutreachResult,
    RecentOutreachResult,
    TimingSignal,
    TimingSignalsResult,
)
from resolver import Resolver

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("flarion-sdr-mcp")

mcp = FastMCP(
    name="flarion-sdr",
    instructions=(
        "Single MCP server for Flarion SDR prep. Use these tools to resolve one account, "
        "pick contacts, inspect timing signals, check recent outreach, "
        "and log draft outreach. Agent can write draft/skipped/failed only."
    ),
)


class ServiceContainer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.resolver = Resolver(
            scorer_file=settings.scorer_file,
            crm_accounts_file=settings.crm_accounts_file,
            crm_contacts_file=settings.crm_contacts_file,
            ecosystem_people_file=settings.ecosystem_people_file,
            clay_profiles=settings.clay_profiles,
        )
        self.airtable = AirtableClient(settings) if settings.has_airtable else None


_services: ServiceContainer | None = None


def services() -> ServiceContainer:
    global _services
    if _services is None:
        _services = ServiceContainer(load_settings())
    return _services


@mcp.tool()
def get_account_score(account_id: str) -> AccountScoreResult:
    """Return the materialized fit score and highlights for one account.

    Pass the canonical account_id when possible. The tool will also try known aliases and names.
    """
    svc = services()
    account = svc.resolver.resolve_account(account_id)
    best_record: dict[str, Any] | None = None
    best_path: Path | None = None
    if svc.settings.scorer_file.exists():
        for file_path, record in read_records(svc.settings.scorer_file):
            if svc.resolver.account_record_matches(record, account):
                best_record = record
                best_path = file_path
                break
    if not best_record:
        return AccountScoreResult(
            account=account,
            reasons=["No scorer record matched this account"],
            highlights=[],
            source_record={},
        )

    # Map real scorer fields first, then fall back to generic keys
    fit_score = coerce_float(best_record, [
        "observed_weighted_de_score", "fit_score", "score", "account_score", "priority_score",
    ])
    tier = coerce_str(best_record, [
        "de_signal_tier", "tier", "segment", "priority", "bucket",
    ])

    reasons = coerce_list(best_record, ["reasons", "fit_reasons", "why_fit", "notes"])
    highlights = coerce_list(best_record, ["highlights", "summary_points", "signals"])
    if not reasons:
        summary = coerce_str(best_record, ["summary", "why_fit_summary", "fit_summary"])
        if summary:
            reasons.append(summary)

    # Build highlights from scorer booleans if no explicit highlights
    if not highlights:
        if best_record.get("has_leader"):
            highlights.append("has DE leader")
        spark = best_record.get("spark_tech")
        if spark:
            highlights.append(f"spark: {', '.join(spark) if isinstance(spark, list) else spark}")
        infra = best_record.get("infrastructure")
        if infra:
            highlights.append(f"infra: {', '.join(infra) if isinstance(infra, list) else infra}")
        if best_record.get("bizdev"):
            highlights.append("bizdev interest")

    # Add scorer path only if not already in source_files from resolver
    if best_path and str(best_path) not in account.source_files:
        account.source_files.append(str(best_path))

    return AccountScoreResult(
        account=account,
        fit_score=fit_score,
        tier=tier,
        reasons=reasons,
        highlights=highlights,
        source_record={},  # Never expose raw scorer data to agent
    )


@mcp.tool()
def get_best_contacts(account_id: str, limit: int = 5) -> BestContactsResult:
    """Return the best candidate contacts for a single account.

    This combines CRM and ecosystem records and sorts by available relationship and seniority hints.
    """
    svc = services()
    account = svc.resolver.resolve_account(account_id)
    contacts = svc.resolver.best_contacts_for_account(account, limit=max(1, min(limit, 10)))
    notes: list[str] = []
    if not contacts:
        notes.append("No contacts matched this account in CRM or ecosystem data")
    else:
        notes.append(f"Returned {len(contacts)} ranked contacts")
    return BestContactsResult(account=account, contacts=contacts, notes=notes)


@mcp.tool()
def get_timing_signals(account_id: str, limit: int = 5) -> TimingSignalsResult:
    """Return recent timing signals for one account from the verified signals store."""
    svc = services()
    account = svc.resolver.resolve_account(account_id)
    signals: list[TimingSignal] = []
    for file_path, record in read_records(svc.settings.signals_file):
        if not svc.resolver.account_record_matches(record, account):
            continue
        summary = coerce_str(record, ["summary", "signal", "description", "event", "title"]) or "Signal matched the account"
        observed = coerce_str(record, SIGNAL_DATE_KEYS)
        score = coerce_float(record, SIGNAL_SCORE_KEYS)
        signal_type = coerce_str(record, ["signal_type", "type", "category"])
        signals.append(
            TimingSignal(
                signal_type=signal_type,
                summary=summary,
                observed_at=observed,
                score=score,
                source=file_path.name,
                source_record={},  # Don't leak raw signal data to agent
            )
        )
    signals.sort(
        key=lambda item: (
            parse_isoish(item.observed_at) or parse_isoish("1970-01-01"),
            item.score or 0.0,
        ),
        reverse=True,
    )
    limited = signals[: max(1, min(limit, 10))]
    notes = [f"Returned {len(limited)} timing signals"] if limited else ["No timing signals matched this account"]
    return TimingSignalsResult(account=account, signals=limited, notes=notes)


@mcp.tool()
def get_recent_outreach(account_id: str, limit: int = 10) -> RecentOutreachResult:
    """Return recent outreach rows from Airtable for a single account.

    Requires Airtable credentials and an interactions table in SDR_SECRETS.
    """
    svc = services()
    account = svc.resolver.resolve_account(account_id)
    if svc.airtable is None:
        return RecentOutreachResult(
            account=account,
            records=[],
            notes=["Airtable is not configured yet"],
        )
    records = svc.airtable.list_recent_outreach(account, limit=max(1, min(limit, svc.settings.max_recent_outreach)))
    notes = [f"Returned {len(records)} Airtable records"] if records else ["No recent outreach found in Airtable"]
    return RecentOutreachResult(account=account, records=records, notes=notes)


@mcp.tool()
def enrich_contact(crm_contact_id: str) -> EnrichmentResult:
    """Return cached Clay enrichment for one contact. Read-only: does NOT trigger Clay webhook.

    Clay webhook queueing is a host-side action (not available to the agent).
    """
    svc = services()
    contact = svc.resolver.resolve_contact(crm_contact_id)
    cache_record: dict[str, Any] | None = None
    if svc.settings.clay_profiles and svc.settings.clay_profiles.exists():
        for _, record in read_records(svc.settings.clay_profiles):
            if svc.resolver.contact_record_matches(record, contact):
                cache_record = safe_preview(record)
                break
    # Read-only: return cache hit/miss status, never queue Clay webhook
    notes: list[str] = []
    if cache_record:
        notes.append("used Clay cache")
    else:
        notes.append("not found in Clay cache (webhook queueing is host-only)")
    return EnrichmentResult(
        contact=contact,
        found_in_cache=bool(cache_record),
        queued_with_clay=False,
        cache_record=cache_record or {},
        queue_response={},
        notes=notes,
    )


@mcp.tool()
def log_outreach(payload: dict[str, Any]) -> LogOutreachResult:
    """Write one outreach decision row to Airtable.

    Agent can write: draft, skipped, failed.
    Agent CANNOT write: approved, sent (those are host-only after human approval).
    """
    svc = services()
    if svc.airtable is None:
        return LogOutreachResult(success=False, notes=["Airtable is not configured yet"])
    validated = LogOutreachPayload.model_validate(payload)
    # Always overwrite run_id from host env (agent cannot choose its own audit key)
    validated.run_id = os.environ.get("SDR_RUN_ID")
    # Server-side timestamp — agent cannot control when a record was logged
    validated.logged_at = datetime.now(timezone.utc).isoformat()
    # Strip host-only fields the agent should not set
    validated.approved_by = None
    validated.sent_at = None
    # Resolve to canonical account ID before writing to prevent fragmentation
    account = svc.resolver.resolve_account(validated.account_id)
    validated.account_id = account.id
    return svc.airtable.upsert_outreach_record(validated)


def main() -> None:
    try:
        load_settings()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise
    mcp.run()


if __name__ == "__main__":
    main()
