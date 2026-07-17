"""Weekly fitness scorecard (HARNESS_DESIGN.md §3.2 / §7 acceptance).
Computes F over the week, persists a scorecards row, renders a committed markdown view,
and prepares the Telegram summary line. Delivery is GATED — actually sending requires the
operator-approved Telegram round-trip (a message send is never autonomous here).

    python orchestrator/scorecard.py            # build this week's card (no send)
    python orchestrator/scorecard.py --deliver  # build + send (blocked until Telegram approved)

Stdlib only."""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DB = ROOT / "ledger" / "ledger.db"
VIEW_DIR = ROOT / "memory" / "scorecards"
CANARY_TOTAL = 5


def _week_start() -> datetime:
    return datetime.now() - timedelta(days=7)


def canaries_green(since: datetime) -> tuple[int, int]:
    """(passed, ran) canary tasks in the window. A canary passes on human verdict if
    present, else critic verdict."""
    with sqlite3.connect(LEDGER_DB) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT critic_verdict, human_verdict FROM tasks "
            "WHERE mission_id='canaries' AND status='done' AND created_at>=?",
            (since.isoformat(timespec="seconds"),)).fetchall()
    passed = sum(1 for r in rows if (r["human_verdict"] or r["critic_verdict"]) == "pass")
    return passed, len(rows)


def _fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v*100:.0f}%" if pct else f"{v}"


def render_md(week: str, fit: dict, green: int, ran: int) -> str:
    return (
        f"# Scorecard {week}\n\n"
        f"- **Fitness F:** {_fmt(fit.get('fitness'))}\n"
        f"- Tasks attempted: {fit.get('tasks_attempted', 0)}\n"
        f"- Completion: {_fmt(fit.get('completion_rate'), pct=True)}\n"
        f"- Accuracy (spot-checked {fit.get('spot_checked', 0)}): "
        f"{_fmt(fit.get('accuracy'), pct=True)}\n"
        f"- Intervention rate: {_fmt(fit.get('intervention_rate'), pct=True)}\n"
        f"- Avg cost/task: ${fit.get('avg_cost_usd', 0) or 0}\n"
        f"- Canaries green: {green}/{ran or CANARY_TOTAL}\n"
        f"- Note: {fit.get('note', '')}\n\n"
        f"_Generated {datetime.now().isoformat(timespec='seconds')}. "
        f"Excludes quota_wait / infra_failed (not task attempts)._\n"
    )


def telegram_line(week: str, fit: dict, green: int, ran: int) -> str:
    f = fit.get("fitness")
    if fit.get("tasks_attempted", 0) == 0:
        return f"📊 {week}: no task attempts this week ({fit.get('note', '')})."
    return (f"📊 {week} · F={_fmt(f)} · {fit['tasks_attempted']} tasks · "
            f"done {_fmt(fit.get('completion_rate'), pct=True)} · "
            f"acc {_fmt(fit.get('accuracy'), pct=True)} · "
            f"${fit.get('avg_cost_usd', 0) or 0}/task · canaries {green}/{ran or CANARY_TOTAL}")


def deliver_telegram(line: str) -> None:
    # GATED: sending a message is never autonomous (policy.yaml escalation). The operator
    # must approve the Telegram round-trip first; until then this refuses closed.
    raise NotImplementedError(
        "Telegram delivery gated — approve the escalation send-test before enabling.")


def build(deliver: bool = False) -> tuple[str, str]:
    since = _week_start()
    fit = ledger.weekly_fitness()
    green, ran = canaries_green(since)
    week = datetime.now().strftime("%Y-W%V")

    with sqlite3.connect(LEDGER_DB) as c:
        c.execute(
            "INSERT INTO scorecards (week_start, tasks_attempted, completion_rate, accuracy,"
            " intervention_rate, avg_cost_usd, fitness, canaries_green, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (since.strftime("%Y-%m-%d"), fit.get("tasks_attempted"),
             fit.get("completion_rate"), fit.get("accuracy"), fit.get("intervention_rate"),
             fit.get("avg_cost_usd"), fit.get("fitness"), green, fit.get("note", "")))

    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    md = render_md(week, fit, green, ran)
    (VIEW_DIR / f"{week}.md").write_text(md, encoding="utf-8")
    line = telegram_line(week, fit, green, ran)
    if deliver:
        deliver_telegram(line)
    return md, line


if __name__ == "__main__":
    # This box's console is cp1252 — emoji/unicode in the summary crash a bare print()
    # (and would crash the weekly cron run). Force utf-8 stdout (machine rule).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--deliver", action="store_true", help="send to Telegram (gated)")
    args = ap.parse_args()
    md, line = build(deliver=args.deliver)
    print(md)
    print("TELEGRAM:", line)
