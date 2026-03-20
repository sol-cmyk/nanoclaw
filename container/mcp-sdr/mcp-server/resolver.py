from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data import (
    ACCOUNT_ID_KEYS,
    ACCOUNT_NAME_KEYS,
    CONTACT_ACCOUNT_LINK_KEYS,
    CONTACT_EMAIL_KEYS,
    CONTACT_ID_KEYS,
    CONTACT_LINKEDIN_KEYS,
    CONTACT_NAME_KEYS,
    CONTACT_TITLE_KEYS,
    coerce_str,
    extract_account_aliases,
    extract_contact_aliases,
    match_key,
    normalize_profile_url,
    normalize_text,
    read_records,
    record_values,
    safe_preview,
    slugify,
)
from models import ContactCandidate, ResolvedEntity


@dataclass(slots=True)
class Match:
    entity: ResolvedEntity
    source_record: dict[str, Any]
    source_path: Path | None
    score: int


class Resolver:
    """Resolve account and contact refs across scorer, CRM, ecosystem, and Clay cache.

    Uses explicit file paths per source type to prevent cross-contamination
    (e.g. account rows being treated as contacts).
    """

    def __init__(
        self,
        scorer_file: Path,
        crm_accounts_file: Path,
        crm_contacts_file: Path,
        ecosystem_people_file: Path,
        clay_profiles: Path | None = None,
    ):
        self.scorer_file = scorer_file
        self.crm_accounts_file = crm_accounts_file
        self.crm_contacts_file = crm_contacts_file
        self.ecosystem_people_file = ecosystem_people_file
        self.clay_profiles = clay_profiles

    # ------------------------------------------------------------------
    # Account resolution
    # ------------------------------------------------------------------

    def resolve_account(self, account_ref: str) -> ResolvedEntity:
        """Resolve an account ref to a canonical entity.

        Searches scorer then CRM (authoritative for accounts).
        Falls back to ecosystem only if neither matches.
        Merges aliases from all matching records across sources.
        """
        matches: list[Match] = []

        # Priority 1: scorer (authoritative account list with scoring data)
        if self.scorer_file.exists():
            for file_path, record in read_records(self.scorer_file):
                match = self._match_account_record(account_ref, record, file_path)
                if match:
                    matches.append(match)

        # Priority 2: CRM accounts
        if self.crm_accounts_file.exists():
            for file_path, record in read_records(self.crm_accounts_file):
                match = self._match_account_record(account_ref, record, file_path)
                if match:
                    matches.append(match)

        # Priority 3: ecosystem (person records) - only if no account-level match
        if not matches and self.ecosystem_people_file.exists():
            for file_path, record in read_records(self.ecosystem_people_file):
                match = self._match_account_record(account_ref, record, file_path)
                if match:
                    matches.append(match)

        if not matches:
            guessed = slugify(account_ref) or account_ref
            return ResolvedEntity(ref=account_ref, id=guessed, name=account_ref, aliases=[account_ref], confidence="guessed")

        # Pick the best match as canonical, then merge aliases from all matches
        best = max(matches, key=lambda m: m.score)
        merged_aliases: list[str] = list(best.entity.aliases)
        merged_source_files: list[str] = list(best.entity.source_files)
        for m in matches:
            if m is best:
                continue
            for alias in m.entity.aliases:
                if match_key(alias) not in {match_key(a) for a in merged_aliases}:
                    merged_aliases.append(alias)
            # Add CRM record ID as alias if different from canonical
            if m.entity.id != best.entity.id:
                id_mk = match_key(m.entity.id)
                if id_mk not in {match_key(a) for a in merged_aliases}:
                    merged_aliases.append(m.entity.id)
            for sf in m.entity.source_files:
                if sf not in merged_source_files:
                    merged_source_files.append(sf)

        best.entity.aliases = merged_aliases
        best.entity.source_files = merged_source_files
        return best.entity

    # ------------------------------------------------------------------
    # Contact resolution — only scans contact-shaped sources
    # ------------------------------------------------------------------

    def resolve_contact(self, contact_ref: str) -> ResolvedEntity:
        """Resolve a contact ref. Only scans CRM contacts, ecosystem people, and Clay profiles."""
        matches: list[Match] = []
        contact_sources = [self.crm_contacts_file, self.ecosystem_people_file]
        if self.clay_profiles:
            contact_sources.append(self.clay_profiles)
        for path in contact_sources:
            if path is None or not path.exists():
                continue
            for file_path, record in read_records(path):
                match = self._match_contact_record(contact_ref, record, file_path)
                if match:
                    matches.append(match)
        if not matches:
            guessed = slugify(contact_ref) or contact_ref
            return ResolvedEntity(ref=contact_ref, id=guessed, name=contact_ref, aliases=[contact_ref], confidence="guessed")
        best = max(matches, key=lambda m: m.score)
        return best.entity

    # ------------------------------------------------------------------
    # Record matching (used by tool functions)
    # ------------------------------------------------------------------

    def account_record_matches(self, record: dict[str, Any], account: ResolvedEntity) -> bool:
        record_keys = {match_key(v) for v in extract_account_aliases(record)}
        account_keys = {match_key(account.id), match_key(account.ref)}
        account_keys.update(match_key(a) for a in account.aliases)
        if account.name:
            account_keys.add(match_key(account.name))
        return bool(record_keys.intersection(account_keys))

    def contact_record_matches(self, record: dict[str, Any], contact: ResolvedEntity) -> bool:
        record_keys = {match_key(v) for v in extract_contact_aliases(record)}
        contact_keys = {match_key(contact.id), match_key(contact.ref)}
        contact_keys.update(match_key(a) for a in contact.aliases)
        if contact.name:
            contact_keys.add(match_key(contact.name))
        return bool(record_keys.intersection(contact_keys))

    # ------------------------------------------------------------------
    # Contact ranking — only scans contact-shaped sources
    # ------------------------------------------------------------------

    def best_contacts_for_account(self, account: ResolvedEntity, limit: int = 5) -> list[ContactCandidate]:
        """Find and rank contacts for an account. Only scans contacts + ecosystem people.

        Deduplicates across sources by email first, then LinkedIn, then normalized name+account.
        Prefers CRM records over ecosystem on ties.
        """
        candidates: list[ContactCandidate] = []
        # Only contact-shaped sources — never accounts.json
        # CRM contacts scanned second so they win ties in dedup
        contact_sources = [self.ecosystem_people_file, self.crm_contacts_file]
        for path in contact_sources:
            if not path.exists():
                continue
            for file_path, record in read_records(path):
                if not self._record_links_to_account(record, account):
                    continue
                contact_id = coerce_str(record, CONTACT_ID_KEYS) or slugify(coerce_str(record, CONTACT_NAME_KEYS) or "contact")
                name = coerce_str(record, CONTACT_NAME_KEYS)
                title = coerce_str(record, CONTACT_TITLE_KEYS)
                email = coerce_str(record, CONTACT_EMAIL_KEYS)
                linkedin = coerce_str(record, CONTACT_LINKEDIN_KEYS)
                score = self._contact_priority_score(record)
                why_selected = self._contact_reasons(record)
                candidates.append(
                    ContactCandidate(
                        crm_contact_id=contact_id,
                        name=name,
                        title=title,
                        email=email,
                        linkedin=linkedin,
                        score=score,
                        why_selected=why_selected,
                        account_id=account.id,
                        source=file_path.name,
                        source_record={},  # Don't leak full CRM row to agent
                    )
                )
        # Sort: score descending, CRM source wins ties (contacts.json > persons.csv)
        def _sort_key(c: ContactCandidate) -> tuple:
            return (
                c.score or 0.0,
                1 if c.source == "contacts.json" else 0,
            )
        candidates.sort(key=_sort_key, reverse=True)

        # Dedupe across sources: email > linkedin (path-aware) > name+account
        deduped: dict[str, ContactCandidate] = {}
        seen_emails: dict[str, str] = {}
        seen_linkedins: dict[str, str] = {}
        seen_names: dict[str, str] = {}

        for candidate in candidates:
            dedup_key: str | None = None
            # Check email first
            if candidate.email:
                email_norm = match_key(candidate.email)
                if email_norm in seen_emails:
                    dedup_key = seen_emails[email_norm]
                else:
                    seen_emails[email_norm] = candidate.crm_contact_id
            # Check LinkedIn — use normalize_profile_url (keeps path, not just host)
            if not dedup_key and candidate.linkedin:
                li_norm = normalize_profile_url(candidate.linkedin)
                if li_norm in seen_linkedins:
                    dedup_key = seen_linkedins[li_norm]
                else:
                    seen_linkedins[li_norm] = candidate.crm_contact_id
            # Check name + account
            if not dedup_key and candidate.name:
                name_norm = f"{match_key(candidate.name)}@{match_key(candidate.account_id or '')}"
                if name_norm in seen_names:
                    dedup_key = seen_names[name_norm]
                else:
                    seen_names[name_norm] = candidate.crm_contact_id
            # If already seen, skip (higher-score version already kept)
            if dedup_key and dedup_key != candidate.crm_contact_id:
                continue
            deduped.setdefault(candidate.crm_contact_id, candidate)
        return list(deduped.values())[:limit]

    # ------------------------------------------------------------------
    # Internal matching helpers
    # ------------------------------------------------------------------

    def _match_account_record(self, ref: str, record: dict[str, Any], source_path: Path) -> Match | None:
        aliases = extract_account_aliases(record)
        if not aliases:
            return None
        ref_key = match_key(ref)
        alias_keys = {match_key(alias): alias for alias in aliases}
        canonical_id = coerce_str(record, ACCOUNT_ID_KEYS) or slugify(coerce_str(record, ACCOUNT_NAME_KEYS) or ref)
        name = coerce_str(record, ACCOUNT_NAME_KEYS)
        score = 0
        confidence = "guessed"
        if ref_key == match_key(canonical_id):
            score = 100
            confidence = "exact"
        elif ref_key in alias_keys:
            score = 90
            confidence = "alias"
        elif any(ref_key and ref_key in match_key(alias) for alias in aliases):
            score = 70
            confidence = "fuzzy"
        else:
            return None
        entity = ResolvedEntity(
            ref=ref,
            id=canonical_id,
            name=name,
            aliases=aliases,
            confidence=confidence,
            source_files=[str(source_path)],
        )
        return Match(entity=entity, source_record=record, source_path=source_path, score=score)

    def _match_contact_record(self, ref: str, record: dict[str, Any], source_path: Path) -> Match | None:
        aliases = extract_contact_aliases(record)
        if not aliases:
            return None
        ref_key = match_key(ref)
        alias_keys = {match_key(alias): alias for alias in aliases}
        canonical_id = coerce_str(record, CONTACT_ID_KEYS) or slugify(coerce_str(record, CONTACT_NAME_KEYS) or ref)
        name = coerce_str(record, CONTACT_NAME_KEYS)
        score = 0
        confidence = "guessed"
        if ref_key == match_key(canonical_id):
            score = 100
            confidence = "exact"
        elif ref_key in alias_keys:
            score = 90
            confidence = "alias"
        elif any(ref_key and ref_key in match_key(alias) for alias in aliases):
            score = 70
            confidence = "fuzzy"
        else:
            return None
        entity = ResolvedEntity(
            ref=ref,
            id=canonical_id,
            name=name,
            aliases=aliases,
            confidence=confidence,
            source_files=[str(source_path)],
        )
        return Match(entity=entity, source_record=record, source_path=source_path, score=score)

    def _record_links_to_account(self, record: dict[str, Any], account: ResolvedEntity) -> bool:
        record_keys = {match_key(v) for v in record_values(record, CONTACT_ACCOUNT_LINK_KEYS)}
        account_keys = {match_key(account.id), match_key(account.ref)}
        account_keys.update(match_key(a) for a in account.aliases)
        if account.name:
            account_keys.add(match_key(account.name))
        return bool(record_keys.intersection(account_keys))

    @staticmethod
    def _contact_priority_score(record: dict[str, Any]) -> float:
        score = 0.0
        for key in ("priority_score", "score", "relationship_score", "strength", "path_score"):
            value = record.get(key)
            if value in {None, ""}:
                continue
            try:
                score += float(value)
                break
            except (TypeError, ValueError):
                continue
        title = normalize_text(coerce_str(record, CONTACT_TITLE_KEYS) or "")
        if any(keyword in title for keyword in ["vp", "head", "chief", "director", "cfo", "finops"]):
            score += 15
        if record.get("warm_path") or record.get("intro_available"):
            score += 10
        if record.get("email") or record.get("work_email"):
            score += 5
        return round(score, 2)

    @staticmethod
    def _contact_reasons(record: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        title = coerce_str(record, CONTACT_TITLE_KEYS)
        if title:
            reasons.append(f"title: {title}")
        if record.get("warm_path"):
            reasons.append("warm path available")
        if record.get("intro_available"):
            reasons.append("intro available")
        if record.get("relationship_score") not in {None, ""}:
            reasons.append(f"relationship_score: {record.get('relationship_score')}")
        if record.get("department") not in {None, ""}:
            reasons.append(f"department: {record.get('department')}")
        return reasons[:4]
