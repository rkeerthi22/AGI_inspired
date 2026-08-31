"""Run one explicitly requested task through the canonical task pipeline.

This module is a CLI adapter only. Model invocation, failure classification,
critic handling, and ledger finalization belong to task_runner.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import execution_pause  # noqa: E402
import integrity  # noqa: E402
import ledger  # noqa: E402
import runlock  # noqa: E402
import task_runner  # noqa: E402
from batch_runner import load_roles  # noqa: E402
from outcomes import ExecutionOutcome, OutcomeKind  # noqa: E402
from prompts import pass_criteria_for  # noqa: E402
from runtime_context import MISSIONS, RUNS  # noqa: E402
from scheduler import parse_mission  # noqa: E402

LOCK_PATH_NAME = ".batch.lock"
MISSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def resolve_mission_path(mission_id: str) -> Path:
    """Resolve an ordinary mission filename and prove it remains contained."""
    if not MISSION_ID_RE.fullmatch(mission_id):
        raise ValueError("mission id may contain only letters, numbers, '_' and '-'")
    mission_root = MISSIONS.resolve(strict=True)
    candidate = (mission_root / f"{mission_id}.md").resolve(strict=True)
    if candidate.parent != mission_root or not candidate.is_file():
        raise ValueError("mission path is outside the mission directory")
    return candidate


def _outcome_for(status: str) -> ExecutionOutcome:
    mapping = {
        "done": ExecutionOutcome(OutcomeKind.PASS, 0),
        "failed": ExecutionOutcome(OutcomeKind.FAILED, 4),
        "infra_failed": ExecutionOutcome(OutcomeKind.INFRA_FAILED, 3, retryable=True),
        "quota_wait": ExecutionOutcome(OutcomeKind.QUOTA_WAIT, 2, retryable=True),
        "chain_exhausted": ExecutionOutcome(
            OutcomeKind.QUOTA_WAIT, 2, retryable=True,
            error_category="provider_chain_exhausted"),
        "budget_skip": ExecutionOutcome(
            OutcomeKind.QUOTA_WAIT, 2, retryable=True,
            error_category="budget_insufficient"),
    }
    return mapping.get(status, ExecutionOutcome(
        OutcomeKind.INFRA_FAILED, 3, error_category="unknown_runner_status",
        message=f"canonical runner returned unknown status: {status!r}"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", required=True)
    parser.add_argument("--niche", default="")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the task without queueing it")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        resolve_mission_path(args.mission)
        mission = parse_mission(args.mission)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"[blocked] invalid mission: {exc}", file=sys.stderr)
        return 5

    spec = f"Mission {args.mission}: gather 3 sourced facts. Niche: {args.niche or 'TBD'}"
    criteria = pass_criteria_for(mission)
    if args.dry_run:
        print(f"[dry-run] mission={args.mission} spec={spec}")
        return 0

    # Admission is before every durable write. The canonical provider dispatcher
    # checks the same fail-closed gate again immediately before every model call.
    execution_pause.verify_pause_integrity()
    if execution_pause.pause_engaged():
        print("[paused] Hermes ESTOP is engaged; task was not queued", file=sys.stderr)
        return 6

    RUNS.mkdir(parents=True, exist_ok=True)
    try:
        with runlock.acquire(RUNS / LOCK_PATH_NAME):
            if not integrity.preflight():
                print("[blocked] harness preflight failed", file=sys.stderr)
                return 5
            # The process may have waited for another lock owner. Recheck so an
            # ESTOP engaged during that wait cannot race with queue admission.
            if execution_pause.pause_engaged():
                print("[paused] Hermes ESTOP engaged before queue admission",
                      file=sys.stderr)
                return 6
            tid = ledger.queue_task(args.mission, spec, criteria)
            print(f"[ledger] queued task {tid}")
            outcome = _outcome_for(task_runner.run_task(tid, mission, load_roles()))
    except runlock.AlreadyRunning as exc:
        print(f"[blocked] another harness run owns the lock: {exc}", file=sys.stderr)
        return 5

    print(f"[{outcome.kind.value}] task {tid}{': ' + outcome.message if outcome.message else ''}")
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
