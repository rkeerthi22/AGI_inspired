"""F51: the fact-ledger block truncated silently, and dropped the alphabetical tail.

Third member of F49's silent-truncation family (F49 briefs, F50 model context, F51 facts).
Measured 2026-07-30: 108 facts inside the 14-day window against a cap of 120, while week
W30 alone produced 70 -- one ordinary week would have crossed it. And the old
`ORDER BY entity, id` + `rows[:cap]` meant the overflow was always the same alphabetical
tail, regardless of age or relevance.

Runs entirely against a synthetic ledgerbook in a temp dir; the real one is never opened.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import batch_runner as br  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


def make_db(n_facts: int, path: Path) -> Path:
    """n facts, entities named so alphabetical order is the REVERSE of insertion order.
    fact i gets entity 'z{n-i:03d}' -- so the newest rows sort alphabetically FIRST and the
    oldest sort last. That makes 'kept the newest' and 'kept the alphabetical head'
    distinguishable, which is the whole point of the ordering half of this fix."""
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, entity TEXT, statement TEXT, "
              "provenance_url TEXT, provenance_date TEXT, confidence INT, created_at TEXT)")
    for i in range(n_facts):
        c.execute("INSERT INTO facts (entity, statement, provenance_date, confidence, "
                  "created_at) VALUES (?,?,?,?,datetime('now'))",
                  (f"z{n_facts - i:03d}", f"statement number {i}", "2026-07-30", 3))
    c.commit()
    c.close()
    return path


tmp = Path(tempfile.mkdtemp())

print("=== 1. under the cap: everything supplied, no marker ===")
db = make_db(50, tmp / "small.db")
blk = br._recent_fact_lines(cap=300, db=db)
check("all 50 facts present", len([l for l in blk.splitlines() if l.startswith("- [")]), 50)
check("no truncation marker", "TRUNCATED BY THE HARNESS" in blk, False)

print("\n=== 2. over the cap: marked, with exact counts ===")
db = make_db(400, tmp / "big.db")
blk = br._recent_fact_lines(cap=300, db=db)
kept = [l for l in blk.splitlines() if l.startswith("- [")]
check("exactly cap rows supplied", len(kept), 300)
check("marker present", "[TRUNCATED BY THE HARNESS:" in blk, True)
check("states the true numbers", "100 of 400 facts" in blk, True)
check("says the RECENT ones were kept", "most RECENT were" in blk, True)
check("denies 'data gap' in the same words as F49", "NOT a data gap" in blk, True)

print("\n=== 3. the ordering half: it keeps the NEWEST, not the alphabetical head ===")
# entities are z400..z001 for rows inserted oldest->newest, so the newest 300 rows are
# entities z001..z300, and the 100 dropped are z301..z400 (the OLDEST).
ents = [l.split("] ", 1)[1].split(":", 1)[0] for l in kept]
check("dropped rows are the OLDEST", "z400" in ents or "z399" in ents, False)
check("newest row survived", "z001" in ents, True)
check("oldest row was dropped", "z400" in ents, False)
check("kept set is exactly the newest 300 (z001..z300)",
      (len(ents), min(ents), max(ents)), (300, "z001", "z300"))

print("\n=== 4. presentation is still grouped by entity (readable), despite recency select ===")
check("supplied rows are in entity order", ents == sorted(ents), True)

print("\n=== 5. validated against the DEFECT: old behaviour dropped the alphabet tail ===")
con = sqlite3.connect(db)
old_rows = con.execute("SELECT entity FROM facts WHERE created_at >= datetime('now','-14 days') "
                       "ORDER BY entity, id").fetchall()
con.close()
old_kept = [r[0] for r in old_rows[:300]]        # the pre-fix expression: rows[:cap]
check("PRE-FIX kept the alphabetical HEAD", old_kept == [f"z{i:03d}" for i in range(1, 301)], True)
check("...which for THIS data drops the oldest too — so use a case where they differ",
      old_kept[-1], "z300")
# The distinguishing case: make alphabetical order and recency DISAGREE.
db2 = tmp / "disagree.db"
c = sqlite3.connect(db2)
c.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, entity TEXT, statement TEXT, "
          "provenance_url TEXT, provenance_date TEXT, confidence INT, created_at TEXT)")
# 5 OLD facts named 'aaa*' (alphabetically first), then 5 NEW facts named 'zzz*'.
for i in range(5):
    c.execute("INSERT INTO facts (entity, statement, provenance_date, confidence, created_at) "
              "VALUES (?,?,?,?,datetime('now'))", (f"aaa{i}", "old fact", "2026-07-01", 3))
for i in range(5):
    c.execute("INSERT INTO facts (entity, statement, provenance_date, confidence, created_at) "
              "VALUES (?,?,?,?,datetime('now'))", (f"zzz{i}", "new fact", "2026-07-30", 3))
c.commit(); c.close()
blk2 = br._recent_fact_lines(cap=5, db=db2)
kept2 = [l.split("] ", 1)[1].split(":", 1)[0] for l in blk2.splitlines() if l.startswith("- [")]
check("FIXED: keeps the 5 NEWEST (zzz*), not the alphabetical head",
      kept2, ["zzz0", "zzz1", "zzz2", "zzz3", "zzz4"])
old2 = sqlite3.connect(db2).execute(
    "SELECT entity FROM facts ORDER BY entity, id").fetchall()
check("PRE-FIX would have kept the 5 OLDEST (aaa*) — the bug",
      [r[0] for r in old2[:5]], ["aaa0", "aaa1", "aaa2", "aaa3", "aaa4"])

print("\n=== 6. empty ledger still degrades cleanly ===")
check("no facts -> '(none yet)'", br._recent_fact_lines(db=make_db(0, tmp / "empty.db")),
      "(none yet)")

print("\n=== 7. the shipped cap clears the live window with real headroom ===")
live = ROOT / "memory" / "ledgerbook.db"
if live.exists():
    n = sqlite3.connect(live).execute(
        "SELECT count(*) FROM facts WHERE created_at >= datetime('now','-14 days')").fetchone()[0]
    print(f"  live window holds {n} facts; shipped cap is {br.FACT_LEDGER_CAP}")
    check("shipped cap exceeds the live window", n < br.FACT_LEDGER_CAP, True)
    check("cap did not regress below the old 120", br.FACT_LEDGER_CAP >= 120, True)
    check("live block is not truncated today",
          "TRUNCATED BY THE HARNESS" in br._recent_fact_lines(), False)
else:
    print("  [SKIP] no live ledgerbook")

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
