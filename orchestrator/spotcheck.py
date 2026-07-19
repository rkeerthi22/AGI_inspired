"""Operator spot-check workflow — the missing input for the fitness accuracy term.

    python orchestrator/spotcheck.py list             # done tasks awaiting your verdict
    python orchestrator/spotcheck.py pass 12 [note]   # mark task 12 correct
    python orchestrator/spotcheck.py fail 12 [note]   # mark task 12 wrong

`human_verdict` feeds ledger.weekly_fitness() accuracy (already implemented) — 3–5
spot-checks per week during the M1 baseline keeps accuracy measurable. Stdlib only."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "ledger" / "ledger.db"


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def cmd_list() -> None:
    with _conn() as c:
        rows = c.execute(
            "SELECT task_id, mission_id, spec, critic_verdict, artifacts, human_verdict "
            "FROM tasks WHERE status='done' ORDER BY task_id").fetchall()
    pending = [r for r in rows if r["human_verdict"] not in ("pass", "fail")]
    checked = len(rows) - len(pending)
    print(f"{len(pending)} task(s) awaiting spot-check ({checked} already checked):\n")
    for r in pending:
        art = (r["artifacts"] or "").strip("[]\"").replace("\\\\", "\\")
        print(f"  #{r['task_id']:<3} [{r['mission_id']}] critic={r['critic_verdict'] or '-'}")
        print(f"       {r['spec'][:90]}")
        if art:
            print(f"       artifact: {art}")
    if pending:
        print(f"\nVerdict: python orchestrator/spotcheck.py pass|fail <task_id> [note]")


def cmd_verdict(verdict: str, task_id: int, note: str) -> None:
    with _conn() as c:
        row = c.execute("SELECT status, human_verdict FROM tasks WHERE task_id=?",
                        (task_id,)).fetchone()
        if row is None:
            print(f"no task #{task_id}"); raise SystemExit(1)
        if row["status"] != "done":
            print(f"task #{task_id} is '{row['status']}' — only done tasks can be spot-checked")
            raise SystemExit(1)
        if row["human_verdict"] in ("pass", "fail"):
            print(f"task #{task_id} already spot-checked ({row['human_verdict']}) — overwriting")
        c.execute("UPDATE tasks SET human_verdict=?, critic_notes=COALESCE(critic_notes,'') || ? "
                  "WHERE task_id=?",
                  (verdict, f" | HUMAN({verdict}): {note}" if note else "", task_id))
    print(f"task #{task_id}: human_verdict={verdict}")
    # Retract facts extracted from a task the operator failed — they are tainted
    # and must not persist as current truths (see second-opinion review G1).
    if verdict == "fail":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from batch_runner import retract_facts
        n = retract_facts(task_id)
        if n:
            print(f"  retracted {n} fact(s) from ledgerbook.db (validity windows closed)")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ledger
    fit = ledger.weekly_fitness()
    print(f"accuracy now: {fit.get('accuracy')} (spot-checked {fit.get('spot_checked')}) "
          f"| fitness: {fit.get('fitness')}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if not args or args[0] == "list":
        cmd_list()
    elif args[0] in ("pass", "fail") and len(args) >= 2 and args[1].isdigit():
        cmd_verdict(args[0], int(args[1]), " ".join(args[2:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
