"""F44: the daily budget boundary must live in finished_at's clock domain AND format.

Written to fail at ANY hour. Row-inclusion tests alone are not enough: the UTC and local
day boundaries only disagree during part of the day (00:00-02:00 local at UTC+2), so a
purely row-based test would pass on a buggy build for 22 hours out of 24 and look green.
The boundary-expression assertions below hold regardless of when this runs.
"""
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import policy  # noqa: E402
import ledger  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "ledger.db"
shutil.copy2(ROOT / "ledger" / "ledger.db", tmp)
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
LOCAL_NOW = sql("datetime('now','localtime')")
UTC_NOW = sql("datetime('now')")

print("=== clock context for this run ===")
print(f"  local now            {LOCAL_NOW}")
print(f"  utc now              {UTC_NOW}")
print(f"  LOCAL start of day   {LOCAL_MIDNIGHT}   <- finished_at's domain")
print(f"  UTC   start of day   {UTC_MIDNIGHT}")
print(f"  domains disagree today: {LOCAL_MIDNIGHT[:10] != UTC_MIDNIGHT[:10]}")

# --------------------------------------------------------- time-independent assertions
print("\n=== 1. the boundary is in finished_at's CLOCK DOMAIN (local) ===")
check("policy.today_start() == local start of day", policy.today_start(), LOCAL_MIDNIGHT)

print("\n=== 2. the boundary is in finished_at's FORMAT ('T', not space) ===")
# finished_at is written by datetime.now().isoformat() -> 'YYYY-MM-DDTHH:MM:SS'
check("boundary uses the 'T' separator isoformat() emits",
      policy.today_start()[10], "T")
check("...and so parses as a real timestamp",
      bool(datetime.fromisoformat(policy.today_start())), True)

print("\n=== 3. the boundary is NOT the UTC one (whenever they differ) ===")
if LOCAL_MIDNIGHT[:10] != UTC_MIDNIGHT[:10]:
    check("uses local, not UTC, while the dates diverge",
          policy.today_start() != UTC_MIDNIGHT, True)
else:
    # Same calendar date in both domains: assert on the value instead, which still
    # distinguishes the two because only one carries the 'T'.
    check("boundary still matches finished_at's format, not SQLite's default",
          policy.today_start() != UTC_MIDNIGHT, True)

# --------------------------------------------------------- behavioural assertions
print("\n=== 4. rows are attributed to the correct LOCAL day ===")


def plant(tid, finished_local: datetime, tokens: int):
    with sqlite3.connect(tmp) as c:
        c.execute("INSERT OR REPLACE INTO tasks (task_id,mission_id,spec,status,finished_at,"
                  "tokens_in,tokens_out,pass_criteria,created_at) "
                  "VALUES (?,?,?,'done',?,?,0,'x',datetime('now'))",
                  (tid, "001-shopify-competitor-intel", f"[F44 test] {tid}",
                   finished_local.isoformat(timespec="seconds"), tokens))


def clear():
    with sqlite3.connect(tmp) as c:
        c.execute("DELETE FROM tasks WHERE task_id >= 9700")


local_mid = datetime.fromisoformat(LOCAL_MIDNIGHT)

clear()
plant(9701, local_mid + timedelta(minutes=1), 1_000)
check("a row 1 min AFTER local midnight counts toward today",
      policy.tokens_used_today(), 1_000)

clear()
plant(9702, local_mid - timedelta(minutes=1), 5_000_000)
check("a row 1 min BEFORE local midnight does NOT count (this is the bug's symptom)",
      policy.tokens_used_today(), 0)

clear()
plant(9703, local_mid, 7_000)
check("a row exactly AT local midnight counts (format boundary)",
      policy.tokens_used_today(), 7_000)

print("\n=== 5. the live symptom: yesterday's whole spend must not land on today ===")
clear()
plant(9704, local_mid - timedelta(hours=3), 6_734_838)   # last night's task 18
plant(9705, local_mid - timedelta(hours=21), 2_382_643)  # yesterday morning
check("11.4M spent yesterday reads as 0 today", policy.tokens_used_today(), 0)
plant(9706, local_mid + timedelta(minutes=30), 250_000)
check("...and today's own spend is counted exactly", policy.tokens_used_today(), 250_000)

print("\n=== 6. created_at's UTC window helper is untouched (it is correct as-is) ===")
check("ledger.window_start_sql stays space-separated UTC",
      " " in ledger.window_start_sql(7) and "T" not in ledger.window_start_sql(7), True)

clear()
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
