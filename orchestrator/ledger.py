"""Minimal ledger access — append tasks, record verdicts, compute fitness.
Stdlib only. The orchestrator and any hand-run go through here so the ledger stays
the single source of truth (HARNESS_DESIGN.md §3)."""
import json
import sqlite3
import uuid
from pathlib import Path
from timebase import utc_iso

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
    # yet from SQLite's UTC point of view). Persist every lifecycle timestamp in UTC.
    with _conn() as c:
        c.execute(
            "UPDATE tasks SET status='running', started_at=?, model_used=?, "
            "lease_expires_at=datetime('now', ? || ' seconds') WHERE task_id=?",
            (utc_iso(), model_used,
             f"+{LEASE_SECONDS}", task_id),
        )


def finish_task(task_id: int, *, artifacts, cost_usd=None, tokens_in=None, tokens_out=None,
                critic_verdict=None, critic_notes=None, status="done",
                interventions=None, intervention_types=None, append_note=False) -> None:
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
    stamp = (utc_iso()
             if status in TERMINAL_STATUSES else None)
    with _conn() as c:
        c.execute(
            "UPDATE tasks SET status=?, finished_at=COALESCE(?, finished_at), artifacts=?, "
            "cost_usd=COALESCE(?, cost_usd), tokens_in=COALESCE(?, tokens_in), "
            "tokens_out=COALESCE(?, tokens_out), "
            "critic_verdict=COALESCE(?, critic_verdict), "
            "critic_notes=CASE WHEN ?=1 THEN TRIM(COALESCE(critic_notes,'') || ' | ' || ?) "
            "             ELSE COALESCE(?, critic_notes) END, "
            "interventions=COALESCE(?, interventions), "
            "intervention_types=COALESCE(?, intervention_types) WHERE task_id=?",
            (status, stamp,
             json.dumps(artifacts), cost_usd, tokens_in, tokens_out,
             critic_verdict,
             1 if append_note else 0, critic_notes or "", critic_notes,
             interventions,
             json.dumps(intervention_types) if intervention_types else None, task_id),
        )


def record_intervention(task_id: int, kind: str) -> int:
    """Increment a task's intervention counter and append `kind`. Returns the new count.

    F53 (docs/HARDENING.md): the intervention term was structurally incapable of ever
    being non-zero, so 25% of the fitness score was awarded unconditionally on every
    task in the project's history (all 32 rows read interventions=0). Two independent
    defects compounded, and BOTH had to be fixed for either to matter:

      1. `escalate()` (batch_runner) appended to workspace/ESCALATIONS.md and never
         touched the ledger row -- the signal was generated and then discarded before
         it reached the column that scores it. Same bug class as F33 (synthesis tokens)
         and F48 (canary tokens): measured, then dropped on the floor. Third instance.
      2. `finish_task()` wrote `interventions=?` with a default of 0, UNCONDITIONALLY
         overwriting. It is the one consumption column F21 missed when it moved
         cost/tokens/critic_verdict to COALESCE -- invisible precisely because the value
         was always 0 already, so the clobber never destroyed anything observable. Fixed
         in the same line above: an omitted argument now preserves what is there, which
         is what F21 established for every other column on this row.

    Escalations are the honest signal here because they are exactly "the system could not
    finish this itself and told a human": ambiguous critic verdict, deny-list hit, budget
    exhaustion, degraded failover. Run-scoped escalations (server unreachable, batch
    aborted) pass no task_id and are deliberately NOT counted -- they are not attributable
    to any one task's autonomy.

    NOT backfilled, and the discontinuity is stated rather than smoothed: weeks W29-W31
    genuinely recorded 0 because nothing could write the column, so their intervention
    term is a structural artefact, not a measurement. Comparing a post-F53 week's fitness
    against them will show a DROP that means the metric went live, not that the analyst
    got worse. weekly_fitness() now reports `intervention_measured` so that distinction
    survives into the scorecard instead of living only in this docstring."""
    with _conn() as c:
        row = c.execute("SELECT intervention_types FROM tasks WHERE task_id=?",
                        (task_id,)).fetchone()
        if row is None:
            return 0
        try:
            kinds = json.loads(row["intervention_types"] or "[]")
            if not isinstance(kinds, list):
                kinds = []
        except (json.JSONDecodeError, TypeError):
            kinds = []
        kinds.append(kind)
        c.execute("UPDATE tasks SET interventions=COALESCE(interventions,0)+1, "
                  "intervention_types=? WHERE task_id=?",
                  (json.dumps(kinds), task_id))
    return len(kinds)


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


def latest_human_note(notes: str | None) -> str:
    """The note text of the MOST RECENT `HUMAN(...)` verdict segment, or ''.

    F54 (docs/HARDENING.md): `spotcheck.cmd_verdict()` APPENDS
    (`critic_notes = COALESCE(critic_notes,'') || ?`), so a row accumulates one segment per
    verdict and every earlier one survives verbatim. That is right for audit and wrong for
    classification -- and everything that classified a row grepped the WHOLE field, so a
    single historical AI check marked the row AI-performed permanently. An operator
    re-verification could never clear it, which is precisely the transition the F28
    convention exists to enable and `spotcheck.py`'s own docstring promised."""
    n = notes or ""
    i = n.rfind("| HUMAN(")
    if i < 0:
        i = n.rfind("HUMAN(")          # pre-convention rows carry no leading pipe
        if i < 0:
            return ""
    seg = n[i:]
    j = seg.find("): ")
    return seg[j + 3:].strip() if j >= 0 else ""


def is_ai_performed(notes: str | None) -> bool:
    """True when the LATEST verdict on this row was not an independent operator read.

    F28 specifies the marker must START the note, so `startswith` is the convention's own
    test -- substring-anywhere also matched prose that merely *mentions* the marker (e.g. an
    operator note saying "supersedes the earlier AI-PERFORMED CHECK"), which is exactly how
    the first real operator verdicts on tasks 28/29 failed to register on 2026-08-01.

    Fails CLOSED: pre-convention rows that name a Claude session without using the marker
    (task 2, 2026-07-18) still count as non-independent. Under-claiming independence is the
    safe direction -- independence is the thing being proven, so a false 'independent' is a
    corrupted result while a false 'AI-performed' is only a missed credit."""
    note = latest_human_note(notes)
    if not note:
        return False
    low = note.lower()
    return (note.startswith("AI-PERFORMED CHECK")
            or "by claude session" in low or "(not operator)" in low)


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
    # F28 (docs/HARDENING.md): `human_verdict` exists specifically to be an
    # INDEPENDENT check on the system's own critic (spotcheck.py's own docstring:
    # "the missing input for the fitness accuracy term"). On 2026-07-28 three
    # human_verdict rows were written by this session's own assistant, not the
    # operator -- the CLI has no way to tell the two apart, so they are
    # schema-identical to a genuine independent check. Not fixed by restricting who
    # can write a verdict (spotcheck.py is meant to be run by whoever is at the
    # keyboard); fixed by making the fact visible wherever accuracy is reported, via
    # the marker text those checks were written with (see spotcheck.py's own note),
    # rather than silently discounting them from the accuracy math -- W (§3.2) and
    # this formula are locked, not a place to add a new conditional this session.
    spot_checked_ai = sum(1 for r in spot if is_ai_performed(r["critic_notes"]))   # F54
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
        "spot_checked_ai": spot_checked_ai,
        # F53: which terms actually MEASURED something this window, and how much of the
        # score was awarded regardless. Without this, F reads as four scored dimensions
        # when some are constants: cost_eff falls back to 1.0 whenever avg_cost is 0,
        # which is every week Ollama is the only provider (it reports $0), and before
        # F53 the intervention term was structurally 0 on every task ever run. Two
        # scorecard rows in this very ledger read fitness=0.35 on completion=0.0 and
        # accuracy=None -- a week where NOTHING completed still scored 0.35, because
        # 0.25+0.10 is the floor. Reporting the floor alongside the score is the same
        # honesty fix F7/F45 made for vanishing denominators: the number is not wrong,
        # but it is not what it looks like. W is untouched -- LOCKED (§3.2).
        "intervention_measured": any(r["interventions"] for r in terminal),
        "cost_measured": avg_cost > 0,
        "fitness_floor": round(W["intervention"] * (1 - intervention_norm) * (0 if any(
            r["interventions"] for r in terminal) else 1)
            + W["cost"] * (0 if avg_cost > 0 else cost_eff), 3),
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
