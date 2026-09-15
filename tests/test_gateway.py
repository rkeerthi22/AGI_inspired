"""Gateway admission, real Ed25519 verification, and MCP regressions."""
import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from web_ui_test_support import fixture, sign_attestation
import operator_auth
import trust_gateway as gateway


class GatewayTests(unittest.TestCase):
    def test_dispatch_status_and_deliverable(self):
        with fixture(paused=False) as f:
            result = f.gw.dispatch_task("Research tools", pass_criteria="Verify sources")
            tid = result["task_id"]
            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["budget_enforcement"], "admission_parameters_only")
            self.assertEqual(f.gw.check_status(tid)["status"], "queued")
            with f.gw._conn() as db:
                db.execute("UPDATE tasks SET status='done', critic_verdict='pass' WHERE task_id=?", (tid,))
                db.commit()
            (f.gw.runs_dir / f"task{tid}_a1_worker_raw.txt").write_text("Final research", encoding="utf-8")
            self.assertEqual(f.gw.check_status(tid)["critic_verdict"], "pass")
            self.assertEqual(f.gw.get_deliverable(tid)["deliverable"], "Final research")
            self.assertFalse(f.gw.check_status(999)["found"])

    def test_parameter_caps_and_invalid_numbers(self):
        with fixture(paused=False) as f:
            for value in (1.01, 5, float("nan"), float("inf"), 0, -1, True, "1"):
                with self.subTest(budget=value), self.assertRaises(gateway.GatewayBudgetExceeded):
                    f.gw.dispatch_task("test", max_budget_usd=value)
            for value in (100001, 250000, 0, -1, 1.5, True, "100"):
                with self.subTest(tokens=value), self.assertRaises(gateway.GatewayBudgetExceeded):
                    f.gw.dispatch_task("test", max_tokens=value)
            with self.assertRaises(ValueError):
                f.gw.dispatch_task(" ")
            with f.gw._conn() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(f.gw.dispatch_task("test", max_budget_usd=1, max_tokens=100000)["status"], "queued")

    def test_estop_blocks_gateway_mcp_and_cli(self):
        with fixture() as f:
            with patch.object(f.gw, "_conn", side_effect=AssertionError("ledger must not open")):
                with self.assertRaisesRegex(RuntimeError, "ESTOP engaged"):
                    f.gw.dispatch_task("blocked")
                mcp = gateway.McpServer(f.gw)
                result = mcp.handle_request({"id": 1, "method": "tools/call", "params": {
                    "name": "dispatch_task", "arguments": {"spec": "blocked"}}})
                self.assertTrue(result["result"]["isError"])
                with patch.object(gateway, "Gateway", return_value=f.gw), self.assertRaisesRegex(RuntimeError, "ESTOP"):
                    gateway.main(["dispatch", "blocked"])

    def test_signature_and_policy_validation(self):
        with fixture() as f:
            good = sign_attestation(f)
            valid = f.gw.get_attestation()
            self.assertTrue(valid["attestation_signature_valid"])
            self.assertTrue(valid["attestation_token_valid"])
            self.assertEqual(valid["worker_identity"], "fixture-worker")
            usage = f.gw.runs_dir / "task1_a1_worker.usage.json"
            usage.write_text(json.dumps({"policy_digest": valid["active_policy_digest"]}), encoding="utf-8")
            self.assertTrue(f.gw.get_attestation(1)["task_snapshot"]["matches_active_attestation"])
            payload, signature, version = good.split(".")
            sig_bytes = bytearray(base64.b64decode(signature))
            sig_bytes[0] ^= 1
            bad_signature = f"{payload}.{base64.b64encode(sig_bytes).decode()}.{version}"
            changed = json.loads(base64.b64decode(payload))
            changed["policy_sha256"] = "b" * 64
            bad_payload = f"{base64.b64encode(json.dumps(changed).encode()).decode()}.{signature}.{version}"
            for token in (bad_signature, bad_payload, "a.b.c", "{}", f"{payload}.sig.v1"):
                with self.subTest(token_type=token[-8:]):
                    f.gw.attest_path.write_text(token, encoding="utf-8")
                    result = f.gw.get_attestation(1)
                    self.assertFalse(result["attestation_token_valid"])
                    self.assertFalse(result["attestation_signature_valid"])
                    self.assertIsNone(result["active_policy_digest"])
                    self.assertFalse(result["task_snapshot"]["matches_active_attestation"])
            for overrides in ({"purpose": "authorize-clear"}, {"policy_sha256": "b" * 64},
                              {"issued_at": (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()},
                              {"issued_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
                              {"claims": []}, {"evidence": []}):
                with self.subTest(overrides=overrides):
                    sign_attestation(f, **overrides)
                    self.assertFalse(f.gw.get_attestation()["attestation_token_valid"])
            sign_attestation(f)
            with patch("operator_auth._load_keypair", return_value=operator_auth._generate_keypair()):
                self.assertFalse(f.gw.get_attestation()["attestation_signature_valid"])
                sign_attestation(f)  # Well-formed token signed by an attacker key.
            self.assertFalse(f.gw.get_attestation()["attestation_signature_valid"])
            sign_attestation(f)
            with patch("operator_auth._load_keypair", return_value=None):
                self.assertFalse(f.gw.get_attestation()["attestation_token_valid"])
            with patch("operator_auth._load_keypair", side_effect=OSError("unreadable")):
                self.assertFalse(f.gw.get_attestation()["attestation_token_valid"])
            f.gw.attest_path.unlink()
            self.assertFalse(f.gw.get_attestation()["attestation_token_valid"])

    def test_mcp_protocol(self):
        with fixture(paused=False) as f:
            mcp = gateway.McpServer(f.gw)
            self.assertEqual(mcp.handle_request({"id": 1, "method": "initialize"})["result"]["serverInfo"]["name"], "agi-like-gateway")
            names = {t["name"] for t in mcp.handle_request({"id": 2, "method": "tools/list"})["result"]["tools"]}
            self.assertTrue({"dispatch_task", "get_attestation", "check_status", "get_deliverable"} <= names)
            for arguments, error in (({"spec": "test"}, False), ({"spec": "test", "max_tokens": 100001}, True),
                                     ({"spec": "test", "max_budget_usd": True}, True)):
                result = mcp.handle_request({"id": 3, "method": "tools/call", "params": {"name": "dispatch_task", "arguments": arguments}})
                self.assertEqual(result["result"]["isError"], error)
            self.assertIn("error", mcp.handle_request({"id": 4, "method": "tools/call", "params": {"name": "unknown"}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
