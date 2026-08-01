"""F54: an operator re-verification could never clear the AI-performed flag.

`spotcheck.cmd_verdict()` APPENDS a verdict segment (`critic_notes || ?`), which is correct
for audit. But every classifier grepped the WHOLE field for "AI-PERFORMED CHECK", so one
historical AI check marked a row AI-performed permanently -- making the F28 transition the
tool's own docstring promised structurally impossible.

Found live 2026-08-01: the operator personally verified tasks 28 and 29, the verdicts were
recorded, and `spot_checked_ai` stayed at 7/7. Compounded by the operator note itself
containing the marker inside prose ("supersedes the earlier AI-PERFORMED CHECK").

Temp DB only (F12) apart from one read-only assertion against the live ledger.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import ledger  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


AI = "| HUMAN(pass): AI-PERFORMED CHECK (2026-07-30). Citations verified live."
OP = "| HUMAN(pass): OPERATOR-VERIFIED 2026-08-01. Opened all three URLs myself."
OP_MENTIONS = ("| HUMAN(pass): OPERATOR-VERIFIED 2026-08-01 (supersedes the earlier "
               "AI-PERFORMED CHECK on this row). Opened all three URLs myself.")

print("=== 1. latest-segment extraction ===")
check("single AI segment", ledger.latest_human_note("VERDICT " + AI)[:19], "AI-PERFORMED CHECK ")
check("AI then OPERATOR -> returns the OPERATOR one",
      ledger.latest_human_note("VERDICT " + AI + " " + OP).startswith("OPERATOR-VERIFIED"), True)
check("no HUMAN segment at all", ledger.latest_human_note("VERDICT pass, nothing else"), "")
check("None is safe", ledger.latest_human_note(None), "")

print("\n=== 2. classification uses the LATEST verdict (the actual regression) ===")
check("AI only -> AI-performed", ledger.is_ai_performed("V " + AI), True)
check("AI then OPERATOR -> INDEPENDENT", ledger.is_ai_performed("V " + AI + " " + OP), False)
check("OPERATOR then AI (re-checked by assistant) -> AI-performed",
      ledger.is_ai_performed("V " + OP + " " + AI), True)
check("never spot-checked -> not AI-performed", ledger.is_ai_performed("VERDICT: PASS"), False)

print("\n=== 3. the exact live failure: operator note that MENTIONS the marker ===")
# This is what actually happened on tasks 28/29 -- prose containing the marker string.
check("operator note mentioning the marker is still INDEPENDENT",
      ledger.is_ai_performed("V " + AI + " " + OP_MENTIONS), False)
# validated against the defect: the pre-F54 test was substring-anywhere
pre_f54 = "AI-PERFORMED CHECK" in ("V " + AI + " " + OP_MENTIONS)
check("PRE-F54 substring test would have called it AI-performed", pre_f54, True)

print("\n=== 4. fails CLOSED on pre-convention rows ===")
pre = "| HUMAN(pass): verified in live browser 2026-07-18 by Claude session (not operator): ok"
check("a Claude-session row without the marker still counts as NOT independent",
      ledger.is_ai_performed("V " + pre), True)

print("\n=== 5. end-to-end through weekly_fitness on a temp ledger ===")
tmp = Path(tempfile.mkdtemp()) / "t.db"
sqlite3.connect(tmp).executescript((ROOT / "ledger" / "schema.sql").read_text(encoding="utf-8"))
ledger.LEDGER_DB = tmp
a = ledger.queue_task("m", "spec-a", "crit")
b = ledger.queue_task("m", "spec-b", "crit")
ledger.finish_task(a, artifacts=[], status="done", critic_verdict="pass",
                   critic_notes="VERDICT: PASS " + AI)
ledger.finish_task(b, artifacts=[], status="done", critic_verdict="pass",
                   critic_notes="VERDICT: PASS " + AI + " " + OP_MENTIONS)
with sqlite3.connect(tmp) as c:
    c.execute("UPDATE tasks SET human_verdict='pass'")
f = ledger.weekly_fitness()
check("2 spot-checked", f["spot_checked"], 2)
check("exactly 1 still AI-performed", f["spot_checked_ai"], 1)
check("so exactly 1 is INDEPENDENT", f["spot_checked"] - f["spot_checked_ai"], 1)
check("W untouched — LOCKED", ledger.W,
      {"completion": 0.35, "accuracy": 0.30, "intervention": 0.25, "cost": 0.10})

print("\n=== 6. the live ledger reflects the operator's real 2026-08-01 verdicts ===")
live = sqlite3.connect(f"file:{ROOT / 'ledger' / 'ledger.db'}?mode=ro", uri=True)
live.row_factory = sqlite3.Row
for tid in (28, 29):
    r = live.execute("SELECT critic_notes FROM tasks WHERE task_id=?", (tid,)).fetchone()
    check(f"task {tid} latest verdict is an operator read",
          ledger.is_ai_performed(r["critic_notes"]), False)
    check(f"task {tid} still retains its earlier AI segment for audit",
          "AI-PERFORMED CHECK" in (r["critic_notes"] or ""), True)
live.close()

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(fails)}")
sys.exit(1 if fails else 0)
