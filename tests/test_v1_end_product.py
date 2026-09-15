"""V1 end-product integration: one probe mission exercises Phase A (the research
notebook survives a repair and injects its direction block into the retry prompt)
AND Phase B (the DSSE attestation chain emits every lifecycle step and verifies)
in a SINGLE real-runner run. Model-free: no providers, no ESTOP edits, no
production DB writes. This is the directive's Phase D end-to-end suite.
"""
import unittest
from unittest.mock import patch

# v1_test_support inserts orchestrator/ onto sys.path; import it BEFORE the
# orchestrator modules below (run_all.py does not put orchestrator on PYTHONPATH).
from v1_test_support import context, runner, runner_fixture, source, worker_result
import operator_auth
import attestation_chain as chain
from deliverable_preflight import PreflightReport
from research_notebook import Notebook


class V1EndProductTests(unittest.TestCase):
    def setUp(self):
        # Ephemeral Ed25519 keypair so chain statements are really signed and
        # verify_chain exercises the real signature path (no production key).
        self.keys = operator_auth._generate_keypair()
        self.patch = patch.object(operator_auth, "_load_keypair", return_value=self.keys)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_one_probe_mission_binds_notebook_and_verifies_chain(self):
        prompts = []

        def worker(prompt, *_a, **_kw):
            prompts.append(prompt)
            return worker_result("Combined end-product fixture output. " * 20)

        with runner_fixture(worker) as (_, runs, stack):
            # Phase A lever: force exactly one repair so the notebook direction
            # block is injected into the attempt-2 (repair) prompt. The initial
            # prompt[0] must NOT carry the notebook.
            reports = [
                PreflightReport(False, [{"url": "https://dead.example", "http_status": 404}],
                                ["Insufficient verified sources"], "Find one more source",
                                [source()]),
                PreflightReport(True),
            ]
            stack.enter_context(patch.object(runner.deliverable_preflight, "run_preflight",
                                             side_effect=reports))
            stack.enter_context(patch.object(runner.citecheck, "verify", return_value=[source()]))
            stack.enter_context(patch.object(runner.evaluation, "run_critic",
                                             return_value=("pass", "Fixture pass")))
            stack.enter_context(patch.object(runner.evaluation, "RUNS", runs))
            stack.enter_context(patch.object(runner.evaluation, "extract_facts", return_value=0))
            # Phase B lever: pre-seed DISPATCH so chain.existing binds and the
            # task-runner hooks emit the rest of the lifecycle chain.
            chain.append_step(runs, chain.Step.DISPATCH, 1, 1, {})

            self.assertEqual(runner._run_research_task(context()), "done")

            # --- Phase A: research notebook + direction block (across the repair) ---
            self.assertNotIn("### RESEARCH NOTEBOOK", prompts[0])
            self.assertIn("https://alive.example/review", prompts[1])
            self.assertIn("https://dead.example", prompts[1])
            memory = Notebook.load(runs / "task1_research_notebook.json")
            self.assertEqual(memory.attempts_seen, 2)

            # --- Phase B: DSSE chain, every lifecycle step, verifies end-to-end.
            # DISPATCH + WORKER + PREFLIGHT (a repair adds another WORKER/PREFLIGHT)
            # + CRITIC + DELIVERABLE -> at least one statement per lifecycle step.
            statements = chain.load(chain.chain_path(runs, 1))
            self.assertGreaterEqual(len(statements), 5)
            self.assertEqual(chain.verify_chain(statements), (True, None))

            # --- Phase B binding: the chain binds the notebook by hash; a forged
            # notebook is detected on re-admission (tamper-evident). ---
            (runs / "task1_research_notebook.json").write_text('{"forged":true}')
            with self.assertRaisesRegex(chain.ChainError, "notebook"):
                chain.existing(runs, 1, context().row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
