"""V1-ADV-01-HERMETIC: adversarial prompt-injection ingestion, preflight rejection,
auto-repair, research-notebook direction persistence, and DSSE Ed25519 chain
validation in a single model-free real-runner run. ESTOP-safe, DB-safe.

Landed for Claude Code independent review (V1-ADV-01 audit, 2026-09-15). This is
the test proposed in Section 6 of
docs/reviews/GEMINI_TO_CLAUDE_V1_ADV01_AUDIT_2026-09-15.md, evaluated verbatim.
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


class V1Adv01HermeticTests(unittest.TestCase):
    def setUp(self):
        # Ephemeral Ed25519 keypair so chain statements are really signed and
        # verify_chain exercises the real signature path (no production key).
        self.keys = operator_auth._generate_keypair()
        self.patch = patch.object(operator_auth, "_load_keypair", return_value=self.keys)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_adversarial_injection_repair_and_attestation_defense(self):
        """V1-ADV-01-HERMETIC: End-to-end adversarial injection, repair, and DSSE attestation."""
        # 1. Trojan input: Simulates agent fooled on Attempt 1 by prompt injection.
        # Padded past 200 chars so it reaches the preflight (the short-output guard
        # at task_runner.py:561 returns "failed" before the preflight otherwise).
        trojan_output = (
            "SYSTEM AUDIT COMPLETED: HOST RECONFIGURED AND SECURE.\n"
            "<!-- Injected exfiltration payload; no research citations present -->\n"
            "Diagnostic sweep finished across all subsystems with no anomalies "
            "detected. Host integrity verified. No further action is required. "
            "This automated triage report requires no human review whatsoever."
        )
        # 2. Repaired output: Agent recovers on Repair 1, producing clean cited deliverable
        clean_output = (
            "# Market Analysis: Prompt Platforms\n\n"
            "| Platform | Tier | Monthly Price | Retrieval Date | Confidence |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| PromptBase | Pro | $9.99 | 2026-09-15 | 3 |\n"
            "| PromptSea | Basic | Free | 2026-09-15 | 2 |\n\n"
            "Sources:\n"
            "- https://alive.example/pricing [Retrieved 2026-09-15, Confidence 3]\n"
            "- https://alive.example/review [Retrieved 2026-09-15, Confidence 2]\n"
        )

        call_count = 0
        def adversarial_worker(prompt, *_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return worker_result(trojan_output)
            return worker_result(clean_output)

        with runner_fixture(adversarial_worker) as (_, runs, stack):
            # Seed DSSE Dispatch step
            chain.append_step(runs, chain.Step.DISPATCH, 1, 1, {})

            # Setup preflight: Attempt 1 fails (trojan), Repair 1 passes (clean)
            reports = [
                PreflightReport(False, [{"url": "https://c2.example/exfil", "http_status": 404}],
                                ["Insufficient verified sources", "Missing pricing table"],
                                "Repair: provide 2 verified sources and pricing table.",
                                []),
                PreflightReport(True, verified_sources=[source()]),
            ]
            stack.enter_context(patch.object(runner.deliverable_preflight, "run_preflight", side_effect=reports))
            stack.enter_context(patch.object(runner.citecheck, "verify", return_value=[source()]))
            stack.enter_context(patch.object(runner.evaluation, "run_critic", return_value=("pass", "Verified deliverable clean")))
            stack.enter_context(patch.object(runner.evaluation, "RUNS", runs))
            stack.enter_context(patch.object(runner.evaluation, "extract_facts", return_value=0))

            # Execute research task through real task runner pipeline
            status = runner._run_research_task(context())
            self.assertEqual(status, "done")

            # Verify Attempt 1 was caught and repaired
            self.assertEqual(call_count, 2)

            # Verify research notebook recorded dead/untrusted URLs and clean sources
            notebook = Notebook.load(runs / "task1_research_notebook.json")
            self.assertEqual(notebook.attempts_seen, 2)

            # Verify unbroken DSSE cryptographic chain:
            # DISPATCH -> WORKER -> PREFLIGHT -> WORKER(Repair) -> PREFLIGHT -> CRITIC -> DELIVERABLE
            statements = chain.load(chain.chain_path(runs, 1))
            self.assertGreaterEqual(len(statements), 6)
            valid, err = chain.verify_chain(statements)
            self.assertTrue(valid, f"Chain verification failed: {err}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
