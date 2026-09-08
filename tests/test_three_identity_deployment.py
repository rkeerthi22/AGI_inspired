"""Model-free unit tests for Path 2 Three-Identity Deployment & Audit Signer Isolation.

Tests the configuration contract, SDDL generation, identity separation,
and caller authorization without requiring elevated Windows service provisioning.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import audit_signer_pipe as pipe
import audit_signer_protocol as proto
import audit_signer_service as service
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


VALID_SIGNER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
VALID_CONTROLLER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1002"
VALID_WORKER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1003"
VALID_PIPE = r"\\.\pipe\AGI_like_audit_signer"


class TestThreeIdentityDeployment(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.pub_bytes = service.public_bytes(self.key)
        self.pub_b64 = base64.b64encode(self.pub_bytes).decode("ascii")

    def test_signer_config_valid(self):
        """A valid three-identity configuration loads and holds 3 distinct SIDs."""
        cfg_dict = {
            "schema_version": 1,
            "pipe": VALID_PIPE,
            "signer_sid": VALID_SIGNER_SID,
            "controller_sid": VALID_CONTROLLER_SID,
            "worker_sid": VALID_WORKER_SID,
            "public_key": self.pub_b64,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg_dict, f)
            f_path = Path(f.name)

        try:
            with unittest.mock.patch.dict(os.environ, {"HARNESS_AUDIT_SIGNER_CONFIG": str(f_path)}):
                cfg = proto.load_config()
                self.assertEqual(cfg.pipe, VALID_PIPE)
                self.assertEqual(cfg.signer_sid, VALID_SIGNER_SID)
                self.assertEqual(cfg.controller_sid, VALID_CONTROLLER_SID)
                self.assertEqual(cfg.worker_sid, VALID_WORKER_SID)
                self.assertEqual(cfg.public_key, self.pub_bytes)
                self.assertEqual(len({cfg.signer_sid, cfg.controller_sid, cfg.worker_sid}), 3)
        finally:
            f_path.unlink(missing_ok=True)

    def test_signer_config_rejects_duplicate_sids(self):
        """Duplicate SIDs violate three-identity isolation and must fail closed."""
        # Signer SID equals Controller SID
        cfg_dict = {
            "schema_version": 1,
            "pipe": VALID_PIPE,
            "signer_sid": VALID_CONTROLLER_SID,
            "controller_sid": VALID_CONTROLLER_SID,
            "worker_sid": VALID_WORKER_SID,
            "public_key": self.pub_b64,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg_dict, f)
            f_path = Path(f.name)

        try:
            with unittest.mock.patch.dict(os.environ, {"HARNESS_AUDIT_SIGNER_CONFIG": str(f_path)}):
                with self.assertRaises(proto.SignerError) as ctx:
                    proto.load_config()
                self.assertIn("distinct_signer_controller_worker_required", str(ctx.exception))
        finally:
            f_path.unlink(missing_ok=True)

    def test_signer_config_rejects_invalid_sid_format(self):
        """SIDs must match dedicated account pattern S-1-5-21-* or service virtual accounts S-1-5-80-*."""
        cfg_dict = {
            "schema_version": 1,
            "pipe": VALID_PIPE,
            "signer_sid": "S-1-5-18",  # LocalSystem not dedicated
            "controller_sid": VALID_CONTROLLER_SID,
            "worker_sid": VALID_WORKER_SID,
            "public_key": self.pub_b64,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg_dict, f)
            f_path = Path(f.name)

        try:
            with unittest.mock.patch.dict(os.environ, {"HARNESS_AUDIT_SIGNER_CONFIG": str(f_path)}):
                with self.assertRaises(proto.SignerError) as ctx:
                    proto.load_config()
                self.assertIn("dedicated_account_sids_required", str(ctx.exception))
        finally:
            f_path.unlink(missing_ok=True)

    def test_signer_config_rejects_invalid_pipe_name(self):
        """Named pipe must follow the secure canonical naming convention."""
        cfg_dict = {
            "schema_version": 1,
            "pipe": r"\\.\pipe\arbitrary_insecure_pipe",
            "signer_sid": VALID_SIGNER_SID,
            "controller_sid": VALID_CONTROLLER_SID,
            "worker_sid": VALID_WORKER_SID,
            "public_key": self.pub_b64,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg_dict, f)
            f_path = Path(f.name)

        try:
            with unittest.mock.patch.dict(os.environ, {"HARNESS_AUDIT_SIGNER_CONFIG": str(f_path)}):
                with self.assertRaises(proto.SignerError) as ctx:
                    proto.load_config()
                self.assertIn("local_audit_pipe_required", str(ctx.exception))
        finally:
            f_path.unlink(missing_ok=True)

    def test_pipe_sddl_generation(self):
        """pipe_sddl generates explicit protected DACL denying worker and granting controller."""
        config = proto.SignerConfig(
            pipe=VALID_PIPE,
            signer_sid=VALID_SIGNER_SID,
            controller_sid=VALID_CONTROLLER_SID,
            worker_sid=VALID_WORKER_SID,
            public_key=self.pub_bytes,
        )
        sddl = pipe.pipe_sddl(config)
        # Verify protected DACL prefix
        self.assertTrue(sddl.startswith("D:P"))
        # Verify worker is explicitly denied Generic All
        self.assertIn(f"(D;;GA;;;{VALID_WORKER_SID})", sddl)
        # Verify signer has Generic All
        self.assertIn(f"(A;;GA;;;{VALID_SIGNER_SID})", sddl)
        # Verify controller has read/write client access (0x12019b)
        self.assertIn(f"(A;;0x{proto.CLIENT_ACCESS:x};;;{VALID_CONTROLLER_SID})", sddl)

    def test_audit_signer_caller_authorization(self):
        """AuditSigner allows controller requests and rejects worker or restricted callers."""
        config = proto.SignerConfig(
            pipe=VALID_PIPE,
            signer_sid=VALID_SIGNER_SID,
            controller_sid=VALID_CONTROLLER_SID,
            worker_sid=VALID_WORKER_SID,
            public_key=self.pub_bytes,
        )
        signer = service.AuditSigner(config, self.key, VALID_SIGNER_SID)

        # 1. Controller caller authorized for health
        health_req = {"op": "health", "nonce": "a" * 64}
        resp = signer.handle(health_req, VALID_CONTROLLER_SID, restricted=False)
        self.assertTrue(resp.get("ok"))
        self.assertIn("token", resp)

        # 2. Worker caller denied
        with self.assertRaises(proto.SignerError) as ctx:
            signer.handle(health_req, VALID_WORKER_SID, restricted=False)
        self.assertIn("audit_signer_caller_denied", str(ctx.exception))

        # 3. Restricted token caller denied even if controller SID
        with self.assertRaises(proto.SignerError) as ctx:
            signer.handle(health_req, VALID_CONTROLLER_SID, restricted=True)
        self.assertIn("audit_signer_caller_denied", str(ctx.exception))

        # 4. Unknown SID denied
        with self.assertRaises(proto.SignerError) as ctx:
            signer.handle(health_req, "S-1-5-21-9999999999-9999999999-9999999999-9999", restricted=False)
        self.assertIn("audit_signer_caller_denied", str(ctx.exception))

    def test_deploy_script_plan_action(self):
        """scripts/deploy_three_identity.ps1 -Action Plan executes with exit code 0."""
        script_path = ROOT / "scripts" / "deploy_three_identity.ps1"
        self.assertTrue(script_path.is_file(), "deploy_three_identity.ps1 must exist")

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script_path),
            "-Action", "Plan",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, f"Plan failed: {proc.stderr}")
        self.assertIn("Path 2: Three-Identity Deployment Plan", proc.stdout)
        self.assertIn("Planned Deployment Steps", proc.stdout)


if __name__ == "__main__":
    unittest.main()
