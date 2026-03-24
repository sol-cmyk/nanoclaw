from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

logger = logging.getLogger("flarion-sdr-mcp")

import httpx

from config import Settings
from data import match_key
from models import LogOutreachPayload, LogOutreachResult, OutreachRecord, ResolvedEntity


class AirtableClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.has_airtable:
            raise RuntimeError("Airtable configuration is missing")
        # Route through the Airtable sidecar proxy — it injects the Bearer token
        self._client = httpx.Client(
            base_url=f"{settings.airtable_base_url}/v0/{settings.airtable_base_id}/",
            headers={"Content-Type": "application/json"},
            timeout=20.0,
        )

    def list_recent_outreach(self, account: ResolvedEntity, limit: int) -> list[OutreachRecord]:
        table = self._quoted_table()
        account_field = self.settings.airtable_fields.get("account_id", "account_id")
        match_key_field = self.settings.airtable_fields.get("account_match_key", "account_match_key")

        # Primary: query on normalized match key (stable across URL/domain/name variants)
        account_mk = match_key(account.id)
        mk_formula = self._or_equals_formula(match_key_field, [account_mk])

        # Fallback: also query on all raw variants for rows written before match_key existed
        # Keep ALL raw values (don't dedupe by match_key, which drops valid lookup variants)
        raw_values = [account.id, account.ref, account.name, *account.aliases]
        raw_values = [v for v in raw_values if v]
        # Dedupe only exact string duplicates, not match_key equivalents
        seen: set[str] = set()
        unique_raw: list[str] = []
        for v in raw_values:
            if v not in seen:
                seen.add(v)
                unique_raw.append(v)
        id_formula = self._or_equals_formula(account_field, unique_raw)

        # Combine: match_key OR raw account_id
        parts = [f for f in [mk_formula, id_formula] if f]
        if len(parts) == 2:
            formula = f"OR({parts[0]},{parts[1]})"
        elif len(parts) == 1:
            formula = parts[0]
        else:
            formula = None
        params: dict[str, Any] = {
            "maxRecords": min(limit, self.settings.max_recent_outreach),
            "sort[0][field]": self.settings.airtable_fields.get("logged_at", "logged_at"),
            "sort[0][direction]": "desc",
        }
        if formula:
            params["filterByFormula"] = formula
        response = self._client.get(table, params=params)
        response.raise_for_status()
        data = response.json()
        records: list[OutreachRecord] = []
        for row in data.get("records", []):
            fields = row.get("fields", {})
            records.append(
                OutreachRecord(
                    airtable_record_id=row.get("id"),
                    account_id=fields.get(account_field),
                    crm_contact_id=fields.get(self.settings.airtable_fields.get("crm_contact_id", "crm_contact_id")),
                    status=fields.get(self.settings.airtable_fields.get("status", "status")),
                    angle=fields.get(self.settings.airtable_fields.get("angle", "angle")),
                    why_now=fields.get(self.settings.airtable_fields.get("why_now", "why_now")),
                    # Omit draft_text from readback: prior drafts should not leak to the agent.
                    # The agent only needs to know status/angle/timing to avoid re-contacting.
                    draft_text=None,
                    approved_by=fields.get(self.settings.airtable_fields.get("approved_by", "approved_by")),
                    sent_at=fields.get(self.settings.airtable_fields.get("sent_at", "sent_at")),
                    logged_at=fields.get(self.settings.airtable_fields.get("logged_at", "logged_at")),
                    notes=None,  # Notes may contain sensitive feedback
                    metadata={},  # Strip metadata from readback
                    raw_fields={},  # Never expose raw Airtable fields to agent
                )
            )
        return records

    def upsert_outreach_record(self, payload: LogOutreachPayload) -> LogOutreachResult:
        """Idempotent write: if a record exists for this account+run_id, update it. Otherwise insert."""
        table = self._quoted_table()
        fields_map = self.settings.airtable_fields
        fields: dict[str, Any] = {
            fields_map["account_id"]: payload.account_id,
            fields_map["account_match_key"]: match_key(payload.account_id),
            fields_map["status"]: payload.status.value,
        }
        if payload.logged_at:
            fields[fields_map["logged_at"]] = payload.logged_at
        if payload.run_id:
            fields["run_id"] = payload.run_id
        if payload.crm_contact_id:
            fields[fields_map["crm_contact_id"]] = payload.crm_contact_id
        if payload.angle:
            fields[fields_map["angle"]] = payload.angle
        if payload.why_now:
            fields[fields_map["why_now"]] = payload.why_now
        if payload.draft_text:
            fields[fields_map["draft_text"]] = payload.draft_text
        if payload.approved_by:
            fields[fields_map["approved_by"]] = payload.approved_by
        if payload.sent_at:
            fields[fields_map["sent_at"]] = payload.sent_at
        if payload.notes:
            fields[fields_map["notes"]] = payload.notes
        if payload.metadata:
            fields[fields_map["metadata"]] = json.dumps(payload.metadata, sort_keys=True)

        # Idempotency: check for existing record with same account + run_id
        existing_id = self._find_existing_record(payload.account_id, payload.run_id) if payload.run_id else None

        if existing_id:
            # Update existing record
            response = self._client.patch(
                table,
                json={"records": [{"id": existing_id, "fields": fields}]},
            )
            if response.status_code == 422:
                logger.error(
                    "Airtable 422 on update. Fields: %s. Response: %s",
                    json.dumps(fields, default=str),
                    response.text[:500],
                )
            response.raise_for_status()
            body = response.json()
            record = (body.get("records") or [{}])[0]
            return LogOutreachResult(
                success=True,
                airtable_record_id=record.get("id"),
                table=self.settings.airtable_interactions_table,
                fields={},
                notes=["record updated (idempotent upsert)"],
            )
        else:
            # Insert new record
            request_body = {"records": [{"fields": fields}]}
            response = self._client.post(table, json=request_body)
            if response.status_code == 422:
                logger.error(
                    "Airtable 422 on insert. Request fields: %s. Response: %s",
                    json.dumps(fields, default=str),
                    response.text[:500],
                )
            response.raise_for_status()
            body = response.json()
            record = (body.get("records") or [{}])[0]
            return LogOutreachResult(
                success=True,
                airtable_record_id=record.get("id"),
                table=self.settings.airtable_interactions_table,
                fields={},
                notes=["record created in Airtable"],
            )

    def _find_existing_record(self, account_id: str, run_id: str) -> str | None:
        """Find an existing Airtable record by account_id + run_id."""
        table = self._quoted_table()
        account_field = self.settings.airtable_fields.get("account_id", "account_id")
        formula = f'AND({{{account_field}}}="{self._formula_string_raw(account_id)}",{{run_id}}="{self._formula_string_raw(run_id)}")'
        params: dict[str, Any] = {"filterByFormula": formula, "maxRecords": 1}
        response = self._client.get(table, params=params)
        response.raise_for_status()
        records = response.json().get("records", [])
        return records[0]["id"] if records else None

    @staticmethod
    def _formula_string_raw(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    def _quoted_table(self) -> str:
        return quote(str(self.settings.airtable_interactions_table), safe="")

    @staticmethod
    def _or_equals_formula(field_name: str, values: list[str]) -> str | None:
        unique = []
        seen = set()
        for value in values:
            if value in {None, ""}:
                continue
            key = str(value)
            if key in seen:
                continue
            seen.add(key)
            unique.append(key)
        if not unique:
            return None
        clauses = [f"{{{field_name}}}={AirtableClient._formula_string(value)}" for value in unique]
        if len(clauses) == 1:
            return clauses[0]
        return f"OR({','.join(clauses)})"

    @staticmethod
    def _formula_string(value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _parse_metadata(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {"raw": value}
        return {}
