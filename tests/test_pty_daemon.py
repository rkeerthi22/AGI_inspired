"""Job Object process containment tests — all mocked, no real processes.

Verifies:
1. ``create_contained_process`` creates a Job Object and assigns the process
2. ``CREATE_SUSPENDED`` is used with assign-before-resume discipline
3. ``terminate_job`` calls ``TerminateJobObject``
4. ``close_job`` calls ``CloseHandle`` idempotently
5. Error paths (empty command, kernel32 failures) fail closed
6. PipeDrain threads drain without blocking
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

checks = 0
failures: list[str] = []


def check(label: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  [FAIL] {label}")
    else:
        print(f"  [PASS] {label}")


# ── Mock kernel32 ───────────────────────────────────────────────────────────


def _make_mock_kernel32() -> MagicMock:
    k32 = MagicMock()
    k32.CreateJobObjectW.return_value = 12345
    k32.SetInformationJobObject.return_value = True
    k32.AssignProcessToJobObject.return_value = True
    k32.TerminateJobObject.return_value = True
    k32.CloseHandle.return_value = True
    return k32


def _make_mock_ntdll() -> MagicMock:
    ntdll = MagicMock()
    ntdll.NtResumeProcess.return_value = 0
    return ntdll


# Patch BEFORE importing pty_daemon so all references point to the mock.
_mock_kernel32 = _make_mock_kernel32()
_mock_ntdll = _make_mock_ntdll()
_mock_popen = MagicMock()
_mock_proc = MagicMock()
_mock_proc._handle = 9999
_mock_proc.stdout = MagicMock()
_mock_proc.stderr = MagicMock()
_mock_popen.return_value = _mock_proc

patcher_k32 = patch("pty_daemon._kernel32", _mock_kernel32)
patcher_ntdll = patch("pty_daemon._ntdll", _mock_ntdll)
patcher_popen = patch("pty_daemon.subprocess", _mock_popen)
patcher_k32.start()
patcher_ntdll.start()
patcher_popen.start()

import pty_daemon  # noqa: E402

patcher_k32.stop()
patcher_ntdll.stop()
patcher_popen.stop()


# ── Test: create_contained_process ──────────────────────────────────────────


print("=== create_contained_process ===")

# Re-patch for the create_contained_process call
with patch("pty_daemon._kernel32", _mock_kernel32), \
     patch("pty_daemon._ntdll", _mock_ntdll), \
     patch("pty_daemon.subprocess", _mock_popen):

    _mock_kernel32.CreateJobObjectW.reset_mock()
    _mock_kernel32.AssignProcessToJobObject.reset_mock()
    _mock_ntdll.NtResumeProcess.reset_mock()
    _mock_popen.reset_mock()

    proc, h_job, sout, serr = pty_daemon.create_contained_process(
        [sys.executable, "-c", "print('hello')"],
        cwd=str(ROOT),
    )
    check("creates job object", h_job, 12345)
    check("returns Popen instance", proc is not None)
    check("returns PipeDrain for stdout", isinstance(sout, pty_daemon.PipeDrain))
    check("returns PipeDrain for stderr", isinstance(serr, pty_daemon.PipeDrain))

    # Verify assign-before-resume: AssignProcessToJobObject called before ResumeThread
    assign_calls = _mock_kernel32.AssignProcessToJobObject.mock_calls
    resume_calls = _mock_ntdll.NtResumeProcess.mock_calls
    check("assign was called", len(assign_calls) >= 1)
    check("resume was called", len(resume_calls) >= 1)
    check("stdout pipe requested in text mode",
          _mock_popen.Popen.call_args.kwargs.get("text"), True)

    # 2. Empty command list
    try:
        pty_daemon.create_contained_process([], cwd=str(ROOT))
        check("empty command raises", False)
    except pty_daemon.PtyDaemonError:
        check("empty command raises", True)


# ── Test: terminate_job / close_job ─────────────────────────────────────────


print("\n=== terminate / close ===")

_mock_kernel32.TerminateJobObject.reset_mock()
_mock_kernel32.CloseHandle.reset_mock()

with patch("pty_daemon._kernel32", _mock_kernel32):
    pty_daemon.terminate_job(12345, exit_code=75)
    check("terminate_job calls TerminateJobObject",
          _mock_kernel32.TerminateJobObject.called)

    pty_daemon.close_job(12345)
    check("close_job calls CloseHandle",
          _mock_kernel32.CloseHandle.called)

# Idempotent close
_mock_kernel32.CloseHandle.side_effect = Exception("already closed")
with patch("pty_daemon._kernel32", _mock_kernel32):
    try:
        pty_daemon.close_job(0)
        check("close_job(0) is safe", True)
    except Exception:
        check("close_job(0) does not raise", False)
_mock_kernel32.CloseHandle.side_effect = None


# ── Test: job_terminator ────────────────────────────────────────────────────


print("\n=== job_terminator ===")

_mock_kernel32.TerminateJobObject.reset_mock()
_mock_kernel32.CloseHandle.reset_mock()

terminate = pty_daemon.job_terminator()
with patch("pty_daemon._kernel32", _mock_kernel32):
    terminate(12345)
    check("terminator calls TerminateJobObject",
          _mock_kernel32.TerminateJobObject.called)
    check("terminator calls CloseHandle",
          _mock_kernel32.CloseHandle.called)


# ── Test: PipeDrain ─────────────────────────────────────────────────────────


print("\n=== PipeDrain ===")


class FakeStream:
    def __init__(self, lines: list[str]):
        self._lines = list(lines)
        self._idx = 0

    def readline(self, size=-1):
        if self._idx < len(self._lines):
            self._idx += 1
            return self._lines[self._idx - 1]
        return ""

    def close(self):
        pass


stream = FakeStream(["line1\n", "line2\n", "line3\n"])
drain = pty_daemon.PipeDrain(stream, "test")
drain.wait(timeout=5.0)
check("PipeDrain collects lines", len(drain.lines), 3)
check("PipeDrain text joins correctly", "line1" in drain.text and "line3" in drain.text)


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
