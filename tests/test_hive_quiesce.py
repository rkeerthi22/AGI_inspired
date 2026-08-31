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


def make_state(agent_id="god", status="idle", action="awaiting",
               cwd=r"S:\AGI_like", capable=True,
               recency=None, breaker=None):
    return cohort_hive_quiesce.HiveAgentState(
        agent_id, status, action, cwd, capable, recency, breaker)


# Classification matrix (Codex review #2): only explicit idle/stopped with a
# quiescent action counts as quiet; everything else fails closed.
checks["known idle status is quiescent"] = (
    cohort_hive_quiesce.classify_agent_state(make_state()) == "quiescent")
checks["active status is active"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(status="active")) ==
    "active")
checks["running status is active"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(status="running")) ==
    "active")
checks["unknown status fails closed"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(status="hibernating?")) ==
    "unknown")
checks["malformed (empty) status fails closed"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(status="")) == "unknown")
checks["non-empty action fails closed"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(action="writing tests")) ==
    "active")
checks["recent fleet heartbeat fails closed"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(recency=30.0)) == "active")
checks["old fleet heartbeat stays quiescent"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(recency=9446.0)) ==
    "quiescent")
checks["tripped breaker fails closed"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(breaker="tripped")) ==
    "unknown")
checks["healthy breaker stays quiescent"] = (
    cohort_hive_quiesce.classify_agent_state(make_state(breaker="healthy")) ==
    "quiescent")
checks["non-mutation-capable agent never blocks"] = (
    cohort_hive_quiesce.classify_agent_state(
        make_state(status="active", capable=False)) == "quiescent")

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
states_unknown = [
    cohort_hive_quiesce.HiveAgentState("pam-mtgg4sp1", "paused", "awaiting",
                                       r"S:\AGI_like", True),
]
checks["idle mutation-capable agents are quiesced"] = (
    cohort_hive_quiesce.hive_quiesced(states_idle))
checks["active mutation-capable agent blocks quiescence"] = not (
    cohort_hive_quiesce.hive_quiesced(states_active))
checks["archived agents do not block quiescence"] = (
    cohort_hive_quiesce.hive_quiesced(states_archived))
checks["unknown status blocks quiescence"] = not (
    cohort_hive_quiesce.hive_quiesced(states_unknown))


def make_probe(roster, fleet):
    return lambda: (roster, fleet)


ROSTER = {"agents": [
    {"id": "god", "cwd": r"S:\MunderState\AGI_like",
     "command": "claude --model deepseek"},
    {"id": "dwight", "cwd": r"S:\AGI_like", "command": "claude"},
]}
FRESH_FLEET = {"ts": cohort_hive_quiesce.time.time() * 1000, "agents": []}


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
    cohort_hive_quiesce.ensure_hive_quiesced(make_probe({"agents": []}, FRESH_FLEET))
    empty_blocked = False
except cohort_hive_quiesce.HiveQuiesceError:
    empty_blocked = True
checks["empty roster fails closed"] = empty_blocked

# Offender reporting.
try:
    cohort_hive_quiesce.ensure_hive_quiesced(make_probe({"agents": [
        {"id": "god", "status": "idle", "cwd": r"S:\AGI_like", "command": "claude"},
        {"id": "dwight", "status": "running", "cwd": r"S:\AGI_like", "command": "claude"},
    ]}, FRESH_FLEET))
    offenders = []
except cohort_hive_quiesce.HiveQuiesceError as exc:
    offenders = [s for s in str(exc).split() if "dwight" in s]
checks["offender list names the active agent"] = bool(offenders)

# --- fleet classification (Codex review #2, operator option B) -----------

now_ms = cohort_hive_quiesce.time.time() * 1000
checks["fresh fleet is fresh"] = (
    cohort_hive_quiesce.classify_fleet({"ts": now_ms})[0] == "fresh")
checks["stale fleet is stale"] = (
    cohort_hive_quiesce.classify_fleet({"ts": now_ms - 3600_000})[0] == "stale")
checks["missing fleet payload is missing"] = (
    cohort_hive_quiesce.classify_fleet(None)[0] == "missing")
checks["fleet without timestamp is unverifiable"] = (
    cohort_hive_quiesce.classify_fleet({"agents": []})[0] == "unverifiable")
checks["malformed fleet payload is unverifiable"] = (
    cohort_hive_quiesce.classify_fleet("not-a-dict")[0] == "unverifiable")


def run_gate(roster, fleet, inventory=None):
    """ensure_hive_quiesced with an injected process inventory (hermetic)."""
    import os
    old = os.environ.get(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV)
    try:
        if inventory is not None:
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", delete=False,
                    encoding="utf-8") as handle:
                json.dump(inventory, handle)
                inv_path = handle.name
            os.environ[cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV] = inv_path
        elif old is None:
            os.environ.pop(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV, None)
        try:
            record = cohort_hive_quiesce.ensure_hive_quiesced(
                make_probe(roster, fleet))
            return {"opened": True, "record": record}
        except cohort_hive_quiesce.HiveQuiesceError as exc:
            return {"opened": False, "error": str(exc)}
    finally:
        if inventory is not None:
            import os as _os
            _os.unlink(inv_path)
            os.environ.pop(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV, None)
        elif old is not None:
            os.environ[cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV] = old


IDLE_ROSTER = {"agents": [
    {"id": "god", "cwd": r"S:\MunderState\AGI_like",
     "command": "claude --model deepseek",
     "status": "idle", "action": "awaiting"},
    {"id": "dwight", "cwd": r"S:\AGI_like", "command": "claude",
     "status": "idle", "action": "awaiting"},
]}
EMPTY_INVENTORY: list = []

# Missing fleet + clean positive inventory → opens (operator option B).
result = run_gate(IDLE_ROSTER, None, EMPTY_INVENTORY)
checks["missing fleet opens only with positive process proof"] = (
    result["opened"] and
    result["record"]["process_scan"]["source"] == "env-file" and
    result["record"]["fleet"]["state"] == "missing")
checks["missing fleet record notes the fallback proof"] = (
    result["opened"] and result["record"]["fleet"]["state"] == "missing")

# Missing fleet + dirty inventory → refuses (never 'missing means idle').
CLAUDE_LIVE = [{"pid": 24872, "name": "claude.exe",
                "cmdline": r"C:\...\claude.exe --bg-pty-host",
                "cwd": r"S:\AGI_like", "create_time": 1234.5}]
result = run_gate(IDLE_ROSTER, None, CLAUDE_LIVE)
checks["missing fleet with live dev CLI refuses"] = (
    not result["opened"] and "pid=24872" in result["error"])

# Stale fleet → ALWAYS refuses, even with a clean inventory.
result = run_gate(IDLE_ROSTER, {"ts": now_ms - 3600_000, "agents": []},
                  EMPTY_INVENTORY)
checks["stale fleet always refuses"] = (
    not result["opened"] and "stale" in result["error"])

# Unverifiable fleet (no ts) → refuses even with a clean inventory.
result = run_gate(IDLE_ROSTER, {"agents": []}, EMPTY_INVENTORY)
checks["unverifiable fleet always refuses"] = (
    not result["opened"] and "unverifiable" in result["error"])

# Unknown status → refuses.
result = run_gate({"agents": [{"id": "pam", "cwd": r"S:\AGI_like",
                               "command": "gemini", "status": "paused",
                               "action": "awaiting"}]},
                  FRESH_FLEET, EMPTY_INVENTORY)
checks["unknown status refuses window"] = not result["opened"]

# Non-empty action → refuses.
result = run_gate({"agents": [{"id": "dwight", "cwd": r"S:\AGI_like",
                               "command": "claude", "status": "idle",
                               "action": "refactoring cohort_isolation"}]},
                  FRESH_FLEET, EMPTY_INVENTORY)
checks["non-empty action refuses window"] = not result["opened"]

# Known idle + fresh fleet + clean inventory → opens.
result = run_gate(IDLE_ROSTER, FRESH_FLEET, EMPTY_INVENTORY)
checks["known idle with fresh fleet and clean inventory opens"] = (
    result["opened"])

# Active agent → refuses regardless of fleet/inventory.
result = run_gate({"agents": [{"id": "dwight", "cwd": r"S:\AGI_like",
                               "command": "claude", "status": "running",
                               "action": "writing"}]},
                  FRESH_FLEET, EMPTY_INVENTORY)
checks["active agent refuses window"] = not result["opened"]

# Fresh fleet but a live process → still refuses (defense-in-depth).
result = run_gate(IDLE_ROSTER, FRESH_FLEET, CLAUDE_LIVE)
checks["fresh fleet with live process still refuses"] = (
    not result["opened"] and "pid=24872" in result["error"])

# --- process inventory matching -------------------------------------------

# Unrelated OS processes (explorer, bash in home, python) are not offenders.
UNRELATED = [
    {"pid": 101, "name": "explorer.exe", "cmdline": "C:\\Windows\\explorer.exe",
     "cwd": "C:\\Users\\moham", "create_time": 1.0},
    {"pid": 102, "name": "bash.exe", "cmdline": "C:\\...\\bash.exe -c ls",
     "cwd": "C:\\Users\\moham", "create_time": 2.0},
    {"pid": 103, "name": "python.exe", "cmdline": "python tests/run_all.py",
     "cwd": "S:\\AGI_like", "create_time": 3.0},
]
checks["unrelated OS processes are not offenders"] = (
    cohort_hive_quiesce.scan_mutation_processes(
        UNRELATED, repo_root=Path(r"S:\AGI_like")) == [])

# bash running a hive script IS an offender (hive marker).
HIVE_BASH = [{"pid": 104, "name": "bash.exe",
              "cmdline": "bash cth-hook.cjs", "cwd": "C:\\", "create_time": 4.0}]
checks["hive marker in any process is an offender"] = bool(
    cohort_hive_quiesce.scan_mutation_processes(
        HIVE_BASH, repo_root=Path(r"S:\AGI_like")))

# Munder host app is always an offender, regardless of cwd/cmdline.
MUNDER_HOST = [{"pid": 105, "name": "Munder Difflin.exe",
                "cmdline": "C:\\...\\munder difflin.exe --type=utility",
                "cwd": "C:\\Users\\moham\\AppData", "create_time": 5.0}]
checks["munder host app is always an offender"] = bool(
    cohort_hive_quiesce.scan_mutation_processes(
        MUNDER_HOST, repo_root=Path(r"S:\AGI_like")))

# Dev CLI in the repo (cwd linkage) is an offender.
checks["dev CLI linked to repo by cwd is an offender"] = bool(
    cohort_hive_quiesce.scan_mutation_processes(
        CLAUDE_LIVE, repo_root=Path(r"S:\AGI_like")))

# Dev CLI running from home (no repo link, no hive link) is NOT an offender.
CLAUDE_HOME = [{"pid": 106, "name": "claude.exe", "cmdline": "claude.exe daemon run",
                "cwd": "C:\\Users\\moham", "create_time": 6.0}]
checks["unlinked dev CLI in home is not an offender"] = (
    cohort_hive_quiesce.scan_mutation_processes(
        CLAUDE_HOME, repo_root=Path(r"S:\AGI_like")) == [])

# Dev CLI in the hive state home IS an offender (hive-cwd linkage).
CLAUDE_HIVE = [{"pid": 107, "name": "claude.exe",
                "cmdline": "claude --model deepseek",
                "cwd": r"S:\MunderState\AGI_like", "create_time": 7.0}]
scanned = cohort_hive_quiesce.scan_mutation_processes(
    CLAUDE_HIVE, repo_root=Path(r"S:\AGI_like"))
checks["dev CLI in hive home is an offender by cwd linkage"] = bool(scanned)

# Process identity mismatch: excluded PID is not an offender even when the
# record matches everything else (the harness never flags itself).
SELF_RECORD = [{"pid": __import__("os").getpid(), "name": "claude.exe",
                "cmdline": "claude --permission-mode bypassPermissions",
                "cwd": r"S:\AGI_like", "create_time": 8.0}]
checks["self/ancestor pid is excluded from offender scan"] = (
    cohort_hive_quiesce.scan_mutation_processes(
        SELF_RECORD, repo_root=Path(r"S:\AGI_like")) == [])

# Identity fields are recorded: PID, start time, command line, cwd.
if scanned:
    first = scanned[0]
    checks["offender record carries full process identity"] = (
        first["pid"] == 107 and first["create_time"] == 7.0 and
        bool(first["cmdline"]) and bool(first["cwd"]))
else:
    checks["offender record carries full process identity"] = False

# Broken injected inventory file fails closed.
import os as _os
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                 encoding="utf-8") as handle:
    handle.write("{not json")
    broken_path = handle.name
old_env = _os.environ.get(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV)
_os.environ[cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV] = broken_path
try:
    try:
        cohort_hive_quiesce.ensure_hive_quiesced(
            make_probe(IDLE_ROSTER, FRESH_FLEET))
        broken_inventory_blocked = False
    except cohort_hive_quiesce.HiveQuiesceError:
        broken_inventory_blocked = True
    checks["broken injected inventory fails closed"] = broken_inventory_blocked
finally:
    _os.unlink(broken_path)
    if old_env is None:
        _os.environ.pop(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV, None)
    else:
        _os.environ[cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV] = old_env

# --- canary quiescence gate ------------------------------------------------

def run_canary_gate(inventory):
    old = _os.environ.get(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(inventory, handle)
            path = handle.name
        _os.environ[cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV] = path
        try:
            record = cohort_hive_quiesce.ensure_canary_process_quiescence()
            return {"allowed": True, "record": record}
        except cohort_hive_quiesce.HiveQuiesceError as exc:
            return {"allowed": False, "error": str(exc)}
    finally:
        _os.unlink(path)
        if old is None:
            _os.environ.pop(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV, None)
        else:
            _os.environ[cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV] = old


checks["canary gate allows clean inventory"] = run_canary_gate(UNRELATED)[
    "allowed"]
checks["canary gate refuses live dev CLI"] = not run_canary_gate(CLAUDE_LIVE)[
    "allowed"]
checks["canary gate refuses munder host"] = not run_canary_gate(MUNDER_HOST)[
    "allowed"]
canary_result = run_canary_gate(CLAUDE_LIVE)
checks["canary refusal names the pid and reason"] = (
    not canary_result["allowed"] and "pid=24872" in canary_result["error"] and
    "dev_cli_repo" in canary_result["error"])

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
    import os
    os.environ["MUNDER_HARNESS_HOME"] = str(Path(td) / "missing-hive")
    old_inv = os.environ.get(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV)
    if old_inv is None:
        os.environ.pop(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV, None)
    try:
        LiveBackend().ensure_hive_quiesced()
        live_blocked = False
    except RuntimeError:
        live_blocked = True
    finally:
        del os.environ["MUNDER_HARNESS_HOME"]
    checks["LiveBackend fails closed on unreadable hive home"] = live_blocked

# Case D2: LiveBackend against a REAL well-formed temp hive home with a
# fresh fleet, idle agents, and a clean injected inventory → opens.  This
# exercises the live file-reading path end-to-end without the real hive.
with tempfile.TemporaryDirectory() as td:
    import os
    home = Path(td) / "hive-home"
    (home / "hive").mkdir(parents=True)
    (home / "roster.json").write_text(json.dumps({
        "agents": [
            {"id": "god", "cwd": str(home), "command": "claude",
             "status": "idle", "action": "awaiting"},
        ]}), encoding="utf-8")
    (home / "hive" / "fleet.json").write_text(json.dumps({
        "ts": cohort_hive_quiesce.time.time() * 1000, "agents": []}),
        encoding="utf-8")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8") as handle:
        json.dump([], handle)
        inv_path = handle.name
    os.environ["MUNDER_HARNESS_HOME"] = str(home)
    os.environ[cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV] = inv_path
    try:
        record = LiveBackend().ensure_hive_quiesced()
        live_opened = record.get("quiesced") is True
    except RuntimeError:
        live_opened = False
    finally:
        del os.environ["MUNDER_HARNESS_HOME"]
        os.environ.pop(cohort_hive_quiesce.PROCESS_INVENTORY_FILE_ENV, None)
        os.unlink(inv_path)
    checks["LiveBackend opens on fresh fleet + idle agents + clean inventory"] = (
        live_opened)

# Case E: tree taint report.
report = cohort_hive_quiesce.tree_taint_report(
    ["docs/A.md"], status_now_provider=lambda: ["docs/A.md"])
checks["no new paths -> clean taint report"] = (
    report["verified"] is True and report["new_paths"] == [])
report2 = cohort_hive_quiesce.tree_taint_report(
    ["docs/A.md"], status_now_provider=lambda: ["docs/A.md", "docs/B.md"])
checks["new path during window is reported as taint"] = (
    report2["verified"] is True and report2["new_paths"] == ["docs/B.md"])
report3 = cohort_hive_quiesce.tree_taint_report(
    None, status_now_provider=lambda: [])
checks["missing open snapshot -> unverifiable report"] = (
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