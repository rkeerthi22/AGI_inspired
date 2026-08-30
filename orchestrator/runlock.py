"""Exclusive run lock for the batch orchestrator.

Fixes F1 (docs/HARDENING.md): with no mutual exclusion, an overlapping run (e.g.
Sunday's canaries at 03:30 running long into the 04:00 scorecard fire, or any
manual command issued during a cron window) triggered db_integrity_check() to
delete the OTHER run's legitimate rows and raise a false "worker wrote directly
to a database" alarm — proven via a probe on DB copies, 2026-07-19.

Stdlib only, portable: os.O_CREAT|O_EXCL is atomic on both Windows and POSIX.
A stale lock (owner crashed/killed, never released) is reclaimed after
STALE_AFTER_SECONDS rather than wedging the harness forever — a coarse
crash-recovery aid; H3 handles per-task recovery precisely.
"""
import json
import os
import sys
import time
import uuid
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from pathlib import Path

STALE_AFTER_SECONDS = 3600  # generous: longest observed real run ≈ 9 min/task,
                             # worst-case 5-task canary sweep ≈ 45 min


class AlreadyRunning(Exception):
    """Another batch_runner instance holds the lock and it is not stale."""


class LockCorrupted(AlreadyRunning):
    """An existing lock cannot be trusted; fail closed for operator review."""


def _read_lock(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("lock payload is not an object")
        if not isinstance(data.get("pid"), int) or data["pid"] <= 0:
            raise ValueError("lock requires a positive integer pid")
        if not isinstance(data.get("started_at"), (int, float)):
            raise ValueError("lock requires numeric started_at")
        if not isinstance(data.get("process_start_id"), str) or not data["process_start_id"]:
            raise ValueError("lock requires process_start_id")
        if not isinstance(data.get("lock_id"), str) or not data["lock_id"]:
            raise ValueError("lock requires lock_id")
        return data
    except Exception as exc:
        raise LockCorrupted(f"unreadable/corrupt lock at {path}: {exc}") from exc


def _process_start_identity(pid: int) -> str | None:
    """Return an OS process-creation identity, or None when PID is absent.

    A PID alone is unsafe because operating systems reuse it.  Windows creation
    FILETIME and Linux /proc start ticks identify the particular process that
    owns the PID.  Query failures other than a positively absent process raise,
    allowing callers to fail closed.
    """
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME))
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error in {87, 1168}:  # invalid parameter / element not found
                return None
            raise OSError(error, f"cannot inspect process {pid}")
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user)):
                error = ctypes.get_last_error()
                raise OSError(error, f"cannot read creation time for process {pid}")
            value = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return f"windows-filetime:{value}"
        finally:
            kernel32.CloseHandle(handle)

    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        raw = proc_stat.read_text(encoding="ascii")
        # Field 2 (comm) may contain spaces and parentheses. Everything after
        # the final ')' begins at field 3; starttime is field 22 (index 19).
        fields = raw[raw.rfind(")") + 2:].split()
        start_ticks = fields[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii").strip()
        return f"linux-proc:{boot_id}:{start_ticks}"
    except FileNotFoundError:
        return None
    except (IndexError, OSError, ValueError) as exc:
        raise OSError(f"cannot inspect process {pid} start identity") from exc


def _owner_is_same_process(lock: dict) -> bool:
    """Fail closed unless the recorded PID is positively absent or reused."""
    try:
        current = _process_start_identity(lock["pid"])
    except OSError:
        return True
    return current is not None and current == lock["process_start_id"]


def _is_stale(path: Path) -> bool:
    lock = _read_lock(path)
    return ((time.time() - lock["started_at"]) > STALE_AFTER_SECONDS
            and not _owner_is_same_process(lock))


@contextmanager
def acquire(path: Path):
    """Acquire the exclusive run lock or raise AlreadyRunning. Reclaims a stale
    lock once, then retries acquisition."""
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    process_start_id = _process_start_identity(os.getpid())
    if process_start_id is None:
        raise RuntimeError("cannot establish current process start identity")
    lock_id = uuid.uuid4().hex
    mine = {"pid": os.getpid(), "process_start_id": process_start_id,
            "lock_id": lock_id, "started_at": time.time()}
    for attempt in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps(mine).encode("utf-8"))
            finally:
                os.close(fd)
            acquired = True
            break
        except FileExistsError:
            if attempt == 0 and _is_stale(path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue  # one retry after reclaiming
            raise AlreadyRunning(f"lock held: {_read_lock(path)}")
    try:
        yield
    finally:
        if acquired:
            try:
                current = _read_lock(path)
                if (current["lock_id"] == lock_id and
                        current["process_start_id"] == process_start_id):
                    path.unlink()
            except FileNotFoundError:
                pass
            except LockCorrupted:
                # Never delete a lock that was replaced or damaged while held.
                pass
