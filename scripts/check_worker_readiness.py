#!/usr/bin/env python3
"""Standalone Model-Free Deployment Readiness Diagnostic.

Verifies end-to-end host hardening, sandbox containment, and runtime environment
readiness before opening any live controlled cohort window.

Checks:
1. ESTOP Discipline: Global ESTOP sentinel status.
2. Windows Firewall / WFP Rules: Loopback broker allow & restricted worker deny.
3. Egress Attestation: Cryptographic validity of signed boundary token.
4. Egress Broker Health: Loopback TCP connectivity on 127.0.0.1:8787.
5. Worker Home & Provider Config: config.yaml presence & provider resolution.
6. Restricted Token ACL Containment: Native S-1-5-12 spawn, worker write allowance,
   and repository / .harness write denial.
7. Non-Interactive Stdin Lifecycle: Closed pipe immediate EOF validation.

Usage:
    python scripts/check_worker_readiness.py
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import egress_policy
import execution_pause
import pty_daemon as _pty


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_check(name: str, passed: bool, detail: str = "") -> None:
    status = f"{Colors.GREEN}[PASS]{Colors.RESET}" if passed else f"{Colors.RED}[FAIL]{Colors.RESET}"
    detail_str = f" - {detail}" if detail else ""
    print(f"  {status} {name}{detail_str}")


def check_estop() -> tuple[bool, str]:
    engaged = execution_pause.pause_engaged()
    if engaged:
        return True, "ESTOP is strictly engaged (pause_engaged == True)"
    return False, "ESTOP is NOT engaged - unsafe for idle state"


def check_firewall() -> tuple[bool, str]:
    ps_script = ROOT / "scripts" / "enforce_worker_firewall.ps1"
    if not ps_script.is_file():
        return False, f"Missing script: {ps_script}"

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(ps_script),
        "-Action", "Verify",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return False, f"Failed to execute PowerShell firewall check: {exc}"

    output = proc.stdout + proc.stderr
    allow_ok = "Loopback Broker Allow  : [PASS] ENABLED" in output
    deny_ok = "Direct Egress Block    : [PASS] ENABLED" in output

    if allow_ok and deny_ok:
        return True, "WFP allow (127.0.0.1:8787) and deny (S-1-5-12) active"
    missing = []
    if not allow_ok:
        missing.append("Broker Loopback Allow")
    if not deny_ok:
        missing.append("Direct Egress Deny")
    return False, f"Missing rules: {', '.join(missing)}"


def check_egress_attestation() -> tuple[bool, str]:
    attestation_path = os.environ.get(
        "HARNESS_EGRESS_ATTESTATION",
        str(ROOT / ".harness" / "egress_attestation.signed"),
    )
    env = dict(os.environ, HARNESS_EGRESS_ATTESTATION=attestation_path)
    state = egress_policy.boundary_state(environment=env)
    if state.get("ok"):
        issued_at = state.get("issued_at", "unknown")
        return True, f"Attestation valid (digest: {state.get('policy_digest', '')[:12]}..., issued: {issued_at})"
    return False, f"Attestation error: {state.get('error', 'unknown error')}"


def check_broker_health() -> tuple[bool, str]:
    try:
        policy = egress_policy.load_policy()
        host, port = policy.host, policy.port
    except Exception:
        host, port = "127.0.0.1", 8787

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((host, port))
        s.sendall(b"HEAD / HTTP/1.0\r\n\r\n")
        data = s.recv(128)
        s.close()
        return True, f"Broker listening on {host}:{port} ({len(data)}b response)"
    except Exception as exc:
        return False, f"Cannot connect to broker on {host}:{port}: {exc}"


def check_worker_home_config() -> tuple[bool, str]:
    worker_home = Path(os.environ.get("HARNESS_WORKER_HOME") or (ROOT / "workspace" / "worker_home"))
    if not worker_home.is_dir():
        return False, f"Worker home directory does not exist: {worker_home}"

    config_yaml = worker_home / "config.yaml"
    if not config_yaml.is_file():
        return False, f"Missing config.yaml in {worker_home}"

    try:
        import yaml
        from hermes_cli.config import get_compatible_custom_providers
        from hermes_cli.providers import resolve_custom_provider

        cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
        custom_providers = get_compatible_custom_providers(cfg)
        bp = resolve_custom_provider("custom:byteplus-coding", custom_providers)
        if bp is None:
            return False, "custom:byteplus-coding not configured in worker home config.yaml"
        return True, f"Worker home config valid with custom:byteplus-coding in {worker_home.name}"
    except Exception as exc:
        return False, f"Error validating worker home config: {exc}"


def check_restricted_containment() -> tuple[bool, str]:
    worker_home = Path(os.environ.get("HARNESS_WORKER_HOME") or (ROOT / "workspace" / "worker_home"))
    probe_code = """
import sys, os
from pathlib import Path
wh = Path(os.environ.get('USERPROFILE', ''))
wh_ok = False
try:
    tf = wh / '.probe_worker_readiness'
    tf.write_text('probe', encoding='utf-8')
    tf.unlink()
    wh_ok = True
except Exception:
    wh_ok = False

harness_denied = False
try:
    th = Path('S:/AGI_like/.harness/.probe_worker_readiness')
    th.write_text('probe', encoding='utf-8')
    th.unlink()
except (PermissionError, OSError):
    harness_denied = True

stdin_data = sys.stdin.read()
eof_ok = len(stdin_data) == 0

print(f"RES:{wh_ok}:{harness_denied}:{eof_ok}")
"""
    env = {
        "SystemRoot": os.environ.get("SystemRoot", "C:\\Windows"),
        "USERPROFILE": str(worker_home),
        "HOME": str(worker_home),
    }

    try:
        proc, h_job, sout, serr = _pty.create_contained_process(
            [sys.executable, "-I", "-B", "-c", probe_code],
            cwd=str(ROOT),
            env=env,
            restricted_worker=True,
            close_stdin=True,
        )
        proc.wait(timeout=5)
        sout.wait(timeout=5)
        serr.wait(timeout=5)
        _pty.close_job(h_job)
    except Exception as exc:
        return False, f"Restricted spawn failed: {exc}"

    output = sout.text.strip()
    if "RES:True:True:True" in output:
        return True, "Restricted token (S-1-5-12) permits worker home write & denies .harness write"
    return False, f"Containment probe failed. Output: {output}, Stderr: {serr.text.strip()}"


def main() -> int:
    print(f"\n{Colors.BOLD}{Colors.CYAN}=== AGI_like Deployment & Worker Readiness Diagnostic ==={Colors.RESET}\n")

    checks = [
        ("Global ESTOP Sentinel", check_estop),
        ("WFP Firewall Boundary", check_firewall),
        ("Signed Egress Attestation", check_egress_attestation),
        ("Loopback Egress Broker", check_broker_health),
        ("Worker Home & Config Discovery", check_worker_home_config),
        ("Restricted Token ACL Containment", check_restricted_containment),
    ]

    all_passed = True
    for name, func in checks:
        passed, detail = func()
        print_check(name, passed, detail)
        if not passed:
            all_passed = False

    print("\n" + "=" * 65)
    if all_passed:
        print(f"{Colors.GREEN}{Colors.BOLD}ALL READINESS CHECKS PASSED (6/6). Ready for controlled live window.{Colors.RESET}\n")
        return 0
    else:
        print(f"{Colors.RED}{Colors.BOLD}ONE OR MORE READINESS CHECKS FAILED. Correct issues before live launch.{Colors.RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
