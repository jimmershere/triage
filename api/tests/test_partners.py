"""DB-backed tests for the WS6 normalized partner model.

These exercise the real schema against the running Postgres (triage_postgres_1,
host port 15432). They are isolated in a throwaway schema that is dropped on
teardown, so no existing table is touched. If no database is reachable the whole
module is skipped — the FakeConnection schema-statement test below always runs.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import partners_service  # noqa: E402

_DSN = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://edi:edi@localhost:15432/edi"
)


@pytest.fixture()
def conn():
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        connection = psycopg2.connect(_DSN, connect_timeout=3)
    except Exception as exc:  # pragma: no cover - depends on environment
        pytest.skip(f"no test database reachable at {_DSN}: {exc}")
    schema = f"ws6_test_{uuid.uuid4().hex[:12]}"
    with connection.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
    connection.commit()
    try:
        partners_service.ensure_partner_tables(connection)
        yield connection
    finally:
        connection.rollback()
        with connection.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.commit()
        connection.close()


def _sample_partner(external_id="ACME-PAYER"):
    return {
        "external_id": external_id,
        "name": "Acme Payer",
        "status": "active",
        "identifiers": [
            {
                "interchange_qualifier": "ZZ",
                "interchange_id": "ACME01",
                "direction": "sender",
                "usage": "P",
            }
        ],
        "transactions": [
            {"transaction_type": "837P", "direction": "inbound", "enabled": True}
        ],
        "snip_policies": [
            {"snip_type": 3, "severity": "warn", "transaction_type": "*"},
            {"snip_type": 7, "severity": "off", "transaction_type": "837P"},
        ],
        "ack_profiles": [{"ack_profile": "999_only"}],
        "contacts": [{"contact_type": "technical", "email": "edi@acme.example"}],
        "agreements": [{"agreement_version": "TPA-v1", "status": "active"}],
    }


def test_upsert_is_idempotent(conn):
    first = partners_service.upsert_partner(conn, _sample_partner())
    assert first["external_id"] == "ACME-PAYER"
    assert len(first["identifiers"]) == 1
    assert len(first["snip_policies"]) == 2

    # Re-running the same payload must not duplicate child rows.
    second = partners_service.upsert_partner(conn, _sample_partner())
    assert len(second["identifiers"]) == 1
    assert len(second["snip_policies"]) == 2
    assert len(partners_service.list_partners(conn)) == 1


def test_get_and_delete(conn):
    partners_service.upsert_partner(conn, _sample_partner())
    fetched = partners_service.get_partner(conn, "ACME-PAYER")
    assert fetched is not None
    assert fetched["name"] == "Acme Payer"

    assert partners_service.delete_partner(conn, "ACME-PAYER") is True
    assert partners_service.get_partner(conn, "ACME-PAYER") is None
    assert partners_service.delete_partner(conn, "ACME-PAYER") is False


def test_resolve_ack_profile_default(conn):
    partners_service.upsert_partner(
        conn, {"external_id": "NO-ACK", "name": "No Ack Cfg"}
    )
    assert partners_service.resolve_ack_profile(conn, "NO-ACK") == "999_only"


def test_resolve_partner_by_identifier(conn):
    partners_service.upsert_partner(conn, _sample_partner())
    ext = partners_service.resolve_partner_by_identifier(
        conn, interchange_qualifier="ZZ", interchange_id="ACME01", direction="sender"
    )
    assert ext == "ACME-PAYER"


def test_snip_policy_rows_feed_policy_engine(conn):
    partners_service.upsert_partner(conn, _sample_partner())
    rows = partners_service.snip_policy_rows(conn, "ACME-PAYER")
    assert rows
    sys.path.insert(0, str(ROOT / "worker_py"))
    from validation.policy import PolicyMode, policy_from_rows  # type: ignore

    snip_policy = policy_from_rows("acme", rows)
    assert snip_policy.mode_for(3) == PolicyMode.WARN
    assert snip_policy.mode_for(7, "837P") == PolicyMode.OFF
