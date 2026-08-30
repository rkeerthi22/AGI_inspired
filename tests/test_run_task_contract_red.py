"""RED contract suite for replacing run_task.py's legacy execution stack."""
import importlib
import inspect
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))
import run_task

source = (ORCH / "run_task.py").read_text(encoding="utf-8")
checks = {}

try:
    outcomes = importlib.import_module("outcomes")
except ModuleNotFoundError:
    outcomes = None

checks["production typed ExecutionOutcome exists"] = bool(
    outcomes and hasattr(outcomes, "ExecutionOutcome") and hasattr(outcomes, "OutcomeKind"))
checks["main accepts explicit argv for inert unit tests"] = (
    "argv" in inspect.signature(run_task.main).parameters)
checks["legacy CLI does not invoke hermes subprocess directly"] = (
    'cmd = ["hermes"' not in source and "subprocess.run(" not in source)
checks["legacy CLI delegates to canonical task runner"] = (
    "task_runner.run_task(" in source or "batch_runner.run_task(" in source)
checks["legacy duplicate failure classifiers are removed"] = all(
    marker not in source for marker in ("def hermes_oneshot(", "def worker_failed(",
                                         "def is_quota_error("))

dry = source.find("if args.dry_run:")
queue = source.find("ledger.queue_task(")
checks["dry-run returns before any task is queued"] = dry >= 0 and queue >= 0 and dry < queue

pause = source.find("pause_engaged(")
checks["ESTOP admission occurs before queueing"] = pause >= 0 and queue >= 0 and pause < queue
checks["mission path has explicit containment validation"] = (
    "resolve_mission_path" in source or "validate_mission_id" in source)
checks["canonical run lock protects shared state"] = "runlock.acquire(" in source

# Behavioral enforcement uses only mocks and a disposable runs directory. It
# never opens the production ledger or reaches a provider.
try:
    with patch.object(run_task, "resolve_mission_path", return_value=Path("mission.md")), \
         patch.object(run_task, "parse_mission", return_value={"id": "safe"}), \
         patch.object(run_task, "pass_criteria_for", return_value="criteria"), \
         patch.object(run_task.ledger, "queue_task") as queue:
        rc = run_task.main(["--mission", "safe", "--dry-run"])
    checks["dry-run behavior performs no ledger mutation"] = rc == 0 and not queue.called
except Exception:
    checks["dry-run behavior performs no ledger mutation"] = False

try:
    with patch.object(run_task, "resolve_mission_path", return_value=Path("mission.md")), \
         patch.object(run_task, "parse_mission", return_value={"id": "safe"}), \
         patch.object(run_task, "pass_criteria_for", return_value="criteria"), \
         patch.object(run_task.execution_pause, "pause_engaged", return_value=True), \
         patch.object(run_task.ledger, "queue_task") as queue:
        rc = run_task.main(["--mission", "safe"])
    checks["paused behavior performs no ledger mutation"] = rc == 6 and not queue.called
except Exception:
    checks["paused behavior performs no ledger mutation"] = False

@contextmanager
def _fake_lock(_path):
    yield

try:
    with patch.object(run_task, "RUNS", ROOT / "runs"), \
         patch.object(run_task, "resolve_mission_path", return_value=Path("mission.md")), \
         patch.object(run_task, "parse_mission", return_value={"id": "safe"}), \
         patch.object(run_task, "pass_criteria_for", return_value="criteria"), \
         patch.object(run_task.execution_pause, "pause_engaged", return_value=False), \
         patch.object(run_task.runlock, "acquire", side_effect=_fake_lock), \
         patch.object(run_task.integrity, "preflight", return_value=True), \
         patch.object(run_task.ledger, "queue_task", return_value=77) as queue, \
         patch.object(run_task.task_runner, "run_task", return_value="done") as canonical, \
         patch.object(run_task, "load_roles", return_value={"worker": {}}):
        rc = run_task.main(["--mission", "safe", "--niche", "test"])
    checks["live path queues once and delegates once"] = (
        rc == 0 and queue.call_count == 1 and canonical.call_count == 1)
except Exception:
    checks["live path queues once and delegates once"] = False

try:
    run_task.resolve_mission_path("../escape")
except ValueError:
    checks["path traversal is rejected before filesystem access"] = True
else:
    checks["path traversal is rejected before filesystem access"] = False

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'EXPECTED FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("run_task RED contract unmet: " + ", ".join(failed))
