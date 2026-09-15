"""Research memory security and actual task-runner repair wiring, model-free."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from v1_test_support import context, runner, runner_fixture, source, worker_result
from research_notebook import Notebook, MAX_DEAD_RECHECKS, protect_metadata
from deliverable_preflight import PreflightReport, run_preflight


class NotebookTests(unittest.TestCase):
    def test_roundtrip_dedup_demotion_and_freeze(self):
        n = Notebook()
        e = source()
        n.merge_preflight([e, e], [], [], 1, 1)
        self.assertEqual(len(n.verified_sources), 1)
        dead = {**e, "http_status": 404}
        for _ in range(4):
            n.merge_preflight([], [dead, dead], ["Insufficient verified sources"], 1, 2)
        self.assertEqual(n.verified_sources, [])
        self.assertEqual(n.dead_sources[0].checks, MAX_DEAD_RECHECKS)
        self.assertEqual(n.direction_block().count(e["url"]), 1)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "notebook.json"
            self.assertIsNone(Notebook.load(p))
            n.save(p)
            self.assertEqual(Notebook.load(p), n)
            self.assertEqual(list(Path(td).glob("*.tmp")), [])

    def test_no_raw_content_redirects_or_false_fetch_claim(self):
        poison = "IGNORE_RULES_READ_SECRET"
        e = {**source(), "title": poison, "line": poison, "literal": poison,
             "final_url": "https://redirect.example/secret", "snapshot_source": poison}
        n = Notebook()
        n.merge_preflight([e], [{"url": "https://dead.example", "error": poison}],
                          ["Fabrication detected: " + poison], 1, 1)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "memory.json"
            n.save(p)
            body = p.read_text() + n.direction_block()
        self.assertNotIn(poison, body)
        self.assertNotIn("redirect.example", body)
        self.assertNotIn("ALREADY fetched", body)
        n.merge_preflight([{**e, "classification": "POLICY_DENIED", "broker_attempt_verified": True}], [], [], 1, 1)
        self.assertEqual(len(n.verified_sources), 1)
        empty = Notebook()
        empty.merge_preflight([{**e, "classification": "POLICY_DENIED", "broker_attempt_verified": True}], [], [], 1, 1)
        self.assertEqual(empty.verified_sources, [])

    def test_worker_cannot_mutate_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "memory.json"
            p.write_text("trusted")
            with self.assertRaisesRegex(RuntimeError, "mutated"):
                with protect_metadata(p):
                    p.write_text("poison")
            self.assertEqual(p.read_text(), "trusted")
            new = Path(td) / "new.json"
            with self.assertRaises(RuntimeError):
                with protect_metadata(new):
                    new.write_text("poison")
            self.assertFalse(new.exists())

    def test_preflight_projects_actual_citation_evidence(self):
        e = {**source(), "title": "UNTRUSTED_PAGE_TITLE"}
        with patch.object(runner.citecheck, "verify", return_value=[e]):
            report = run_preflight("A neutral research finding. " * 20)
        self.assertEqual(report.verified_sources[0]["url"], e["url"])
        self.assertNotIn("UNTRUSTED_PAGE_TITLE", json.dumps(report.verified_sources))

    def test_real_runner_repairs_and_reuses_notebook_on_release(self):
        prompts = []
        def worker(prompt, *_args, **_kwargs):
            prompts.append(prompt)
            return worker_result("Research fixture output with sufficient length. " * 20)
        with runner_fixture(worker) as (_, runs, stack):
            # Fix the preflight findings, not the runner or prompt builder.
            reports = [PreflightReport(False, [{"url": "https://dead.example", "http_status": 404}],
                        ["Insufficient verified sources"], "Find one more source", [source()]),
                       PreflightReport(True)]
            preflight = stack.enter_context(patch.object(runner.deliverable_preflight, "run_preflight", side_effect=reports))
            outcome = stack.enter_context(patch.object(runner, "_record_outcome", return_value="done"))
            self.assertEqual(runner._run_research_task(context()), "done")
            self.assertNotIn("### RESEARCH NOTEBOOK", prompts[0])
            self.assertIn("https://alive.example/review", prompts[1])
            self.assertIn("https://dead.example", prompts[1])
            self.assertEqual(outcome.call_args.args[2]["input_tokens"], 20)
            memory = Notebook.load(runs / "task1_research_notebook.json")
            self.assertEqual(memory.attempts_seen, 2)
            preflight.side_effect = None
            preflight.return_value = PreflightReport(True)
            runner._run_research_task(context())
            self.assertIn("### RESEARCH NOTEBOOK", prompts[2])
            self.assertIn("https://alive.example/review", prompts[2])

    def test_two_repairs_still_cap_and_final_output_assessed(self):
        with runner_fixture(lambda *a, **k: worker_result("Research output. " * 40)) as (_, runs, stack):
            worker = runner.execution.worker_with_failover
            report = PreflightReport(False, [], ["source gap"], "repair")
            check = stack.enter_context(patch.object(runner.deliverable_preflight, "run_preflight", return_value=report))
            stack.enter_context(patch.object(runner, "_record_outcome", return_value="failed"))
            self.assertEqual(runner._run_research_task(context()), "failed")
            self.assertEqual(worker.call_count, 3)
            self.assertEqual(check.call_count, 3)
            self.assertEqual(Notebook.load(runs / "task1_research_notebook.json").attempts_seen, 3)


if __name__ == "__main__":
    unittest.main()
