"""RED mutation/recovery suite using disposable SQLite databases only."""
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import integrity

TABLES = {
    "tasks": "task_id INTEGER PRIMARY KEY, value TEXT, run_id TEXT",
    "facts": "id INTEGER PRIMARY KEY, value TEXT, run_id TEXT",
    "entities": "id INTEGER PRIMARY KEY, value TEXT",
    "decisions": "id INTEGER PRIMARY KEY, value TEXT",
    "experiences": "id INTEGER PRIMARY KEY, value TEXT",
    "failures": "id INTEGER PRIMARY KEY, value TEXT",
}


def create_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        for table, columns in TABLES.items():
            con.execute(f"CREATE TABLE {table} ({columns})")
            id_col = "task_id" if table == "tasks" else "id"
            if table in {"tasks", "facts"}:
                con.execute(f"INSERT INTO {table} ({id_col},value,run_id) "
                            "VALUES (1,'original','trusted-run')")
            else:
                con.execute(f"INSERT INTO {table} ({id_col},value) VALUES (1,'original')")
        con.commit()


def read_value(path: Path, table: str = "tasks"):
    with closing(sqlite3.connect(path)) as con:
        return con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def enforce(before, context: str) -> bool:
    """A real containment event must restore state and stop normal execution."""
    try:
        integrity.db_integrity_check(before, context)
    except integrity.DatabaseMutationViolation:
        return True
    return False


checks = {}
fixture = ROOT / "tests" / "fixtures" / "db_guard_red"
generated = [fixture / "ledger.db", fixture / "ledger.db-wal", fixture / "ledger.db-shm",
             fixture / "memory" / "ledgerbook.db",
             fixture / "memory" / "ledgerbook.db-wal",
             fixture / "memory" / "ledgerbook.db-shm"]
for path in generated:
    path.unlink(missing_ok=True)
try:
    temp = fixture
    ledger_db = temp / "ledger.db"
    book_dir = temp / "memory"
    book_dir.mkdir(exist_ok=True)
    book_db = book_dir / "ledgerbook.db"
    create_db(ledger_db)
    create_db(book_db)

    old_root, old_ledger, old_runs = integrity.ROOT, integrity.ledger.LEDGER_DB, integrity.RUNS
    old_escalate, old_log = integrity.escalate, integrity.log
    integrity.ROOT = temp
    integrity.ledger.LEDGER_DB = ledger_db
    integrity.RUNS = temp / "runs"
    integrity.escalate = lambda *a, **k: None
    integrity.log = lambda *a, **k: None
    try:
        checks["production DatabaseMutationGuard contract exists"] = hasattr(
            integrity, "DatabaseMutationGuard")

        before = integrity.db_integrity_snapshot()
        with closing(sqlite3.connect(ledger_db)) as con:
            con.execute("UPDATE tasks SET value='tampered' WHERE task_id=1")
            con.commit()
        blocked = enforce(before, "synthetic update")
        checks["existing-row update is detected and restored"] = (
            blocked and read_value(ledger_db)[0][1] == "original")

        with closing(sqlite3.connect(ledger_db)) as con:
            con.execute("UPDATE tasks SET value='original' WHERE task_id=1")
            con.commit()
        before = integrity.db_integrity_snapshot()
        with closing(sqlite3.connect(ledger_db)) as con:
            con.execute("DELETE FROM tasks WHERE task_id=1")
            con.commit()
        blocked = enforce(before, "synthetic delete")
        checks["row deletion is detected and restored"] = (
            blocked and read_value(ledger_db) == [(1, "original", "trusted-run")])

        with closing(sqlite3.connect(ledger_db)) as con:
            con.execute("DELETE FROM tasks")
            con.execute("INSERT INTO tasks VALUES (1,'original','trusted-run')")
            con.commit()
        before = integrity.db_integrity_snapshot()
        with closing(sqlite3.connect(ledger_db)) as con:
            con.execute("DELETE FROM tasks WHERE task_id=1")
            con.execute("INSERT INTO tasks VALUES (2,'replacement','forged-run')")
            con.commit()
        blocked = enforce(before, "synthetic same-count replacement")
        checks["same-count delete/insert is detected and restored"] = (
            blocked and read_value(ledger_db) == [(1, "original", "trusted-run")])

        before = integrity.db_integrity_snapshot()
        with closing(sqlite3.connect(ledger_db)) as con:
            con.execute("CREATE TRIGGER rogue AFTER INSERT ON tasks BEGIN "
                        "UPDATE tasks SET value='triggered'; END")
            con.commit()
        blocked = enforce(before, "synthetic schema mutation")
        with closing(sqlite3.connect(ledger_db)) as con:
            rogue = con.execute("SELECT count(*) FROM sqlite_schema "
                                "WHERE type='trigger' AND name='rogue'").fetchone()[0]
        checks["schema/trigger mutation is detected and restored"] = blocked and rogue == 0

        before = integrity.db_integrity_snapshot()
        with closing(sqlite3.connect(book_db)) as con:
            con.execute("INSERT INTO facts VALUES (2,'poison','forged-non-null-run')")
            con.commit()
        blocked = enforce(before, "synthetic forged provenance")
        checks["forged non-null run_id is not an authorization bypass"] = (
            blocked and read_value(book_db, "facts") == [(1, "original", "trusted-run")])

        missing = temp / "missing" / "absent.db"
        integrity.ledger.LEDGER_DB = missing
        try:
            integrity.db_integrity_snapshot()
        except (OSError, sqlite3.Error):
            pass
        checks["missing DB fails closed without creating a new file"] = not missing.exists()

        integrity.ledger.LEDGER_DB = ledger_db
        before = integrity.db_integrity_snapshot()
        real_owner = dict(before["owner"])
        before["owner"] = {"pid": real_owner["pid"], "process_start_id": "forged"}
        try:
            integrity.db_integrity_check(before, "synthetic owner mismatch")
        except integrity.DatabaseRecoveryError:
            blocked = True
        else:
            blocked = False
        before["owner"] = real_owner
        integrity.db_integrity_check(before, "synthetic owner cleanup")
        checks["snapshot token is bound to exact OS process-start identity"] = blocked

        before = integrity.db_integrity_snapshot()
        before["owner"] = {"pid": 2_147_483_647, "process_start_id": "dead-owner"}
        Path(before["journal"]).write_text(json.dumps(before), encoding="utf-8")
        with closing(sqlite3.connect(ledger_db)) as con:
            con.execute("UPDATE tasks SET value='crash-window-tamper' WHERE task_id=1")
            con.commit()
        integrity.recover_database_mutation_guards()
        checks["orphan journal restores after exact owner process is gone"] = (
            read_value(ledger_db)[0][1] == "original"
            and not Path(before["journal"]).exists())

        try:
            with integrity.DatabaseMutationGuard("synthetic context manager"):
                with closing(sqlite3.connect(ledger_db)) as con:
                    con.execute("UPDATE entities SET value='context-tamper' WHERE id=1")
                    con.commit()
        except integrity.DatabaseMutationViolation:
            blocked = True
        else:
            blocked = False
        checks["context manager enforces and restores protected call"] = (
            blocked and read_value(ledger_db, "entities") == [(1, "original")])

        task_source = (ROOT / "orchestrator" / "task_runner.py").read_text(encoding="utf-8")
        canary_source = (ROOT / "orchestrator" / "workflow.py").read_text(encoding="utf-8")
        checks["canonical task and canary paths both use the guard"] = (
            "DatabaseMutationGuard" in task_source and "DatabaseMutationGuard" in canary_source)
    finally:
        integrity.ROOT, integrity.ledger.LEDGER_DB, integrity.RUNS = (
            old_root, old_ledger, old_runs)
        integrity.escalate, integrity.log = old_escalate, old_log
finally:
    for path in generated:
        path.unlink(missing_ok=True)
    shutil.rmtree(fixture / "runs", ignore_errors=True)

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'EXPECTED FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("DB mutation guard RED contract unmet: " + ", ".join(failed))
