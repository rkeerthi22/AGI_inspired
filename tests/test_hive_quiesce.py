"""Model-free regression: hive quiescence gate in the cohort window (2026-08-31).

Uses fake backends with the extended IsolationBackend protocol
(snapshot_hive / ensure_hive_quiesced).  No real hive state, no ESTOP
changes, no processes touched.
"""
import base64
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.path.insert(0, str(ROOT / "workspace" / "validation"))

import cohort_hive_quiesce  # noqa: E402
from cohort_isolation import CohortIsolation  # noqa: E402

checks = {}

# --- pure function coverage on the module itself -------------------------

states_idle = [
    cohort_hive_quiesce.HiveAgentState("god", "idle", "awaiting",
                                       r"S:\MunderState\AGI_like", True),
    cohort_hive_quiesce.HiveAgentState("jim-mtgg46e6", "idle", "",
                                       r"S:\AGI_like", True),
]
states_active = states_idle + [
    cohort_hive_quiesce.HiveAgentState("dwight-mtgg4xv5", "active", "writing",
                                       r"S:\AGI_like", True),
]
states_archived = [
    cohort_hive_quiesce.HiveAgentState("kevin-mtgg50o4", "archived", "",
                                       r"S:\AGI_like", True),
]
checks["idle mutation-capable agents are quiesced"] = (
    cohort_hive_quiesce.hive_quiesced(states_idle))
checks["active mutation-capable agent blocks quiescence"] = not (
    cohort_hive_quiesce.hive_quiesced(states_active))
checks["archived agents do not block quiescence"] = (
    cohort_hive_quiesce.hive_quiesced(states_archived))


def make_probe(agents):
    payload = {"agents": agents}
    return lambda: (payload, payload)


# Unreadable probe → fail closed as HiveQuiesceError.
try:
    cohort_hive_quiesce.ensure_hive_quiesced(lambda: (_ for _ in ()).throw(
        RuntimeError("unreadable")))
    probe_failed_closed = False
except cohort_hive_quiesce.HiveQuiesceError:
    probe_failed_closed = True
checks["unreadable roster probe raises HiveQuiesceError"] = probe_failed_closed

# Empty roster (no agents) is ambiguous → fail closed.
try:
    cohort_hive_quiesce.ensure_hive_quiesced(make_probe([]))
    empty_blocked = False
except cohort_hive_quiesce.HiveQuiesceError:
    empty_blocked = True
checks["empty roster fails closed"] = empty_blocked

# Offender reporting.
try:
    cohort_hive_quiesce.ensure_hive_quiesced(make_probe([
        {"id": "god", "status": "idle", "cwd": r"S:\AGI_like", "command": "claude"},
        {"id": "dwight", "status": "running", "cwd": r"S:\AGI_like", "command": "claude"},
    ]))
    offenders = []
except cohort_hive_quiesce.HiveQuiesceError as exc:
    offenders = [s for s in str(exc).split() if "dwight" in s]
checks["offender list names the active agent"] = bool(offenders)

# --- extended isolation backend in the full window lifecycle -------------

class FakeHiveBackend:
    """Full IsolationBackend with the two hive methods, hive state injected."""
    def __init__(self, hive_states, quiesced=True, fail=False):
        self.hive_states = hive_states
        self.quiesced = quiesced
        self.fail = fail
        self.tasks = [{"name": "AGI_M1_a", "enabled": True, "state": "Ready"}]
        self.cron = [{"id": "123456789abc", "active": True}]
        self.gateway = True
        self.events = []

    def snapshot_tasks(self): return [dict(t) for t in self.tasks]
    def set_task_enabled(self, name, enabled):
        self.events.append(("task", name, enabled))
        next(t for t in self.tasks if t["name"] == name)["enabled"] = enabled
    def snapshot_cron(self): return [dict(c) for c in self.cron]
    def set_cron_active(self, job_id, active):
        self.events.append(("cron", job_id, active))
        next(c for c in self.cron if c["id"] == job_id)["active"] = active
    def gateway_running(self): return self.gateway
    def set_gateway_running(self, running):
        self.events.append(("gateway", running))
        self.gateway = running
    def snapshot_hive(self):
        if self.fail:
            raise RuntimeError("hive snapshot unavailable")
        return [dict(s) for s in self.hive_states]
    def ensure_hive_quiesced(self):
        snapshot = self.snapshot_hive()
        active = [s for s in snapshot
                  if s.get("mutation_capable") and s.get("status") in
                  ("active", "running", "working", "busy", "starting up")]
        if active or not self.quiesced:
            raise RuntimeError(
                f"controlled window refused: active mutation-capable hive "
                f"agents: {[s['id'] for s in active]}")
        return {"agents": snapshot, "quiesced": True,
                "offenders": []}


HIVE_IDLE = [{"id": "god", "status": "idle", "cwd": r"S:\AGI_like",
              "mutation_capable": True},
             {"id": "dwight", "status": "idle", "cwd": r"S:\AGI_like",
              "mutation_capable": True}]
HIVE_ACTIVE = [{"id": "dwight", "status": "running", "cwd": r"S:\AGI_like",
                "mutation_capable": True}]

# Case A: quiet hive → window opens; journal records hive snapshot + quiesced.
with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    original = b'{"reason":"quiet-hive"}\n'
    estop.write_bytes(original)
    backend = FakeHiveBackend(HIVE_IDLE)
    try:
        with CohortIsolation(estop, backend, journal,
                             launch_guardian=lambda *_: 4242):
            state = json.loads(journal.read_text(encoding="utf-8"))
            checks["quiet hive opens the window"] = not estop.exists()
            checks["journal snapshots hive agents"] = (
                len(state.get("hive", [])) == 2)
            checks["journal records hive_quiesced"] = (
                state.get("hive_quiesced") is True)
            checks["tree status snapshot captured at open"] = isinstance(
                state.get("tree_status_at_open"), list)
            raise RuntimeError("simulated mission failure")
    except RuntimeError as exc:
        assert "simulated mission failure" in str(exc)
    checks["restoration re-engages ESTOP after window"] = (
        estop.read_bytes() == original)
    final = json.loads(journal.read_text(encoding="utf-8"))
    checks["restored journal carries tree-taint audit"] = (
        "tree_taint" in final and isinstance(final["tree_taint"], dict))

# Case B: active hive → window REFUSES to open; full restoration runs;
# ESTOP never left.
with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    original = b'{"reason":"live-hive"}\n'
    estop.write_bytes(original)
    backend = FakeHiveBackend(HIVE_ACTIVE)
    try:
        CohortIsolation(estop, backend, journal,
                        launch_guardian=lambda *_: 4242).open()
        blocked = False
    except RuntimeError:
        blocked = True
    checks["active hive refuses window"] = blocked
    checks["refused window leaves ESTOP engaged"] = (
        estop.read_bytes() == original)
    state = json.loads(journal.read_text(encoding="utf-8"))
    checks["refusal journal reaches restored phase"] = (
        state["phase"] == "restored")

# Case C: hive snapshot unavailable (ambiguous) → fail closed.
with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    original = b'{"reason":"ambiguous"}\n'
    estop.write_bytes(original)
    backend = FakeHiveBackend([], fail=True)
    try:
        CohortIsolation(estop, backend, journal,
                        launch_guardian=lambda *_: 4242).open()
        ambiguous_blocked = False
    except RuntimeError:
        ambiguous_blocked = True
    checks["unavailable hive snapshot refuses window"] = ambiguous_blocked
    checks["ambiguous refusal leaves ESTOP engaged"] = (
        estop.read_bytes() == original)

# Case D: LiveBackend hive methods route through cohort_hive_quiesce and
# fail closed on an unreadable roster (env-scoped temp hive home).
from cohort_isolation import LiveBackend
with tempfile.TemporaryDirectory() as td:
    os_env = None
    import os
    os.environ["MUNDER_HARNESS_HOME"] = str(Path(td) / "missing-hive")
    try:
        LiveBackend().ensure_hive_quiesced()
        live_blocked = False
    except RuntimeError:
        live_blocked = True
    finally:
        del os.environ["MUNDER_HARNESS_HOME"]
    checks["LiveBackend fails closed on unreadable hive home"] = live_blocked

# Case E: tree taint report.
    class TaintCheck:
        pass
    report = cohort_hive_quiesce.tree_taint_report(
        ["docs/A.md"], status_now_provider=lambda: ["docs/A.md"])
    checks["no new paths → clean taint report"] = (
        report["verified"] is True and report["new_paths"] == [])
    report2 = cohort_hive_quiesce.tree_taint_report(
        ["docs/A.md"], status_now_provider=lambda: ["docs/A.md", "docs/B.md"])
    checks["new path during window is reported as taint"] = (
        report2["verified"] is True and report2["new_paths"] == ["docs/B.md"])
    report3 = cohort_hive_quiesce.tree_taint_report(
        None, status_now_provider=lambda: [])
    checks["missing open snapshot → unverifiable report"] = (
        report3["verified"] is False)

# Case F: guardian path restores through the extended backend (journal from
# an open window with a dead owner, hive methods present).
import os as _os
with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    temp = Path(td)
    estop = temp / "ESTOP"
    journal = temp / "state.json"
    stale = {
        "version": 2, "phase": "open", "owner_pid": 999999,
        "owner_process_start_id": "windows-filetime:1",
        "estop_b64": base64.b64encode(b"paused-after-crash\n").decode(),
        "gateway_running": True,
        "cron": [{"id": "123456789abc", "active": True}],
        "tasks": [{"name": "AGI_M1_a", "enabled": True, "state": "Ready"}],
        "hive": HIVE_IDLE,
        "tree_status_at_open": [],
    }
    journal.write_text(json.dumps(stale), encoding="utf-8")
    backend = FakeHiveBackend(HIVE_IDLE)
    recovered = CohortIsolation(estop, backend, journal,
                                launch_guardian=None).recover_abandoned()
    checks["guardian recovery restores with hive-aware backend"] = recovered

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("hive quiesce failures: " + ", ".join(failed))