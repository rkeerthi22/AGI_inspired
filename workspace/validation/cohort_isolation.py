"""Transactional isolation for the explicitly authorized validation cohort.

The safety order is deliberate: production dispatchers are quiesced and
verified before ESTOP is cleared.  Restoration engages ESTOP first, then
returns every dispatcher to its captured state.  A durable journal permits
recovery after an interrupted parent process.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "workspace" / "validation" / "cohort_isolation_state.json"
TASK_PREFIX = "AGI_M1_"

sys.path.insert(0, str(ROOT / "orchestrator"))
from runlock import _process_start_identity  # noqa: E402


def _run(argv: list[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False)


class IsolationBackend(Protocol):
    def snapshot_tasks(self) -> list[dict]: ...
    def set_task_enabled(self, name: str, enabled: bool) -> None: ...
    def snapshot_cron(self) -> list[dict]: ...
    def set_cron_active(self, job_id: str, active: bool) -> None: ...
    def gateway_running(self) -> bool: ...
    def set_gateway_running(self, running: bool) -> None: ...


class LiveBackend:
    """Read and control only the explicitly scoped production dispatchers."""

    def snapshot_tasks(self) -> list[dict]:
        script = (f"Get-ScheduledTask | Where-Object {{$_.TaskName -like '{TASK_PREFIX}*'}} | "
                  "Select-Object TaskName,State,@{n='Enabled';e={$_.Settings.Enabled}} | "
                  "ConvertTo-Json -Compress")
        result = _run(["powershell", "-NoProfile", "-Command", script])
        if result.returncode:
            raise RuntimeError(f"scheduled-task inventory failed: {result.stderr.strip()}")
        raw = result.stdout.strip()
        values = [] if not raw else json.loads(raw)
        if isinstance(values, dict):
            values = [values]
        return [{"name": str(v["TaskName"]), "enabled": bool(v["Enabled"]),
                 "state": str(v["State"])} for v in values]

    def set_task_enabled(self, name: str, enabled: bool) -> None:
        if not name.startswith(TASK_PREFIX) or any(c in name for c in "'\"`$;"):
            raise ValueError(f"refusing out-of-scope scheduled task: {name!r}")
        verb = "Enable" if enabled else "Disable"
        result = _run(["powershell", "-NoProfile", "-Command",
                       f"{verb}-ScheduledTask -TaskName '{name}' | Out-Null"])
        if result.returncode:
            raise RuntimeError(f"could not {verb.lower()} task {name}: {result.stderr.strip()}")

    def snapshot_cron(self) -> list[dict]:
        result = _run(["hermes", "cron", "list", "--all"])
        if result.returncode:
            raise RuntimeError(f"Hermes cron inventory failed: {result.stderr.strip()}")
        output = result.stdout.strip()
        jobs = re.findall(r"(?mi)^\s*([0-9a-f]{12})\s+\[(active|paused|disabled)\]", output)
        candidate_ids = re.findall(r"(?i)(?<![-0-9a-f])[0-9a-f]{12}(?![-0-9a-f])", output)
        explicit_empty = bool(re.search(r"(?i)\b(no cron jobs|no jobs found|0 jobs)\b", output))
        if ((not jobs and not explicit_empty) or len(candidate_ids) != len(jobs)):
            raise RuntimeError("Hermes cron inventory was empty, incomplete, or unparsable")
        return [{"id": job_id, "active": state == "active"} for job_id, state in jobs]

    def set_cron_active(self, job_id: str, active: bool) -> None:
        if not re.fullmatch(r"[0-9a-f]{12}", job_id):
            raise ValueError(f"invalid Hermes cron id: {job_id!r}")
        result = _run(["hermes", "cron", "resume" if active else "pause", job_id])
        if result.returncode:
            raise RuntimeError(f"could not update cron {job_id}: {result.stderr.strip()}")

    def gateway_running(self) -> bool:
        result = _run(["hermes", "gateway", "status"], timeout=60)
        if result.returncode:
            raise RuntimeError(f"Hermes gateway inventory failed: {result.stderr.strip()}")
        text = (result.stdout + "\n" + result.stderr).strip().lower()
        if re.search(r"\b(unknown|unavailable|indeterminate)\b", text):
            raise RuntimeError("Hermes gateway state was ambiguous or unparsable")
        stopped = bool(re.search(r"\b(not running|stopped|inactive|no gateway process detected|no gateway process)\b", text))
        running = bool(re.search(r"\brunning\b", text)) and not stopped
        if running == stopped:
            raise RuntimeError("Hermes gateway state was ambiguous or unparsable")
        return running

    def set_gateway_running(self, running: bool) -> None:
        result = _run(["hermes", "gateway", "start" if running else "stop", "--all"], timeout=90)
        if result.returncode:
            raise RuntimeError(f"could not {'start' if running else 'stop'} gateway: "
                               f"{result.stderr.strip()}")


def _write_journal(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _owner_is_current(state: dict) -> bool:
    """True only when the journal still belongs to the exact OS process."""
    pid = state.get("owner_pid")
    identity = state.get("owner_process_start_id")
    if not isinstance(pid, int) or not isinstance(identity, str) or not identity:
        raise RuntimeError("isolation journal has no trustworthy owner identity")
    try:
        current = _process_start_identity(pid)
    except OSError:
        return True  # inspection failure is unknown, so fail closed
    return current is not None and current == identity


def _launch_guardian(journal: Path, estop: Path) -> int:
    """Start a detached owner monitor before ESTOP can be removed."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "--guard",
           str(journal), str(estop)]
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
                                     getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs).pid


def _guard(journal: Path, estop: Path) -> int:
    """Restore a window automatically when its exact owning process disappears."""
    while True:
        try:
            state = json.loads(journal.read_text(encoding="utf-8"))
            if state.get("phase") == "restored":
                return 0
            if not _owner_is_current(state):
                CohortIsolation(estop, LiveBackend(), journal, state=state,
                                launch_guardian=None).restore()
                return 0
        except FileNotFoundError:
            return 0
        except Exception:
            # ESTOP restoration itself is fail-closed and runs before dispatcher
            # restoration. Retry transient inventory/control failures indefinitely.
            pass
        time.sleep(1)


@dataclass
class CohortIsolation:
    estop: Path
    backend: IsolationBackend
    journal: Path = JOURNAL
    state: dict | None = None
    launch_guardian: Callable[[Path, Path], int] | None = _launch_guardian

    def recover_abandoned(self) -> bool:
        """Restore an unfinished journal iff its exact OS owner is gone/reused."""
        if not self.journal.exists():
            return False
        prior = json.loads(self.journal.read_text(encoding="utf-8"))
        if prior.get("phase") == "restored":
            return False
        if _owner_is_current(prior):
            raise RuntimeError("unfinished cohort isolation journal belongs to a live owner")
        self.state = prior
        self.restore()
        self.state = None
        return True

    def open(self) -> dict:
        if self.journal.exists():
            prior = json.loads(self.journal.read_text(encoding="utf-8"))
            if prior.get("phase") != "restored":
                # Automatic stale-owner recovery is the second safety net behind
                # the detached guardian. restore() engages ESTOP before dispatchers.
                self.recover_abandoned()
        try:
            estop_bytes = self.estop.read_bytes()
        except FileNotFoundError:
            raise RuntimeError("ESTOP must be engaged before opening a cohort window")
        tasks = self.backend.snapshot_tasks()
        running = [t["name"] for t in tasks if str(t.get("state", "")).lower() == "running"]
        if running:
            raise RuntimeError(f"production scheduled tasks already running: {running}")
        state = {
            "version": 2, "phase": "captured",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "owner_pid": os.getpid(),
            "owner_process_start_id": _process_start_identity(os.getpid()),
            "estop_b64": base64.b64encode(estop_bytes).decode("ascii"),
            "tasks": tasks, "cron": self.backend.snapshot_cron(),
            "gateway_running": self.backend.gateway_running(),
        }
        self.state = state
        _write_journal(self.journal, state)
        try:
            if not state["owner_process_start_id"]:
                raise RuntimeError("cannot establish cohort owner process identity")
            if self.launch_guardian is not None:
                state["guardian_pid"] = self.launch_guardian(self.journal, self.estop)
                _write_journal(self.journal, state)
            for task in tasks:
                if task["enabled"]:
                    self.backend.set_task_enabled(task["name"], False)
            for job in state["cron"]:
                if job["active"]:
                    self.backend.set_cron_active(job["id"], False)
            if state["gateway_running"]:
                self.backend.set_gateway_running(False)
            # Verify quiescence before changing the fail-closed sentinel.
            if any(t["enabled"] for t in self.backend.snapshot_tasks()):
                raise RuntimeError("scheduled-task disable verification failed")
            if any(j["active"] for j in self.backend.snapshot_cron()):
                raise RuntimeError("Hermes cron pause verification failed")
            if self.backend.gateway_running():
                raise RuntimeError("Hermes gateway stop verification failed")
            state["phase"] = "quiesced"
            _write_journal(self.journal, state)
            self.estop.unlink()
            state["phase"] = "open"
            _write_journal(self.journal, state)
            return state
        except BaseException:
            self.restore()
            raise

    def restore(self) -> None:
        state = self.state
        if state is None:
            if not self.journal.exists():
                return
            state = json.loads(self.journal.read_text(encoding="utf-8"))
            self.state = state
        # Fail closed before any dispatcher can be re-enabled.
        self.estop.parent.mkdir(parents=True, exist_ok=True)
        self.estop.write_bytes(base64.b64decode(state["estop_b64"]))
        state["phase"] = "restoring"
        _write_journal(self.journal, state)
        errors: list[str] = []
        actions = [
            (lambda: self.backend.set_gateway_running(bool(state["gateway_running"])), "gateway"),
            *[(lambda j=j: self.backend.set_cron_active(j["id"], bool(j["active"])),
               f"cron:{j['id']}") for j in state["cron"]],
            *[(lambda t=t: self.backend.set_task_enabled(t["name"], bool(t["enabled"])),
               f"task:{t['name']}") for t in state["tasks"]],
        ]
        for action, label in actions:
            try:
                action()
            except Exception as exc:
                errors.append(f"{label}: {exc}")
        if errors:
            state["restore_errors"] = errors
            _write_journal(self.journal, state)
            raise RuntimeError("dispatcher restoration incomplete: " + "; ".join(errors))
        state.pop("restore_errors", None)
        state["phase"] = "restored"
        state["restored_at"] = datetime.now(timezone.utc).isoformat()
        _write_journal(self.journal, state)

    def __enter__(self) -> "CohortIsolation":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.restore()
        return False


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--guard":
        raise SystemExit(_guard(Path(sys.argv[2]), Path(sys.argv[3])))
    raise SystemExit("cohort_isolation.py is internal; expected --guard JOURNAL ESTOP")
