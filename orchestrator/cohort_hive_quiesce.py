"""Hive quiescence for the controlled isolation window (boundary hardening).

The cohort isolation window already quiesces AGI's own dispatchers (scheduled
tasks, Hermes cron, gateway) before clearing ESTOP.  This module extends the
same transactional discipline to the Munder Difflin hive, because a live
window is only safe when no hive agent can concurrently mutate the AGI
repository.

Design contract:

* SNAPSHOT-ONLY for processes.  This module never kills hive processes.  The
  operator stops hive agents (or the window is opened when they are verifiably
  idle/stopped); the window REFUSES TO OPEN otherwise.  Fail closed beats
  blind killing — "do not blindly kill unrelated processes" is a hard
  requirement, and process-kill logic on a misidentified PID is exactly the
  F1/E23 hazard class this harness exists to prevent.
* The IsolationBackend protocol gains two methods (snapshot_hive /
  ensure_hive_quiesced).  LiveBackend implements them against the roster +
  fleet/log files under the Munder harness home.  Tests use fakes.
* Restoration is verification-only: the window never started, killed, or
  suspended anything, so the only restoration duty is to re-verify that hive
  activity did not leave the tree dirty mid-window and to record the check.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

DEFAULT_HIVE_HOME = Path(r"S:\MunderState\AGI_like")

# Roster/fleet fields that describe an agent capable of mutating the repo.
_ACTIVE_STATUSES = frozenset({"active", "running", "working", "busy", "starting up"})
_IDLE_STATUSES = frozenset({"idle", "stopped", "archived", "offline"})


class HiveQuiesceError(RuntimeError):
    """Hive activity cannot be verified safe for a controlled window."""


@dataclass(frozen=True)
class HiveAgentState:
    agent_id: str
    status: str
    action: str
    cwd: str
    capable_of_repo_mutation: bool


def _default_hive_home() -> Path:
    override = os.environ.get("MUNDER_HARNESS_HOME", "").strip()
    return Path(override) if override else DEFAULT_HIVE_HOME


def _active_status(status: str) -> bool:
    """True when a roster status string indicates live agent activity."""
    s = (status or "").strip().lower()
    return s in _ACTIVE_STATUSES


def _mutation_capable(agent: dict) -> bool:
    """True when an agent runs with a shell/toolset that can edit the AGI tree.

    cwd anywhere in the AGI repo, or a Claude/Codex/Gemini-class CLI command,
    marks the agent as mutation-capable.  Ollama-only local workers without
    repo access are not, but are still snapshotted for the audit record.
    """
    cwd = str(agent.get("cwd") or "")
    command = str(agent.get("command") or "")
    repo_root = str(Path(r"S:\AGI_like")).lower()
    if cwd.lower().startswith(repo_root):
        return True
    return bool(re.search(r"\b(claude|codex|gemini|cursor)\b", command, re.I))


def _read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _agent_states_from_roster(roster: dict | list | None,
                              fleet: dict | list | None) -> list[HiveAgentState]:
    agents: list[dict] = []
    if isinstance(roster, dict):
        raw = roster.get("agents")
        if isinstance(raw, list):
            agents = [a for a in raw if isinstance(a, dict)]
    elif isinstance(roster, list):
        agents = [a for a in roster if isinstance(a, dict)]
    # fleet.json is the live per-agent refresh; merge status/action by id.
    fleet_by_id: dict[str, dict] = {}
    if isinstance(fleet, dict):
        raw = fleet.get("agents")
        if isinstance(raw, list):
            for entry in [e for e in raw if isinstance(e, dict)]:
                agent_id = str(entry.get("id") or "")
                if agent_id:
                    fleet_by_id[agent_id] = entry
    states: list[HiveAgentState] = []
    for agent in agents:
        agent_id = str(agent.get("id") or agent.get("name") or "unknown")
        live = fleet_by_id.get(agent_id, {})
        status = str(live.get("status") or agent.get("status") or "unknown")
        action = str(live.get("action") or agent.get("action") or "")
        cwd = str(agent.get("cwd") or live.get("cwd") or "")
        states.append(HiveAgentState(
            agent_id=agent_id, status=status, action=action, cwd=cwd,
            capable_of_repo_mutation=_mutation_capable(agent)))
    return states


class HiveProbe(Protocol):
    """Callable returning the current roster/fleet snapshot, fail-closed."""
    def __call__(self) -> tuple[dict | list | None, dict | list | None]: ...


def live_roster_probe(hive_home: Path | None = None) -> HiveProbe:
    """Read roster.json + fleet.json from the hive home; unparsable = fail."""
    home = hive_home or _default_hive_home()

    def probe() -> tuple[dict | list | None, dict | list | None]:
        roster = _read_json(home / "roster.json")
        if roster is None:
            raise HiveQuiesceError(
                f"hive roster unreadable at {home / 'roster.json'} — "
                "cannot verify hive quiescence; refusing to open the window")
        fleet = _read_json(home / "fleet.json")
        # fleet.json is refreshed continuously; absence is treated as
        # unknown-but-live and fails closed, NOT as quiet.
        if fleet is None:
            raise HiveQuiesceError(
                f"hive fleet state unreadable at {home / 'fleet.json'} — "
                "cannot verify hive quiescence; refusing to open the window")
        return roster, fleet

    return probe


def _idle_state(state: HiveAgentState) -> bool:
    if state.capable_of_repo_mutation:
        return not _active_status(state.status)
    return True  # non-mutation-capable agents cannot dirty the tree


def snapshot_hive_state(probe: HiveProbe) -> list[HiveAgentState]:
    """Take the current hive snapshot through the probe (fail-closed)."""
    try:
        roster, fleet = probe()
    except HiveQuiesceError:
        raise
    except Exception as exc:  # unreadable/partial hive state is unknown state
        raise HiveQuiesceError(
            f"hive snapshot probe failed ({type(exc).__name__}: {exc}); "
            "cannot verify hive quiescence — refusing to open the window"
        ) from exc
    states = _agent_states_from_roster(roster, fleet)
    if not states:
        raise HiveQuiesceError(
            "hive roster parsed but listed no agents — ambiguous state; "
            "refusing to open the window")
    return states


def hive_quiesced(states: list[HiveAgentState]) -> bool:
    """True when NO mutation-capable agent is in an active status."""
    return all(_idle_state(s) for s in states)


def ensure_hive_quiesced(probe: HiveProbe,
                          emit_event: Callable[..., None] | None = None) -> dict:
    """Verify hive quiescence for a controlled window; fail closed.

    Returns an audit record; raises HiveQuiesceError when any
    mutation-capable hive agent is active or state is ambiguous.  Never
    kills anything; never mutates hive state.  emit_event (optional) is the
    health_events.emit-style audit sink used for refusals.
    """
    states = snapshot_hive_state(probe)
    offenders = [s for s in states if not _idle_state(s)]
    record = {
        "agents": [
            {"id": s.agent_id, "status": s.status, "cwd": s.cwd,
             "mutation_capable": s.capable_of_repo_mutation}
            for s in states
        ],
        "quiesced": not offenders,
        "offenders": [s.agent_id for s in offenders],
    }
    if offenders:
        message = (f"controlled window refused: active mutation-capable hive "
                   f"agents: {record['offenders']}")
        if emit_event is not None:
            try:
                emit_event("hive_quiesce", "window_refused",
                           HiveQuiesceError(message), **{
                               "offenders": record["offenders"]})
            except Exception:
                pass
        raise HiveQuiesceError(message)
    return record


def tree_taint_report(status_at_open: list[str] | None,
                      status_now_provider: Callable[[], list[str]] | None = None,
                      emit_event: Callable[..., None] | None = None) -> dict:
    """Post-window audit: did the AGI tree change during the window?

    Compares porcelain status lines at open vs. now.  New paths that appeared
    during the window are 'taint'.  Returns a report; never raises and never
    blocks restoration — dispatchers and ESTOP restoration are completed
    first and unconditionally; this is the audit layer on top.
    """
    if status_at_open is None:
        report = {"verified": False, "new_paths": [], "reason": "no open snapshot"}
        if emit_event is not None:
            try:
                emit_event("hive_quiesce", "tree_taint_unverifiable",
                           RuntimeError(report["reason"]))
            except Exception:
                pass
        return report
    if status_now_provider is None:
        def status_now_provider() -> list[str]:
            import subprocess
            proc = subprocess.run(
                ["git", "-C", str(Path(__file__).resolve().parents[1]),
                 "status", "--porcelain=v1"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30)
            if proc.returncode:
                raise HiveQuiesceError(f"git status failed: {proc.stderr.strip()}")
            return [line[3:] for line in proc.stdout.splitlines() if line.strip()]
    try:
        now = status_now_provider()
    except HiveQuiesceError:
        report = {"verified": False, "new_paths": [],
                  "reason": "git status unavailable at restore"}
        if emit_event is not None:
            try:
                emit_event("hive_quiesce", "tree_taint_unverifiable",
                           RuntimeError(report["reason"]))
            except Exception:
                pass
        return report
    new_paths = [p for p in now if p not in set(status_at_open)]
    report = {"verified": True, "new_paths": new_paths}
    if new_paths and emit_event is not None:
        try:
            emit_event("hive_quiesce", "tree_tainted",
                       HiveQuiesceError("AGI tree dirtied during window"),
                       paths=new_paths[:50])
        except Exception:
            pass
    return report


def main() -> int:
    """CLI: report hive quiescence state without opening anything."""
    import sys
    try:
        record = ensure_hive_quiesced(live_roster_probe())
    except HiveQuiesceError as exc:
        print(json.dumps({"quiesced": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())