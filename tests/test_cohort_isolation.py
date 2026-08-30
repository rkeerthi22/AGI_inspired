"""Model-free regression coverage for the transactional cohort window."""
import base64
import json
import tempfile
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workspace" / "validation"))
import cohort_isolation
from cohort_isolation import CohortIsolation, LiveBackend

runner = ROOT / "workspace" / "validation" / "run_cohort.py"
source = runner.read_text(encoding="utf-8")

checks = {
    "runner is present": runner.is_file(),
    "transactional isolation required": '"--controlled-window"' in source,
    "ESTOP must be clear for controlled Hermes": "if pause_engaged():" in source,
    "unique run marker preserves audit rows": "validation-run:{RUN_MARKER}" in source,
    "no task-row deletion": 'DELETE FROM tasks' not in source,
    "no duplicate deletion helper": "delete_duplicate_spec" not in source,
    "journal recovery CLI exists": '"--recover"' in source and ".restore()" in source,
}


class FakeBackend:
    def __init__(self):
        self.tasks = [{"name": "AGI_M1_a", "enabled": True, "state": "Ready"},
                      {"name": "AGI_M1_b", "enabled": False, "state": "Disabled"}]
        self.cron = [{"id": "123456789abc", "active": True},
                     {"id": "abcdef123456", "active": False}]
        self.gateway = True
        self.events = []

    def snapshot_tasks(self): return [dict(x) for x in self.tasks]
    def snapshot_cron(self): return [dict(x) for x in self.cron]
    def gateway_running(self): return self.gateway
    def set_task_enabled(self, name, enabled):
        self.events.append(("task", name, enabled))
        next(x for x in self.tasks if x["name"] == name)["enabled"] = enabled
    def set_cron_active(self, job_id, active):
        self.events.append(("cron", job_id, active))
        next(x for x in self.cron if x["id"] == job_id)["active"] = active
    def set_gateway_running(self, running):
        self.events.append(("gateway", running))
        self.gateway = running


with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    original = b'{"reason":"test"}\n'
    estop.write_bytes(original)
    backend = FakeBackend()
    try:
        with CohortIsolation(estop, backend, journal, launch_guardian=lambda *_: 4242):
            assert not estop.exists()
            assert not any(t["enabled"] for t in backend.tasks)
            assert not any(j["active"] for j in backend.cron)
            assert not backend.gateway
            raise RuntimeError("simulated cohort failure")
    except RuntimeError as exc:
        assert str(exc) == "simulated cohort failure"
    checks.update({
        "ESTOP exact bytes restored on failure": estop.read_bytes() == original,
        "gateway exact state restored": backend.gateway,
        "cron exact states restored": [j["active"] for j in backend.cron] == [True, False],
        "task exact states restored": [t["enabled"] for t in backend.tasks] == [True, False],
        "journal records restoration": __import__("json").loads(
            journal.read_text(encoding="utf-8"))["phase"] == "restored",
        "journal records exact owner identity": bool(__import__("json").loads(
            journal.read_text(encoding="utf-8")).get("owner_process_start_id")),
        "ESTOP restored before gateway": backend.events.index(("gateway", True)) >
            backend.events.index(("gateway", False)),
    })

with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    state = {
        "version": 1, "phase": "open", "estop_b64": base64.b64encode(b"paused\n").decode(),
        "gateway_running": True,
        "cron": [{"id": "123456789abc", "active": True}],
        "tasks": [{"name": "AGI_M1_a", "enabled": True, "state": "Ready"},
                  {"name": "AGI_M1_b", "enabled": True, "state": "Ready"}],
    }
    journal.write_text(json.dumps(state), encoding="utf-8")
    backend = FakeBackend()
    backend.tasks[1]["enabled"] = False
    original_set_task = backend.set_task_enabled
    calls = {"n": 0}

    def fail_fourth_action(name, enabled):
        calls["n"] += 1
        # gateway and cron are actions 1-2; task a is action 3; task b is 4.
        if name == "AGI_M1_b":
            backend.events.append(("task_failed", name, enabled))
            raise RuntimeError("simulated fourth-step failure")
        original_set_task(name, enabled)

    backend.set_task_enabled = fail_fourth_action
    isolation = CohortIsolation(estop, backend, journal)
    try:
        isolation.restore()
        partial_failed = False
    except RuntimeError:
        partial_failed = True
    partial_state = json.loads(journal.read_text(encoding="utf-8"))
    checks.update({
        "partial restore fails closed with ESTOP restored": partial_failed and estop.read_bytes() == b"paused\n",
        "restore steps 1-3 completed before step 4 failed": backend.gateway and
            backend.cron[0]["active"] and backend.tasks[0]["enabled"],
        "partial restore journal remains recoverable": partial_state["phase"] == "restoring" and
            any("AGI_M1_b" in e for e in partial_state.get("restore_errors", [])),
    })
    backend.set_task_enabled = original_set_task
    CohortIsolation(estop, backend, journal).restore()
    recovered_state = json.loads(journal.read_text(encoding="utf-8"))
    checks["second recovery completes and clears prior errors"] = (
        recovered_state["phase"] == "restored" and
        "restore_errors" not in recovered_state and backend.tasks[1]["enabled"])

# LiveBackend inventory parsing is tri-state. Unknown/empty output is not a
# synonym for quiescence and therefore cannot authorize ESTOP removal.
original_run = cohort_isolation._run
try:
    cohort_isolation._run = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    try:
        LiveBackend().snapshot_cron()
        cron_empty_failed = False
    except RuntimeError:
        cron_empty_failed = True
    cohort_isolation._run = lambda *a, **k: subprocess.CompletedProcess(
        a, 0, "123456789abc [mystery]\n", "")
    try:
        LiveBackend().snapshot_cron()
        cron_unknown_failed = False
    except RuntimeError:
        cron_unknown_failed = True
    cohort_isolation._run = lambda *a, **k: subprocess.CompletedProcess(
        a, 0, "Gateway status unavailable\n", "")
    try:
        LiveBackend().gateway_running()
        gateway_unknown_failed = False
    except RuntimeError:
        gateway_unknown_failed = True
    cohort_isolation._run = lambda *a, **k: subprocess.CompletedProcess(
        a, 0, "Gateway is running\n", "")
    gateway_running_parsed = LiveBackend().gateway_running()
finally:
    cohort_isolation._run = original_run
checks.update({
    "empty cron inventory fails closed": cron_empty_failed,
    "unknown cron state fails closed": cron_unknown_failed,
    "unknown gateway state fails closed": gateway_unknown_failed,
    "explicit running gateway parses": gateway_running_parsed,
})

with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    estop.write_bytes(b"must-remain-paused\n")
    backend = FakeBackend()
    backend.snapshot_cron = lambda: (_ for _ in ()).throw(
        RuntimeError("unparsable cron inventory"))
    try:
        CohortIsolation(estop, backend, journal,
                        launch_guardian=lambda *_: 4242).open()
        inventory_blocked_open = False
    except RuntimeError:
        inventory_blocked_open = True
    checks["unparsable inventory refuses to clear ESTOP"] = (
        inventory_blocked_open and estop.read_bytes() == b"must-remain-paused\n")

# Simulate PID reuse: the journal PID exists, but its process-start identity no
# longer matches. Recovery must engage ESTOP first and restore all dispatchers.
with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    stale = {
        "version": 2, "phase": "open", "owner_pid": 777,
        "owner_process_start_id": "old-process",
        "estop_b64": base64.b64encode(b"paused-after-crash\n").decode(),
        "gateway_running": True,
        "cron": [{"id": "123456789abc", "active": True}],
        "tasks": [{"name": "AGI_M1_a", "enabled": True, "state": "Ready"}],
    }
    journal.write_text(json.dumps(stale), encoding="utf-8")
    backend = FakeBackend()
    backend.tasks = [dict(stale["tasks"][0], enabled=False)]
    backend.cron = [dict(stale["cron"][0], active=False)]
    backend.gateway = False
    original_identity = cohort_isolation._process_start_identity
    try:
        cohort_isolation._process_start_identity = lambda pid: "new-reused-process"
        recovered = CohortIsolation(estop, backend, journal,
                                    launch_guardian=None).recover_abandoned()
        recovered_state = json.loads(journal.read_text(encoding="utf-8"))
        journal.write_text(json.dumps(stale), encoding="utf-8")
        cohort_isolation._process_start_identity = lambda pid: "old-process"
        try:
            CohortIsolation(estop, backend, journal,
                            launch_guardian=None).recover_abandoned()
            live_owner_blocked = False
        except RuntimeError:
            live_owner_blocked = True
    finally:
        cohort_isolation._process_start_identity = original_identity
    checks.update({
        "stale process identity restores ESTOP": recovered and
            estop.read_bytes() == b"paused-after-crash\n",
        "stale process recovery restores dispatchers": backend.gateway and
            backend.cron[0]["active"] and backend.tasks[0]["enabled"],
        "stale process recovery completes journal": recovered_state["phase"] == "restored",
        "matching live owner blocks takeover": live_owner_blocked,
    })

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("cohort isolation failures: " + ", ".join(failed))
