"""Normalized trading-partner persistence (Workstream 6).

The single source of truth for partner identity, enabled transactions, per-SNIP
severity policy, acknowledgement profile, contacts and trading-partner
agreements. Workstream 2 (code-set scoping) and Workstream 7 (SNIP toggle + ack
switch) read their per-partner configuration from here.

Writes are idempotent upserts keyed on a stable ``external_id``: the partner row
is upserted and its child collections replaced as a unit, so re-syncing the same
payload is a no-op. SNIP/ack rows carry ``effective_from`` / ``effective_to`` so
configuration is versioned over time.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from psycopg2.extras import RealDictCursor


# ---------------------------------------------------------------------------
# Schema bootstrap (mirrors migrations/002_partners.sql)
# ---------------------------------------------------------------------------

def ensure_partner_tables(conn) -> None:
    """Create the normalized partner tables if they do not already exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner (
                id BIGSERIAL PRIMARY KEY,
                external_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','inactive','suspended','test')),
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_identifier (
                id BIGSERIAL PRIMARY KEY,
                partner_id BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
                interchange_qualifier TEXT NOT NULL,
                interchange_id TEXT NOT NULL,
                application_id TEXT,
                direction TEXT NOT NULL CHECK (direction IN ('sender','receiver')),
                usage TEXT NOT NULL DEFAULT 'P' CHECK (usage IN ('P','T')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT partner_identifier_unique
                    UNIQUE (interchange_qualifier, interchange_id, direction, usage)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS partner_identifier_partner_idx ON partner_identifier (partner_id)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_transaction (
                id BIGSERIAL PRIMARY KEY,
                partner_id BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
                transaction_type TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT partner_transaction_unique
                    UNIQUE (partner_id, transaction_type, direction)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS partner_transaction_partner_idx ON partner_transaction (partner_id)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_snip_policy (
                id BIGSERIAL PRIMARY KEY,
                partner_id BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
                transaction_type TEXT NOT NULL DEFAULT '*',
                snip_type INTEGER NOT NULL CHECK (snip_type BETWEEN 1 AND 7),
                severity TEXT NOT NULL CHECK (severity IN ('enforce-reject','warn','off')),
                policy_name TEXT NOT NULL DEFAULT 'edig-parity-v1',
                effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
                effective_to DATE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS partner_snip_policy_lookup_idx ON partner_snip_policy (partner_id, transaction_type, snip_type, effective_from)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_ack_profile (
                id BIGSERIAL PRIMARY KEY,
                partner_id BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
                ack_profile TEXT NOT NULL DEFAULT '999_only'
                    CHECK (ack_profile IN ('999_only','999_plus_277CA')),
                effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
                effective_to DATE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS partner_ack_profile_lookup_idx ON partner_ack_profile (partner_id, effective_from)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_contact (
                id BIGSERIAL PRIMARY KEY,
                partner_id BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
                contact_type TEXT NOT NULL DEFAULT 'technical'
                    CHECK (contact_type IN ('technical','billing','administrative','enrollment')),
                name TEXT,
                email TEXT,
                phone TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS partner_contact_partner_idx ON partner_contact (partner_id)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_agreement (
                id BIGSERIAL PRIMARY KEY,
                partner_id BIGINT NOT NULL REFERENCES partner(id) ON DELETE CASCADE,
                agreement_version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','active','terminated')),
                signed_date DATE,
                effective_from DATE,
                effective_to DATE,
                document_ref TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS partner_agreement_partner_idx ON partner_agreement (partner_id)"
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _row(d: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Idempotent upsert
# ---------------------------------------------------------------------------

def upsert_partner(conn, data: dict[str, Any]) -> dict[str, Any]:
    """Idempotently create/update a partner and replace its child collections.

    Keyed on ``external_id``. Child collections present in ``data`` replace the
    stored set as a unit; absent collections are left untouched.
    """
    external_id = (data.get("external_id") or "").strip()
    if not external_id:
        raise ValueError("external_id is required")
    name = data.get("name") or external_id
    status = data.get("status") or "active"
    notes = data.get("notes")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO partner (external_id, name, status, notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (external_id) DO UPDATE SET
                name = EXCLUDED.name,
                status = EXCLUDED.status,
                notes = EXCLUDED.notes,
                updated_at = NOW()
            RETURNING id
            """,
            (external_id, name, status, notes),
        )
        partner_id = cur.fetchone()["id"]

        _replace_identifiers(cur, partner_id, data.get("identifiers"))
        _replace_transactions(cur, partner_id, data.get("transactions"))
        _replace_snip_policies(cur, partner_id, data.get("snip_policies"))
        _replace_ack_profiles(cur, partner_id, data.get("ack_profiles"))
        _replace_contacts(cur, partner_id, data.get("contacts"))
        _replace_agreements(cur, partner_id, data.get("agreements"))

    conn.commit()
    return get_partner(conn, external_id)


def _replace_identifiers(cur, partner_id: int, items: Optional[list]) -> None:
    if items is None:
        return
    cur.execute("DELETE FROM partner_identifier WHERE partner_id = %s", (partner_id,))
    for it in items:
        cur.execute(
            """
            INSERT INTO partner_identifier
                (partner_id, interchange_qualifier, interchange_id, application_id, direction, usage)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                partner_id,
                it["interchange_qualifier"],
                it["interchange_id"],
                it.get("application_id"),
                it["direction"],
                it.get("usage", "P"),
            ),
        )


def _replace_transactions(cur, partner_id: int, items: Optional[list]) -> None:
    if items is None:
        return
    cur.execute("DELETE FROM partner_transaction WHERE partner_id = %s", (partner_id,))
    for it in items:
        cur.execute(
            """
            INSERT INTO partner_transaction (partner_id, transaction_type, direction, enabled)
            VALUES (%s, %s, %s, %s)
            """,
            (partner_id, it["transaction_type"], it["direction"], it.get("enabled", True)),
        )


def _replace_snip_policies(cur, partner_id: int, items: Optional[list]) -> None:
    if items is None:
        return
    cur.execute("DELETE FROM partner_snip_policy WHERE partner_id = %s", (partner_id,))
    for it in items:
        cur.execute(
            """
            INSERT INTO partner_snip_policy
                (partner_id, transaction_type, snip_type, severity, policy_name, effective_from, effective_to)
            VALUES (%s, %s, %s, %s, %s, COALESCE(%s, CURRENT_DATE), %s)
            """,
            (
                partner_id,
                it.get("transaction_type", "*"),
                int(it["snip_type"]),
                it["severity"],
                it.get("policy_name", "edig-parity-v1"),
                it.get("effective_from"),
                it.get("effective_to"),
            ),
        )


def _replace_ack_profiles(cur, partner_id: int, items: Optional[list]) -> None:
    if items is None:
        return
    cur.execute("DELETE FROM partner_ack_profile WHERE partner_id = %s", (partner_id,))
    for it in items:
        cur.execute(
            """
            INSERT INTO partner_ack_profile (partner_id, ack_profile, effective_from, effective_to)
            VALUES (%s, %s, COALESCE(%s, CURRENT_DATE), %s)
            """,
            (partner_id, it.get("ack_profile", "999_only"), it.get("effective_from"), it.get("effective_to")),
        )


def _replace_contacts(cur, partner_id: int, items: Optional[list]) -> None:
    if items is None:
        return
    cur.execute("DELETE FROM partner_contact WHERE partner_id = %s", (partner_id,))
    for it in items:
        cur.execute(
            """
            INSERT INTO partner_contact (partner_id, contact_type, name, email, phone)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (partner_id, it.get("contact_type", "technical"), it.get("name"), it.get("email"), it.get("phone")),
        )


def _replace_agreements(cur, partner_id: int, items: Optional[list]) -> None:
    if items is None:
        return
    cur.execute("DELETE FROM partner_agreement WHERE partner_id = %s", (partner_id,))
    for it in items:
        cur.execute(
            """
            INSERT INTO partner_agreement
                (partner_id, agreement_version, status, signed_date, effective_from, effective_to, document_ref)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                partner_id,
                it["agreement_version"],
                it.get("status", "pending"),
                it.get("signed_date"),
                it.get("effective_from"),
                it.get("effective_to"),
                it.get("document_ref"),
            ),
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _children(cur, table: str, partner_id: int) -> list[dict[str, Any]]:
    cur.execute(f"SELECT * FROM {table} WHERE partner_id = %s ORDER BY id", (partner_id,))
    return [_row(dict(r)) for r in cur.fetchall()]


def get_partner(conn, external_id: str) -> Optional[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM partner WHERE external_id = %s", (external_id,))
        row = cur.fetchone()
        if not row:
            return None
        partner = _row(dict(row))
        pid = row["id"]
        partner["identifiers"] = _children(cur, "partner_identifier", pid)
        partner["transactions"] = _children(cur, "partner_transaction", pid)
        partner["snip_policies"] = _children(cur, "partner_snip_policy", pid)
        partner["ack_profiles"] = _children(cur, "partner_ack_profile", pid)
        partner["contacts"] = _children(cur, "partner_contact", pid)
        partner["agreements"] = _children(cur, "partner_agreement", pid)
    return partner


def list_partners(conn) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT external_id FROM partner ORDER BY external_id")
        ids = [r["external_id"] for r in cur.fetchall()]
    return [get_partner(conn, ext) for ext in ids]


def delete_partner(conn, external_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM partner WHERE external_id = %s RETURNING id", (external_id,)
        )
        deleted = cur.fetchone() is not None
    conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# Resolution helpers consumed by WS2 / WS7
# ---------------------------------------------------------------------------

def snip_policy_rows(conn, external_id: str, *, as_of: dt.date | None = None) -> list[dict[str, Any]]:
    """Return effective ``partner_snip_policy`` rows for a partner."""
    as_of = as_of or dt.date.today()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT psp.transaction_type, psp.snip_type, psp.severity,
                   psp.policy_name, psp.effective_from, psp.effective_to
              FROM partner_snip_policy psp
              JOIN partner p ON p.id = psp.partner_id
             WHERE p.external_id = %s
               AND psp.effective_from <= %s
               AND (psp.effective_to IS NULL OR psp.effective_to >= %s)
             ORDER BY psp.effective_from
            """,
            (external_id, as_of, as_of),
        )
        return [_row(dict(r)) for r in cur.fetchall()]


def resolve_ack_profile(conn, external_id: str, *, as_of: dt.date | None = None) -> str:
    """Resolve the partner's effective ack profile (default ``999_only``)."""
    as_of = as_of or dt.date.today()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ack_profile
              FROM partner_ack_profile pap
              JOIN partner p ON p.id = pap.partner_id
             WHERE p.external_id = %s
               AND pap.effective_from <= %s
               AND (pap.effective_to IS NULL OR pap.effective_to >= %s)
             ORDER BY pap.effective_from DESC
             LIMIT 1
            """,
            (external_id, as_of, as_of),
        )
        row = cur.fetchone()
    return row[0] if row else "999_only"


def resolve_partner_by_identifier(
    conn,
    *,
    interchange_qualifier: str,
    interchange_id: str,
    direction: str = "sender",
    usage: str = "P",
) -> Optional[str]:
    """Resolve a partner's ``external_id`` from an ISA/GS qualifier+id pair."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.external_id
              FROM partner_identifier pi
              JOIN partner p ON p.id = pi.partner_id
             WHERE pi.interchange_qualifier = %s
               AND pi.interchange_id = %s
               AND pi.direction = %s
               AND pi.usage = %s
             LIMIT 1
            """,
            (interchange_qualifier, interchange_id, direction, usage),
        )
        row = cur.fetchone()
    return row[0] if row else None
