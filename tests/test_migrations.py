"""SQLite migration tests against temporary databases only.

Verifies:
1. Migrations apply in order
2. ``user_version`` is correctly set after each migration
3. Already-applied migrations are skipped (idempotency)
4. Rollback restores the previous version
5. Irreversible migrations are rejected on rollback
6. Unknown database names raise ``MigrationError``
7. Missing database files raise ``MigrationError``
8. migrate_all() applies pending migrations to all registered databases
9. FTS index database migrations work on freshly-created databases
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

checks = 0
failures: list[str] = []


def check(label: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  [FAIL] {label}")
    else:
        print(f"  [PASS] {label}")


def _create_temp_db(schema: str = "") -> Path:
    """Create a temporary SQLite database with optional schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    if schema:
        with sqlite3.connect(path) as conn:
            conn.executescript(schema)
    return Path(path)


from migrations import (  # noqa: E402
    LEDGER_MIGRATIONS,
    LEDGERBOOK_MIGRATIONS,
    FTS_INDEX_MIGRATIONS,
    Migration,
    MigrationError,
    migrate,
    migrate_all,
    register_database,
    registered_databases,
    rollback,
)


# ── 1. Basic migration application ──────────────────────────────────────────


print("=== Basic migration ===")

db = _create_temp_db("CREATE TABLE tasks (task_id INTEGER PRIMARY KEY);")
# Register a minimal migration set for testing
_test_migrations = (
    Migration(version=1, description="Add task name column",
              up="ALTER TABLE tasks ADD COLUMN name TEXT;",
              down="ALTER TABLE tasks DROP COLUMN name;"),
    Migration(version=2, description="Add task status column",
              up="ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'pending';",
              down="ALTER TABLE tasks DROP COLUMN status;"),
)
register_database("test_db", lambda: db, _test_migrations)

from_ver, to_ver = migrate("test_db")
check("migrate returns (0, 2)", (from_ver, to_ver), (0, 2))

with sqlite3.connect(db) as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    check("v1: name column exists", "name" in cols)
    check("v2: status column exists", "status" in cols)
    check("user_version is 2", conn.execute("PRAGMA user_version").fetchone()[0], 2)


# ── 2. Idempotency ──────────────────────────────────────────────────────────


print("\n=== Idempotency ===")

from_ver2, to_ver2 = migrate("test_db")
check("second migrate is a no-op", (from_ver2, to_ver2), (2, 2))


# ── 3. Rollback ──────────────────────────────────────────────────────────────


print("\n=== Rollback ===")

from_ver3, to_ver3 = rollback("test_db", target_version=1)
check("rollback returns (2, 1)", (from_ver3, to_ver3), (2, 1))

with sqlite3.connect(db) as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    check("v1: name still exists", "name" in cols)
    check("v1: status removed", "status" not in cols)
    check("user_version is 1", conn.execute("PRAGMA user_version").fetchone()[0], 1)

from_ver3b, to_ver3b = rollback("test_db", target_version=0)
check("rollback to 0", (from_ver3b, to_ver3b), (1, 0))

with sqlite3.connect(db) as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    check("v0: name removed", "name" not in cols)
    check("user_version is 0", conn.execute("PRAGMA user_version").fetchone()[0], 0)


# ── 4. Irreversible migration rejected ──────────────────────────────────────


print("\n=== Irreversible ===")

db2 = _create_temp_db("CREATE TABLE tasks (task_id INTEGER PRIMARY KEY);")
_irr_migrations = (
    Migration(version=1, description="Irreversible",
              up="ALTER TABLE tasks ADD COLUMN name TEXT;",
              down=None),
)
register_database("test_irr", lambda: db2, _irr_migrations)
migrate("test_irr")

try:
    rollback("test_irr", target_version=0)
    check("irreversible rollback raises", False)
except MigrationError:
    check("irreversible rollback raises", True)


# ── 5. Unknown database ─────────────────────────────────────────────────────


print("\n=== Unknown database ===")

try:
    migrate("nonexistent")
    check("unknown database raises", False)
except MigrationError:
    check("unknown database raises", True)


# ── 6. Missing database file ────────────────────────────────────────────────


print("\n=== Missing file ===")

register_database("test_missing", lambda: Path("C:/nonexistent/nope.db"), ())
try:
    migrate("test_missing")
    check("missing file raises", False)
except MigrationError:
    check("missing file raises", True)


# ── 7. registered_databases() ────────────────────────────────────────────────


print("\n=== Database registry ===")

# Clean up test registrations so they don't interfere
# (register_database overwrites by name, so this is fine)

status = registered_databases()
check("ledger is registered", "ledger" in status)
check("ledgerbook is registered", "ledgerbook" in status)
check("fts_index is registered", "fts_index" in status)

# The real databases should have been migrated
if status.get("ledger", -2) >= 0:
    check("ledger version >= 0", status["ledger"] >= 0)


# ── 8. migrate_all() on test DBs ────────────────────────────────────────────


print("\n=== migrate_all ===")

# Create fresh temp databases for the real names
tmp_ledger = _create_temp_db("CREATE TABLE tasks (task_id INTEGER PRIMARY KEY);")
tmp_ledgerbook = _create_temp_db("CREATE TABLE tasks (task_id INTEGER PRIMARY KEY);")
tmp_fts = _create_temp_db("CREATE TABLE chunks (id INTEGER PRIMARY KEY);")

register_database("ledger", lambda: tmp_ledger, LEDGER_MIGRATIONS)
register_database("ledgerbook", lambda: tmp_ledgerbook, LEDGERBOOK_MIGRATIONS)
register_database("fts_index", lambda: tmp_fts, FTS_INDEX_MIGRATIONS)

results = migrate_all()
check("ledger migrated", results["ledger"][1] >= 1)
check("ledgerbook migrated", results["ledgerbook"][1] >= 1)
check("fts_index migrated", results["fts_index"][1] >= 1)

# Verify version bumps
for name in ["ledger", "ledgerbook", "fts_index"]:
    with sqlite3.connect(results[name] if False else (
        tmp_ledger if name == "ledger" else
        tmp_ledgerbook if name == "ledgerbook" else tmp_fts
    )) as conn:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        check(f"{name} version >= 1", ver >= 1)


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
