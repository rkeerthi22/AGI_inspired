"""Operator spot-check workflow — the missing input for the fitness accuracy term.

    python orchestrator/spotcheck.py list             # done tasks awaiting your verdict
    python orchestrator/spotcheck.py notify           # PUSH that queue to Telegram
    python orchestrator/spotcheck.py pass 12 [note]   # mark task 12 correct
    python orchestrator/spotcheck.py fail 12 [note]   # mark task 12 wrong

`human_verdict` feeds ledger.weekly_fitness() accuracy (already implemented) — 3–5
spot-checks per week during the M1 baseline keeps accuracy measurable.

F28 (docs/HARDENING.md): this CLI has no way to tell who is actually at the keyboard.
On 2026-07-28 the assistant ran real verification (fetched cited pages, compared claims
against them) and recorded the result through this exact tool -- schema-identical to a
genuine operator check, because `human_verdict` and `critic_notes` cannot express who
did the checking. That is dangerous specifically in a project whose recurring theme is
an agent's own output being indistinguishable from independent verification (see the
2026-07-18 rogue-write incident and F5's manager==critic note).

CONVENTION, not enforced by code: if the assistant performs a check on your behalf
(rather than you reading the source yourself), the note MUST start with
"AI-PERFORMED CHECK" verbatim. ledger.weekly_fitness() greps for that exact string to
report `spot_checked_ai` alongside `spot_checked`, and scorecard.py surfaces it as a
caveat -- so the number stays visible without silently reclassifying it or touching the
locked accuracy formula. Re-running this command yourself on the same task overwrites
the row with a genuine independent read."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "ledger" / "ledger.db"


def _conn():
    c = sqlite3.connect(DB, timeout=30)
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


def pending_rows() -> tuple[list, list]:
    """(never spot-checked, AI-performed awaiting operator confirmation).

    The second list is the F28 case: `human_verdict` is already set, so those tasks
    drop out of the pull-based `list` view entirely -- but an assistant-recorded check
    is not independent of the system it is checking, and is exactly the thing that most
    needs a human to look at it. Pull-only surfacing meant it was invisible unless the
    operator went hunting.

    Canaries are excluded and newest sorts first, because this queue is spent out of a
    3-5 minute weekly budget and must point at the rows that actually move the number:
    ledger.weekly_fitness() computes accuracy over `mission_id != 'canaries'` inside a
    7-day window, so a spot-check on a canary contributes nothing to it, and one on a
    three-week-old task contributes nothing to the current week. Plain `ORDER BY task_id`
    put precisely those two categories at the top of the first pushed message."""
    with _conn() as c:
        rows = c.execute(
            "SELECT task_id, mission_id, spec, critic_verdict, human_verdict, critic_notes "
            "FROM tasks WHERE status='done' AND mission_id != 'canaries' "
            "ORDER BY task_id DESC").fetchall()
    unchecked = [r for r in rows if r["human_verdict"] not in ("pass", "fail")]
    ai_done = [r for r in rows if r["human_verdict"] in ("pass", "fail")
               and "AI-PERFORMED CHECK" in (r["critic_notes"] or "")]
    return unchecked, ai_done


def notify_pending(max_listed: int = 5) -> bool:
    """Directive-5 (2026-07-29): PUSH the spot-check queue instead of waiting to be asked.

    Accuracy is 30% of fitness and is the only term that cannot be produced by the
    system itself -- it needs the operator to read a source. Until now the only way to
    discover there was anything to check was to remember to run `spotcheck.py list`, so
    the weeks where nobody remembered simply reported accuracy on a stale, tiny sample
    (W31 closed with 3 spot-checks, all three AI-performed). Returns True if a message
    was actually delivered."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scorecard import send_telegram
    unchecked, ai_done = pending_rows()
    if not unchecked and not ai_done:
        return False
    parts = []
    if unchecked:
        listed = "\n".join(f"  #{r['task_id']} [{r['mission_id'][:24]}] "
                           f"critic={r['critic_verdict'] or '-'} · {r['spec'][:60]}"
                           for r in unchecked[:max_listed])
        more = (f"\n  …and {len(unchecked) - max_listed} more"
                if len(unchecked) > max_listed else "")
        parts.append(f"🔍 {len(unchecked)} deliverable(s) awaiting your spot-check:\n"
                     f"{listed}{more}")
    if ai_done:
        parts.append(f"⚠ {len(ai_done)} spot-check(s) were AI-performed and still need "
                     f"YOUR independent confirmation (task(s) "
                     f"{', '.join(str(r['task_id']) for r in ai_done[:8])}) — re-running "
                     f"the same command yourself overwrites the row with a real read.")
    parts.append("python orchestrator/spotcheck.py list  →  "
                 "python orchestrator/spotcheck.py pass|fail <id> [note]")
    return send_telegram("\n\n".join(parts))


def cmd_notify() -> None:
    unchecked, ai_done = pending_rows()
    ok = notify_pending()
    print(f"{len(unchecked)} awaiting spot-check, {len(ai_done)} AI-performed awaiting "
          f"confirmation — telegram: {'sent' if ok else 'nothing to send / undeliverable'}")


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
    elif args[0] == "notify":
        cmd_notify()
    elif args[0] in ("pass", "fail") and len(args) >= 2 and args[1].isdigit():
        cmd_verdict(args[0], int(args[1]), " ".join(args[2:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
