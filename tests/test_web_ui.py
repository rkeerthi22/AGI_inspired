"""Authenticated web console integration tests with temporary state only."""
import json
import unittest
from web_ui_test_support import fixture, request, serving, sign_attestation


class WebConsoleTests(unittest.TestCase):
    def test_authenticated_dashboard_and_apis(self):
        with fixture(paused=False) as f, serving(f.gw) as server:
            sign_attestation(f)
            code, headers, body = request(server)
            self.assertEqual(code, 200)
            html = body.decode()
            for label in ("AGI_like", "MiroFish", "Munder Difflin", "Swarm Floor", "BUDGET PARAMETER CAP"):
                self.assertIn(label, html)
            self.assertNotIn("__BEARER_TOKEN_JSON__", html)
            self.assertNotIn("cdn.tailwindcss.com", html)
            self.assertNotIn("json.stringify", html)
            self.assertEqual(headers["cache-control"], "no-store")
            code, _, body = request(server, "POST", "/api/dispatch", {"spec": "fixture mission", "max_budget_usd": 0.5})
            self.assertEqual(code, 200)
            queued = json.loads(body)
            self.assertEqual(queued["status"], "queued")
            code, _, body = request(server, path="/api/tasks")
            self.assertEqual(code, 200)
            self.assertEqual(len(json.loads(body)["tasks"]), 1)
            code, _, body = request(server, path="/api/status")
            status = json.loads(body)
            self.assertTrue(status["attestation_valid"])
            self.assertFalse(status["estop_engaged"])
            self.assertEqual(status["total_tasks"], 1)
            self.assertEqual(status["worker_identity"], "fixture-worker")
            for path, key in (("/api/graph", "nodes"), ("/api/candidates", "candidates"),
                              (f"/api/tasks/{queued['task_id']}", "broker_audit")):
                code, _, body = request(server, path=path)
                self.assertEqual(code, 200)
                self.assertIn(key, json.loads(body))
            self.assertEqual(request(server, path="/unknown")[0], 404)
            self.assertEqual(request(server, path="/api/tasks/nope")[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
