"""F44: daily token accounting uses canonical UTC while accepting legacy timestamps."""
import sqlite3
import inspect
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import policy  # noqa: E402
import ledger  # noqa: E402

# F12: LEDGER_DB is injectable so the test does not need to copy the live DB.
# Copying the live ledger.db (as the previous version did) brought in today's real
# W35 task rows (ids 65, 66, 68, 69, 70 etc.), which the test's "delete task_id >= 9700"
# cleanup did NOT remove (those ids are below 9700), and the test then asserted
# tokens_used_today() == planted_value while the function was actually returning
# planted_value + ~3.08M of real W35 spend. Using a fresh empty schema gives the
# policy code the same column layout without any data the test did not plant.
_TASKS_SCHEMA = """
CREATE TABLE tasks (
    task_id INTEGER PRIMARY KEY,
    mission_id TEXT,
    spec TEXT,
    status TEXT,
    finished_at TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    pass_criteria TEXT,
    created_at TEXT
);
"""

tmp = Path(tempfile.mkdtemp()) / "ledger.db"
with sqlite3.connect(tmp) as _c:
    _c.executescript(_TASKS_SCHEMA)
policy.LEDGER_DB = tmp
ledger.LEDGER_DB = tmp
fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got!r}\n        want={want!r}")


def sql(expr):
    with sqlite3.connect(tmp) as c:
        return c.execute("SELECT " + expr).fetchone()[0]


LOCAL_MIDNIGHT = sql("replace(datetime('now','localtime','start of day'),' ','T')")
UTC_MIDNIGHT = sql("datetime('now','start of day')")
UTC_MIDNIGHT_RFC3339 = sql("strftime('%Y-%m-%dT%H:%M:%SZ','now','start of day')")
LOCAL_NOW = sql("datetime('now','localtime')")
UTC_NOW = sql("datetime('now')")

print("=== clock context for this run ===")
print(f"  local now            {LOCAL_NOW}")
print(f"  utc now              {UTC_NOW}")
print(f"  LOCAL start of day   {LOCAL_MIDNIGHT}   <- finished_at's domain")
print(f"  UTC   start of day   {UTC_MIDNIGHT}")
print(f"  domains disagree today: {LOCAL_MIDNIGHT[:10] != UTC_MIDNIGHT[:10]}")

# --------------------------------------------------------- time-independent assertions
print("\n=== 1. the boundary is canonical UTC ===")
check("policy.today_start() == UTC start of day", policy.today_start(), UTC_MIDNIGHT_RFC3339)

print("\n=== 2. the boundary is in finished_at's FORMAT ('T', not space) ===")
# finished_at is written by datetime.now().isoformat() -> 'YYYY-MM-DDTHH:MM:SS'
check("boundary uses the 'T' separator isoformat() emits",
      policy.today_start()[10], "T")
check("...and so parses as a real timestamp",
      bool(datetime.fromisoformat(policy.today_start())), True)
check("boundary carries explicit UTC designator", policy.today_start().endswith("Z"), True)

print("\n=== 3. SQL normalizes timestamp formats before comparison ===")
check("budget query uses datetime normalization", "datetime(finished_at)" in
      inspect.getsource(policy.tokens_used_today), True)

# --------------------------------------------------------- behavioural assertions
print("\n=== 4. rows are attributed to the correct UTC day ===")


def plant(tid, finished_at: datetime, tokens: int):
    with sqlite3.connect(tmp) as c:
        c.execute("INSERT OR REPLACE INTO tasks (task_id,mission_id,spec,status,finished_at,"
                  "tokens_in,tokens_out,pass_criteria,created_at) "
                  "VALUES (?,?,?,'done',?,?,0,'x',datetime('now'))",
                  (tid, "001-shopify-competitor-intel", f"[F44 test] {tid}",
                   finished_at.isoformat(timespec="seconds"), tokens))


def clear():
    with sqlite3.connect(tmp) as c:
        c.execute("DELETE FROM tasks WHERE task_id >= 9700")


utc_mid = datetime.fromisoformat(UTC_MIDNIGHT_RFC3339.replace("Z", "+00:00"))

clear()
plant(9701, utc_mid + timedelta(minutes=1), 1_000)
check("a row 1 min AFTER UTC midnight counts toward today",
      policy.tokens_used_today(), 1_000)

clear()
plant(9702, utc_mid - timedelta(minutes=1), 5_000_000)
check("a row 1 min BEFORE UTC midnight does not count",
      policy.tokens_used_today(), 0)

clear()
plant(9703, utc_mid, 7_000)
check("a row exactly AT UTC midnight counts (format boundary)",
      policy.tokens_used_today(), 7_000)

print("\n=== 5. the live symptom: yesterday's whole spend must not land on today ===")
clear()
plant(9704, utc_mid - timedelta(hours=3), 6_734_838)
plant(9705, utc_mid - timedelta(hours=21), 2_382_643)
check("11.4M spent yesterday reads as 0 today", policy.tokens_used_today(), 0)
plant(9706, utc_mid + timedelta(minutes=30), 250_000)
check("...and today's own spend is counted exactly", policy.tokens_used_today(), 250_000)

print("\n=== 6. created_at's UTC window helper is untouched (it is correct as-is) ===")
check("ledger.window_start_sql stays space-separated UTC",
      " " in ledger.window_start_sql(7) and "T" not in ledger.window_start_sql(7), True)

clear()
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
