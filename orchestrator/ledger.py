"""Minimal ledger access — append tasks, record verdicts, compute fitness.
Stdlib only. The orchestrator and any hand-run go through here so the ledger stays
the single source of truth (HARNESS_DESIGN.md §3)."""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DB = ROOT / "ledger" / "ledger.db"

# One run_id per PROCESS (generated once at import, which happens once per
# `python orchestrator/...` invocation). Stamped on every row this process
# inserts into tasks/facts. H2 (docs/HARDENING.md): a NULL run_id on a newly
# appeared row is the real rogue-write signature — the worker is never told
# this schema exists (containment fix, docs/INCIDENTS.md 2026-07-18), so it
# has no way to produce a value here even if it tried to write directly.
RUN_ID = uuid.uuid4().hex[:12]

# Fitness weights — FIXED for 8 weeks, do not tune mid-window (§3.2)
W = {"completion": 0.35, "accuracy": 0.30, "intervention": 0.25, "cost": 0.10}
COST_TARGET = 0.50


def _conn(db=None):
    # F12 (docs/HARDENING.md): db=LEDGER_DB as a default arg binds the path at
    # IMPORT time, so a test/probe that reassigns ledger.LEDGER_DB to redirect
    # at a copy is silently ignored and writes land in the real DB — this is
    # exactly what happened during the 2026-07-19 audit. Resolve at CALL time.
    c = sqlite3.connect(db if db is not None else LEDGER_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def window_start_sql(days: int = 7) -> str:
    """UTC-domain window boundary (now - `days`), computed entirely inside
    SQLite so it matches created_at's clock domain -- both the value AND the
    string format. F17/F19 (docs/HARDENING.md): Python's datetime.now() runs
    ~2h ahead of SQLite's UTC datetime('now') on this machine, AND
    datetime.isoformat() emits a 'T' separator that sorts after SQLite's own
    ' ' separator in a string '>=' comparison -- either mismatch alone
    silently drops same-day rows from a window query against created_at
    (compounding: live-measured 2026-07-27, the two together excluded 4 of 7
    true in-window tasks from weekly_fitness(), not just a boundary sliver).
    Never construct a comparison boundary against created_at in Python;
    always ask SQLite for it, in SQLite's own format."""
    with _conn() as c:
        return c.execute("SELECT datetime('now', ?)", (f"-{days} days",)).fetchone()[0]


def queue_task(mission_id: str, spec: str, pass_criteria: str) -> int:
    """Create a task with pre-written pass criteria. Returns task_id."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tasks (mission_id, spec, pass_criteria, status, run_id) "
            "VALUES (?,?,?,'queued',?)",
            (mission_id, spec, pass_criteria, RUN_ID),
        )
        return cur.lastrowid


# H3 (docs/HARDENING.md, fixes F2): worst-case legitimate single-task duration is the
# worker subprocess timeout + critic call (≤300s) + fact extraction (~60s) + margin.
# A task still 'running' past its lease on the NEXT process startup means the owning
# process crashed/was killed — start_task() sets the lease once; there is no periodic
# refresh because a task is one blocking call, never a long-running loop that could benefit
# from one.
#
# COUPLED CONSTANT — must stay > batch_runner.WORKER_TIMEOUT_S + ~360s. Raised 1500 -> 2400
# on 2026-07-28 together with WORKER_TIMEOUT_S 900 -> 1800. Leaving it at 1500 while the
# worker may legitimately run 1800s would let reconcile_interrupted_tasks() declare a task
# that is STILL RUNNING to be crash-orphaned, reset it to 'interrupted', and burn one of its
# 3 MAX_TASK_ATTEMPTS — a self-inflicted failure that looks exactly like a real crash.
# Note gemma's LOCAL_FALLBACK_TIMEOUT_S (3600s) already exceeds even this; a failed-over
# local run can therefore still outlive its lease. Accepted for now (failover is rare and
# escalates), but it is the next instance of this same coupling to fix.
LEASE_SECONDS = 2400  # 40 min
MAX_TASK_ATTEMPTS = 3  # crash-loop cap: after this many interruptions, give up honestly


def start_task(task_id: int, model_used: str) -> None:
    # F17 (docs/HARDENING.md): this machine's Python local clock runs 2h ahead of
    # SQLite's datetime('now') (UTC) -- measured directly. A lease compared later via
    # SQL's datetime('now') must be COMPUTED in that same SQL/UTC clock domain, not
    # Python's, or the comparison is silently wrong by the local UTC offset (caught by
    # the H3 test: a "10 minutes ago, local time" lease looked like it hadn't expired
    # yet from SQLite's UTC point of view). started_at stays Python-local for
    # human-readable logs; the lease is UTC-only end to end.
    with _conn() as c:
        c.execute(
            "UPDATE tasks SET status='running', started_at=?, model_used=?, "
            "lease_expires_at=datetime('now', ? || ' seconds') WHERE task_id=?",
            (datetime.now().isoformat(timespec="seconds"), model_used,
             f"+{LEASE_SECONDS}", task_id),
        )


def finish_task(task_id: int, *, artifacts, cost_usd=None, tokens_in=None, tokens_out=None,
                critic_verdict=None, critic_notes=None, status="done",
                interventions=0, intervention_types=None, append_note=False) -> None:
    """F21 (docs/HARDENING.md): consumption columns default to None and are written
    via COALESCE, so OMITTING them preserves whatever a previous attempt recorded.

    They used to default to 0/0.0 and overwrite unconditionally. Every failure path
    here (timeout, quota park, infra_failed, short-output) omits them, so RETRYING a
    task silently erased the original run's accounting. Measured live 2026-07-28:
    task 24 held tokens_in=1,781,395 from its first run; a retry that timed out reset
    it to 0 and the daily counter fell 10,786,463 -> 9,001,225 -- exactly that amount.
    The consequence is backwards from safe: policy.tokens_used_today() sums this
    column, so every retry made the daily budget guard protect LESS, and real spend
    from a timed-out run (which burns tokens without returning a usage file) vanished
    from the record entirely. cost_usd carries the identical defect and is fixed in
    the same line -- it is inert only while Ollama reports $0, and would start
    silently erasing real money the day a paid key is added (F17->F19's lesson: fix
    the bug class, not the one instance you happened to measure).

    critic_verdict is COALESCEd for the same reason, and it is the part that actually
    broke the loop: an INFRA failure says nothing about content, but by writing NULL it
    erased the previous review verdict -- and run_task()'s retry-with-feedback block is
    gated on `critic_verdict == 'fail'`, so the next attempt silently lost the reviewer's
    objections. Measured live 2026-07-28: task 24's timeout turned verdict 'fail' +337
    chars of specific objections into NULL + 'worker timeout'. Callers that genuinely
    have a new verdict still pass one and still overwrite. Infra paths should pass
    append_note=True so their marker is added to the review history rather than
    replacing it."""
    # F22b (docs/HARDENING.md): only a TERMINAL status finishes a task. Parking
    # (quota_wait) or re-queueing is not an ending, and stamping finished_at for one
    # silently re-dates the spend it already carries. Found immediately after shipping
    # F22 + F21 together, by running them: parking task 26 (which holds 8,517,508 tokens
    # from its 2026-07-27 run) re-stamped finished_at to today, and because F22 makes
    # tokens_used_today() sum on finished_at, last Monday's spend was re-attributed to
    # tonight -- the counter jumped 7,219,268 -> 15,743,736 with nothing executed, past a
    # 12M cap. Two individually-correct fixes composed into a wrong one; the guard would
    # then refuse all further work on entirely fictional consumption.
    stamp = (datetime.now().isoformat(timespec="seconds")
             if status in TERMINAL_STATUSES else None)
    with _conn() as c:
        c.execute(
            "UPDATE tasks SET status=?, finished_at=COALESCE(?, finished_at), artifacts=?, "
            "cost_usd=COALESCE(?, cost_usd), tokens_in=COALESCE(?, tokens_in), "
            "tokens_out=COALESCE(?, tokens_out), "
            "critic_verdict=COALESCE(?, critic_verdict), "
            "critic_notes=CASE WHEN ?=1 THEN TRIM(COALESCE(critic_notes,'') || ' | ' || ?) "
            "             ELSE COALESCE(?, critic_notes) END, "
            "interventions=?, intervention_types=? WHERE task_id=?",
            (status, stamp,
             json.dumps(artifacts), cost_usd, tokens_in, tokens_out,
             critic_verdict,
             1 if append_note else 0, critic_notes or "", critic_notes,
             interventions, json.dumps(intervention_types or []), task_id),
        )


def update_model_used(task_id: int, model_used: str) -> None:
    """F9 (docs/HARDENING.md): cross-provider failover means the model that actually
    produced a task's output can differ from the one start_task() recorded before the
    call ran. Without this, model_used stays permanently wrong for any failed-over
    task -- misleading provenance on exactly the deliverables that most need scrutiny."""
    with _conn() as c:
        c.execute("UPDATE tasks SET model_used=? WHERE task_id=?", (model_used, task_id))


def add_lesson(task_id: int, lesson: str, kind: str = "worked") -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO lesson_candidates (task_id, lesson, kind) VALUES (?,?,?)",
            (task_id, lesson, kind),
        )


TERMINAL_STATUSES = ("done", "failed", "infra_failed")  # a worker call was resolved
PENDING_STATUSES = ("queued", "quota_wait", "running", "interrupted",
                    "blocked")  # not yet resolved (blocked: run_task.py --dry-run only)


def weekly_fitness(week_start: str | None = None) -> dict:
    """Compute F over ALL non-canary tasks SCHEDULED in the 7 days from week_start
    (default: last 7 days, via window_start_sql() -- see F17/F19) -- not just the
    ones that happened to reach a terminal state before this call ran. If passed
    explicitly, week_start must already be in SQLite's own datetime() string
    domain (space-separated, e.g. from window_start_sql()) -- not an
    arbitrary ISO string -- so it compares correctly against created_at.

    F7/F18 (docs/HARDENING.md): the original query filtered to
    `status IN ('done','failed')` for BOTH the numerator and the denominator, which
    had two compounding effects, both proven live 2026-07-24 against this exact
    ledger: (1) `stale` rows (quota-starved work superseded by week rollover,
    expire_stale_parked()) and still-`queued`/`quota_wait` rows never entered the
    denominator at all, so unattempted work silently vanished from the score
    instead of depressing it (F7); (2) separately, `run_task`/`run_synthesis` used
    to set status='done' for EVERY resolved task regardless of critic_verdict, so
    a critic-REJECTED deliverable counted as a completion too (F18) -- live
    evidence: task_id 20/21/22 all carry critic_verdict='fail' with status='done'.
    Combined, this ledger's actual last-7-days state (10 scheduled, 3 nominally
    'done' but all 3 critic-failed, rest queued/parked/stale) reported fitness as
    if completion were 100%; the true rate is 0/10. F18 is fixed at the source
    (batch_runner.py now sets status='failed' on any non-pass verdict), so
    completion_rate = done/n_total here is correct as long as that invariant
    holds; this function does not re-derive it from critic_verdict, by design --
    status is meant to be the single resolved-outcome field everything else reads.
    F19 (docs/HARDENING.md): the window boundary itself used to be computed via
    Python's datetime.now() - timedelta(days=7), compared against a UTC,
    space-separated created_at -- wrong clock AND wrong string format, live-
    measured to silently drop 4 of 7 true in-window tasks. Fixed by asking
    SQLite for the boundary (window_start_sql()) instead of computing it here.

    completion_rate's denominator is now EVERYTHING scheduled this window
    (terminal + pending + stale), so a week that ends with most seeds never
    reached cannot report near-100%. avg_cost_usd/intervention_rate stay computed
    over TERMINAL rows only (done/failed/infra_failed) -- folding never-run
    pending/stale rows (cost_usd=0, interventions=0 by construction) into THOSE
    denominators would dilute them in the opposite direction, making the system
    look cheaper/better-behaved the more work it fails to attempt. Weights (W)
    are untouched -- FIXED for 8 weeks (§3.2); this is a denominator/status-
    source fix, not a new scoring term."""
    start = week_start if week_start else window_start_sql(7)
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM tasks WHERE created_at >= ? AND mission_id != 'canaries'",
            (start,)
        ).fetchall()
    n_total = len(rows)
    if n_total == 0:
        return {"tasks_attempted": 0, "fitness": None, "note": "no tasks in window"}
    terminal = [r for r in rows if r["status"] in TERMINAL_STATUSES]
    n_terminal = len(terminal)
    dropped = sum(1 for r in rows if r["status"] == "stale")
    pending = sum(1 for r in rows if r["status"] in PENDING_STATUSES)
    completed = sum(1 for r in rows if r["status"] == "done")
    spot = [r for r in terminal if r["human_verdict"] in ("pass", "fail")]
    accuracy = (sum(1 for r in spot if r["human_verdict"] == "pass") / len(spot)
                if spot else None)
    interventions = sum(r["interventions"] for r in terminal)
    avg_cost = sum(r["cost_usd"] for r in terminal) / n_terminal if n_terminal else 0.0
    completion_rate = completed / n_total
    intervention_norm = min(1.0, interventions / n_terminal) if n_terminal else 0.0
    cost_eff = min(1.0, COST_TARGET / avg_cost) if avg_cost > 0 else 1.0
    acc = accuracy if accuracy is not None else 0.0
    fitness = (W["completion"] * completion_rate + W["accuracy"] * acc +
               W["intervention"] * (1 - intervention_norm) + W["cost"] * cost_eff)
    return {
        "tasks_scheduled": n_total, "tasks_attempted": n_terminal,
        "completion_rate": round(completion_rate, 3),
        "dropped": dropped, "pending": pending,
        "accuracy": round(accuracy, 3) if accuracy is not None else None,
        "intervention_rate": round(intervention_norm, 3),
        "avg_cost_usd": round(avg_cost, 4), "fitness": round(fitness, 3),
        "spot_checked": len(spot),
    }


if __name__ == "__main__":
    # Smoke test the ledger without any model: queue -> start -> finish -> fitness.
    tid = queue_task("000-onboarding", "SMOKE: verify ledger write path",
                     "row exists with verdict")
    start_task(tid, "none/smoke")
    finish_task(tid, artifacts=["workspace/onboarding/_smoke.txt"], cost_usd=0.0,
                critic_verdict="pass", critic_notes="ledger write-path OK",
                status="done")
    add_lesson(tid, "ledger smoke test passes end to end", "worked")
    print(f"queued+finished task_id={tid}")
    print("fitness:", json.dumps(weekly_fitness(), indent=2))
