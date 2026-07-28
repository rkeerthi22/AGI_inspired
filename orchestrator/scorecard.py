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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DB = ROOT / "ledger" / "ledger.db"
VIEW_DIR = ROOT / "memory" / "scorecards"
CANARY_TOTAL = 5


def _week_start() -> str:
    # F17/F19 (docs/HARDENING.md): this used to be datetime.now() - timedelta(days=7),
    # compared as a 'T'-separated Python isoformat() string against created_at (UTC,
    # space-separated -- SQLite's own datetime('now') format). Both the clock (local
    # vs UTC) and the string format were wrong, and live-measured together they
    # silently dropped same-day rows from the window, not just a boundary sliver.
    # ledger.window_start_sql() asks SQLite for the boundary in SQLite's own domain,
    # so it compares correctly against created_at with no conversion needed here.
    return ledger.window_start_sql(7)


def canaries_green(since: str) -> tuple[int, int]:
    """(passed, ran) canary tasks in the window. `since` must already be in SQLite's
    datetime() string domain (see _week_start()). A canary passes on human verdict if
    present, else critic verdict."""
    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT critic_verdict, human_verdict FROM tasks "
            "WHERE mission_id='canaries' AND status='done' AND created_at>=?",
            (since,)).fetchall()
    passed = sum(1 for r in rows if (r["human_verdict"] or r["critic_verdict"]) == "pass")
    return passed, len(rows)


def _fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v*100:.0f}%" if pct else f"{v}"


def crash_recovery_counts(since: str) -> tuple[int, int]:
    """(recovered, gave_up) this week -- H3, docs/HARDENING.md fixes F2. Never silent:
    a task orphaned by a crash/power-loss must show up here, not just quietly vanish.
    `since` must already be in SQLite's datetime() string domain (see _week_start())."""
    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        notes = [r[0] or "" for r in c.execute(
            "SELECT critic_notes FROM tasks WHERE created_at>=? AND "
            "(critic_notes LIKE '%recovered from an orphaned running state%' OR "
            "critic_notes LIKE '%gave up after%interruptions%')",
            (since,)).fetchall()]
    recovered = sum(1 for n in notes if "recovered from an orphaned" in n)
    gave_up = sum(1 for n in notes if "gave up after" in n)
    return recovered, gave_up


def _ai_check_suffix(fit: dict) -> str:
    """F28 (docs/HARDENING.md): human_verdict exists to be an INDEPENDENT signal on
    the critic's own judgment, but the CLI that records it cannot tell an operator's
    keystroke from an assistant acting on the operator's behalf -- both write the
    identical column. Rather than silently reclassify or discount those rows (a change
    to the locked accuracy formula this session has no standing to make), surface the
    count wherever accuracy is reported, so the number is never read as more
    independent than it actually is."""
    n = fit.get("spot_checked_ai", 0)
    return f", {n} AI-performed pending operator confirmation" if n else ""


def render_md(week: str, fit: dict, green: int, ran: int, recovered: int = 0, gave_up: int = 0) -> str:
    crash_line = (f"- Crash recovery: {recovered} task(s) recovered, {gave_up} gave up "
                  f"after repeated interruption\n" if (recovered or gave_up) else "")
    # H5/F7 (docs/HARDENING.md): dropped/pending are first-class, always-shown lines,
    # not folded quietly out of the denominator -- a week that drops most of its
    # scheduled work must show that plainly here, not just in a lower F.
    scope_line = (f"- Tasks scheduled: {fit.get('tasks_scheduled', fit.get('tasks_attempted', 0))} "
                 f"(attempted {fit.get('tasks_attempted', 0)}, dropped {fit.get('dropped', 0)}, "
                 f"still pending {fit.get('pending', 0)})\n")
    return (
        f"# Scorecard {week}\n\n"
        f"- **Fitness F:** {_fmt(fit.get('fitness'))}\n"
        f"{scope_line}"
        f"- Completion: {_fmt(fit.get('completion_rate'), pct=True)} (of tasks SCHEDULED, "
        f"not just resolved)\n"
        f"- Accuracy (spot-checked {fit.get('spot_checked', 0)}"
        f"{_ai_check_suffix(fit)}): {_fmt(fit.get('accuracy'), pct=True)}\n"
        f"- Intervention rate: {_fmt(fit.get('intervention_rate'), pct=True)}\n"
        f"- Avg cost/task: ${fit.get('avg_cost_usd', 0) or 0}\n"
        f"- Canaries green: {green}/{ran or CANARY_TOTAL}\n"
        f"{crash_line}"
        f"- Note: {fit.get('note', '')}\n\n"
        f"_Generated {datetime.now().isoformat(timespec='seconds')}. Completion is measured "
        f"against everything scheduled this week; avg cost/intervention rate are measured "
        f"only over tasks that actually resolved (done/failed/infra_failed)._\n"
    )


def telegram_line(week: str, fit: dict, green: int, ran: int, recovered: int = 0, gave_up: int = 0) -> str:
    f = fit.get("fitness")
    crash_suffix = f" · ⚠ {recovered} recovered/{gave_up} gave up (crash)" if (recovered or gave_up) else ""
    if fit.get("tasks_attempted", 0) == 0:
        return f"📊 {week}: no task attempts this week ({fit.get('note', '')}).{crash_suffix}"
    dropped, pending = fit.get("dropped", 0), fit.get("pending", 0)
    drop_suffix = f" · {dropped} dropped/{pending} pending" if (dropped or pending) else ""
    ai_n = fit.get("spot_checked_ai", 0)
    ai_suffix = f" · ⚠ {ai_n} spot-check(s) AI-performed, pending your confirmation" if ai_n else ""
    return (f"📊 {week} · F={_fmt(f)} · {fit.get('tasks_scheduled', fit['tasks_attempted'])} scheduled · "
            f"done {_fmt(fit.get('completion_rate'), pct=True)} · "
            f"acc {_fmt(fit.get('accuracy'), pct=True)} · "
            f"${fit.get('avg_cost_usd', 0) or 0}/task · canaries {green}/{ran or CANARY_TOTAL}"
            f"{drop_suffix}{crash_suffix}{ai_suffix}")


def send_telegram(text: str) -> bool:
    """Push via `hermes send --to telegram` (operator-approved 2026-07-18). Fail-soft:
    returns False when undeliverable — currently the case until the operator messages
    their bot once so a home channel exists (Telegram platform rule: bots cannot
    initiate first contact). The markdown file remains the source of truth either way."""
    import subprocess
    try:
        p = subprocess.run(["hermes", "send", "--to", "telegram", "--quiet", text],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        return p.returncode == 0
    except Exception:
        return False


def deliver_telegram(line: str) -> bool:
    ok = send_telegram(line)
    print(f"[deliver] telegram: {'sent' if ok else 'undeliverable (no home channel yet) — file only'}")
    return ok


def build(deliver: bool = False) -> tuple[str, str]:
    since = _week_start()
    fit = ledger.weekly_fitness()
    green, ran = canaries_green(since)
    recovered, gave_up = crash_recovery_counts(since)
    week = datetime.now().strftime("%Y-W%V")

    with sqlite3.connect(LEDGER_DB, timeout=30) as c:
        c.execute(
            "INSERT INTO scorecards (week_start, tasks_attempted, completion_rate, accuracy,"
            " intervention_rate, avg_cost_usd, fitness, canaries_green, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (since[:10], fit.get("tasks_attempted"),
             fit.get("completion_rate"), fit.get("accuracy"), fit.get("intervention_rate"),
             fit.get("avg_cost_usd"), fit.get("fitness"), green, fit.get("note", "")))

    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    md = render_md(week, fit, green, ran, recovered, gave_up)
    (VIEW_DIR / f"{week}.md").write_text(md, encoding="utf-8")
    line = telegram_line(week, fit, green, ran, recovered, gave_up)
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
