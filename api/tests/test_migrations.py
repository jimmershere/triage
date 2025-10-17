import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import ensure_app_users, ensure_bootstrap_admin, ensure_x12_addon_tables


class FakeCursor:
    def __init__(
        self,
        connection: "FakeConnection",
        cursor_factory: Optional[Any] = None,
    ) -> None:
        self.connection = connection
        self.cursor_factory = cursor_factory
        self._result: List[Any] = []
        self.rowcount: int = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - nothing to clean up
        pass

    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> None:
        normalized = re.sub(r"\s+", " ", query.strip())
        self.connection.executed.append((normalized, params))
        self.rowcount = 0
        if "information_schema.columns" in normalized:
            rows = []
            for name, meta in self.connection.columns.items():
                rows.append(
                    (
                        name,
                        meta.get("data_type", "text"),
                        "YES" if meta.get("is_nullable", True) else "NO",
                    )
                )
            self._result = rows
        elif "FROM pg_constraint" in normalized:
            self._result = [(1,)] if self.connection.constraint_exists else []
        elif "FROM app_users" in normalized and "SELECT username" in normalized:
            if self.cursor_factory is RealDictCursor and self.connection.bootstrap_admin_row:
                self._result = [self.connection.bootstrap_admin_row]
            else:
                self._result = []
        elif normalized.startswith("SELECT 1 FROM app_users"):
            self._result = [(1,)] if self.connection.bootstrap_admin_row else []
        elif normalized.startswith("UPDATE app_users"):
            row = self.connection.bootstrap_admin_row
            if not row:
                self.rowcount = 0
            else:
                needs_update = (
                    (row.get("role", "").strip().lower() != "admin")
                    or (not bool(row.get("allow_portal")))
                    or (not bool(row.get("allow_admin")))
                    or row.get("updated_at") is None
                )
                self.rowcount = 1 if needs_update else 0
                if needs_update:
                    row["role"] = "admin"
                    row["allow_portal"] = True
                    row["allow_admin"] = True
                    row["updated_at"] = "now"
        else:
            self._result = []

    def fetchall(self) -> List[Any]:
        return list(self._result)

    def fetchone(self) -> Optional[Any]:
        if not self._result:
            return None
        return self._result[0]


class FakeConnection:
    def __init__(
        self,
        columns: Optional[Dict[str, Dict[str, Any]]] = None,
        constraint_exists: bool = False,
        bootstrap_admin_row: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.columns = columns or {}
        self.constraint_exists = constraint_exists
        self.bootstrap_admin_row = bootstrap_admin_row
        self.executed: List[Tuple[str, Optional[Iterable[Any]]]] = []
        self.commits: int = 0

    def cursor(self, cursor_factory: Optional[Any] = None) -> FakeCursor:
        return FakeCursor(self, cursor_factory)

    def commit(self) -> None:
        self.commits += 1


def _normalize_statements(conn: FakeConnection) -> List[str]:
    return [stmt for stmt, _ in conn.executed]


def test_ensure_app_users_adds_missing_columns_and_bootstrap_admin() -> None:
    conn = FakeConnection()
    ensure_app_users(conn)

    statements = _normalize_statements(conn)
    assert any("ALTER TABLE app_users ADD COLUMN allow_portal" in stmt for stmt in statements)
    assert any("ALTER TABLE app_users ADD COLUMN allow_admin" in stmt for stmt in statements)
    assert any("ALTER TABLE app_users ADD COLUMN created_at" in stmt for stmt in statements)
    assert any("ALTER TABLE app_users ADD COLUMN updated_at" in stmt for stmt in statements)
    assert any("INSERT INTO app_users" in stmt and "VALUES (%s, %s, 'admin', TRUE, TRUE)" in stmt for stmt in statements)
    assert conn.commits >= 2


def test_ensure_x12_addon_tables_creates_required_tables() -> None:
    conn = FakeConnection()
    ensure_x12_addon_tables(conn)

    statements = _normalize_statements(conn)
    assert any("CREATE TABLE IF NOT EXISTS era_835_header" in stmt for stmt in statements)
    assert any("CREATE TABLE IF NOT EXISTS era_835_clp" in stmt for stmt in statements)
    assert any("CREATE TABLE IF NOT EXISTS era_835_cas" in stmt for stmt in statements)
    assert any("CREATE TABLE IF NOT EXISTS era_835_plb" in stmt for stmt in statements)
    assert any("CREATE TABLE IF NOT EXISTS eligibility_271" in stmt for stmt in statements)
    assert any("CREATE TABLE IF NOT EXISTS claim_status_277" in stmt for stmt in statements)
    assert conn.commits == 1


def test_ensure_bootstrap_admin_upgrades_existing_user() -> None:
    conn = FakeConnection(
        bootstrap_admin_row={
            "username": "admin",
            "role": "view",
            "allow_portal": False,
            "allow_admin": False,
            "updated_at": None,
        }
    )

    ensure_bootstrap_admin(conn)

    statements = _normalize_statements(conn)
    assert any(stmt.startswith("UPDATE app_users") for stmt in statements)
    assert conn.commits >= 1
    assert conn.bootstrap_admin_row["role"] == "admin"
    assert conn.bootstrap_admin_row["allow_portal"] is True
    assert conn.bootstrap_admin_row["allow_admin"] is True
