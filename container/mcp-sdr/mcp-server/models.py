from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ResolvedEntity(BaseModel):
    ref: str = Field(description="Original identifier or alias passed into the tool")
    id: str = Field(description="Canonical stable identifier used by the MCP server")
    name: str | None = Field(default=None, description="Best human-readable name for the entity")
    aliases: list[str] = Field(default_factory=list, description="Known aliases used for matching")
    confidence: Literal["exact", "alias", "fuzzy", "guessed"] = Field(
        default="guessed",
        description="How strongly the resolver matched the input to the canonical id",
    )
    source_files: list[str] = Field(default_factory=list, description="Files that contributed to the match")


class AccountScoreResult(BaseModel):
    account: ResolvedEntity
    fit_score: float | None = None
    tier: str | None = None
    reasons: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    source_record: dict[str, Any] = Field(default_factory=dict)


class ContactCandidate(BaseModel):
    crm_contact_id: str
    name: str | None = None
    title: str | None = None
    email: str | None = None
    linkedin: str | None = None
    score: float | None = None
    why_selected: list[str] = Field(default_factory=list)
    account_id: str | None = None
    source: str | None = None
    source_record: dict[str, Any] = Field(default_factory=dict)


class BestContactsResult(BaseModel):
    account: ResolvedEntity
    contacts: list[ContactCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TimingSignal(BaseModel):
    signal_type: str | None = None
    summary: str
    observed_at: str | None = None
    score: float | None = None
    source: str | None = None
    source_record: dict[str, Any] = Field(default_factory=dict)


class TimingSignalsResult(BaseModel):
    account: ResolvedEntity
    signals: list[TimingSignal] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OutreachRecord(BaseModel):
    airtable_record_id: str | None = None
    account_id: str | None = None
    crm_contact_id: str | None = None
    status: str | None = None
    angle: str | None = None
    why_now: str | None = None
    draft_text: str | None = None
    approved_by: str | None = None
    sent_at: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_fields: dict[str, Any] = Field(default_factory=dict)


class RecentOutreachResult(BaseModel):
    account: ResolvedEntity
    records: list[OutreachRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    contact: ResolvedEntity
    found_in_cache: bool = False
    queued_with_clay: bool = False
    cache_record: dict[str, Any] = Field(default_factory=dict)
    queue_response: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class OutreachStatus(str, Enum):
    """Agent-writable statuses. 'approved' and 'sent' are host-only (require approval flow)."""
    draft = "draft"
    skipped = "skipped"
    failed = "failed"


# Host-only statuses (not in the enum, written by cockpit bot after human approval)
HOST_ONLY_STATUSES = {"approved", "sent"}


class LogOutreachPayload(BaseModel, extra="forbid"):
    account_id: str = Field(description="Canonical account id")
    crm_contact_id: str | None = Field(default=None, description="Canonical CRM contact id")
    status: OutreachStatus = Field(description="draft, skipped, or failed. Agent cannot write approved/sent.")
    run_id: str | None = Field(default=None, description="Host-injected run ID for idempotency and audit trail")
    angle: str | None = None
    why_now: str | None = None
    draft_text: str | None = None
    approved_by: str | None = None
    sent_at: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sent_at")
    @classmethod
    def validate_sent_at(cls, value: str | None) -> str | None:
        if value in {None, ""}:
            return None
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class LogOutreachResult(BaseModel):
    success: bool
    airtable_record_id: str | None = None
    table: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
