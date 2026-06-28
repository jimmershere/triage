"""Tests for the advisory mapping routes (Workstream 1).

Covers the table-bootstrap contract (``ensure_mapping_tables``) and the
advisory ``/mapping/advisor/suggest`` endpoint on the no-persistence path, so
the route wiring + request model + advisor integration are exercised without a
live database.
"""

import re
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.mapping_routes import SuggestRequest, advisor_suggest, ensure_mapping_tables


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        pass

    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> None:
        self.connection.executed.append((re.sub(r"\s+", " ", query.strip()), params))


class FakeConnection:
    def __init__(self) -> None:
        self.executed: List[Tuple[str, Optional[Iterable[Any]]]] = []
        self.commits = 0

    def cursor(self, cursor_factory: Optional[Any] = None) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1


def _statements(conn: FakeConnection) -> List[str]:
    return [stmt for stmt, _ in conn.executed]


def test_ensure_mapping_tables_creates_queue_and_rule_tables() -> None:
    conn = FakeConnection()
    ensure_mapping_tables(conn)
    statements = _statements(conn)
    assert any("CREATE TABLE IF NOT EXISTS mapping_suggestion" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS mapping_rule" in s for s in statements)
    assert any("UNIQUE (rule_key, version)" in s for s in statements)
    assert conn.commits == 1


X12_837 = (
    "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       "
    "*060628*1600*^*00501*000000001*0*T*:~"
    "GS*HC*SENDER*RECEIVER*20060628*1600*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "CLM*ACCT001*500***11:B:1~"
    "SE*4*0001~GE*1*1~IEA*1*000000001~"
)
FLAT_FILE = "acct,charge\nACCT001,500\n"


def test_suggest_advisory_only_without_persistence() -> None:
    # persist=False keeps this off the database entirely; calling the route
    # function directly exercises the request model + advisor integration.
    req = SuggestRequest(
        x12_text=X12_837,
        flat_file_text=FLAT_FILE,
        delimiter=",",
        header=True,
        partner_id="ACME",
        persist=False,
    )
    body = advisor_suggest(req)
    assert body["advisory"] is True
    assert body["transaction_set"] == "837"
    assert "enqueued_ids" not in body  # persistence disabled
    assert body["candidate_count"] >= 1
    top = body["candidates"][0]
    assert top["source"]["segment_id"] == "CLM"
    assert top["target"]["name"] == "acct"
