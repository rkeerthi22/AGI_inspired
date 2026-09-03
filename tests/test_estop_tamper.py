"""Model-free regression: ESTOP tamper detection (boundary hardening 2026-08-31).

All cases use an isolated HERMES_HOME (temp dir) and an isolated cohort
journal via AGI_COHORT_JOURNAL. No real sentinel, marker, or journal is
touched; no provider is called.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.path.insert(0, str(ROOT / "workspace" / "validation"))

import execution_pause  # noqa: E402

checks = {}


def iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "hermes-home"
    home.mkdir()
    # pause_engaged() requires a stable Hermes-home marker before an absent
    # sentinel can ever be read as "not engaged"; the real home has
    # hermes-agent/, the test home gets the config.yaml alternative.
    (home / "config.yaml").write_text("# test home\n", encoding="utf-8")
    journal = Path(td) / "journal.json"
    env_home = str(home)
    os.environ["HERMES_HOME"] = env_home
    os.environ["AGI_COHORT_JOURNAL"] = str(journal)

    sentinel = home / "ESTOP"

    # 1. Engaged sentinel → integrity 'engaged', nothing re-engaged.
    sentinel.write_text('{"reason":"test"}\n', encoding="utf-8")
    checks["engaged sentinel reports engaged"] = (
        execution_pause.verify_pause_integrity() == "engaged")

    # 2. Sentinel removed, no marker, no journal → tamper detected, re-engaged.
    sentinel.unlink()
    checks["unauthorized removal is classified tamper"] = (
        execution_pause.verify_pause_integrity() == "tamper_reengaged")
    checks["tamper re-engages the sentinel"] = sentinel.exists()
    checks["re-engaged sentinel reason records recovery"] = (
        "tamper-recovery" in json.loads(
            sentinel.read_text(encoding="utf-8"))["reason"])

    # 3. Fresh operator clear marker authorizes the absent sentinel.
    sentinel.unlink(missing_ok=True)
    execution_pause.authorize_clear(ttl_hours=1)
    checks["fresh operator clear marker authorizes absence"] = (
        execution_pause.verify_pause_integrity() ==
        "authorized:operator_clear_marker")
    checks["clear marker leaves sentinel absent"] = not sentinel.exists()

    # 4. Stale clear marker does NOT authorize absence.
    marker = home / execution_pause.TRANSITION_MARKER_NAME
    # Produce a signed marker with a stale timestamp (5 hours ago, TTL 1 hour).
    stale_payload = {
        "issued_at": iso(datetime.now(timezone.utc) - timedelta(hours=5)),
        "by": "operator", "action": "authorize-clear", "ttl_hours": 1,
    }
    marker.write_text(execution_pause._signed_marker(stale_payload), encoding="utf-8")
    checks["stale clear marker triggers re-engagement"] = (
        execution_pause.verify_pause_integrity() == "tamper_reengaged")
    marker.unlink()

    # Unsigned JSON can never authorize an absent ESTOP.
    sentinel.unlink(missing_ok=True)
    marker.write_text(json.dumps({
        "issued_at": iso(datetime.now(timezone.utc)),
        "by": "operator", "action": "authorize-clear", "ttl_hours": 1,
    }), encoding="utf-8")
    checks["unsigned clear marker triggers re-engagement"] = (
        execution_pause.verify_pause_integrity() == "tamper_reengaged")
    marker.unlink()

    # 5. Active controlled window (quiesced/open/restoring) with a live owner
    #    authorizes absence. Owner identity: this process.
    from runlock import _process_start_identity
    sentinel.unlink(missing_ok=True)
    live_state = {
        "version": 2, "phase": "open",
        "owner_pid": os.getpid(),
        "owner_process_start_id": _process_start_identity(os.getpid()),
        "estop_b64": "eA==",
    }
    journal.write_text(json.dumps(live_state), encoding="utf-8")
    checks["active controlled window authorizes absence"] = (
        execution_pause.verify_pause_integrity() == "authorized:controlled_window")

    # 6. Window journal whose owner is dead does NOT authorize absence.
    dead_state = dict(live_state, owner_pid=999999,
                      owner_process_start_id="windows-filetime:1")
    journal.write_text(json.dumps(dead_state), encoding="utf-8")
    checks["dead window owner does not authorize absence"] = (
        execution_pause.verify_pause_integrity() == "tamper_reengaged")

    # 7. Restored window journal does not authorize absence.
    sentinel.unlink()  # re-engaged by test 6's tamper recovery
    journal.write_text(json.dumps(dict(live_state, phase="restored")),
                       encoding="utf-8")
    checks["restored window does not authorize absence"] = (
        execution_pause.verify_pause_integrity() == "tamper_reengaged")

    # 8. Canary authorization: single-use consumption semantics.
    journal.unlink()
    sentinel.unlink(missing_ok=True)
    execution_pause.authorize_canary()
    canary_marker = home / execution_pause.CANARY_AUTH_NAME
    checks["canary marker issued"] = canary_marker.is_file()
    data = execution_pause.consume_canary_authorization()
    checks["canary marker consumed once"] = (
        data.get("use") == "single-connectivity-canary" and
        not canary_marker.exists())
    try:
        execution_pause.consume_canary_authorization()
        replay_blocked = False
    except RuntimeError:
        replay_blocked = True
    checks["canary replay is refused"] = replay_blocked

    # 9. Stale canary marker is refused and consumed.
    execution_pause.authorize_canary()
    stale_canary = {
        "issued_at": iso(datetime.now(timezone.utc) - timedelta(hours=2)),
        "by": "operator", "action": "authorize-canary",
        "use": "single-connectivity-canary",
    }
    canary_marker.write_text(execution_pause._signed_marker(stale_canary), encoding="utf-8")
    try:
        execution_pause.consume_canary_authorization()
        stale_blocked = False
    except RuntimeError:
        stale_blocked = True
    checks["stale canary authorization refused and consumed"] = (
        stale_blocked and not canary_marker.exists())

    # 10. Malformed canary marker is refused and consumed.
    execution_pause.authorize_canary()
    canary_marker.write_text("{not json", encoding="utf-8")
    try:
        execution_pause.consume_canary_authorization()
        malformed_blocked = False
    except RuntimeError:
        malformed_blocked = True
    checks["malformed canary marker refused and consumed"] = (
        malformed_blocked and not canary_marker.exists())

    # 10b. Well-formed but unsigned JSON is also refused and consumed.
    canary_marker.write_text(json.dumps({
        "issued_at": iso(datetime.now(timezone.utc)),
        "by": "operator", "action": "authorize-canary",
        "use": "single-connectivity-canary",
    }), encoding="utf-8")
    try:
        execution_pause.consume_canary_authorization()
        unsigned_blocked = False
    except RuntimeError:
        unsigned_blocked = True
    checks["unsigned canary marker refused and consumed"] = (
        unsigned_blocked and not canary_marker.exists())

    # 10c. A valid signature for another purpose cannot be replayed as canary auth.
    wrong_purpose = {
        "issued_at": iso(datetime.now(timezone.utc)),
        "by": "operator", "action": "authorize-clear", "ttl_hours": 1,
    }
    canary_marker.write_text(execution_pause._signed_marker(wrong_purpose),
                             encoding="utf-8")
    try:
        execution_pause.consume_canary_authorization()
        purpose_blocked = False
    except RuntimeError:
        purpose_blocked = True
    checks["wrong-purpose signed marker refused and consumed"] = (
        purpose_blocked and not canary_marker.exists())

    # 10d. Signing failure cannot degrade into an unsigned authorization.
    with mock.patch("operator_auth.sign_marker",
                    side_effect=RuntimeError("signer unavailable")):
        try:
            execution_pause.authorize_canary()
            signing_failed_closed = False
        except RuntimeError:
            signing_failed_closed = True
    checks["signing failure creates no canary authorization"] = (
        signing_failed_closed and not canary_marker.exists())

    # 11. No marker at all: refused.
    try:
        execution_pause.consume_canary_authorization()
        absent_blocked = False
    except RuntimeError:
        absent_blocked = True
    checks["missing canary marker refused"] = absent_blocked

# 12. Real canary CLI now refuses without operator authorization: the flag
#     alone is insufficient. (Model-free: it aborts at the authorization
#     gate BEFORE any key/env/provider call. The temp home carries a
#     stability marker AND an engaged sentinel, so the canary passes its
#     "ESTOP must remain engaged" precondition and is refused by the
#     missing operator marker — the new boundary under test.)
with tempfile.TemporaryDirectory() as td:
    home2 = Path(td) / "home2"
    home2.mkdir()
    (home2 / "config.yaml").write_text("# test home\n", encoding="utf-8")
    (home2 / "ESTOP").write_text('{"reason":"test"}\n', encoding="utf-8")
    os.environ["HERMES_HOME"] = str(home2)
    canary = ROOT / "workspace" / "validation" / "byteplus_connectivity_canary.py"
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-B", str(canary), "--authorize-single-estop-bypass"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env={**os.environ, "ARK_API_KEY": "dummy-not-used"})
    # SystemExit with a string prints to stderr, not stdout.
    checks["canary CLI aborts without operator marker"] = (
        proc.returncode != 0 and
        "operator canary authorization" in (proc.stdout + proc.stderr))

# 12b. End-to-end canary quiescence gate: WITH a valid operator marker but a
#      dirty process inventory injected, the canary must refuse at the
#      process-quiescence gate AFTER consuming the one-shot marker (model-free:
#      it aborts before any provider call; the dirty inventory simulates a
#      live mutation-capable development process).
with tempfile.TemporaryDirectory() as td12:
    home3 = Path(td12) / "home3"
    home3.mkdir()
    (home3 / "config.yaml").write_text("# test home\n", encoding="utf-8")
    (home3 / "ESTOP").write_text('{"reason":"test"}\n', encoding="utf-8")
    inv = Path(td12) / "inventory.json"
    inv.write_text(json.dumps([{
        "pid": 424242, "name": "claude.exe",
        "cmdline": "claude.exe --permission-mode bypassPermissions",
        "cwd": str(ROOT), "create_time": 12345.0}]), encoding="utf-8")
    os.environ["HERMES_HOME"] = str(home3)
    os.environ["AGI_PROCESS_INVENTORY_FILE"] = str(inv)
    execution_pause.authorize_canary()
    proc = subprocess.run(
        [sys.executable, "-B", str(canary), "--authorize-single-estop-bypass"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env={**os.environ, "ARK_API_KEY": "dummy-not-used"})
    combined = proc.stdout + proc.stderr
    checks["canary CLI refuses with live dev process"] = (
        proc.returncode != 0 and "mutation-capable" in combined)
    checks["canary refusal names the offending pid"] = (
        "pid=424242" in combined)
    checks["refused canary still burns the one-shot marker"] = (
        not (home3 / execution_pause.CANARY_AUTH_NAME).exists())
    os.environ.pop("AGI_PROCESS_INVENTORY_FILE", None)

# 13. batch_runner and run_task call verify_pause_integrity (source check).
    batch_src = (ROOT / "orchestrator" / "batch_runner.py").read_text(encoding="utf-8")
    run_task_src = (ROOT / "orchestrator" / "run_task.py").read_text(encoding="utf-8")
    checks["batch_runner gates on verify_pause_integrity"] = (
        "verify_pause_integrity" in batch_src)
    checks["run_task gates on verify_pause_integrity"] = (
        "verify_pause_integrity" in run_task_src)

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("estop tamper failures: " + ", ".join(failed))
