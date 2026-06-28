"""Database persistence for the code-set registry (Workstream 2).

The in-process :class:`CodesetRegistry` is the runtime resolver; these helpers
make Postgres the system of record. ``ensure_codeset_tables`` creates the schema
(mirrors ``migrations/003_codesets.sql``), ``persist_version`` writes an ingested
version + its values, and ``load_active_registry`` rebuilds a registry from the
stored versions (used on startup / after a ``codeset.reloaded`` event).

psycopg2 is imported lazily so importing the validation package never requires a
database driver.
"""
from __future__ import annotations

from typing import Any

from .registry import CodesetRegistry, CodesetVersion, make_version


def ensure_codeset_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS codeset (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                publisher TEXT,
                cadence TEXT,
                x12_source_or_ecl TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS codeset_version (
                id BIGSERIAL PRIMARY KEY,
                codeset_id BIGINT NOT NULL REFERENCES codeset(id) ON DELETE CASCADE,
                version_label TEXT NOT NULL,
                source_effective_date DATE,
                publication_date DATE,
                implementation_date DATE,
                ingest_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                checksum TEXT NOT NULL,
                complete BOOLEAN NOT NULL DEFAULT FALSE,
                CONSTRAINT codeset_version_unique UNIQUE (codeset_id, version_label)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS codeset_version_effective_idx ON codeset_version (codeset_id, source_effective_date)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS codeset_value (
                id BIGSERIAL PRIMARY KEY,
                codeset_version_id BIGINT NOT NULL REFERENCES codeset_version(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                description TEXT,
                valid_from DATE,
                valid_to DATE,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','deactivated')),
                CONSTRAINT codeset_value_unique UNIQUE (codeset_version_id, code)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS codeset_value_code_idx ON codeset_value (codeset_version_id, code)"
        )
    conn.commit()


def persist_version(conn, version: CodesetVersion) -> int:
    """Upsert a code set, its version and value rows. Returns codeset_version.id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO codeset (name) VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (version.codeset.strip().lower(),),
        )
        codeset_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO codeset_version
                (codeset_id, version_label, source_effective_date, publication_date,
                 implementation_date, checksum, complete)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (codeset_id, version_label) DO UPDATE SET
                source_effective_date = EXCLUDED.source_effective_date,
                publication_date = EXCLUDED.publication_date,
                implementation_date = EXCLUDED.implementation_date,
                checksum = EXCLUDED.checksum,
                complete = EXCLUDED.complete,
                ingest_ts = NOW()
            RETURNING id
            """,
            (
                codeset_id,
                version.version_label,
                version.source_effective_date,
                version.publication_date,
                version.implementation_date,
                version.checksum,
                version.complete,
            ),
        )
        version_id = cur.fetchone()[0]
        cur.execute(
            "DELETE FROM codeset_value WHERE codeset_version_id = %s", (version_id,)
        )
        for value in version.values.values():
            cur.execute(
                """
                INSERT INTO codeset_value
                    (codeset_version_id, code, description, valid_from, valid_to, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    version_id,
                    value.code,
                    value.description,
                    value.valid_from,
                    value.valid_to,
                    value.status,
                ),
            )
    conn.commit()
    return version_id


def load_active_registry(conn) -> CodesetRegistry:
    """Rebuild a :class:`CodesetRegistry` from the persisted code-set tables."""
    from psycopg2.extras import RealDictCursor

    registry = CodesetRegistry()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT cv.id, c.name AS codeset, cv.version_label, cv.source_effective_date,
                   cv.publication_date, cv.implementation_date, cv.checksum, cv.complete
              FROM codeset_version cv
              JOIN codeset c ON c.id = cv.codeset_id
             ORDER BY c.name, cv.source_effective_date
            """
        )
        versions = cur.fetchall()
        for v in versions:
            cur.execute(
                """
                SELECT code, description, valid_from, valid_to, status
                  FROM codeset_value WHERE codeset_version_id = %s
                """,
                (v["id"],),
            )
            values: list[dict[str, Any]] = [dict(r) for r in cur.fetchall()]
            registry.register(
                make_version(
                    codeset=v["codeset"],
                    version_label=v["version_label"],
                    values=values,
                    source_effective_date=v["source_effective_date"],
                    publication_date=v["publication_date"],
                    implementation_date=v["implementation_date"],
                    checksum=v["checksum"],
                    complete=v["complete"],
                )
            )
    return registry
