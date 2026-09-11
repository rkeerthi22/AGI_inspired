"""Windows SCM service wrapper for the AGI_AuditSigner named-pipe daemon.

D3 (Codex Astra audit): Raw 'python audit_signer_service.py serve' is not
SCM-managed — Windows Service Control Manager cannot start/stop/restart it.
This module provides a proper ServiceFramework subclass so the signer daemon
is a first-class Windows service with lifecycle management and recovery.

Usage (operator-only, elevated):
    python audit_signer_scm.py install   # Register with SCM
    python audit_signer_scm.py start     # Start the service
    python audit_signer_scm.py stop      # Stop the service
    python audit_signer_scm.py remove    # Unregister from SCM

The deploy_three_identity.ps1 InstallSignerService action uses New-Service
pointing at this file instead, so the operator does NOT run these commands
directly — they are available for manual lifecycle management if needed.
"""
from __future__ import annotations

import sys
import threading

# Graceful degradation: if pywin32 is not installed, the service wrapper
# cannot function. This is detected at import time so the deployment script
# can report a clear error rather than a cryptic traceback.
try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
    _HAS_PYWIN32 = True
except ImportError:
    _HAS_PYWIN32 = False


class AuditSignerService(win32serviceutil.ServiceFramework):
    """SCM-managed wrapper around audit_signer_service._main('serve')."""

    _svc_name_ = "AGI_AuditSigner"
    _svc_display_name_ = "AGI_like Ed25519 Audit Signer Daemon"
    _svc_description_ = (
        "Provides cryptographic Ed25519 trajectory signing via named pipe. "
        "Runs as AGI_Signer identity for three-identity separation."
    )

    def __init__(self, args):
        super().__init__(args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._stop_flag = threading.Event()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)
        self._stop_flag.set()

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        try:
            self._serve()
        except Exception as exc:
            servicemanager.LogErrorMsg(f"AGI_AuditSigner failed: {exc}")
        finally:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )

    def _serve(self):
        """Run the audit signer pipe server until SCM requests stop."""
        from pathlib import Path
        import os

        # Ensure orchestrator is importable
        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        from audit_signer_service import AuditSigner, _load_key
        from audit_signer_protocol import load_config
        from audit_signer_pipe import current_sid, serve_pipe

        config = load_config()
        sid = current_sid()
        signer = AuditSigner(config, _load_key(), sid)
        serve_pipe(config, signer.handle, self._stop_flag)


def main():
    if not _HAS_PYWIN32:
        print("ERROR: pywin32 is required for SCM service management.", file=sys.stderr)
        print("Install with: pip install pywin32", file=sys.stderr)
        return 1

    if len(sys.argv) == 1:
        # Started by SCM — no command-line arguments
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(AuditSignerService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(AuditSignerService)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
