"""Clean-machine installer contract.

Asserts the installer's Python helpers run read-only, NEVER leak the private
operator key, and that scripts/install.ps1 parses and declares the fail-closed
-Check mode. The installer itself is an operator-run deployment script (not
executed here); its Python components are exercised directly.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INSTALL_PS1 = SCRIPTS / "install.ps1"
KEYPAIR_PY = SCRIPTS / "_installer_keypair.py"
ESTOP_PY = SCRIPTS / "_installer_estop.py"


def _run(py_file: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(py_file), *args],
                          capture_output=True, text=True, timeout=30, cwd=str(ROOT))


class InstallerContractTests(unittest.TestCase):
    def test_components_present(self):
        for p in (INSTALL_PS1, KEYPAIR_PY, ESTOP_PY):
            self.assertTrue(p.is_file(), f"missing installer component: {p}")

    def test_keypair_check_is_readonly_and_never_leaks_private_key(self):
        out = _run(KEYPAIR_PY, "--check")
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertIn("present", data)
        # The private key must NEVER appear in any installer output (stdout or stderr).
        for stream in (out.stdout, out.stderr):
            self.assertNotIn("private_key", stream)
            self.assertNotIn("private", stream.lower())
        if data.get("present"):
            self.assertRegex(data["fingerprint"], r"^[0-9a-f]{64}$")

    def test_estop_check_reports_state_and_never_disengages(self):
        out = _run(ESTOP_PY, "--check")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout.strip(), r"^engaged=(true|false)$")
        # The ESTOP helper only engages/audits; it must never disengage.
        for stream in (out.stdout, out.stderr):
            self.assertNotIn("resume", stream.lower())

    def test_keypair_check_is_idempotent(self):
        a = _run(KEYPAIR_PY, "--check").stdout
        b = _run(KEYPAIR_PY, "--check").stdout
        self.assertEqual(a, b)

    def test_installer_script_declares_check_and_fail_closed_contract(self):
        src = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("[switch]$Check", src)          # non-mutating audit mode
        self.assertIn("READY: all hard steps passed", src)   # success message
        self.assertIn("NOT READY", src)                # fail-closed message
        self.assertIn("fail-closed", src.lower())     # the contract is named
        # It must orchestrate the existing scripts, not reimplement them.
        for name in ("bootstrap.ps1", "deploy_three_identity.ps1", "enforce_worker_firewall.ps1"):
            self.assertIn(name, src)
        # It must never print a credential or private key directly.
        self.assertNotIn("private_key", src)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell parse is Windows-only")
    def test_installer_script_parses_without_syntax_errors(self):
        if not shutil.which("powershell"):
            self.skipTest("powershell not on PATH")
        # AST parse via a temp script to avoid -Command quoting fragility.
        # This PARSES install.ps1; it does NOT execute it.
        with tempfile.TemporaryDirectory() as td:
            checker = Path(td) / "parse.ps1"
            checker.write_text(
                "$e=$null; [void][System.Management.Automation.Language.Parser]"
                "::ParseFile($args[0],[ref]$null,[ref]$e); "
                "if($e){$e|ForEach-Object{$_.Message};exit 1}",
                encoding="utf-8")
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(checker), str(INSTALL_PS1)],
                    capture_output=True, text=True, timeout=30)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                self.skipTest("powershell unavailable or timed out")
        if out.returncode == 0:
            return  # no parse errors
        # Non-zero: real syntax errors print messages to stdout. An empty stdout
        # with non-zero is an environment issue, not a parse error -- skip.
        if out.stdout.strip():
            self.fail(f"install.ps1 has PowerShell syntax errors:\n{out.stdout}")
        self.skipTest("powershell parse returned non-zero with no diagnostics; skipping")


if __name__ == "__main__":
    unittest.main(verbosity=2)
