"""Real Ed25519/DSSE tamper, ordering, admission and append-only regressions."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from web_ui_test_support import fixture, local_http_request, serving
import attestation_chain as chain
import operator_auth
from trust_gateway import McpServer
from v1_test_support import context, runner, runner_fixture, source, worker_result


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.keys = operator_auth._generate_keypair()  # ephemeral test key only
        self.patch = patch.object(operator_auth, "_load_keypair", return_value=self.keys)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def complete_chain(self):
        result = []
        for step in chain.Step:
            result.append(chain.emit_step(step, 7, 1, {"status": "done"} if step == chain.Step.DELIVERABLE else {},
                          chain.compute_digest(result[-1]) if result else None))
        return result

    def test_complete_chain_and_standard_pae(self):
        records = self.complete_chain()
        self.assertEqual(chain.verify_chain(records), (True, None))
        self.assertEqual(chain.pae(b"hello", "text/plain"), b"DSSEv1 10 text/plain 5 hello")
        envelope = records[0]
        body = base64.b64decode(envelope["payload"])
        kind = envelope["payloadType"].encode()
        # Construct DSSE verification input independently of implementation PAE.
        signed = b"DSSEv1 %d %s %d %s" % (len(kind), kind, len(body), body)
        operator_auth._reconstruct_verifier(self.keys[1]).verify(
            base64.b64decode(envelope["signatures"][0]["sig"]), signed)

    def test_tampering_signature_payload_type_and_foreign_key(self):
        good = self.complete_chain()
        for field in ("signature", "payload", "type", "keyid"):
            bad = copy.deepcopy(good)
            if field == "signature":
                bad[2]["signatures"][0]["sig"] = base64.b64encode(b"x" * 64).decode()
            elif field == "payload":
                body = json.loads(base64.b64decode(bad[2]["payload"]))
                body["claims"]["passed"] = True
                bad[2]["payload"] = base64.b64encode(json.dumps(body).encode()).decode()
            elif field == "type":
                bad[2]["payloadType"] = "text/plain"
            else:
                bad[2]["signatures"][0]["keyid"] = "0" * 64
            with self.subTest(field=field):
                self.assertFalse(chain.verify_chain(bad)[0])
        with patch.object(operator_auth, "_load_keypair", return_value=operator_auth._generate_keypair()):
            self.assertFalse(chain.verify_chain(good)[0])

    def test_broken_link_reordering_truncation_cross_task(self):
        good = self.complete_chain()
        broken = good[:2] + [chain.emit_step(chain.Step.PREFLIGHT, 7, 1, {}, "0" * 64)] + good[3:]
        self.assertEqual(chain.verify_chain(broken)[1], "broken_prior_digest")
        for bad in (good[1:], good[:-1], good[:2] + good[3:], good[:1] + [good[2], good[1]] + good[3:], []):
            self.assertFalse(chain.verify_chain(bad)[0])
        self.assertFalse(chain.verify_chain(good, task_id=8)[0])
        # Even correctly re-signed links cannot skip the worker/preflight stage.
        skipped = [good[0], chain.emit_step(chain.Step.CRITIC, 7, 1, {}, chain.compute_digest(good[0]))]
        self.assertFalse(chain.verify_chain(skipped, require_complete=False)[0])

    def test_append_preserves_bytes_and_rejects_corrupt_existing(self):
        with tempfile.TemporaryDirectory() as td:
            runs = Path(td)
            chain.append_step(runs, chain.Step.DISPATCH, 1, 1, {})
            path = chain.chain_path(runs, 1)
            before = path.read_bytes()
            chain.append_step(runs, chain.Step.WORKER, 1, 1, {})
            self.assertTrue(path.read_bytes().startswith(before))
            path.write_bytes(path.read_bytes()[:-1])
            corrupt = path.read_bytes()
            with self.assertRaises(chain.ChainError):
                chain.append_step(runs, chain.Step.PREFLIGHT, 1, 1, {})
            self.assertEqual(path.read_bytes(), corrupt)

    def test_signing_missing_key_never_creates_key_or_queues(self):
        with fixture(paused=False) as f, patch.object(operator_auth, "_load_keypair", return_value=None), \
                patch.object(operator_auth, "_generate_keypair", side_effect=AssertionError("must not create")):
            with self.assertRaises(chain.ChainError):
                f.gw.dispatch_task("test")
            with f.gw._conn() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_missing_required_chain_refuses_and_legacy_not_invented(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(chain.ChainError):
                chain.existing(Path(td), 1, {"run_id": chain.GATEWAY_RUN_ID})
            self.assertFalse(chain.existing(Path(td), 1, {"run_id": "legacy"}))
            self.assertFalse(chain.status(Path(td), 1)["attestation_chain_valid"])

    def test_gateway_mcp_and_authenticated_web_surface_chain(self):
        with fixture(paused=False) as f:
            tid = f.gw.dispatch_task("test")["task_id"]
            for step in list(chain.Step)[1:]:
                chain.append_step(f.gw.runs_dir, step, tid, 1,
                    {"status": "done"} if step == chain.Step.DELIVERABLE else {})
            self.assertTrue(f.gw.get_attestation(tid)["attestation_chain_valid"])
            response = McpServer(f.gw).handle_request({"id": 1, "method": "tools/call", "params": {
                "name": "get_attestation", "arguments": {"task_id": tid}}})
            self.assertTrue(json.loads(response["result"]["content"][0]["text"])["attestation_chain_valid"])
            with serving(f.gw) as server:
                code, _, _ = local_http_request(server.server_port, path="/api/status")
                self.assertEqual(code, 401)
                code, _, raw = local_http_request(server.server_port, path="/api/status",
                    headers={"Authorization": "Bearer " + server.bearer_token})
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(raw)["attestation_chain_steps"], 5)
                self.assertTrue(json.loads(raw)["attestation_chain_valid"])

    def test_real_runner_emits_each_stage_and_binds_notebook(self):
        with runner_fixture(lambda *a, **k: worker_result("A neutral finding. " * 30)) as (_, runs, stack):
            stack.enter_context(patch.object(runner.citecheck, "verify", return_value=[source()]))
            stack.enter_context(patch.object(runner.evaluation, "run_critic", return_value=("pass", "Fixture pass")))
            stack.enter_context(patch.object(runner.evaluation, "RUNS", runs))
            stack.enter_context(patch.object(runner.evaluation, "extract_facts", return_value=0))
            chain.append_step(runs, chain.Step.DISPATCH, 1, 1, {})
            self.assertEqual(runner._run_research_task(context()), "done")
            statements = chain.load(chain.chain_path(runs, 1))
            self.assertEqual(len(statements), 5)
            self.assertEqual(chain.verify_chain(statements), (True, None))
            notebook = runs / "task1_research_notebook.json"
            notebook.write_text('{"forged":true}')
            with self.assertRaisesRegex(chain.ChainError, "notebook"):
                chain.existing(runs, 1, context().row)


if __name__ == "__main__":
    unittest.main()
