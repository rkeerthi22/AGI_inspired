"""Unified read-only operator CLI for the AGI_like harness.

`agi status`, `agi health --model-free`, and `agi preflight canary` compose
existing authoritative readers into one operator-facing view. This module is
OBSERVATIONAL AND DIAGNOSTIC ONLY:

  * It never becomes a second safety authority. ESTOP, runlock, isolation,
    and canary admission remain owned by their existing modules; a green
    "preflight" here authorizes nothing.
  * It never mutates ESTOP, the canary marker, isolation state, the batch
    lock, ACTIVE_WORK, the ledger databases, runs/, or Git state.
  * `agi health --model-free` runs the existing test gate as a subprocess
    (tests are read-only by contract; containment suites run in a disposable
    fixture repository).
  * It never contacts a provider or executes a mission. Provider state is
    reported from RECORDED health events only, marked UNKNOWN when absent.

Unknown states are reported as "unknown" -- never guessed as pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"

if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

import secrets as credential_vault

RUNS = ROOT / "runs"
HEALTH_EVENTS = RUNS / "health_events.jsonl"
ACTIVE_WORK = ROOT / "docs" / "ACTIVE_WORK.json"
TIERS_MANIFEST = ROOT / "tests" / "tiers.json"
BATCH_LOCK = RUNS / ".batch.lock"
CANARY_SCRIPT = ROOT / "workspace" / "validation" / "byteplus_connectivity_canary.py"

# ---------------------------------------------------------------- helpers ---

_UNKNOWN = "unknown"


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _git(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _safe(func: Callable[[], Any], fail: Any = _UNKNOWN) -> Any:
    """Run a reader; any failure surfaces as the UNKNOWN sentinel."""
    try:
        return func()
    except Exception:
        return fail


def _provider_secret_present(provider: str, env_name: str) -> bool:
    """Read-only presence check aligned with the runtime secret resolver."""
    if credential_vault.get_api_key(provider) is not None:
        return True
    try:
        import execution_pause
        loaded = dotenv_values(execution_pause.estop_path().parent / ".env")
        return bool(str(loaded.get(env_name) or "").strip())
    except Exception:
        return False


# ------------------------------------------------------------------ status --

def _git_state() -> dict:
    code, out, err = _git(["rev-parse", "HEAD"])
    head = out if code == 0 else _UNKNOWN
    code, out, _ = _git(["status", "--porcelain=v1", "--untracked-files=all"])
    lines = [ln[3:] for ln in out.splitlines() if ln.strip()] if code == 0 else []
    tree_clean = code == 0 and not lines
    code, out, _ = _git(["rev-list", "--left-right", "--count", "origin/master...master"])
    if code == 0:
        try:
            behind_s, ahead_s = out.split()
            behind, ahead = int(behind_s), int(ahead_s)
        except ValueError:
            behind, ahead = None, None
    else:
        behind, ahead = None, None
    code, out, _ = _git(["branch", "--show-current"])
    return {
        "head": head,
        "branch": out if code == 0 and out else _UNKNOWN,
        "tree_clean": tree_clean,
        "changed_paths": lines,
        "upstream_divergence": {"ahead": ahead, "behind": behind},
    }


def _continuity_state() -> dict:
    import continuity
    try:
        result = continuity.recover()
        discrepancies = result.get("discrepancies") or []
        brief = result.get("brief") or {}
        return {
            "revision": brief.get("brief_revision", _UNKNOWN),
            "valid": len(discrepancies) == 0,
            "discrepancies": discrepancies,
            "status": brief.get("status", _UNKNOWN),
            "task_phase": (brief.get("task") or {}).get("phase", _UNKNOWN),
            "recovery": "complete",
            "brief_available": True,
            "error": None,
        }
    except Exception as recovery_error:
        # A parseable cached brief is useful diagnostic metadata, but it is not
        # proof that live repository recovery succeeded. Preserve only that
        # metadata and surface validity as UNKNOWN so every admission caller
        # fails closed instead of converting a recovery error into PASS.
        try:
            brief = continuity.load_current()
        except Exception:
            brief = {}
        return {
            "revision": brief.get("brief_revision", _UNKNOWN),
            "valid": None,
            "discrepancies": [],
            "status": brief.get("status", _UNKNOWN),
            "task_phase": (brief.get("task") or {}).get("phase", _UNKNOWN),
            "recovery": "error",
            "brief_available": bool(brief),
            # Exception class is sufficient for diagnosis without leaking Git
            # stderr, filesystem details, or any sensitive message contents.
            "error": type(recovery_error).__name__,
        }


def _estop_state() -> dict:
    import execution_pause
    engaged = _safe(execution_pause.pause_engaged, True)  # fail closed -> engaged
    # Purely observational: verify_pause_integrity() RE-ENGAGES the sentinel on
    # tamper, which is a write -- a status command must never do that. When the
    # sentinel is absent, classify the absence without re-engaging it.
    if engaged is False:
        authorized, how = _safe(lambda: execution_pause.clear_is_authorized(),
                                (False, "unknown"))
        integrity = f"authorized:{how}" if authorized else "unauthorized_absence"
    else:
        integrity = "engaged"
    canary_marker = execution_pause.canary_authorization_path()
    return {
        "engaged": engaged,
        "integrity": integrity,
        "canary_authorization_marker_present": _safe(lambda: canary_marker.is_file()),
    }


def _isolation_state() -> dict:
    import execution_pause
    journal = execution_pause._cohort_journal_path()
    data = _read_json(journal)
    if not isinstance(data, dict):
        return {"phase": "restored" if not journal.is_file() else _UNKNOWN,
                "journal_present": journal.is_file()}
    phase = data.get("phase", _UNKNOWN)
    owner_alive = None
    if phase in ("quiesced", "open", "restoring"):
        owner_alive = _safe(lambda: execution_pause._window_owner_alive(data))
    return {"phase": phase, "journal_present": True,
            "owner_alive": owner_alive,
            "owner_pid": data.get("owner_pid")}


def _runlock_state() -> dict:
    import runlock
    if not BATCH_LOCK.is_file():
        return {"engaged": False, "present": False}
    lock = _safe(runlock._read_lock, None)
    if lock is None:
        return {"engaged": True, "present": True, "state": "corrupt"}
    try:
        stale = runlock._is_stale(BATCH_LOCK)
    except Exception:
        stale = None
    return {"engaged": True, "present": True, "state": "stale" if stale else "held",
            "pid": lock.get("pid"), "started_at": lock.get("started_at"),
            "lock_id": lock.get("lock_id")}


def _active_work_state() -> dict:
    data = _read_json(ACTIVE_WORK)
    if not isinstance(data, dict):
        return {"parseable": False, "owners": _UNKNOWN}
    owners = []
    for agent in data.get("active_agents") or []:
        if not isinstance(agent, dict):
            continue
        if agent.get("status") == "in_progress" and (agent.get("owned_paths") or []):
            owners.append({"agent": agent.get("agent"), "task_id": agent.get("task_id"),
                           "owned_paths": agent.get("owned_paths")})
    return {"parseable": True, "owners": owners,
            "updated": data.get("last_updated", _UNKNOWN)}


def _munder_quiescence() -> dict:
    import cohort_hive_quiesce
    try:
        records, source = cohort_hive_quiesce.process_inventory()
        offenders = cohort_hive_quiesce.scan_mutation_processes(records)
        return {"quiesced": not offenders, "source": source,
                "offenders": len(offenders),
                "offender_details": [
                    {"pid": o.get("pid"), "matched_by": o.get("matched_by")}
                    for o in offenders[:10]]}
    except Exception as exc:
        # Fail closed: an unverifiable inventory is never reported as quiesced.
        return {"quiesced": False, "source": _UNKNOWN, "offenders": _UNKNOWN,
                "error": f"{type(exc).__name__}: {exc}"}


def _backup_state() -> dict:
    import backup
    latest = {}
    for name, src in backup.SOURCES.items():
        candidates = sorted((ROOT / "backups").glob(f"{name}_*.db"))
        if not candidates:
            latest[name] = {"present": False}
            continue
        newest = candidates[-1]
        stat = _safe(lambda: newest.stat())
        age_hours = (_utc_timestamp_seconds()
                     - _safe(lambda: stat.st_mtime, 0)) / 3600 if stat else None
        latest[name] = {
            "present": True, "newest": newest.name,
            "age_hours": round(age_hours, 1) if age_hours is not None else _UNKNOWN,
        }
    offsite = _safe(backup.offsite_dir)
    return {"databases": latest,
            "offsite_configured": offsite is not None,
            "offsite_path": str(offsite) if offsite else None}


def _utc_timestamp_seconds() -> float:
    import time
    return time.time()


def _provider_state() -> dict:
    """RECORDED provider health only. No probe, no network, no secrets."""
    events = []
    try:
        text = HEALTH_EVENTS.read_text(encoding="utf-8")
        events = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    except (OSError, ValueError):
        events = []
    # newest-last recorded event per subsystem
    latest: dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        subsystem = str(event.get("subsystem") or "")
        if subsystem:
            latest[subsystem] = event
    out = {}
    for subsystem, event in sorted(latest.items()):
        error = event.get("error")
        out[subsystem] = {
            "recorded_at": event.get("timestamp") or event.get("ts") or _UNKNOWN,
            "ok": False if error else True,
            "error": str(error)[:120] if error else None,
            "probed": False,
            "note": "recorded health events only; no live probe performed",
        }
    return {"probed": False, "note": "no provider contact; recorded events only",
            "recorded_subsystems": out}


def collect_status() -> dict:
    return {
        "command": "status",
        "generated_at": _utc_now(),
        "git": _git_state(),
        "continuity": _continuity_state(),
        "estop": _estop_state(),
        "isolation": _isolation_state(),
        "runlock": _runlock_state(),
        "active_work": _active_work_state(),
        "munder_quiescence": _munder_quiescence(),
        "backup": _backup_state(),
        "provider": _provider_state(),
    }


# ------------------------------------------------------------------ health --

def _db_readonly_check(db: Path) -> dict:
    if not db.is_file():
        return {"present": False}
    uri = f"file:{db.as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            wal = con.execute("PRAGMA journal_mode").fetchone()[0]
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        finally:
            con.close()
        return {"present": True, "quick_check_ok": ok, "journal_mode": wal,
                "tables": len(tables)}
    except sqlite3.Error as exc:
        return {"present": True, "quick_check_ok": False, "error": str(exc)[:120]}


def collect_health_model_free() -> dict:
    db_reports = {
        "ledger": _db_readonly_check(ROOT / "ledger" / "ledger.db"),
        "ledgerbook": _db_readonly_check(ROOT / "memory" / "ledgerbook.db"),
        "predictions": _db_readonly_check(ROOT / "prediction_machine" / "data" / "predictions.db"),
    }
    continuity = _continuity_state()
    # The test gate runs as a subprocess in the unit/containment/integration
    # tiers only (never the live tier). Containment suites execute inside a
    # disposable fixture repository created by run_all.py itself.
    gate = _run_model_free_gate()
    return {
        "command": "health",
        "mode": "model-free",
        "generated_at": _utc_now(),
        "databases": db_reports,
        "continuity": continuity,
        "test_gate": gate,
    }


def _run_model_free_gate() -> dict:
    if BATCH_LOCK.is_file():
        return {"ran": False, "ok": False,
                "reason": "batch lock present; refusing to start the gate"}
    cmd = [sys.executable, "-B", str(ROOT / "tests" / "run_all.py")]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ran": False, "ok": False, "error": str(exc)[:200]}
    last = (proc.stdout or "").strip().splitlines()
    summary = last[-1] if last else ""
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*suites green", summary)
    return {
        "ran": True,
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "summary": summary,
        "suites_passed": int(m.group(1)) if m else None,
        "suites_total": int(m.group(2)) if m else None,
    }


# --------------------------------------------------------------- preflight --

def _canary_prerequisites() -> list[dict]:
    """Evaluate every known canary prerequisite. Read-only; authorize nothing."""
    checks: list[dict] = []

    def add(name: str, ok: bool | None, detail: str, blocker: bool) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "blocker": blocker})

    # 1. ESTOP must be ENGAGED for the scoped-bypass canary design.
    estop = _estop_state()
    add("estop_engaged", estop.get("engaged") is True,
        f"engaged={estop.get('engaged')} integrity={estop.get('integrity')}", True)

    # 2. No may-be-consumed authorization marker may pre-exist (single-use).
    add("no_pending_canary_marker", estop.get("canary_authorization_marker_present") is False,
        f"marker_present={estop.get('canary_authorization_marker_present')}", True)

    # 3. Canary script exists.
    add("canary_script_present", CANARY_SCRIPT.is_file(), str(CANARY_SCRIPT.relative_to(ROOT)), True)

    # 4. Provider configured in models.yaml (config read only; no secrets read).
    provider_ok: bool | None = None
    detail = "models.yaml unreadable"
    try:
        import yaml  # noqa: F401
        with open(ROOT / "config" / "models.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        provider = (cfg.get("providers") or {}).get("byteplus_coding")
        provider_ok = isinstance(provider, dict) and bool(provider.get("endpoint"))
        detail = f"provider configured={provider_ok}"
    except Exception as exc:
        provider_ok = None
        detail = f"models.yaml unreadable: {type(exc).__name__}"
    add("provider_configured", provider_ok, detail, True)

    # 5. Credential presence only. The vault reader never reports the value.
    key_present = _provider_secret_present("byteplus_coding", "ARK_API_KEY")
    add("ark_api_key_present_in_env", key_present,
        "Credential Manager, environment, or Hermes private .env presence only; value never reported", True)

    # 6. Batch lock must be free.
    add("batch_lock_free", not BATCH_LOCK.is_file(),
        f"lock present={BATCH_LOCK.is_file()}", True)

    # 7. Munder/process quiescence (engine-independent).
    quiesce = _munder_quiescence()
    add("munder_process_quiescence", quiesce.get("quiesced") is True,
        f"source={quiesce.get('source')} offenders={quiesce.get('offenders')}", True)

    # 8. Isolation window must be closed/restored.
    iso = _isolation_state()
    add("isolation_window_closed",
        iso.get("phase") in ("restored", None) or iso.get("journal_present") is False,
        f"phase={iso.get('phase')}", True)

    # 9. Working tree state -- informational, not a blocker by itself.
    git_state = _git_state()
    add("git_tree_state_informational", None,
        f"clean={git_state['tree_clean']} changed={len(git_state['changed_paths'])} "
        f"ahead={git_state['upstream_divergence']['ahead']}", False)

    # 10. Continuity valid.
    cont = _continuity_state()
    valid = cont.get("valid")
    continuity_ok = True if valid is True else (False if valid is False else None)
    add("continuity_valid", continuity_ok,
        f"revision={cont.get('revision')} recovery={cont.get('recovery')} "
        f"discrepancies={len(cont.get('discrepancies') or [])} "
        f"error={cont.get('error')}", True)

    return checks


def collect_preflight_canary() -> dict:
    checks = _canary_prerequisites()
    blockers = [c for c in checks if c["blocker"] and c["ok"] is not True]
    unknowns = [c for c in checks if c["ok"] is None]
    return {
        "command": "preflight",
        "target": "canary",
        "generated_at": _utc_now(),
        "diagnostic_only": True,
        "authorized": False,
        "checks": checks,
        "blockers": blockers,
        "unknowns": unknowns,
        "safe_to_proceed": not blockers,
    }


# ------------------------------------------------------------------ output --

def _render_status(data: dict) -> str:
    lines = ["=== AGI STATUS ==="]
    g = data.get("git") or {}
    lines.append(f"git: head={str(g.get('head'))[:12]} branch={g.get('branch')} "
                 f"clean={g.get('tree_clean')} "
                 f"ahead/behind={g.get('upstream_divergence', {}).get('ahead')}/"
                 f"{g.get('upstream_divergence', {}).get('behind')}")
    if g.get("changed_paths"):
        lines.append(f"git: {len(g['changed_paths'])} changed path(s)")
    c = data.get("continuity") or {}
    lines.append(f"continuity: revision={c.get('revision')} valid={c.get('valid')} "
                 f"status={c.get('status')} phase={c.get('task_phase')}")
    for d in c.get("discrepancies") or []:
        lines.append(f"continuity discrepancy: {d.get('field')} -> {d.get('live')}")
    e = data.get("estop") or {}
    lines.append(f"estop: engaged={e.get('engaged')} integrity={e.get('integrity')}")
    lines.append(f"canary marker present: {e.get('canary_authorization_marker_present')}")
    i = data.get("isolation") or {}
    lines.append(f"isolation: phase={i.get('phase')} journal={i.get('journal_present')}")
    r = data.get("runlock") or {}
    if r.get("engaged"):
        lines.append(f"batch lock: HELD (state={r.get('state')} pid={r.get('pid')})")
    else:
        lines.append("batch lock: free")
    a = data.get("active_work") or {}
    lines.append(f"active work: parseable={a.get('parseable')} owners={len(a.get('owners') or [])}")
    for o in a.get("owners") or []:
        lines.append(f"  owner: {o.get('agent')} ({o.get('task_id')}) -> "
                     f"{len(o.get('owned_paths') or [])} path(s)")
    q = data.get("munder_quiescence") or {}
    lines.append(f"munder quiescence: quiesced={q.get('quiesced')} "
                 f"source={q.get('source')} offenders={q.get('offenders')}")
    for o in q.get("offender_details") or []:
        lines.append(f"  offender: pid={o.get('pid')} matched_by={o.get('matched_by')}")
    b = data.get("backup") or {}
    for name, info in (b.get("databases") or {}).items():
        if info.get("present"):
            lines.append(f"backup {name}: age={info.get('age_hours')}h "
                         f"file={info.get('newest')}")
        else:
            lines.append(f"backup {name}: NONE")
    lines.append(f"backup offsite configured: {b.get('offsite_configured')}")
    p = data.get("provider") or {}
    lines.append(f"provider: probed={p.get('probed')} "
                 f"recorded subsystems={list(p.get('recorded_subsystems') or [])}")
    return "\n".join(lines)


def _render_health(data: dict) -> str:
    lines = ["=== AGI HEALTH (MODEL-FREE) ==="]
    for name, report in (data.get("databases") or {}).items():
        if not report.get("present"):
            lines.append(f"db {name}: ABSENT")
        elif report.get("quick_check_ok"):
            lines.append(f"db {name}: quick_check=ok journal={report.get('journal_mode')} "
                         f"tables={report.get('tables')}")
        else:
            lines.append(f"db {name}: quick_check=FAIL {report.get('error', '')}")
    c = data.get("continuity") or {}
    lines.append(f"continuity: revision={c.get('revision')} valid={c.get('valid')}")
    gate = data.get("test_gate") or {}
    if gate.get("ran"):
        lines.append(f"model-free test gate: {'PASS' if gate.get('ok') else 'FAIL'} "
                     f"{gate.get('summary', '')}")
    else:
        lines.append(f"model-free test gate: NOT RUN ({gate.get('reason') or gate.get('error')})")
    lines.append("provider/network calls: none (model-free contract)")
    return "\n".join(lines)


def _render_preflight(data: dict) -> str:
    lines = ["=== AGI PREFLIGHT: CANARY (DIAGNOSTIC ONLY) ==="]
    lines.append("This preflight authorizes nothing. It never creates or consumes")
    lines.append("canary authorization and never contacts a provider.")
    for c in data.get("checks") or []:
        mark = "PASS" if c["ok"] is True else ("FAIL" if c["ok"] is False else "UNKNOWN")
        flag = " [blocker]" if c["blocker"] else ""
        lines.append(f"[{mark}]{flag} {c['check']}: {c['detail']}")
    if data.get("blockers"):
        lines.append(f"")
        lines.append(f"RESULT: BLOCKED ({len(data['blockers'])} blocker(s))")
        lines.append("Resolve blockers, then have the OPERATOR authorize the canary")
        lines.append("with the existing single-use marker procedure.")
    else:
        lines.append("")
        lines.append("RESULT: no blockers observed. Canary execution still requires")
        lines.append("operator-issued single-use authorization; this output is not it.")
    return "\n".join(lines)


_RENDERERS = {"status": _render_status, "health": _render_health,
             "preflight": _render_preflight}


def _output(data: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(_RENDERERS[data["command"]](data))
    if data["command"] == "preflight":
        return 1 if data.get("blockers") else 0
    if data["command"] == "health":
        gate = data.get("test_gate") or {}
        return 0 if gate.get("ok") else 1
    return 0


# -------------------------------------------------------------------- main --

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agi",
        description="Read-only operator view over the AGI_like harness. "
                    "Observational and diagnostic only; never a safety authority.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="consolidated read-only state")
    p_status.add_argument("--json", action="store_true", help="stable JSON output")

    p_health = sub.add_parser("health", help="health checks")
    p_health.add_argument("--model-free", action="store_true", required=True,
                           help="run the model-free gate (no providers, no network)")
    p_health.add_argument("--json", action="store_true", help="stable JSON output")

    p_pre = sub.add_parser("preflight", help="diagnostic preflight")
    p_pre.add_argument("target", choices=["canary"], help="preflight target")
    p_pre.add_argument("--json", action="store_true", help="stable JSON output")

    args = parser.parse_args(argv)
    if args.command == "status":
        data = collect_status()
    elif args.command == "health":
        data = collect_health_model_free()
    else:
        data = collect_preflight_canary()
    return _output(data, args.json)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
