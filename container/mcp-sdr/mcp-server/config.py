from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when the MCP server is missing required configuration."""


DEFAULT_AIRTABLE_FIELDS: dict[str, str] = {
    "account_id": "account_id",
    "account_match_key": "account_match_key",
    "crm_contact_id": "crm_contact_id",
    "status": "status",
    "angle": "angle",
    "why_now": "why_now",
    "draft_text": "draft_text",
    "approved_by": "approved_by",
    "sent_at": "sent_at",
    "logged_at": "logged_at",
    "notes": "notes",
    "metadata": "metadata",
}

# Fixed container paths — data is bind-mounted at /data/
DATA_ROOT = Path("/data")
SCORER_FILE = DATA_ROOT / "scorer" / "account-scores.json"
CRM_ACCOUNTS_FILE = DATA_ROOT / "crm" / "accounts.json"
CRM_CONTACTS_FILE = DATA_ROOT / "crm" / "contacts.json"
ECOSYSTEM_PEOPLE_FILE = DATA_ROOT / "ecosystem-people.csv"
SIGNALS_FILE = DATA_ROOT / "signals.jsonl"
CLAY_PROFILES_FILE = DATA_ROOT / "clay-profiles.jsonl"

# Airtable via sidecar proxy — no direct internet access
AIRTABLE_BASE_URL = "http://nanoclaw-airtable-proxy:3002"
AIRTABLE_BASE_ID = "app9snIQPsaND3WlM"
AIRTABLE_INTERACTIONS_TABLE = "SDR Outreach"


@dataclass(slots=True)
class Settings:
    scorer_file: Path
    crm_accounts_file: Path
    crm_contacts_file: Path
    ecosystem_people_file: Path
    signals_file: Path
    clay_profiles: Path | None
    airtable_base_url: str
    airtable_base_id: str
    airtable_interactions_table: str
    postgres_dsn: str | None = None
    airtable_fields: dict[str, str] = field(default_factory=lambda: DEFAULT_AIRTABLE_FIELDS.copy())
    max_recent_outreach: int = 25

    @property
    def has_airtable(self) -> bool:
        return bool(self.airtable_base_url and self.airtable_base_id and self.airtable_interactions_table)

    @property
    def has_postgres(self) -> bool:
        return bool(self.postgres_dsn)


def load_settings() -> Settings:
    # Validate required files exist
    required = {
        "scorer_file": SCORER_FILE,
        "crm_accounts": CRM_ACCOUNTS_FILE,
        "crm_contacts": CRM_CONTACTS_FILE,
        "ecosystem_people": ECOSYSTEM_PEOPLE_FILE,
        "signals": SIGNALS_FILE,
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise ConfigError(f"Required data files not found: {', '.join(missing)} (expected under {DATA_ROOT})")

    clay_profiles = CLAY_PROFILES_FILE if CLAY_PROFILES_FILE.exists() else None

    # Postgres DSN from env (injected by network-isolation.ts at container startup)
    postgres_dsn = os.environ.get("POSTGRES_DSN", "") or None

    return Settings(
        scorer_file=SCORER_FILE,
        crm_accounts_file=CRM_ACCOUNTS_FILE,
        crm_contacts_file=CRM_CONTACTS_FILE,
        ecosystem_people_file=ECOSYSTEM_PEOPLE_FILE,
        signals_file=SIGNALS_FILE,
        clay_profiles=clay_profiles,
        airtable_base_url=AIRTABLE_BASE_URL,
        airtable_base_id=AIRTABLE_BASE_ID,
        airtable_interactions_table=AIRTABLE_INTERACTIONS_TABLE,
        postgres_dsn=postgres_dsn,
    )
