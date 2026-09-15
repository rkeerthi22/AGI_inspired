"""Adversarial web security tests: temporary state, no host keys or live dispatch."""
import io
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from web_ui_test_support import fixture, request, serving, sign_attestation
import execution_pause
import policy_manager
import web_ui


class WebSecurityTests(unittest.TestCase):
    def test_all_get_and_post_routes_require_auth(self):
        with fixture() as f, serving(f.gw) as server:
            before = f.gw.policy_mgr.policy_path.read_bytes()
            sentinel = f.sentinel.read_bytes()
            routes = {
                "GET": ("/", "/api/status", "/api/tasks", "/api/tasks/1", "/api/candidates", "/api/graph", "/unknown"),
                "POST": ("/api/dispatch", "/api/estop", "/api/candidates/approve", "/api/candidates/reject", "/unknown"),
            }
            with patch.object(f.gw.policy_mgr, "re_sign_attestation") as signer, patch.object(f.gw, "dispatch_task") as dispatch:
                with self.assertLogs(web_ui.LOGGER, level="WARNING") as logs:
                    for method, paths in routes.items():
                        for path in paths:
                            for token in (None, "wrong-token"):
                                with self.subTest(method=method, path=path, token=token):
                                    code, headers, body = request(server, method, path,
                                        {"domain": "attacker.example.com", "action": "resume"} if method == "POST" else None,
                                        token=token)
                                    self.assertEqual(code, 401)
                                    self.assertIn("Bearer", headers["www-authenticate"])
                                    self.assertNotIn(server.bearer_token.encode(), body)
                    # Tokens in URLs/cookies and duplicate headers never authorize.
                    self.assertEqual(request(server, path="/?token=" + server.bearer_token, token=None)[0], 401)
                    self.assertEqual(request(server, token=None, headers={"Cookie": "token=" + server.bearer_token})[0], 401)
                    self.assertEqual(request(server, headers={"authorization": "Bearer " + server.bearer_token})[0], 401)
                self.assertNotIn(server.bearer_token, " ".join(logs.output))
                self.assertNotIn("wrong-token", " ".join(logs.output))
                signer.assert_not_called()
                dispatch.assert_not_called()
            self.assertEqual(f.gw.policy_mgr.policy_path.read_bytes(), before)
            self.assertFalse(f.gw.policy_mgr.approvals_path.exists())
            self.assertEqual(f.sentinel.read_bytes(), sentinel)

    def test_auth_precedes_body_parsing(self):
        with fixture() as f, serving(f.gw) as server:
            with self.assertLogs(web_ui.LOGGER):
                self.assertEqual(request(server, "POST", "/api/candidates/approve", token=None,
                                         headers={"Content-Length": "not-a-number"})[0], 401)
            for body in (b"[1]", b"null", b"not-json", b"\xff"):
                self.assertEqual(request(server, "POST", "/api/dispatch", body=body)[0], 400)
            self.assertEqual(request(server, "POST", "/api/dispatch", headers={"Content-Length": "-1"})[0], 400)
            self.assertEqual(request(server, "POST", "/api/dispatch", headers={"Content-Length": "65537"})[0], 400)

    def test_estop_engage_only_uses_canonical_sentinel(self):
        with fixture(paused=False) as f, serving(f.gw) as server:
            self.assertFalse(execution_pause.pause_engaged())
            with patch.object(web_ui, "ROOT", f.root):
                for action in ("resume", "clear", None):
                    code, _, body = request(server, "POST", "/api/estop", {"action": action})
                    self.assertEqual(code, 403)
                    self.assertIn("controlled-window CLI", json.loads(body)["error"])
                self.assertEqual(request(server, "POST", "/api/estop", {"action": "pause"})[0], 200)
                self.assertTrue(execution_pause.pause_engaged())
                self.assertTrue(f.sentinel.is_file())
                self.assertFalse((f.root / ".harness" / "estop.pause").exists())
                before = f.sentinel.read_bytes()
                self.assertEqual(request(server, "POST", "/api/estop", {"action": "resume"})[0], 403)
                self.assertEqual(request(server, "POST", "/api/estop", {"action": "pause"})[0], 200)
                self.assertEqual(f.sentinel.read_bytes(), before)
            with patch("execution_pause.estop_path", side_effect=OSError("denied")):
                self.assertEqual(request(server, "POST", "/api/estop", {"action": "pause"})[0], 500)

    def test_http_dispatch_refused_during_estop(self):
        with fixture() as f, serving(f.gw) as server:
            code, _, body = request(server, "POST", "/api/dispatch", {"spec": "must not queue"})
            self.assertEqual(code, 400)
            self.assertIn("ESTOP", json.loads(body)["error"])
            self.assertNotEqual(json.loads(body).get("status"), "queued")
            with f.gw._conn() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_http_budget_caps_cannot_be_bypassed(self):
        with fixture(paused=False) as f, serving(f.gw) as server:
            for params in ({"max_budget_usd": 1.01}, {"max_tokens": 100001}, {"max_budget_usd": 0},
                           {"max_budget_usd": float("nan")}, {"max_budget_usd": True}):
                self.assertEqual(request(server, "POST", "/api/dispatch", {"spec": "test", **params})[0], 400)

    def test_attestation_status_cannot_be_forged(self):
        with fixture() as f, serving(f.gw) as server:
            sign_attestation(f)
            self.assertTrue(json.loads(request(server, path="/api/status")[2])["attestation_valid"])
            token = f.gw.attest_path.read_text(encoding="utf-8")
            payload, _, version = token.split(".")
            f.gw.attest_path.write_text(f"{payload}.Zm9yZ2Vk.{version}", encoding="utf-8")
            status = json.loads(request(server, path="/api/status")[2])
            self.assertFalse(status["attestation_valid"])
            self.assertIsNone(status["worker_identity"])
            self.assertIsNone(status["attestation_digest"])

    def test_concurrent_approve_transactions_and_reject(self):
        with fixture() as f, serving(f.gw) as server:
            count = 8
            start = threading.Barrier(count)
            signed_snapshots = []
            mgr = f.gw.policy_mgr
            original_load = mgr.load_policy_yaml

            def slow_load():
                data = original_load()
                time.sleep(0.01)  # Make an unlocked read/modify/write lose updates.
                return data

            def signer():
                signed_snapshots.append(set(mgr.get_allowed_hosts()))
                time.sleep(0.01)
                return True

            mgr._re_sign_runner = signer
            initial = mgr.get_allowed_hosts()
            domains = {f"concurrent-{i}.example.com" for i in range(count)}

            def approve(domain):
                start.wait(timeout=10)
                return request(server, "POST", "/api/candidates/approve", {"domain": domain, "rationale": "fixture"})

            with patch.object(mgr, "load_policy_yaml", side_effect=slow_load), ThreadPoolExecutor(max_workers=count) as pool:
                results = list(pool.map(approve, sorted(domains)))
            self.assertTrue(all(code == 200 and json.loads(body)["success"] for code, _, body in results))
            raw_hosts = mgr.load_policy_yaml()["broker"]["allowed_hosts"]
            self.assertEqual(set(raw_hosts), initial | domains)
            self.assertEqual(len(raw_hosts), len(set(raw_hosts)))
            self.assertEqual(len(signed_snapshots), count)
            self.assertEqual([len(s - initial) for s in signed_snapshots], list(range(1, count + 1)))
            self.assertEqual(len(mgr.get_approval_history()), count)
            self.assertEqual(request(server, "POST", "/api/candidates/reject", {"domain": "reject.example.com"})[0], 200)
            self.assertEqual(len(signed_snapshots), count)
            self.assertEqual(mgr.get_approval_history()[-1]["action"], "reject")
            self.assertTrue(execution_pause.pause_engaged())

    def test_network_binding_requires_explicit_opt_in(self):
        with fixture() as f:
            for host in ("0.0.0.0", "::", "192.0.2.10", "example.com", ""):
                with self.subTest(host=host), self.assertRaisesRegex(ValueError, "allow-network"):
                    web_ui.WebConsoleServer(host, 0, f.gw)
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit_code:
                web_ui.main(["--host", "0.0.0.0", "--port", "0"])
            self.assertEqual(exit_code.exception.code, 2)
            warning = io.StringIO()
            with patch.object(web_ui.ThreadingHTTPServer, "__init__", return_value=None), redirect_stderr(warning):
                web_ui.WebConsoleServer("0.0.0.0", 0, f.gw, allow_network=True)
            self.assertIn("WARNING", warning.getvalue())
            self.assertIn("plaintext HTTP", warning.getvalue())

    def test_startup_prints_one_fresh_token(self):
        with fixture() as f:
            tokens = []
            for _ in range(2):
                out = io.StringIO()
                with patch.object(web_ui, "Gateway", return_value=f.gw), patch.object(
                    web_ui.WebConsoleServer, "serve_forever", side_effect=KeyboardInterrupt
                ), redirect_stdout(out):
                    self.assertEqual(web_ui.main(["--port", "0"]), 0)
                lines = [line for line in out.getvalue().splitlines() if line.startswith("Operator bearer token:")]
                self.assertEqual(len(lines), 1)
                tokens.append(lines[0].split(": ", 1)[1])
                self.assertGreaterEqual(len(tokens[-1]), 43)
            self.assertNotEqual(*tokens)


if __name__ == "__main__":
    unittest.main(verbosity=2)
