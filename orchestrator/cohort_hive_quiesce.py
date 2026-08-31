"""Hive quiescence for the controlled isolation window (boundary hardening).

The cohort isolation window already quiesces AGI's own dispatchers (scheduled
tasks, Hermes cron, gateway) before clearing ESTOP.  This module extends the
same transactional discipline to the Munder Difflin hive, because a live
window is only safe when no hive agent can concurrently mutate the AGI
repository.

Design contract (revised 2026-08-31 after Codex review #2):

* SNAPSHOT-ONLY for processes.  This module never kills hive processes.  The
  operator stops hive agents (or the window is opened when they are verifiably
  idle/stopped); the window REFUSES TO OPEN otherwise.  Fail closed beats
  blind killing.
* FLEET DATA never means "idle" by absence.  A missing or unparsable fleet
  payload forces a fallback: an engine-independent Windows process inventory
  must POSITIVELY prove that no relevant Munder development process can
  mutate S:\\AGI_like.  A fleet payload that exists but is stale, or lacks a
  usable timestamp, fails closed unconditionally - a stale heartbeat is not
  proof of quiet, and the process inventory may not override it.
* STATUS/ACTION CLASSIFICATION is explicit and closed-world: only statuses
  in the recognized idle set AND actions in the recognized quiescent set AND
  no recent fleet activity count as quiet.  Unknown statuses, malformed data,
  ambiguous states, non-empty work actions, or recent fleet activity fail
  closed.
* PROCESS IDENTITY uses more than the executable name: PID, process start
  time, full command line, canonical working directory, and the matched
  reason.  Generic shells (bash/python) are never flagged on their own; a
  dev-CLI binary is flagged only when linked to the AGI repo or to hive
  markers; the Munder host app is always flagged.
* CANARY DEFENSE-IN-DEPTH: the connectivity canary additionally refuses while
  any mutation-capable development process is live.  Current canary
  authorization is a scoped one-shot capability, not cryptographic human
  authentication.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

DEFAULT_HIVE_HOME = Path(r"S:\MunderState\AGI_like")
DEFAULT_REPO_ROOT = Path(r"S:\AGI_like")

# Roster/fleet fields that describe an agent capable of mutating the repo.
_ACTIVE_STATUSES = frozenset({"active", "running", "working", "busy", "starting up"})
_IDLE_STATUSES = frozenset({"idle", "stopped", "archived", "offline"})
# Actions that do not indicate work/activity.  Anything else non-empty fails.
_QUIESCENT_ACTIONS = frozenset({"", "awaiting", "idle", "none", "waiting", "-"})

# Fleet freshness and recency thresholds (seconds).
FLEET_MAX_AGE_SECONDS = 120
ACTIVE_RECENCY_SECONDS = 60

# Candidate fleet locations under the hive home, most canonical first.
FLEET_CANDIDATE_PATHS = ("hive/fleet.json", "fleet.json")

# Process-inventory matching.
DEV_CLI_BINARIES = frozenset({
    "claude", "claude.exe", "claude.cmd",
    "codex", "codex.exe",
    "gemini", "gemini.exe",
    "cursor", "cursor.exe",
})
HIVE_MARKER_NEEDLES = (
    "cth-hook", "hive-proxy", "hive-node", "enforce.js",
    "munderstate", "munder-difflin",
)
_MUNDER_HOST_TOKEN = "munder"

# Env override: a JSON file containing a list of process records, used by
# tests (and operator dry-runs) instead of the live psutil inventory.
PROCESS_INVENTORY_FILE_ENV = "AGI_PROCESS_INVENTORY_FILE"


class HiveQuiesceError(RuntimeError):
    """Hive activity cannot be verified safe for a controlled window."""


@dataclass(frozen=True)
class HiveAgentState:
    agent_id: str
    status: str
    action: str
    cwd: str
    capable_of_repo_mutation: bool
    recency_seconds: float | None = None
    breaker: str | None = None


def _default_hive_home() -> Path:
    override = os.environ.get("MUNDER_HARNESS_HOME", "").strip()
    return Path(override) if override else DEFAULT_HIVE_HOME


def _default_repo_root() -> Path:
    override = os.environ.get("MUNDER_AGI_REPO", "").strip()
    return Path(override) if override else DEFAULT_REPO_ROOT


def _active_status(status: str) -> bool:
    """True when a roster status string indicates live agent activity."""
    s = (status or "").strip().lower()
    return s in _ACTIVE_STATUSES


def classify_agent_state(state: HiveAgentState) -> str:
    """Classify one agent: 'quiescent' | 'active' | 'unknown'.

    Closed-world: only explicitly recognized idle/stopped statuses with a
    quiescent action (and no recent fleet activity, and a healthy breaker
    when a breaker is reported) may count as quiescent.  Everything else is
    'active' or 'unknown', and both fail closed for mutation-capable agents.
    """
    if not state.capable_of_repo_mutation:
        return "quiescent"  # cannot dirty the AGI tree either way
    status = (state.status or "").strip().lower()
    action = (state.action or "").strip().lower()
    if status in _ACTIVE_STATUSES:
        return "active"
    if status not in _IDLE_STATUSES:
        return "unknown"
    if action not in _QUIESCENT_ACTIONS:
        return "active"  # non-empty action indicating work/activity
    if (state.recency_seconds is not None
            and state.recency_seconds <= ACTIVE_RECENCY_SECONDS):
        return "active"  # fleet heartbeat shows work moments ago
    if state.breaker is not None and str(state.breaker).strip().lower() != "healthy":
        return "unknown"  # tripped/unknown breaker is an ambiguous state
    return "quiescent"


def _mutation_capable(agent: dict) -> bool:
    """True when an agent runs with a shell/toolset that can edit the AGI tree.

    cwd anywhere in the AGI repo, or a Claude/Codex/Gemini-class CLI command,
    marks the agent as mutation-capable.  Ollama-only local workers without
    repo access are not, but are still snapshotted for the audit record.
    """
    cwd = str(agent.get("cwd") or "")
    command = str(agent.get("command") or "")
    repo_root = str(_default_repo_root()).lower()
    if cwd.lower().startswith(repo_root):
        return True
    return bool(re.search(r"\b(claude|codex|gemini|cursor)\b", command, re.I))


def _read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def classify_fleet(fleet: object) -> tuple[str, float | None]:
    """Classify a fleet payload: ('fresh'|'stale'|'unverifiable'|'missing', age).

    Classification is derived from the payload the probe returned (hermetic:
    tests control the payload; the live machine state never leaks in).
    """
    if fleet is None:
        return "missing", None
    if not isinstance(fleet, dict):
        return "unverifiable", None
    ts = fleet.get("ts")
    if not isinstance(ts, (int, float)):
        return "unverifiable", None
    age = max(0.0, time.time() - float(ts) / 1000.0)
    return ("fresh" if age <= FLEET_MAX_AGE_SECONDS else "stale"), age


def _fleet_by_id(fleet: dict | list | None) -> dict[str, dict]:
    if not isinstance(fleet, dict):
        return {}
    raw = fleet.get("agents")
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict] = {}
    for entry in raw:
        if isinstance(entry, dict):
            agent_id = str(entry.get("id") or "")
            if agent_id:
                out[agent_id] = entry
    return out


def _agent_states_from_roster(roster: dict | list | None,
                              fleet: dict | list | None) -> list[HiveAgentState]:
    agents: list[dict] = []
    if isinstance(roster, dict):
        raw = roster.get("agents")
        if isinstance(raw, list):
            agents = [a for a in raw if isinstance(a, dict)]
    elif isinstance(roster, list):
        agents = [a for a in roster if isinstance(a, dict)]
    fleet_by_id = _fleet_by_id(fleet)
    states: list[HiveAgentState] = []
    for agent in agents:
        agent_id = str(agent.get("id") or agent.get("name") or "unknown")
        live = fleet_by_id.get(agent_id, {})
        status = str(live.get("status") or agent.get("status") or "unknown")
        action = str(live.get("action") or agent.get("action") or "")
        cwd = str(agent.get("cwd") or live.get("cwd") or "")
        recency = live.get("lastActiveSecAgo")
        recency_seconds: float | None = None
        if isinstance(recency, (int, float)):
            recency_seconds = float(recency)
        breaker = live.get("breaker")
        states.append(HiveAgentState(
            agent_id=agent_id, status=status, action=action, cwd=cwd,
            capable_of_repo_mutation=_mutation_capable(agent),
            recency_seconds=recency_seconds,
            breaker=str(breaker) if breaker is not None else None))
    return states


class HiveProbe(Protocol):
    """Callable returning the current roster/fleet snapshot, fail-closed."""
    def __call__(self) -> tuple[dict | list | None, dict | list | None]: ...


def _read_fleet_payload(home: Path) -> object:
    """Read the first existing candidate fleet file (raw payload).

    Returns the parsed JSON payload, the string 'malformed' when a candidate
    file exists but is unparsable, or None when no candidate file exists.
    """
    for candidate in FLEET_CANDIDATE_PATHS:
        path = home / candidate
        if path.is_file():
            data = _read_json(path)
            return data if data is not None else "malformed"
    return None


def live_roster_probe(hive_home: Path | None = None) -> HiveProbe:
    """Read roster.json + the canonical fleet file; unparsable roster fails."""
    home = hive_home or _default_hive_home()

    def probe() -> tuple[dict | list | None, dict | list | None]:
        roster = _read_json(home / "roster.json")
        if roster is None:
            raise HiveQuiesceError(
                f"hive roster unreadable at {home / 'roster.json'} - "
                "cannot verify hive quiescence; refusing to open the window")
        fleet = _read_fleet_payload(home)
        return roster, fleet

    return probe


# --- engine-independent process inventory ----------------------------------

def _pid_and_ancestors(pid: int) -> set[int]:
    """The given PID plus every ancestor PID (bounded walk, best effort)."""
    seen: set[int] = set()
    try:
        import psutil
        proc = psutil.Process(pid)
        seen.add(proc.pid)
        for parent in proc.parents():
            seen.add(parent.pid)
            if len(seen) > 64:
                break
    except Exception:
        seen.add(pid)
    return seen


def _psutil_inventory() -> list[dict]:
    """Live Windows process inventory via psutil (read-only)."""
    import psutil
    records: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            info = proc.info
            cmdline = " ".join(info.get("cmdline") or [])
            try:
                cwd = proc.cwd()
            except Exception:
                cwd = ""
            records.append({
                "pid": info.get("pid"),
                "name": info.get("name") or "",
                "cmdline": cmdline,
                "cwd": cwd,
                "create_time": info.get("create_time"),
            })
        except Exception:
            continue
    return records


def _inventory_from_file(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("inventory file must contain a JSON list of records")
    return [r for r in data if isinstance(r, dict)]


def process_inventory() -> tuple[list[dict], str]:
    """Return (records, source).  Env-file injection wins for hermetic tests.

    An explicit-but-broken injection fails closed: if the operator or a test
    pointed at an inventory file, that file is authoritative and unreadable
    means unverifiable.
    """
    injected = os.environ.get(PROCESS_INVENTORY_FILE_ENV, "").strip()
    if injected:
        try:
            return _inventory_from_file(injected), "env-file"
        except Exception as exc:
            raise HiveQuiesceError(
                f"process inventory file {injected!r} unreadable "
                f"({type(exc).__name__}: {exc}); cannot prove quiescence")
    try:
        return _psutil_inventory(), "psutil"
    except ImportError as exc:
        raise HiveQuiesceError(
            "psutil unavailable; the engine-independent process inventory "
            "cannot positively prove hive quiescence") from exc


def _binary_name(record: dict) -> str:
    name = str(record.get("name") or "").strip()
    if not name:
        parts = str(record.get("cmdline") or "").split()
        if parts:
            name = parts[0]
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _repo_linked(record: dict, repo_root: str) -> bool:
    """Separator-agnostic repo linkage (paths appear with \\ or / in the wild)."""
    root = repo_root.replace("\\", "/").lower().rstrip("/") + "/"
    cwd = str(record.get("cwd") or "").replace("\\", "/").lower().rstrip("/")
    cmdline = str(record.get("cmdline") or "").replace("\\", "/").lower()
    return (cwd + "/").startswith(root) or root in cmdline


def _hive_linked(record: dict) -> str | None:
    """The hive marker linking a process to the Munder hive, if any.

    Checks BOTH the command line and the working directory: a hive god
    process runs from the Munder state home with no repo path in its
    command line, so cwd linkage is required to catch it.
    """
    haystacks = (str(record.get("cwd") or "").lower(),
                 str(record.get("cmdline") or "").lower())
    for needle in HIVE_MARKER_NEEDLES:
        if any(needle in h for h in haystacks):
            return needle
    return None


def scan_mutation_processes(records: list[dict] | None = None,
                            repo_root: Path | None = None) -> list[dict]:
    """Return the processes that can plausibly mutate the AGI repository.

    Never kills anything.  Identity uses PID, start time, command line, cwd,
    and binary name.  A dev-CLI binary is relevant only when linked to the
    AGI repo or to hive markers; the Munder host app is always relevant;
    generic shells (bash/python/explorer) are never relevant on their own.
    The scanning process and its ancestors are excluded so the harness never
    flags itself.
    """
    if records is None:
        records, _source = process_inventory()
    root = str(repo_root or _default_repo_root()).lower().rstrip("\\/") + "\\"
    try:
        excluded = _pid_and_ancestors(os.getpid())
    except Exception:
        excluded = {os.getpid()}
    offenders: list[dict] = []
    for record in records:
        pid = record.get("pid")
        if isinstance(pid, int) and pid in excluded:
            continue
        binary = _binary_name(record)
        cmdline = str(record.get("cmdline") or "")
        needle = _hive_linked(record)
        if _MUNDER_HOST_TOKEN in binary:
            matched_by = "munder_host"
        elif binary in DEV_CLI_BINARIES and _repo_linked(record, root):
            matched_by = "dev_cli_repo"
        elif binary in DEV_CLI_BINARIES and needle is not None:
            matched_by = f"dev_cli_hive:{needle}"
        elif needle is not None:
            matched_by = f"hive_marker:{needle}"
        else:
            continue
        offenders.append({
            "pid": pid,
            "name": str(record.get("name") or ""),
            "cmdline": cmdline[:400],
            "cwd": str(record.get("cwd") or ""),
            "create_time": record.get("create_time"),
            "matched_by": matched_by,
        })
    return offenders


# --- quiescence gate --------------------------------------------------------

def _idle_state(state: HiveAgentState) -> bool:
    return classify_agent_state(state) == "quiescent"


def _states_or_refuse(roster: object, fleet: object) -> list[HiveAgentState]:
    """Build agent states from one probe payload; empty roster fails closed."""
    if roster is None:
        raise HiveQuiesceError(
            "hive roster unreadable - cannot verify hive quiescence; "
            "refusing to open the window")
    states = _agent_states_from_roster(roster, fleet)
    if not states:
        raise HiveQuiesceError(
            "hive roster parsed but listed no agents - ambiguous state; "
            "refusing to open the window")
    return states


def snapshot_hive_state(probe: HiveProbe) -> list[HiveAgentState]:
    """Take the current hive snapshot through the probe (fail-closed)."""
    try:
        roster, fleet = probe()
    except HiveQuiesceError:
        raise
    except Exception as exc:  # unreadable/partial hive state is unknown state
        raise HiveQuiesceError(
            f"hive snapshot probe failed ({type(exc).__name__}: {exc}); "
            "cannot verify hive quiescence - refusing to open the window"
        ) from exc
    return _states_or_refuse(roster, fleet)


def hive_quiesced(states: list[HiveAgentState]) -> bool:
    """True when NO mutation-capable agent is classified non-quiescent."""
    return all(_idle_state(s) for s in states)


def ensure_hive_quiesced(probe: HiveProbe,
                          emit_event: Callable[..., None] | None = None) -> dict:
    """Verify hive quiescence for a controlled window; fail closed.

    Gate order (each step fail-closed, never weakened by a later pass):
      1. roster must parse and list agents;
      2. fleet must be FRESH (stale or timestamp-less fleet always fails);
         a missing/malformed fleet additionally requires the process
         inventory to positively prove no relevant process exists - and even
         with a fresh fleet the process inventory must be clean
         (defense-in-depth);
      3. every mutation-capable agent must classify as quiescent
         (explicit idle status + quiescent action + no recent heartbeat
         + healthy breaker).

    Returns an audit record; raises HiveQuiesceError on any refusal.  Never
    kills anything; never mutates hive state.  emit_event is the optional
    health_events.emit-style audit sink used for refusals.
    """
    try:
        roster, fleet = probe()
    except HiveQuiesceError:
        raise
    except Exception as exc:
        raise HiveQuiesceError(
            f"hive snapshot probe failed ({type(exc).__name__}: {exc}); "
            "cannot verify hive quiescence - refusing to open the window"
        ) from exc
    states = _states_or_refuse(roster, fleet)
    fleet_state, fleet_age = classify_fleet(fleet)

    reasons: list[str] = []
    agent_offenders: list[str] = []
    for state in states:
        if not _idle_state(state):
            why = classify_agent_state(state)
            agent_offenders.append(state.agent_id)
            reasons.append(
                f"agent {state.agent_id}: status={state.status!r} "
                f"action={state.action!r} classified={why}")
    if fleet_state in ("stale", "unverifiable"):
        age_note = f" (age {fleet_age:.0f}s)" if fleet_age is not None else ""
        reasons.append(
            f"fleet {fleet_state}{age_note} - heartbeat cannot be trusted")
    # A MISSING or MALFORMED fleet never fails closed by itself: the process
    # inventory below must then POSITIVELY prove no relevant Munder process
    # can mutate the AGI tree (operator-approved fallback, option B).  The
    # scan is unconditional defense-in-depth: even a fresh fleet does not
    # open the window while a mutation-capable process is live.

    process_offenders: list[dict] = []
    process_source = "unavailable"
    try:
        records, process_source = process_inventory()
    except HiveQuiesceError as exc:
        if fleet_state in ("missing", "unverifiable"):
            reasons.append(
                "process inventory unavailable and fleet absent - "
                f"no positive proof of quiescence possible ({exc})")
        else:
            reasons.append(
                f"process inventory unavailable - cannot complete "
                f"defense-in-depth quiescence proof ({exc})")
    else:
        process_offenders = scan_mutation_processes(records)
        for proc in process_offenders:
            reasons.append(
                f"process pid={proc['pid']} ({proc['matched_by']}) "
                f"cwd={proc['cwd']!r} {proc['cmdline'][:120]}")

    record = {
        "agents": [
            {"id": s.agent_id, "status": s.status, "cwd": s.cwd,
             "mutation_capable": s.capable_of_repo_mutation,
             "classification": classify_agent_state(s)}
            for s in states
        ],
        "quiesced": not reasons,
        "offenders": agent_offenders + [f"pid:{p['pid']}" for p in process_offenders],
        "fleet": {"state": fleet_state, "age_seconds": fleet_age},
        "process_scan": {"source": process_source, "offenders": process_offenders},
    }
    if reasons:
        message = "controlled window refused: " + "; ".join(reasons)
        record["reasons"] = reasons
        if emit_event is not None:
            try:
                emit_event("hive_quiesce", "window_refused",
                           HiveQuiesceError(message),
                           offenders=record["offenders"])
            except Exception:
                pass
        raise HiveQuiesceError(message)
    return record


def ensure_canary_process_quiescence(
        emit_event: Callable[..., None] | None = None) -> dict:
    """Canary precondition: no mutation-capable dev process may be live.

    Defense-in-depth for the known marker-authentication limitation: current
    canary authorization is a scoped one-shot capability, not cryptographic
    human authentication, so the blast radius of a misused marker is capped
    by refusing to run while development agents can still mutate the
    repository.  Fail-closed; never kills anything.
    """
    try:
        records, source = process_inventory()
    except HiveQuiesceError:
        if emit_event is not None:
            try:
                emit_event("hive_quiesce", "canary_refused",
                           HiveQuiesceError("process inventory unavailable"))
            except Exception:
                pass
        raise
    offenders = scan_mutation_processes(records)
    if offenders:
        detail = "; ".join(
            f"pid={p['pid']} ({p['matched_by']})" for p in offenders)
        message = (f"canary refused: mutation-capable development "
                   f"processes are active: {detail}")
        if emit_event is not None:
            try:
                emit_event("hive_quiesce", "canary_refused",
                           HiveQuiesceError(message))
            except Exception:
                pass
        raise HiveQuiesceError(message)
    return {"quiesced": True, "source": source, "offenders": []}


def tree_taint_report(status_at_open: list[str] | None,
                      status_now_provider: Callable[[], list[str]] | None = None,
                      emit_event: Callable[..., None] | None = None) -> dict:
    """Post-window audit: did the AGI tree change during the window?

    Compares porcelain status lines at open vs. now.  New paths that appeared
    during the window are 'taint'.  Returns a report; never raises and never
    blocks restoration - dispatchers and ESTOP restoration are completed
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
    try:
        record = ensure_hive_quiesced(live_roster_probe())
    except HiveQuiesceError as exc:
        print(json.dumps({"quiesced": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())