from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SUPPORTED_SUFFIXES = {".json", ".jsonl", ".csv"}


ACCOUNT_ID_KEYS = [
    "account_id",
    "id",
    "accountId",
    "account_key",
    "accountKey",
    "company_id",
    "companyId",
    "slug",
]
ACCOUNT_NAME_KEYS = [
    "Account Name",
    "account_name",
    "name",
    "company",
    "company_name",
    "Company",
    "display_name",
    "org_name",
    "organization",
]
ACCOUNT_DOMAIN_KEYS = ["domain", "website", "company_domain", "account_domain", "Company Domain", "Company website"]
CONTACT_ID_KEYS = [
    "crm_contact_id",
    "Contact Id",
    "contact_id",
    "contactId",
    "Row Id",
    "person_id",
    "personId",
    "lead_id",
    "leadId",
    "id",
]
CONTACT_NAME_KEYS = ["Contact Name", "Name", "name", "full_name", "contact_name", "person_name"]
CONTACT_EMAIL_KEYS = ["email", "Email", "work_email", "primary_email"]
CONTACT_TITLE_KEYS = ["Title", "title", "job_title", "role", "Headline", "headline"]
CONTACT_LINKEDIN_KEYS = ["LinkedIn Profile", "LinkedIn", "linkedin", "linkedin_url"]
CONTACT_ACCOUNT_LINK_KEYS = [
    "Account",
    "account_id",
    "accountId",
    "account_key",
    "Account Name",
    "account_name",
    "Company",
    "company",
    "company_name",
    "Company Domain",
    "company_domain",
    "domain",
]
SIGNAL_DATE_KEYS = ["observed_at", "timestamp", "date", "created_at", "updated_at", "event_date"]
SIGNAL_SCORE_KEYS = ["score", "signal_score", "priority", "weight"]


TEXT_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Matches strings that look like domains or URLs (contain a dot followed by 2-6 alpha chars)
DOMAIN_RE = re.compile(r"[a-z0-9][-a-z0-9]*\.[a-z]{2,6}(?:\.[a-z]{2,6})?$")
COMPANY_SUFFIX_RE = re.compile(r"\s*[,.]?\s*\b(inc|incorporated|ltd|limited|llc|corp|corporation|gmbh|plc)\b\.?\s*$", re.IGNORECASE)


class DataShapeError(RuntimeError):
    """Raised when a file exists but its layout is unsupported."""



def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = TEXT_RE.sub(" ", text)
    return text


def _looks_like_domain_or_url(text: str) -> bool:
    """Return True if text looks like a URL or bare domain."""
    return text.startswith(("http://", "https://")) or bool(DOMAIN_RE.search(text))


def _extract_host(text: str) -> str:
    """Extract and normalize hostname from a URL or bare domain.

    https://zetaglobal.com/path?q=1 -> zetaglobal.com
    http://www.zetaglobal.com       -> zetaglobal.com
    www.zetaglobal.com              -> zetaglobal.com
    zetaglobal.com                  -> zetaglobal.com
    foo.co                          -> foo.co  (preserved, not stripped)
    """
    # Strip scheme
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    # Strip www.
    if text.startswith("www."):
        text = text[4:]
    # Strip path, query, fragment
    for sep in ("/", "?", "#"):
        idx = text.find(sep)
        if idx >= 0:
            text = text[:idx]
    # Strip port
    idx = text.find(":")
    if idx >= 0:
        text = text[:idx]
    # Strip trailing dot
    text = text.rstrip(".")
    return text


def normalize_profile_url(value: Any) -> str:
    """Normalize a profile URL for dedup, keeping the path (unlike match_key which strips it).

    Used for LinkedIn dedup where the path IS the identity:
        https://www.linkedin.com/in/jane-doe?trk=1 -> linkedin.com/in/jane-doe
        https://www.linkedin.com/in/john-roe        -> linkedin.com/in/john-roe

    match_key() would collapse both to 'linkedin.com', losing the identity.
    """
    text = str(value or "").strip().lower()
    if not text:
        return text
    # Strip scheme
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    # Strip www.
    if text.startswith("www."):
        text = text[4:]
    # Strip query and fragment
    for sep in ("?", "#"):
        idx = text.find(sep)
        if idx >= 0:
            text = text[:idx]
    # Strip trailing slash
    text = text.rstrip("/")
    return text


def match_key(value: Any) -> str:
    """Canonical comparison key that normalizes URLs, domains, and company names.

    For URLs/domains: extracts bare hostname (strips scheme, www, path, port).
    For company names: strips common legal suffixes (Inc, LLC, Ltd, Corp, etc).
    Does NOT strip .co/.ag/.sa/.plc from domains.

    Examples:
        https://zetaglobal.com/  -> zetaglobal.com
        http://www.zetaglobal.com -> zetaglobal.com
        zetaglobal.com           -> zetaglobal.com
        foo.co                   -> foo.co
        foo.ag                   -> foo.ag
        Acme, Inc.               -> acme
        Acme Inc                 -> acme
        ACME LLC                 -> acme
    """
    text = str(value or "").strip().lower()
    if not text:
        return text
    if _looks_like_domain_or_url(text):
        return _extract_host(text)
    # Company name: strip legal suffixes, collapse whitespace
    text = COMPANY_SUFFIX_RE.sub("", text).strip()
    text = TEXT_RE.sub(" ", text)
    return text



def slugify(value: Any) -> str:
    normalized = normalize_text(value)
    return NON_ALNUM_RE.sub("-", normalized).strip("-")



def safe_preview(record: dict[str, Any], limit: int = 12) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for index, (key, value) in enumerate(record.items()):
        if index >= limit:
            break
        if isinstance(value, (str, int, float, bool)) or value is None:
            preview[str(key)] = value
        else:
            preview[str(key)] = str(value)
    return preview



def record_values(record: dict[str, Any], keys: Iterable[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        if key not in record:
            continue
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if item not in {None, ""})
        elif value not in {None, ""}:
            values.append(str(value))
    return values



def candidate_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            yield path
        return
    if not path.exists():
        return
    for child in path.rglob("*"):
        if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES:
            yield child



def read_records(path: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    for file_path in candidate_files(path):
        suffix = file_path.suffix.lower()
        if suffix == ".json":
            yield from _read_json_records(file_path)
        elif suffix == ".jsonl":
            yield from _read_jsonl_records(file_path)
        elif suffix == ".csv":
            yield from _read_csv_records(file_path)



def _read_json_records(path: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                yield path, row
        return
    if isinstance(data, dict):
        if isinstance(data.get("records"), list):
            for row in data["records"]:
                if isinstance(row, dict):
                    if "fields" in row and isinstance(row["fields"], dict):
                        fields = dict(row["fields"])
                        if row.get("id") and "airtable_record_id" not in fields:
                            fields["airtable_record_id"] = row["id"]
                        yield path, fields
                    else:
                        yield path, row
            return
        for list_key in ("accounts", "contacts", "items", "data", "rows", "results"):
            value = data.get(list_key)
            if isinstance(value, list):
                for row in value:
                    if isinstance(row, dict):
                        yield path, row
                return
        yield path, data
        return
    raise DataShapeError(f"Unsupported JSON layout in {path}")



def _read_jsonl_records(path: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                yield path, row



def _read_csv_records(path: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield path, dict(row)



def coerce_float(record: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = record.get(key)
        if value in {None, ""}:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None



def coerce_str(record: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value in {None, ""}:
            continue
        return str(value)
    return None



def coerce_list(record: dict[str, Any], keys: Iterable[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = record.get(key)
        if value in {None, ""}:
            continue
        if isinstance(value, list):
            values.extend(str(item) for item in value if item not in {None, ""})
        else:
            values.append(str(value))
    return values



def extract_account_aliases(record: dict[str, Any]) -> list[str]:
    aliases = []
    aliases.extend(record_values(record, ACCOUNT_ID_KEYS))
    aliases.extend(record_values(record, ACCOUNT_NAME_KEYS))
    aliases.extend(record_values(record, ACCOUNT_DOMAIN_KEYS))
    return _unique_non_empty(aliases)



def extract_contact_aliases(record: dict[str, Any]) -> list[str]:
    aliases = []
    aliases.extend(record_values(record, CONTACT_ID_KEYS))
    aliases.extend(record_values(record, CONTACT_NAME_KEYS))
    aliases.extend(record_values(record, CONTACT_EMAIL_KEYS))
    return _unique_non_empty(aliases)



def parse_isoish(value: Any) -> datetime | None:
    """Parse an ISO-ish datetime string. Always returns UTC-aware datetimes
    to prevent TypeError when sorting mixed naive/aware values."""
    if value in {None, ""}:
        return None
    text = str(value).strip()
    text = text.replace("Z", "+00:00")
    for candidate in (text, text[:19]):
        try:
            dt = datetime.fromisoformat(candidate)
            # Always return UTC-aware to prevent mixed sort crashes
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None



def _unique_non_empty(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned:
            continue
        key = normalize_text(cleaned)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return ordered
