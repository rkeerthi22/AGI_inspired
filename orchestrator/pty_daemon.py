"""Windows Job Object process containment for long-lived workers.

Phase 4 of the Munder blueprint (``docs/MUNDER_BLUEPRINT.md`` §5).

Eliminates orphan processes and ensures fail-closed termination via
``ctypes`` / ``kernel32`` only — no new dependencies.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

# ── kernel32 types and constants (ctypes only, no pywin32 dependency) ──────

_kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

# JobObjectExtendedLimitInformation class
_JobObjectInfoClassExtendedLimitInformation = 9


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("ChildProcessBreakawayFlags", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", wintypes.DWORD * 8),  # IO_COUNTERS placeholder
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _create_job_object() -> int:
    """Create an anonymous kernel Job Object handle.

    Returns a Windows handle (``HANDLE``) that must eventually be closed.
    """
    # ``CreateJobObjectW(None, None)`` — no security attributes, no name.
    h_job = _kernel32.CreateJobObjectW(None, None)
    if not h_job:
        raise ctypes.WinError()
    return h_job


def _configure_kill_on_close(h_job: int) -> None:
    """Set ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` so closing the handle
    terminates every process in the job."""
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = _kernel32.SetInformationJobObject(
        h_job,
        _JobObjectInfoClassExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise ctypes.WinError()


def _assign_process_to_job(h_job: int, h_process: int) -> None:
    """Assign an already-suspended process handle to the job object."""
    ok = _kernel32.AssignProcessToJobObject(h_job, h_process)
    if not ok:
        raise ctypes.WinError()


def _resume_thread(h_process: int) -> None:
    """Resume the process's primary thread (suspended by ``CREATE_SUSPENDED``)."""
    count = _kernel32.ResumeThread(h_process)
    if count == -1:  # 0xFFFFFFFF
        raise ctypes.WinError()


def _terminate_job(h_job: int, exit_code: int = 75) -> None:
    """Terminate every process in the job with a single kernel call.

    Safe to call even if the job has already been terminated (the kernel
    returns ERROR_INVALID_HANDLE but no crash or leak).
    """
    _kernel32.TerminateJobObject(h_job, wintypes.UINT(exit_code))


def _close_handle(h: int) -> None:
    """Close a kernel handle, swallowing errors (idempotent by intent)."""
    if h:
        try:
            _kernel32.CloseHandle(h)
        except Exception:
            pass


# ── Public API ──────────────────────────────────────────────────────────────


class PtyDaemonError(RuntimeError):
    """The process could not be created or contained."""


class PipeDrain:
    """Continuously drain a pipe handle to prevent OS pipe-buffer deadlock.

    Launches a daemon reader thread that consumes the pipe until EOF.
    """

    def __init__(self, stream, name: str = ""):
        self._stream = stream
        self._name = name
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        try:
            for line in iter(self._stream.readline, ""):
                with self._lock:
                    self._buf.append(line)
        except ValueError:
            pass  # pipe closed
        finally:
            self._done.set()

    @property
    def lines(self) -> list[str]:
        with self._lock:
            return list(self._buf)

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self._buf)

    def wait(self, timeout: float | None = None) -> None:
        self._done.wait(timeout)


def create_contained_process(
    command_list: list[str],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen, int, PipeDrain, PipeDrain]:
    """Create a contained process tree via Windows Job Objects.

    The process is spawned suspended, assigned to a Job Object with
    ``KILL_ON_JOB_CLOSE``, then resumed.  The returned ``h_job`` handle
    must be closed (or terminated) to reap the entire tree.

    Parameters
    ----------
    command_list : list[str]
        The command and arguments to execute.
    cwd : str | Path | None
        Working directory for the child process.
    env : dict[str, str] | None
        Environment variables for the child process.  If ``None`` the
        parent's environment is inherited (the default ``Popen``
        behaviour).

    Returns
    -------
    (proc, h_job, stdout_drain, stderr_drain)
        ``proc`` — the ``subprocess.Popen`` instance.
        ``h_job`` — kernel handle for the Job Object.
        ``stdout_drain``, ``stderr_drain`` — running ``PipeDrain`` threads.
    """
    if not command_list:
        raise PtyDaemonError("command_list must be non-empty")

    h_job = _create_job_object()
    try:
        _configure_kill_on_close(h_job)

        proc = subprocess.Popen(
            command_list,
            cwd=str(cwd) if cwd else None,
            env=env,
            creationflags=CREATE_SUSPENDED,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            _assign_process_to_job(h_job, proc._handle)
            _resume_thread(proc._handle)
        except Exception:
            _terminate_job(h_job)
            proc.kill()
            proc.wait()
            raise

        stdout_drain = PipeDrain(proc.stdout, "stdout")
        stderr_drain = PipeDrain(proc.stderr, "stderr")
        return proc, h_job, stdout_drain, stderr_drain
    except PtyDaemonError:
        _close_handle(h_job)
        raise
    except OSError as exc:
        _close_handle(h_job)
        raise PtyDaemonError(str(exc)) from exc


def terminate_job(h_job: int, exit_code: int = 75) -> None:
    """Terminate every process in a Job Object.

    This is the ESTOP-watchdog primitive: a single kernel call reaps the
    entire process tree (``conhost.exe``, child runners, etc.).
    """
    _terminate_job(h_job, exit_code)


def close_job(h_job: int) -> None:
    """Close a Job Object handle, triggering ``KILL_ON_JOB_CLOSE``.

    Idempotent — safe to call even if the job was already terminated.
    """
    _close_handle(h_job)


def job_terminator() -> Callable[[int], None]:
    """Return a function suitable as an ESTOP-watchdog callback.

    Usage::

        _, h_job, ... = create_contained_process(cmd, cwd)
        watchdog = job_terminator()
        watchdog(h_job)   # → terminate + close
    """

    def _terminate(h_job: int) -> None:
        _terminate_job(h_job)
        _close_handle(h_job)

    return _terminate
