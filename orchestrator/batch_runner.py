"""M1 batch execution engine — runs a mission's weekly tasks, the canaries, or the scorecard.
Called by Windows scheduled tasks (see missions/_M1_INDEX.md) or by hand.

    python orchestrator/batch_runner.py --mission 001-shopify-competitor-intel [--dry-run]
    python orchestrator/batch_runner.py --canaries
    python orchestrator/batch_runner.py --scorecard
    python orchestrator/batch_runner.py --resume            # only retry parked tasks (all missions)

Built around what the live runs exposed (HARNESS_DESIGN.md §1.6 + ledgerbook):
- workers go through `hermes -z` (web toolset — bare API cannot browse, sources would be fake);
- 429/quota → park quota_wait and continue queue; API/conn failure → infra_failed (cb106ef);
- resume-first: parked tasks retry before new ones queue; weekly dedup key prevents re-queueing;
- utf-8 everywhere (cp1252 crashes); every run appends runs/batch_<ts>.log;
- policy caps enforced: max worker calls per run, escalations to workspace/ESCALATIONS.md.
Stdlib + PyYAML only."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402
import policy  # noqa: E402

from runtime_context import (  # noqa: E402
    ROOT, RUNS, log, set_log_file,
)

MAX_WORKER_CALLS_PER_RUN = 12          # policy cost cap proxy (Ollama returns no $)


def load_roles() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["roles"]




# ── model calls ────────────────────────────────────────────────────────────────

from integrity import escalate, preflight, PROTECTED_PATHS  # noqa: E402
from execution import _strip_tool_chatter  # noqa: E402,F401 -- compatibility
from evaluation import (  # noqa: E402,F401  -- Move 5c re-exports
    ENTITY_TYPES, _parse_json_array, extract_facts, run_critic,
    seed_is_synthesis, retract_facts,
)
from scheduler import (  # noqa: E402,F401
    parse_mission, week_key, queue_mission_tasks,
    expire_stale_parked,
    reconcile_interrupted_tasks,
)

# ── Move 5c' compatibility re-exports ──────────────────────────────────
# The four canonical definitions below live in orchestrator/workflow.py.
# batch_runner keeps the same attribute names so existing imports
# (`from batch_runner import run_synthesis`, etc.) keep working without
# a hunt-and-replace pass through the codebase. The identity is asserted
# in tests/test_f58.py §10 (`br.X is workflow.X`) so a future drift --
# e.g. someone re-defining one of these here by accident -- fails the
# gate before it can reach production.
from workflow import (  # noqa: E402,F401  -- Move 5c' re-exports
    run_synthesis, run_canaries, retry_failed_this_fire,
    _check_repeated_failure, CANARIES, MAX_RETRIES_PER_FIRE,
    REPEATED_FAILURE_THRESHOLD,
)



# Move 5d compatibility re-export; task_runner is the canonical owner.
from task_runner import run_task  # noqa: E402,F401

# Deliberate compatibility surface. Everything else in this module exists only
# to compose the CLI and may move without preserving a batch_runner alias.
__all__ = [
    "main", "load_roles", "run_task",
    "ENTITY_TYPES", "_parse_json_array", "extract_facts", "run_critic",
    "seed_is_synthesis", "retract_facts",
    "run_synthesis", "run_canaries", "retry_failed_this_fire",
    "_check_repeated_failure", "CANARIES", "MAX_RETRIES_PER_FIRE",
    "REPEATED_FAILURE_THRESHOLD", "_strip_tool_chatter",
]


# Directive-1 (2026-07-29): a task can park for three very different reasons, and the
# batch loop used to treat all of them as "stop the whole fire". They are not the same:
#
#   budget_skip     -- admission control refused THIS task's predicted cost (F24). A
#                      cheaper seed behind it may well fit. Costs zero model calls.
#   quota_wait      -- the daily hard cap is blown. Every remaining task will park too,
#                      but parking them is free and leaves an honest, annotated row
#                      instead of a silent 'queued' with no explanation.
#   chain_exhausted -- every model in the fallback chain returned a quota error. This is
#                      the only one whose retry costs anything real, so it is the only
#                      one that stops the pass, and only after repeating.
#
# Treating budget_skip as a full stop was F6's head-of-line blocking rebuilt one layer
# up: on 2026-07-28 task 26 alone estimated ~8.5M tokens while tasks 28/29 needed 2.4M
# and 1.4M -- the expensive seed parked first and the two affordable ones behind it were
# never attempted.
PARK_STATUSES = ("quota_wait", "budget_skip", "chain_exhausted")
MAX_CONSECUTIVE_CHAIN_EXHAUSTED = 2

# F43 (docs/HARDENING.md), 2026-07-30: statuses a LATER invocation may pick up again.
# `infra_failed` belongs here and was missing, which F37 turned from harmless into blocking:
# once an API/model failure is correctly classified as infra rather than as a content 'fail',
# the row is no longer retryable at all, so a canary that failed because a model would not
# load stayed failed even after the infrastructure recovered. Found immediately -- cloud
# quota reset, the operator asked to re-run the canaries, and all five would have been
# skipped ("already infra_failed this week").
#
# Note this does NOT contradict directive-2's deliberate exclusion of infra_failed from
# retry_failed_this_fire(). That exclusion is about retrying inside the SAME fire, where
# conditions are unchanged and a timeout would just burn another 1800s. This is a later
# invocation, where the whole point is that conditions may have changed.

# ── main ───────────────────────────────────────────────────────────────────────
LOCK_PATH_NAME = ".batch.lock"  # lives under RUNS; see runlock.py for F1 rationale


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission")
    ap.add_argument("--canaries", action="store_true")
    ap.add_argument("--scorecard", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deliver", action="store_true",
                    help="with --scorecard: also push the summary line to Telegram (fail-soft)")
    ap.add_argument("--max-tasks", type=int, default=MAX_WORKER_CALLS_PER_RUN)
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    set_log_file(RUNS / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    import runlock
    try:
        with runlock.acquire(RUNS / LOCK_PATH_NAME):
            return _run(args)
    except runlock.AlreadyRunning as e:
        log(f"another batch_runner is already running — skipping this fire ({e})")
        return 0


def _run(args) -> int:
    """Everything that touches shared state (ledger/ledgerbook/workspace). Runs
    ONLY while main() holds the exclusive lock — never call this directly."""
    if args.scorecard:
        import scorecard
        md, line = scorecard.build(deliver=args.deliver)
        log("scorecard written"); print(md); print("SUMMARY:", line)
        # Sunday cadence: promotion review rides the scorecard task (no extra schtask).
        # Fail-soft: a quota-blocked review must never break scorecard delivery.
        try:
            import promote
            promote.cmd_review(notify=args.deliver, dry=False)
        except Exception as e:
            log(f"promotion review skipped ({e}) — retries next Sunday")
        return 0

    if not preflight():
        return 3
    # F13 (docs/HARDENING.md): one-time-per-run consistency check between the
    # fs-guard's PROTECTED_PATHS (H9) and policy.yaml's declared writable roots --
    # catches the two lists silently drifting apart. Warns + escalates, doesn't
    # block the run (a stale doc shouldn't halt real work; it should get fixed).
    path_problems = policy.validate_paths(PROTECTED_PATHS)
    if path_problems:
        log(f"policy/fs-guard path inconsistency: {path_problems}")
        escalate(f"policy.yaml/fs-guard path lists are inconsistent: {path_problems}")
    roles = load_roles()
    expire_stale_parked()
    reconcile_interrupted_tasks()

    if args.canaries:
        run_canaries(roles)
        return 0

    if args.resume:
        import sqlite3
        with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
            # Filter OUT canaries (use --canaries to resume those) BEFORE slicing to
            # max_tasks -- found 2026-07-18: slicing first let canary rows, which sort
            # earlier by task_id, silently consume the whole budget while the mission
            # task the operator actually wanted resumed was never reached (no error,
            # just a quiet no-op).
            # F6 (docs/HARDENING.md): never-attempted (started_at NULL -- hit the
            # pre-start_task() token-budget check, not an actual worker call) go before
            # already-attempted, same fairness rule as queue_mission_tasks() above.
            parked = [r[0] for r in c.execute(
                "SELECT task_id, mission_id FROM tasks WHERE status IN "
                "('quota_wait', 'interrupted') AND mission_id != 'canaries' "
                "ORDER BY (started_at IS NOT NULL), task_id")]  # H3
        log(f"resume mode: {len(parked)} parked/interrupted non-canary task(s)")
        ran = 0
        exhausted_streak = 0
        for tid in parked[:args.max_tasks]:
            with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as c:
                mid = c.execute("SELECT mission_id FROM tasks WHERE task_id=?",
                                (tid,)).fetchone()[0]
            st = run_task(tid, parse_mission(mid), roles)
            ran += 1
            # Directive-1: same rule as the main loop -- a task that could not fit the
            # budget must not cancel the resume attempt of every task behind it.
            if st == "chain_exhausted":
                exhausted_streak += 1
                if exhausted_streak >= MAX_CONSECUTIVE_CHAIN_EXHAUSTED:
                    log("every fallback model still quota-limited — stopping resume pass")
                    break
            else:
                exhausted_streak = 0
        return 0

    if not args.mission:
        log("nothing to do (need --mission, --canaries, --scorecard, or --resume)")
        return 1

    mission = parse_mission(args.mission)
    if mission["frontmatter"].get("status") != "active":
        log(f"mission {args.mission} is not active — skipping")
        return 0

    ids = queue_mission_tasks(mission, args.dry_run)
    log(f"{args.mission}: {len(ids)} task(s) to run this pass (dedup week {week_key()})")
    if args.dry_run:
        return 0

    # Directive-1: give EVERY seed its turn. Only a repeatedly-exhausted provider chain
    # (the one park reason whose retry actually costs anything) stops the pass early --
    # see PARK_STATUSES.
    statuses = []
    exhausted_streak = 0
    for tid in ids[:args.max_tasks]:
        st = run_task(tid, mission, roles)
        statuses.append(st)
        if st == "chain_exhausted":
            exhausted_streak += 1
            if exhausted_streak >= MAX_CONSECUTIVE_CHAIN_EXHAUSTED:
                log(f"every fallback model quota-limited on {exhausted_streak} consecutive "
                    f"tasks — stopping this pass, remaining seeds stay queued")
                break
        else:
            exhausted_streak = 0

    # Move 5c' (workflow.py extraction): composition layer supplies the
    # task runner explicitly. workflow.retry_failed_this_fire raises if
    # run_task_fn is omitted -- this is the load-bearing wiring that
    # prevents a workflow -> batch_runner cycle. Before Move 5c' the
    # function used a module-local fallback; the extraction removed
    # that fallback so the seam is enforced here.
    statuses += retry_failed_this_fire(ids, mission, roles, run_task_fn=run_task)
    done = statuses.count("done")
    parked = sum(statuses.count(s) for s in PARK_STATUSES)
    log(f"run complete: {done}/{len(statuses)} done, {parked} parked, "
        f"{statuses.count('infra_failed')} infra, {statuses.count('failed')} failed")
    # Directive-5: a fire that produced new deliverables pushes the spot-check queue
    # instead of waiting for the operator to think of running `spotcheck.py list`.
    # Fail-soft on purpose -- an undeliverable notification must never fail the batch.
    if done:
        try:
            import spotcheck
            if spotcheck.notify_pending():
                log("spot-check queue pushed to Telegram")
        except Exception as e:
            log(f"spot-check notification skipped ({e})")
    print("FITNESS:", json.dumps(ledger.weekly_fitness(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
