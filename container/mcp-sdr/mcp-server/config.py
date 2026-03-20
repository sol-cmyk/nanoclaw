from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    "notes": "notes",
    "metadata": "metadata",
}


@dataclass(slots=True)
class Settings:
    scorer_file: Path
    crm_accounts_file: Path
    crm_contacts_file: Path
    ecosystem_people_file: Path
    signals_file: Path
    clay_profiles: Path | None
    secrets_path: Path | None
    clay_webhook_url: str | None
    airtable_token: str | None
    airtable_base_id: str | None
    airtable_interactions_table: str | None
    airtable_fields: dict[str, str] = field(default_factory=lambda: DEFAULT_AIRTABLE_FIELDS.copy())
    max_recent_outreach: int = 25

    @property
    def has_airtable(self) -> bool:
        return bool(self.airtable_token and self.airtable_base_id and self.airtable_interactions_table)

    @property
    def has_clay_webhook(self) -> bool:
        return bool(self.clay_webhook_url)


def _expand(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    return Path(path_value).expanduser().resolve()



def _read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"Expected JSON object in {path}")
    return data



def load_settings() -> Settings:
    env = os.environ
    secrets_path = _expand(env.get("SDR_SECRETS"))
    secrets = _read_json(secrets_path)

    scorer_dir = _expand(env.get("SCORER_DIR"))
    crm_dir = _expand(env.get("CRM_DIR"))
    ecosystem_people_file = _expand(env.get("ECOSYSTEM_PEOPLE_FILE"))
    signals_file = _expand(env.get("SIGNALS_FILE"))
    clay_profiles = _expand(env.get("CLAY_PROFILES"))

    # Derive explicit file paths
    scorer_file = scorer_dir / "account-scores.json" if scorer_dir else None
    crm_accounts_file = crm_dir / "accounts.json" if crm_dir else None
    crm_contacts_file = crm_dir / "contacts.json" if crm_dir else None

    # Validate required env vars are set
    missing_env = [
        name
        for name, value in {
            "SCORER_DIR": scorer_dir,
            "CRM_DIR": crm_dir,
            "ECOSYSTEM_PEOPLE_FILE": ecosystem_people_file,
            "SIGNALS_FILE": signals_file,
        }.items()
        if value is None
    ]
    if missing_env:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing_env)}")

    # Validate required files exist
    missing_files = [
        str(path)
        for path in [scorer_file, crm_accounts_file, crm_contacts_file, ecosystem_people_file, signals_file]
        if path and not path.exists()
    ]
    if missing_files:
        raise ConfigError(f"Required files not found: {', '.join(missing_files)}")

    airtable_fields = DEFAULT_AIRTABLE_FIELDS.copy()
    extra_fields = secrets.get("airtable_fields")
    if isinstance(extra_fields, dict):
        airtable_fields.update({str(k): str(v) for k, v in extra_fields.items()})

    max_recent = secrets.get("airtable_max_recent_records", 25)
    try:
        max_recent_int = int(max_recent)
    except (TypeError, ValueError):
        max_recent_int = 25

    return Settings(
        scorer_file=scorer_file,
        crm_accounts_file=crm_accounts_file,
        crm_contacts_file=crm_contacts_file,
        ecosystem_people_file=ecosystem_people_file,
        signals_file=signals_file,
        clay_profiles=clay_profiles,
        secrets_path=secrets_path,
        clay_webhook_url=env.get("CLAY_WEBHOOK_URL") or secrets.get("clay_webhook_url"),
        airtable_token=env.get("AIRTABLE_TOKEN") or secrets.get("airtable_token"),
        airtable_base_id=env.get("AIRTABLE_BASE_ID") or secrets.get("airtable_base_id"),
        airtable_interactions_table=env.get("AIRTABLE_INTERACTIONS_TABLE")
        or secrets.get("airtable_interactions_table"),
        airtable_fields=airtable_fields,
        max_recent_outreach=max_recent_int,
    )
