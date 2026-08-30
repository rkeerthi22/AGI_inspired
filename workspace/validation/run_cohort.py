"""Run the validation cohort inside a transactional dispatcher-isolation window."""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))

import batch_runner  # noqa: E402
import ledger  # noqa: E402
import prompts  # noqa: E402
import runlock  # noqa: E402
import runtime_context as rc  # noqa: E402
import scheduler  # noqa: E402
import yaml  # noqa: E402
from execution_pause import estop_path, pause_engaged  # noqa: E402
from cohort_isolation import CohortIsolation, LiveBackend  # noqa: E402

COHORT = ROOT / "workspace" / "validation" / "cohort_missions.json"
SUMMARY = ROOT / "workspace" / "validation" / "cohort_summary.json"
ARTIFACTS_DIR = ROOT / "workspace" / "validation" / "cohort_artifacts"

# We need a fresh "mission_id" per cohort spec because the harness validates
# mission membership. Create synthetic missions via direct ledger insert.
COHORT_MISSION = "001-shopify-competitor-intel"
RUN_MARKER = uuid.uuid4().hex[:12]


def ensure_cohort_mission() -> None:
    """No-op: mission registry is on disk in orchestrator/missions/*.md,
    not in a DB table. The cohort reuses 001-shopify-competitor-intel.
    """
    pass


def validation_roles() -> dict:
    """Use BytePlus only for this cohort without changing production role defaults."""
    config = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    roles = batch_runner.load_roles()
    provider = config["providers"]["byteplus_coding"]
    byteplus = {
        "provider": "byteplus_coding",
        "hermes_provider": provider["hermes_provider"],
        "model": provider["routing_model"],
        "endpoint": provider["endpoint"],
        "authentication_reference": provider["authentication_reference"],
        "quota_group": "byteplus-coding-plan",
    }
    roles["worker"] = dict(byteplus)
    roles["critic"] = dict(byteplus)
    roles["manager"] = dict(byteplus)
    return roles


def task_row(tid: int):
    with sqlite3.connect(ledger.LEDGER_DB, timeout=30) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        return dict(row) if row else None


def run_one_mission(spec: dict) -> dict:
    """Run a single cohort mission end-to-end. Returns per-mission result dict."""
    if pause_engaged():
        raise RuntimeError("Hermes ESTOP is engaged; controlled model execution is unavailable")

    # A unique marker preserves every prior audit row and avoids dedup without
    # deleting historical task evidence.
    mission_spec = f"{spec['spec']} [validation-run:{RUN_MARKER}]"

    print(f"\n--- [{spec['id']}] {spec['type']} ---")
    print(f"  spec: {mission_spec[:120]}...")

    # Make sure the mission row exists
    ensure_cohort_mission()

    # Parse mission spec then queue task
    mission = scheduler.parse_mission(COHORT_MISSION)
    started = time.time()
    # Cohort missions use the cohort's own pass criteria, not the mission's
    # default (which is the weekly Shopify competitor brief criteria).
    task_id = ledger.queue_task(COHORT_MISSION, mission_spec,
                                spec["pass_criteria"])
    rc.set_log_file(rc.RUNS / f"cohort_{spec['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    status = None
    with runlock.acquire(rc.RUNS / batch_runner.LOCK_PATH_NAME):
        status = batch_runner.run_task(task_id, mission, validation_roles())
    elapsed = time.time() - started
    after = task_row(task_id)
    print(f"  status: {status} ({elapsed:.1f}s)")

    # Collect artifact paths
    paths = {
        "worker_usage": rc.RUNS / f"task{task_id}_worker.usage.json",
        "retrieval_audit": rc.RUNS / f"task{task_id}_worker.usage.retrieval.jsonl",
        "worker_raw": rc.RUNS / f"task{task_id}_worker_raw.txt",
        "critic_trace": rc.RUNS / f"task{task_id}_critic_reasoning.txt",
        "critic_usage": rc.RUNS / f"task{task_id}_critic.usage.json",
        "citation_evidence": rc.RUNS / f"task{task_id}_citation_evidence.json",
        "mission_usage": rc.RUNS / f"task{task_id}_mission.usage.json",
    }
    artifacts = {}
    for k, p in paths.items():
        artifacts[k] = {"path": str(p), "exists": p.exists(),
                         "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None}

    return {
        "id": spec["id"],
        "type": spec["type"],
        "spec": mission_spec,
        "pass_criteria": spec["pass_criteria"],
        "task_id": task_id,
        "status": status,
        "elapsed_s": round(elapsed, 1),
        "estop_engaged_after": pause_engaged(),
        "row_after": after,
        "artifacts": artifacts,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--recover", action="store_true",
                   help="restore ESTOP and dispatchers from the durable isolation journal")
    p.add_argument("--only", nargs="*", default=None,
                   help="If set, run only these mission ids (e.g. M1 M3)")
    p.add_argument("--from", dest="start", default=None,
                   help="If set, start from this mission id")
    p.add_argument("--stop-after", type=int, default=None,
                   help="Stop after this many missions")
    p.add_argument("--controlled-window", action="store_true",
                   help="transactionally isolate production dispatchers for this run")
    args = p.parse_args()

    if args.recover:
        if args.controlled_window or args.only or args.start or args.stop_after:
            raise SystemExit("ABORT: --recover cannot be combined with execution options")
        isolation = CohortIsolation(estop_path(), LiveBackend())
        if not isolation.journal.exists():
            raise SystemExit(f"ABORT: isolation journal not found: {isolation.journal}")
        isolation.restore()
        print(json.dumps({"recovered": True, "journal": str(isolation.journal),
                          "phase": (isolation.state or {}).get("phase"),
                          "estop_engaged": pause_engaged()}))
        return

    cohort = json.loads(COHORT.read_text(encoding="utf-8"))
    missions = cohort["specs"]
    if args.only:
        missions = [m for m in missions if m["id"] in args.only]
    if args.start:
        seen = False
        filtered = []
        for m in missions:
            if m["id"] == args.start:
                seen = True
            if seen:
                filtered.append(m)
        missions = filtered
    if args.stop_after:
        missions = missions[: args.stop_after]

    if not args.controlled_window:
        raise SystemExit("ABORT: pass --controlled-window to enforce dispatcher isolation")
    if not pause_engaged():
        raise SystemExit("ABORT: ESTOP must be engaged before opening a controlled window")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = {"started_at": datetime.now().isoformat(),
               "isolated": True, "estop_engaged_before": pause_engaged(),
               "run_marker": RUN_MARKER,
               "missions": []}
    isolation = CohortIsolation(estop_path(), LiveBackend())
    try:
        with isolation:
            summary["isolation_state"] = isolation.state
            for spec in missions:
                try:
                    res = run_one_mission(spec)
                except Exception as e:
                    res = {"id": spec["id"], "type": spec["type"], "error": str(e)}
                summary["missions"].append(res)
                # Persist incrementally so partial cohort survives a crash.
                SUMMARY.write_text(json.dumps(summary, indent=2, default=str),
                                   encoding="utf-8")
    finally:
        summary["estop_engaged_after"] = pause_engaged()
        summary["restoration_state"] = isolation.state

    summary["finished_at"] = datetime.now().isoformat()
    SUMMARY.write_text(json.dumps(summary, indent=2, default=str),
                       encoding="utf-8")
    print(f"\nCohort run complete: {len(summary['missions'])} missions")
    print(f"Summary at: {SUMMARY}")


if __name__ == "__main__":
    main()
