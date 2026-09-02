"""Operator CLI contract tests: read-only, model-free, no second authority.

Proves for `agi status`, `agi health --model-free`, and `agi preflight canary`:

  * ZERO mutation of: ESTOP, canary marker, isolation journal, batch lock,
    ACTIVE_WORK, ledger DBs, prediction DB, runs/ contents, Git state.
  * ZERO provider/mission invocation (no network modules are even importable
    from the CLI paths exercised).
  * UNKNOWN is reported instead of guessed PASS.
  * Exit codes: preflight nonzero when unsafe; health nonzero when gate fails.

All state is injected via temp directories and temp process inventories; the
live repository is only ever READ.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import operator_cli  # noqa: E402

failures: list[str] = []
checks = 0


def check(name, got, want=True):
    global checks
    checks += 1
    ok = got == want
    if not ok:
        failures.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + ("" if ok else f"\n         got={got!r}\n         want={want!r}"))


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# --------------------------------------------------------------------------
# Shared fixture: a temp "live" world the CLI reads instead of the real one.
# --------------------------------------------------------------------------

def build_world(td: Path, *, estop: bool = True, marker: bool = False,
                 batch_lock: bool = False, isolation_phase: str | None = None,
                 active_work: dict | None = None, continuity_rev: int = 1,
                 git_clean: bool = True) -> dict:
    """Create an injected state tree; return env + paths the CLI must use."""
    hermes_home = td / "hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text("test: true\n", encoding="utf-8")
    if estop:
        (hermes_home / "ESTOP").write_text('{"reason": "test"}\n', encoding="utf-8")
    if marker:
        from datetime import datetime, timezone
        (hermes_home / ".canary-operator-auth.json").write_text(json.dumps({
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "by": "operator", "use": "single-connectivity-canary"}) + "\n",
            encoding="utf-8")

    # Continuity brief (schema v2 minimal, valid, revision pinned)
    brief = {
        "schema_version": 2,
        "brief_revision": continuity_rev,
        "created_at": "2026-08-31T00:00:00+00:00",
        "status": "complete",
        "repository": {"branch": "master", "upstream": "origin/master",
                       "tree_clean": git_clean, "changed_paths": [],
                       "based_on_head": "0" * 40},
        "task": {"id": "TEST", "phase": "test", "status": "complete",
                 "next_action": "test"},
        "completed": [], "locked_constraints": [],
        "references": [], "gate": {"status": "passed", "detail": "test"},
    }
    continuity_dir = td / "harness" / "continuity"
    continuity_dir.mkdir(parents=True, exist_ok=True)
    (continuity_dir / "current.json").write_text(json.dumps(brief), encoding="utf-8")

    # runs/ with health events + optional batch lock + isolation journal
    runs = td / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "health_events.jsonl").write_text("", encoding="utf-8")
    if batch_lock:
        import time as _time
        (runs / ".batch.lock").write_text(json.dumps(
            {"pid": os.getpid(), "started_at": _time.time(),
             "process_start_id": "test-identity", "lock_id": "t"}), encoding="utf-8")
    if isolation_phase is not None:
        journal = td / "workspace" / "validation" / "cohort_isolation_state.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps({
            "phase": isolation_phase, "owner_pid": os.getpid(),
            "owner_process_start_id": "test-identity"}), encoding="utf-8")

    # ACTIVE_WORK with an in-progress owner claiming paths
    work = active_work if active_work is not None else {
        "schema_version": 1, "last_updated": "2026-08-31T00:00:00Z",
        "active_agents": [], "coordination_rules": []}
    docs = td / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "ACTIVE_WORK.json").write_text(json.dumps(work), encoding="utf-8")

    # DBs
    ledger_dir = td / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / "ledger.db").write_bytes(b"")  # invalid on purpose? no -- sqlite needs real db
    # create a real empty sqlite db
    import sqlite3
    for rel in ("ledger/ledger.db", "memory/ledgerbook.db",
                "prediction_machine/data/predictions.db"):
        p = td / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE t (x INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        con.close()

    # backups
    backups = td / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    (backups / "ledger_test_20260831_000000.db").write_bytes(b"x")

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["AGI_COHORT_JOURNAL"] = str(td / "workspace" / "validation"
                                    / "cohort_isolation_state.json")
    return {"env": env, "td": td}


def snapshot_live_repo() -> dict:
    """Digest every live artifact the CLI must never mutate."""
    targets = {}
    hermes_home = Path(os.environ.get(
        "HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes")))
    estop = hermes_home / "ESTOP"
    targets["estop"] = (estop.is_file(), digest(estop))
    canary_marker = hermes_home / ".canary-operator-auth.json"
    targets["canary_marker"] = (canary_marker.is_file(), digest(canary_marker))
    targets["ledger"] = (ROOT / "ledger" / "ledger.db").is_file(), digest(ROOT / "ledger" / "ledger.db")
    targets["ledgerbook"] = (ROOT / "memory" / "ledgerbook.db").is_file(), digest(ROOT / "memory" / "ledgerbook.db")
    targets["predictions"] = (ROOT / "prediction_machine" / "data" / "predictions.db").is_file(), \
        digest(ROOT / "prediction_machine" / "data" / "predictions.db")
    targets["active_work"] = (ROOT / "docs" / "ACTIVE_WORK.json").is_file(), digest(ROOT / "docs" / "ACTIVE_WORK.json")
    targets["continuity"] = (ROOT / ".harness" / "continuity" / "current.json").is_file(), \
        digest(ROOT / ".harness" / "continuity" / "current.json")
    targets["tiers"] = (ROOT / "tests" / "tiers.json").is_file(), digest(ROOT / "tests" / "tiers.json")
    targets["batch_lock"] = (ROOT / "runs" / ".batch.lock").is_file(), digest(ROOT / "runs" / ".batch.lock")
    targets["isolation"] = (ROOT / "workspace" / "validation"
                            / "cohort_isolation_state.json").is_file(), \
        digest(ROOT / "workspace" / "validation" / "cohort_isolation_state.json")
    proc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    targets["git_head"] = (True, proc.stdout.strip())
    proc = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain=v1",
                           "--untracked-files=all"], capture_output=True, text=True)
    targets["git_status"] = (True, proc.stdout)
    runs_digests = {p.name: digest(p) for p in (ROOT / "runs").glob("*") if p.is_file()}
    targets["runs"] = (True, runs_digests)
    return targets


# --------------------------------------------------------------------------
# Section 1: agi status -- read-only against injected state
# --------------------------------------------------------------------------

print("=== 1. agi status: read-only, injected world ===")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    before = snapshot_live_repo()

    with mock.patch.dict(os.environ, {k: v for k, v in env.items() if k in
                                      ("HERMES_HOME", "AGI_COHORT_JOURNAL")}):
        # status must not re-engage/modify ESTOP: patch reengage to explode
        import execution_pause
        with mock.patch.object(execution_pause, "reengage",
                               side_effect=AssertionError("status mutated ESTOP")):
            data = operator_cli.collect_status()

    after = snapshot_live_repo()
    check("status leaves live repo untouched", before == after)

    check("status reports estop engaged", data["estop"]["engaged"], True)
    check("status reports no canary marker", data["estop"]["canary_authorization_marker_present"], False)
    check("status reports isolation restored", data["isolation"]["phase"], "restored")
    check("status reports batch lock free", data["runlock"]["engaged"], False)
    check("status reports active_work parseable", data["active_work"]["parseable"], True)
    check("status git head is a sha", len(data["git"]["head"]) >= 12, True)

# Status with dirty tree, live lock, pending marker
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw), estop=True, marker=True, batch_lock=True,
                        isolation_phase="open")
    env = world["env"]
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}), \
         mock.patch.object(operator_cli, "BATCH_LOCK",
                           world["td"] / "runs" / ".batch.lock"):
        data = operator_cli.collect_status()
    check("status detects pending canary marker", data["estop"]["canary_authorization_marker_present"], True)
    check("status detects held batch lock", data["runlock"]["engaged"], True)
    check("status reports isolation open phase", data["isolation"]["phase"], "open")

# ESTOP absent WITHOUT authorization -> unauthorized absence reported (and no re-engage from status)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw), estop=False)
    env = world["env"]
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}):
        import execution_pause
        with mock.patch.object(execution_pause, "reengage",
                               side_effect=AssertionError("status re-engaged ESTOP")):
            data = operator_cli.collect_status()
    check("status reports unauthorized estop absence",
          data["estop"]["integrity"], "unauthorized_absence")
    check("status reports estop not engaged", data["estop"]["engaged"], False)

# ESTOP absent WITH fresh operator marker -> authorized
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw), estop=False)
    env = world["env"]
    from datetime import datetime, timezone
    marker = Path(env["HERMES_HOME"]) / ".estop-transition.json"
    marker.write_text(json.dumps({"issued_at": datetime.now(timezone.utc).isoformat(),
                                  "by": "operator", "ttl_hours": 24}), encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}):
        data = operator_cli.collect_status()
    check("status reports authorized clear marker",
          data["estop"]["integrity"], "authorized:operator_clear_marker")

# Munder quiescence via injected inventory
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    inv = Path(raw) / "inventory.json"
    # an inventory that lists only the CLI process itself (excluded by pid)
    inv.write_text(json.dumps([{"pid": os.getpid(), "name": "python",
                                "cmdline": "python operator_cli", "cwd": str(ROOT)}]),
                   encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"],
                                       "AGI_PROCESS_INVENTORY_FILE": str(inv)}):
        data = operator_cli.collect_status()
    q = data["munder_quiescence"]
    check("injected clean inventory reports quiesced", q["quiesced"], True)
    check("quiescence source is env-file", q["source"], "env-file")

    # now inject an offender: a munder host process
    inv.write_text(json.dumps([
        {"pid": 999999, "name": "Munder.exe", "cmdline": "C:/Munder/Munder.exe",
         "cwd": "C:/MunderState"}]), encoding="utf-8")
    # Keep the second probe inside the injected inventory environment too.
    # Otherwise it inspects the reviewer's live process table and makes this
    # model-free contract test depend on whichever development CLI is running.
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"],
                                       "AGI_PROCESS_INVENTORY_FILE": str(inv)}):
        data = operator_cli.collect_status()
    q = data["munder_quiescence"]
    check("injected offender reports not quiesced", q["quiesced"], False)
    check("offender classified as munder_host",
          (q["offender_details"] or [{}])[0].get("matched_by"), "munder_host")

# Unreadable inventory fails closed (never guessed quiesced)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    bad = Path(raw) / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"],
                                       "AGI_PROCESS_INVENTORY_FILE": str(bad)}):
        data = operator_cli.collect_status()
    check("unreadable inventory fails closed (not quiesced)",
          data["munder_quiescence"]["quiesced"], False)
    check("unreadable inventory reports unknown source",
          data["munder_quiescence"]["source"], "unknown")

# Provider state: recorded health events only; may surface persisted probe results
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    (world["td"] / "runs" / "health_events.jsonl").write_text(
        json.dumps({"subsystem": "estop", "operation": "tamper_recovery",
                    "error": "boom", "timestamp": "2026-08-31T00:00:00Z"}) + "\n",
        encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}), \
         mock.patch.object(operator_cli, "RUNS", world["td"] / "runs"):
        data = operator_cli.collect_status()
    p = data["provider"]
    check("provider never probed", p["probed"], False)
    check("provider recorded subsystem surfaced", "estop" in p.get("recorded_subsystems", {}), True)
    check("provider recorded event flagged not-ok",
          p["recorded_subsystems"]["estop"]["ok"], False)

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    (world["td"] / "runs" / "health_events.jsonl").write_text(
        json.dumps({
            "subsystem": "provider",
            "operation": "connectivity_canary",
            "provider": "byteplus_coding",
            "purpose": "connectivity_canary",
            "probed": True,
            "ok": True,
            "model": "ark-code-latest",
            "request_id": "req-123",
            "latency_seconds": 1.234,
            "input_tokens": 5,
            "output_tokens": 7,
            "finish_reason": "stop",
            "timestamp": "2026-09-02T20:14:55Z",
        }) + "\n",
        encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}), \
         mock.patch.object(operator_cli, "RUNS", world["td"] / "runs"):
        data = operator_cli.collect_status()
    p = data["provider"]
    check("provider probe event sets probed true", p["probed"], True)
    check("provider latest probe surfaced", p["latest_probe"]["provider"], "byteplus_coding")
    check("provider latest probe retains model", p["latest_probe"]["model"], "ark-code-latest")
    check("provider subsystem marked probed",
          p["recorded_subsystems"]["provider"]["probed"], True)

# --------------------------------------------------------------------------
# Section 2: agi status --json stability
# --------------------------------------------------------------------------

print("=== 2. agi status --json stable contract ===")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}):
        data = operator_cli.collect_status()
    rendered = json.dumps(data, indent=2, default=str)
    check("status json serializes", isinstance(json.loads(rendered), dict), True)
    required = ["command", "generated_at", "git", "continuity", "estop",
                "isolation", "runlock", "active_work", "munder_quiescence",
                "backup", "provider"]
    check("status json has all sections",
          all(k in data for k in required), True)

# --------------------------------------------------------------------------
# Section 3: preflight canary -- diagnostic only, blockers, nonzero exit
# --------------------------------------------------------------------------

print("=== 3. agi preflight canary ===")

# 3a: safe world (ESTOP engaged, no marker, clean inventory, key absent?)
# Note: ARK_API_KEY presence is a real blocker -- inject both branches.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    inv = Path(raw) / "inventory.json"
    inv.write_text(json.dumps([{"pid": os.getpid(), "name": "python",
                                "cmdline": "python", "cwd": str(ROOT)}]),
                   encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"],
                                       "AGI_PROCESS_INVENTORY_FILE": str(inv),
                                       "ARK_API_KEY": "test-presence-only"}), \
         mock.patch.object(operator_cli, "_continuity_state",
                           return_value={"revision": 99, "valid": True,
                                         "discrepancies": [], "status": "complete",
                                         "task_phase": "test"}):
        data = operator_cli.collect_preflight_canary()
    check("preflight never authorizes", data["authorized"], False)
    check("preflight marked diagnostic-only", data["diagnostic_only"], True)
    check("preflight safe world has no blockers", data["blockers"], [])
    check("preflight safe_to_proceed true", data["safe_to_proceed"], True)
    names = [c["check"] for c in data["checks"]]
    for expected in ("estop_engaged", "no_pending_canary_marker",
                      "canary_script_present", "provider_configured",
                      "ark_api_key_present_in_env", "batch_lock_free",
                      "munder_process_quiescence", "isolation_window_closed",
                      "continuity_valid"):
        check(f"preflight covers {expected}", expected in names, True)

# 3b: blocked world (marker pending, offenders, lock held, isolation open)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw), marker=True, batch_lock=True,
                        isolation_phase="open")
    env = world["env"]
    inv = Path(raw) / "inventory.json"
    inv.write_text(json.dumps([
        {"pid": os.getpid(), "name": "python", "cmdline": "python", "cwd": str(ROOT)},
        {"pid": 999999, "name": "Munder.exe", "cmdline": "Munder",
         "cwd": "C:/MunderState"}]), encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"],
                                       "AGI_PROCESS_INVENTORY_FILE": str(inv),
                                       "ARK_API_KEY": ""}), \
         mock.patch.object(operator_cli, "BATCH_LOCK",
                           world["td"] / "runs" / ".batch.lock"):
        data = operator_cli.collect_preflight_canary()
    blocker_names = [c["check"] for c in data["blockers"]]
    check("preflight blocked on pending marker",
          "no_pending_canary_marker" in blocker_names, True)
    check("preflight blocked on missing key",
          "ark_api_key_present_in_env" in blocker_names, True)
    check("preflight blocked on offenders",
          "munder_process_quiescence" in blocker_names, True)
    check("preflight blocked on batch lock",
          "batch_lock_free" in blocker_names, True)
    check("preflight blocked on open isolation",
          "isolation_window_closed" in blocker_names, True)
    check("preflight unsafe -> safe_to_proceed false", data["safe_to_proceed"], False)
    # exit code must be nonzero when blocked
    rendered = operator_cli._render_preflight(data)
    check("preflight render mentions blockers", "BLOCKED" in rendered, True)

# 3c: preflight never mutates anything in the live repo
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    before = snapshot_live_repo()
    inv = Path(raw) / "inventory.json"
    inv.write_text(json.dumps([{"pid": os.getpid(), "name": "python",
                                "cmdline": "python", "cwd": str(ROOT)}]),
                   encoding="utf-8")
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"],
                                       "AGI_PROCESS_INVENTORY_FILE": str(inv)}):
        # marker consumption is the critical thing to prove absent
        import execution_pause
        with mock.patch.object(execution_pause, "consume_canary_authorization",
                               side_effect=AssertionError("preflight consumed auth")), \
             mock.patch.object(execution_pause, "reengage",
                               side_effect=AssertionError("preflight mutated ESTOP")):
            data = operator_cli.collect_preflight_canary()
    after = snapshot_live_repo()
    check("preflight leaves live repo untouched", before == after)

# --------------------------------------------------------------------------
# Section 4: health --model-free
# --------------------------------------------------------------------------

print("=== 4. agi health --model-free ===")

# 4a: DB read-only checks against real DBs (read-only URI)
report = operator_cli._db_readonly_check(ROOT / "ledger" / "ledger.db")
check("ledger quick_check ok", report.get("quick_check_ok"), True)
report = operator_cli._db_readonly_check(ROOT / "memory" / "ledgerbook.db")
check("ledgerbook quick_check ok", report.get("quick_check_ok"), True)
report = operator_cli._db_readonly_check(ROOT / "does-not-exist.db")
check("missing db reported absent", report.get("present"), False)

# 4b: health never mutates DBs
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    before = snapshot_live_repo()
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}), \
         mock.patch.object(operator_cli, "_run_model_free_gate",
                           return_value={"ran": False, "ok": True, "reason": "test"}):
        data = operator_cli.collect_health_model_free()
    after = snapshot_live_repo()
    check("health leaves live repo untouched", before == after)
    check("health db reports present", data["databases"]["ledger"]["present"], True)

# 4c: the gate runner refuses when a batch lock exists (never fight a live run)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw), batch_lock=True)
    env = world["env"]
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}), \
         mock.patch.object(operator_cli, "BATCH_LOCK", world["td"] / "runs" / ".batch.lock"):
        gate = operator_cli._run_model_free_gate()
    check("gate refuses when batch lock present", gate["ran"], False)
    check("gate refusal reason mentions lock", "batch lock" in gate.get("reason", ""), True)

# 4d/4e: exit codes (renderers print to stdout; capture it)
import contextlib
import io

def _exit_of(data, as_json=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return operator_cli._output(data, as_json=as_json)

check("health exit ok when gate ok",
      _exit_of({"command": "health", "test_gate": {"ok": True},
                "databases": {}, "continuity": {}}), 0)
check("health exit nonzero when gate failed",
      _exit_of({"command": "health", "test_gate": {"ok": False},
                "databases": {}, "continuity": {}}), 1)

# 4e: preflight exit code
check("preflight exit 0 when no blockers",
      _exit_of({"command": "preflight", "blockers": []}), 0)
check("preflight exit nonzero when blocked",
      _exit_of({"command": "preflight", "blockers": [{"check": "x"}]}), 1)

# 4f: continuity truthfulness -- cached metadata never converts recovery
# failure/ambiguity into PASS.
print("=== 4f. continuity recovery truthfulness ===")

import continuity

_valid_brief = {
    "brief_revision": 36,
    "status": "complete",
    "task": {"phase": "awaiting-review"},
}

before = snapshot_live_repo()

with mock.patch.object(continuity, "recover", return_value={
        "brief": _valid_brief, "discrepancies": []}):
    continuity_valid = operator_cli._continuity_state()
check("successful continuity recovery reports valid",
      continuity_valid["valid"], True)
check("successful continuity recovery reports complete",
      continuity_valid["recovery"], "complete")

with mock.patch.object(
        continuity, "recover",
        side_effect=continuity.ContinuityError("live Git inspection failed")), \
     mock.patch.object(continuity, "load_current", return_value=_valid_brief):
    continuity_cached_only = operator_cli._continuity_state()
check("parsed brief plus recovery failure is UNKNOWN",
      continuity_cached_only["valid"], None)
check("cached metadata retained without becoming authority",
      continuity_cached_only["revision"], 36)
check("recovery failure is explicit in JSON state",
      continuity_cached_only["recovery"], "error")
check("parsed cached brief is identified",
      continuity_cached_only["brief_available"], True)

with mock.patch.object(
        continuity, "recover",
        side_effect=continuity.ContinuityError("live recovery unavailable")), \
     mock.patch.object(
        continuity, "load_current",
        side_effect=continuity.ContinuityError("brief malformed")):
    continuity_unavailable = operator_cli._continuity_state()
check("unavailable continuity is UNKNOWN",
      continuity_unavailable["valid"], None)
check("unavailable brief is identified",
      continuity_unavailable["brief_available"], False)
check("unavailable continuity never invents a revision",
      continuity_unavailable["revision"], "unknown")

_discrepancy = {
    "field": "reference_integrity", "recorded": "expected",
    "live": "content changed", "winner": "live",
}
with mock.patch.object(continuity, "recover", return_value={
        "brief": _valid_brief, "discrepancies": [_discrepancy]}):
    continuity_discrepant = operator_cli._continuity_state()
check("confirmed continuity discrepancy reports invalid",
      continuity_discrepant["valid"], False)
check("confirmed discrepancy remains visible",
      continuity_discrepant["discrepancies"], [_discrepancy])


def _preflight_for_continuity(state: dict) -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
        missing_lock = Path(raw) / "no-batch-lock"
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "test-presence-only"}), \
             mock.patch.object(operator_cli, "BATCH_LOCK", missing_lock), \
             mock.patch.object(operator_cli, "_estop_state", return_value={
                 "engaged": True, "integrity": "engaged",
                 "canary_authorization_marker_present": False}), \
             mock.patch.object(operator_cli, "_munder_quiescence", return_value={
                 "quiesced": True, "source": "test", "offenders": 0}), \
             mock.patch.object(operator_cli, "_isolation_state", return_value={
                 "phase": "restored", "journal_present": True}), \
             mock.patch.object(operator_cli, "_git_state", return_value={
                 "tree_clean": True, "changed_paths": [],
                 "upstream_divergence": {"ahead": 0, "behind": 0}}), \
             mock.patch.object(operator_cli, "_continuity_state",
                               return_value=state):
            return operator_cli.collect_preflight_canary()


preflight_valid = _preflight_for_continuity(continuity_valid)
valid_check = next(c for c in preflight_valid["checks"]
                   if c["check"] == "continuity_valid")
check("successful recovery passes preflight continuity", valid_check["ok"], True)

for label, state, expected_ok in (
        ("cached-only recovery error", continuity_cached_only, None),
        ("unavailable continuity", continuity_unavailable, None),
        ("confirmed discrepancy", continuity_discrepant, False)):
    preflight = _preflight_for_continuity(state)
    continuity_check = next(c for c in preflight["checks"]
                            if c["check"] == "continuity_valid")
    check(f"{label} has truthful preflight state",
          continuity_check["ok"], expected_ok)
    check(f"{label} blocks preflight",
          continuity_check in preflight["blockers"], True)
    check(f"{label} exits nonzero",
          _exit_of(preflight), 1)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    json_exit = operator_cli._output(
        {"command": "status", "continuity": continuity_cached_only},
        as_json=True)
json_payload = json.loads(buf.getvalue())
check("status --json exits zero for diagnostic output", json_exit, 0)
check("status --json exposes UNKNOWN as null",
      json_payload["continuity"]["valid"], None)
check("status --json exposes recovery error state",
      json_payload["continuity"]["recovery"], "error")

after = snapshot_live_repo()
check("continuity error paths leave all protected state untouched",
      before == after, True)

# --------------------------------------------------------------------------
# Section 5: zero provider / mission invocation
# --------------------------------------------------------------------------

print("=== 5. zero provider/mission invocation ===")

# The CLI module must not import provider or mission execution modules.
# Substring scanning of the source is unreliable (e.g. "execution" matches
# "execution_pause"), so the contract is proven two ways:
#   (a) exact-import statements must not target banned modules;
#   (b) collecting status/preflight must not load banned modules.
BANNED_MODULES = ("provider_chat", "batch_runner", "task_runner",
                  "controlled_hermes", "run_task", "workflow")
import ast

_tree = ast.parse(cli_source := (ORCH / "operator_cli.py").read_text(encoding="utf-8"))
imported_names: set[str] = set()
for node in ast.walk(_tree):
    if isinstance(node, ast.Import):
        imported_names.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imported_names.add(node.module.split(".")[0])
for mod in BANNED_MODULES:
    check(f"operator_cli never imports {mod}", mod not in imported_names, True)
check("operator_cli never imports requests", "requests" not in imported_names, True)

# The live tier is never run by health: the gate subprocess must not use --live
import inspect
src = inspect.getsource(operator_cli._run_model_free_gate)
check("gate subprocess has no --live flag", "--live" not in src, True)
check("gate subprocess has no --tier live", "--tier" not in src, True)

# Network/mission modules are not loaded by any CLI collection path
net_banned = ("requests", "urllib.request", "socket", "http.client")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    fresh_modules_before = set(sys.modules)
    with mock.patch.dict(os.environ, {"HERMES_HOME": env["HERMES_HOME"],
                                       "AGI_COHORT_JOURNAL": env["AGI_COHORT_JOURNAL"]}), \
         mock.patch.object(operator_cli, "_run_model_free_gate",
                           return_value={"ran": False, "ok": True, "reason": "test"}):
        operator_cli.collect_status()
        operator_cli.collect_preflight_canary()
    newly = set(sys.modules) - fresh_modules_before
    net_loaded = [m for m in newly for b in net_banned if m == b or m.startswith(b + ".")]
    check("no network modules loaded by status/preflight", net_loaded, [])
    mission_loaded = [m for m in newly for b in BANNED_MODULES
                      if m == b or m.startswith(b + ".")]
    check("no mission/provider modules loaded by status/preflight", mission_loaded, [])

# --------------------------------------------------------------------------
# Section 6: agi.ps1 is routing only
# --------------------------------------------------------------------------

print("=== 6. agi.ps1 routing-only contract ===")

ps1 = (ROOT / "agi.ps1").read_text(encoding="utf-8")
for forbidden in ("Remove-Item", "Set-Content", "Add-Content", "Out-File",
                  "New-Item", "Clear-Content", "Move-Item", "git ", "rm "):
    check(f"agi.ps1 never contains {forbidden!r}", forbidden not in ps1, True)
check("agi.ps1 references operator_cli.py", "operator_cli.py" in ps1, True)
# Routing-only: one python invocation, no state-mutating cmdlets, no git.
check("agi.ps1 invokes python exactly once", ps1.count("& $python.Source") == 1, True)
_mutation_cmdlets = ("remove-item", "set-content", "add-content", "out-file",
                     "new-item", "clear-content", "move-item", "copy-item",
                     "start-process", "stop-process")
_ps1_lower = ps1.lower()
check("agi.ps1 has no state-mutating cmdlets",
      [c for c in _mutation_cmdlets if c in _ps1_lower], [])
check("agi.ps1 never invokes git", "git" in _ps1_lower, False)

# --------------------------------------------------------------------------
# Section 7: real subprocess invocation end-to-end (temp world)
# --------------------------------------------------------------------------

print("=== 7. subprocess end-to-end ===")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    world = build_world(Path(raw))
    env = world["env"]
    proc = subprocess.run(
        [sys.executable, "-B", str(ORCH / "operator_cli.py"), "status", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(ROOT), timeout=120)
    check("status --json subprocess exits 0", proc.returncode, 0)
    try:
        payload = json.loads(proc.stdout)
        ok = payload.get("command") == "status"
    except ValueError:
        ok = False
    check("status --json subprocess emits valid JSON", ok, True)

    if os.name == "nt":
        launcher = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ROOT / "agi.ps1"), "status", "-Json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, cwd=str(ROOT), timeout=120)
        check("agi.ps1 status -Json exits 0", launcher.returncode, 0)
        try:
            launcher_payload = json.loads(launcher.stdout)
            launcher_ok = launcher_payload.get("command") == "status"
        except ValueError:
            launcher_ok = False
        check("agi.ps1 status -Json emits valid JSON", launcher_ok, True)

    # preflight subprocess: blocked world -> nonzero
    world2 = build_world(Path(raw) / "w2", marker=True, batch_lock=True,
                         isolation_phase="open")
    env2 = world2["env"]
    proc = subprocess.run(
        [sys.executable, "-B", str(ORCH / "operator_cli.py"),
         "preflight", "canary", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env2, cwd=str(ROOT), timeout=120)
    check("preflight subprocess blocked -> nonzero exit", proc.returncode != 0, True)
    try:
        payload = json.loads(proc.stdout)
        blocked = bool(payload.get("blockers"))
    except ValueError:
        blocked = False
    check("preflight subprocess reports blockers in JSON", blocked, True)

# Health subprocess is exercised by the full gate itself (test_operator_cli is
# part of the gate), so running it here would recurse. Contract proven above.

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

print(f"\n{checks - len(failures)}/{checks} assertions passed")
if failures:
    print("FAILED: " + ", ".join(failures))
raise SystemExit(1 if failures else 0)
