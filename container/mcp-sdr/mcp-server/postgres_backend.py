"""Postgres backend for container MCP SDR server.

Self-contained — does NOT depend on cockpit's db.py.
Connects via POSTGRES_DSN env var (standard libpq connection string).

If POSTGRES_DSN is not set or Postgres is unreachable, available() returns False
and the MCP server falls back to file-based reads.

DSN format: postgresql://user:password@host:port/dbname
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("flarion-sdr-mcp")

_KEY_RE = re.compile(r"[^a-z0-9]+")


def _normalize_key(name: str) -> str:
    """Normalize a company name to a lookup key: lowercase, underscores."""
    key = name.lower().strip()
    key = _KEY_RE.sub("_", key)
    return key.strip("_")


class PostgresBackend:
    """Postgres-backed data provider for MCP SDR tools.

    Uses psycopg2 with a DSN from the POSTGRES_DSN env var.
    Read-only connection for queries, write connection for log_outreach.
    """

    _available: bool | None = None
    _ro_conn: Any = None
    _rw_conn: Any = None

    @classmethod
    def available(cls) -> bool:
        """Check if Postgres is reachable. Cached after first call."""
        if cls._available is not None:
            return cls._available
        dsn = os.environ.get("POSTGRES_DSN", "")
        if not dsn:
            logger.info("POSTGRES_DSN not set — Postgres backend disabled")
            cls._available = False
            return False
        try:
            import psycopg2
            conn = psycopg2.connect(dsn, connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            cls._available = True
            logger.info("Postgres backend available")
        except Exception as e:
            logger.warning("Postgres backend unavailable: %s", e)
            cls._available = False
        return cls._available

    @classmethod
    def _get_ro_conn(cls):
        """Lazy-init read-only connection."""
        if cls._ro_conn is None or cls._ro_conn.closed:
            import psycopg2
            import psycopg2.extras
            dsn = os.environ.get("POSTGRES_DSN", "")
            cls._ro_conn = psycopg2.connect(dsn, connect_timeout=5)
            cls._ro_conn.autocommit = True
        return cls._ro_conn

    @classmethod
    def _get_rw_conn(cls):
        """Lazy-init read-write connection."""
        if cls._rw_conn is None or cls._rw_conn.closed:
            import psycopg2
            dsn = os.environ.get("POSTGRES_DSN", "")
            cls._rw_conn = psycopg2.connect(dsn, connect_timeout=5)
            cls._rw_conn.autocommit = True
        return cls._rw_conn

    def _ro(self, query: str, params: tuple | None = None) -> list[dict[str, Any]]:
        """Execute read-only query, return list of dicts."""
        conn = self._get_ro_conn()
        with conn.cursor() as cur:
            cur.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _ro_one(self, query: str, params: tuple | None = None) -> dict[str, Any] | None:
        """Execute read-only query, return one dict or None."""
        conn = self._get_ro_conn()
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def _execute(self, query: str, params: tuple | None = None) -> int:
        """Execute write query, return rowcount."""
        conn = self._get_rw_conn()
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount

    # ------------------------------------------------------------------
    # Account resolution
    # ------------------------------------------------------------------

    def resolve_account_id(self, account_ref: str) -> tuple[int | None, str | None, str | None]:
        """Resolve account ref (name, key, id, or Airtable record ID) to (db_id, name_key, display_name)."""
        key = _normalize_key(account_ref)
        row = self._ro_one("SELECT id, name_key, name FROM accounts WHERE name_key = %s", (key,))
        if row:
            return row["id"], row["name_key"], row["name"]

        # Try Airtable record ID via external_refs (agent often passes these)
        if account_ref.startswith("rec"):
            ref_row = self._ro_one("""
                SELECT a.id, a.name_key, a.name
                FROM external_refs er
                JOIN accounts a ON a.id = er.entity_id
                WHERE er.external_id = %s
                  AND er.system = 'airtable'
                  AND er.entity_type = 'account'
                LIMIT 1
            """, (account_ref,))
            if ref_row:
                return ref_row["id"], ref_row["name_key"], ref_row["name"]

        # Try partial match on name
        rows = self._ro(
            "SELECT id, name_key, name FROM accounts WHERE name_key LIKE %s LIMIT 3",
            (f"%{key}%",),
        )
        if len(rows) == 1:
            return rows[0]["id"], rows[0]["name_key"], rows[0]["name"]
        return None, None, None

    # ------------------------------------------------------------------
    # Tool data providers
    # ------------------------------------------------------------------

    def get_account_score(self, account_ref: str) -> dict[str, Any] | None:
        """Get fit score + account metadata."""
        db_id, key, name = self.resolve_account_id(account_ref)
        if not db_id:
            return None

        acct = self._ro_one("SELECT * FROM accounts WHERE id = %s", (db_id,))
        fit = self._ro_one("""
            SELECT score, tier, de_density, signals_used, model_version
            FROM account_fit_scores WHERE account_id = %s
            ORDER BY scored_at DESC LIMIT 1
        """, (db_id,))

        highlights = []
        if acct.get("cloud_platform"):
            highlights.append(f"platform: {acct['cloud_platform']}")
        if acct.get("employee_range"):
            highlights.append(f"employees: {acct['employee_range']}")
        if acct.get("annual_spend"):
            highlights.append(f"spend: ${float(acct['annual_spend']):,.0f}/yr")
        if acct.get("industry"):
            highlights.append(f"industry: {acct['industry']}")

        return {
            "account_id": key,
            "account_name": name,
            "fit_score": float(fit["score"]) if fit and fit.get("score") else None,
            "tier": (fit["tier"] if fit else None) or acct.get("tier"),
            "de_density": float(fit["de_density"]) if fit and fit.get("de_density") else None,
            "reasons": [],
            "highlights": highlights,
        }

    def get_best_contacts(self, account_ref: str, limit: int = 5) -> list[dict[str, Any]]:
        """Get ranked contacts for an account."""
        db_id, _, _ = self.resolve_account_id(account_ref)
        if not db_id:
            return []

        contacts = self._ro("""
            SELECT c.id, c.full_name, c.email, c.title, c.linkedin_url,
                   c.seniority, c.persona_group, c.persona_confidence,
                   acr.role, acr.is_primary
            FROM contacts c
            JOIN account_contact_roles acr ON c.id = acr.contact_id
            WHERE acr.account_id = %s
            ORDER BY acr.is_primary DESC NULLS LAST,
                     c.persona_confidence DESC NULLS LAST,
                     c.full_name
            LIMIT %s
        """, (db_id, max(1, min(limit, 10))))

        return [
            {
                "crm_contact_id": str(c["id"]),
                "name": c["full_name"],
                "email": c["email"],
                "title": c["title"],
                "linkedin": c["linkedin_url"],
                "seniority": c["seniority"],
                "persona_group": c["persona_group"],
                "role": c["role"],
            }
            for c in contacts
        ]

    _ACTIONABLE_SIGNAL_TYPES = [
        'buying_signal', 'ecosystem_shift', 'competitive_move',
        'community_signal', 'job_posting', 'product_launch',
        'competitive_campaign', 'project_milestone',
    ]

    def get_timing_signals(self, account_ref: str, limit: int = 5) -> list[dict[str, Any]]:
        """Get timing signals for an account.

        Filters to actionable signal types only (excludes channel_status
        and other health-check signals that are not outreach triggers).
        """
        db_id, _, _ = self.resolve_account_id(account_ref)
        if not db_id:
            return []

        signals = self._ro("""
            SELECT signal_type, signal_source, headline, summary,
                   why_it_matters, signal_score, detected_at
            FROM account_signals
            WHERE account_id = %s
              AND signal_type = ANY(%s)
              AND (freshness_status IS NULL OR freshness_status != 'expired')
            ORDER BY detected_at DESC NULLS LAST
            LIMIT %s
        """, (db_id, self._ACTIONABLE_SIGNAL_TYPES, max(1, min(limit, 10))))

        return [
            {
                "signal_type": s["signal_type"],
                "summary": s["headline"] or s.get("summary", ""),
                "observed_at": s["detected_at"].isoformat() if s.get("detected_at") else None,
                "score": float(s["signal_score"]) if s.get("signal_score") else None,
                "source": s["signal_source"],
                "why_it_matters": s.get("why_it_matters"),
            }
            for s in signals
        ]

    def get_recent_outreach(self, account_ref: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent outreach events."""
        db_id, _, _ = self.resolve_account_id(account_ref)
        if not db_id:
            return []

        outreach = self._ro("""
            SELECT oe.id, oe.channel, oe.direction, oe.subject, oe.status,
                   oe.sent_at, oe.source, oe.metadata,
                   c.full_name as contact_name
            FROM outreach_events oe
            LEFT JOIN contacts c ON oe.contact_id = c.id
            WHERE oe.account_id = %s
            ORDER BY oe.sent_at DESC NULLS LAST, oe.created_at DESC
            LIMIT %s
        """, (db_id, max(1, min(limit, 25))))

        return [
            {
                "status": o["status"],
                "channel": o.get("channel"),
                "subject": o.get("subject"),
                "contact_name": o.get("contact_name"),
                "sent_at": o["sent_at"].isoformat() if o.get("sent_at") else None,
            }
            for o in outreach
        ]

    def enrich_contact(self, contact_id: str) -> dict[str, Any] | None:
        """Get enrichment data for a contact."""
        try:
            cid = int(contact_id)
        except (ValueError, TypeError):
            return None

        enrichment = self._ro_one("""
            SELECT e.bio, e.keywords, e.enriched_at,
                   c.full_name, c.email, c.title, c.linkedin_url
            FROM enrichment e
            JOIN contacts c ON e.contact_id = c.id
            WHERE e.contact_id = %s
            ORDER BY e.enriched_at DESC LIMIT 1
        """, (cid,))

        if not enrichment:
            return None

        keywords = enrichment.get("keywords")
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except (json.JSONDecodeError, TypeError):
                keywords = []

        return {
            "name": enrichment["full_name"],
            "email": enrichment["email"],
            "title": enrichment["title"],
            "linkedin": enrichment["linkedin_url"],
            "bio": enrichment.get("bio"),
            "keywords": keywords or [],
            "found_in_cache": True,
        }

    def log_outreach(self, account_ref: str, contact_id: str | None,
                     status: str, angle: str | None, why_now: str | None,
                     draft_text: str | None, run_id: str | None,
                     metadata: dict | None = None) -> dict[str, Any]:
        """Write outreach event to Postgres."""
        db_id, key, name = self.resolve_account_id(account_ref)
        if not db_id:
            return {"success": False, "notes": [f"Account not found: {account_ref}"]}

        cid = None
        if contact_id:
            try:
                cid = int(contact_id)
            except (ValueError, TypeError):
                pass

        try:
            self._execute("""
                INSERT INTO outreach_events (
                    account_id, contact_id, channel, direction, subject,
                    status, source, metadata
                ) VALUES (%s, %s, 'email', 'outbound', %s, %s, 'nanoclaw', %s)
            """, (
                db_id, cid, angle, status,
                json.dumps({
                    "run_id": run_id,
                    "why_now": why_now,
                    "draft_text": draft_text,
                    **(metadata or {}),
                }),
            ))
            return {"success": True, "notes": [f"Logged {status} for {name}"]}
        except Exception as e:
            logger.error("Postgres log_outreach failed: %s", e)
            return {"success": False, "notes": [str(e)]}
