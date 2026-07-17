"""Create ledger.db and memory/ledgerbook.db from schema.sql. Idempotent (CREATE IF NOT EXISTS).
Usage:  python ledger/init_ledger.py
Stdlib only — no framework lock-in (HARNESS_DESIGN.md §1.3)."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "ledger" / "schema.sql").read_text(encoding="utf-8")

TARGETS = [
    ROOT / "ledger" / "ledger.db",       # task ledger + scorecards live here
    ROOT / "memory" / "ledgerbook.db",   # typed domain memory lives here
]

def main() -> None:
    for db_path in TARGETS:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.executescript(SCHEMA)
        # verify
        with sqlite3.connect(db_path) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print(f"OK  {db_path}  ->  {len(tables)} tables: {', '.join(tables)}")

if __name__ == "__main__":
    main()
