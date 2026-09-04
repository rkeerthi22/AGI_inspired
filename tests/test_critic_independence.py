"""Regression coverage for the independent-provider critic boundary.

No provider is contacted. The execution transport is patched so these checks
prove that same-provider grading is rejected before a critic request is made.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))

import evaluation  # noqa: E402


class CriticIndependenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.runs = Path(self.temp.name)
        self.patches = [
            mock.patch.object(evaluation, "RUNS", self.runs),
            mock.patch.object(evaluation.citecheck, "verify", return_value=[]),
            mock.patch.object(evaluation.citecheck, "summarize", return_value={
                "checked": 0, "dead": 0, "dead_frac": 0.0,
            }),
            mock.patch.object(evaluation.citecheck, "is_hard_fail", return_value=False),
            mock.patch.object(evaluation.policy, "manager_call_budget_breached",
                              return_value=False),
            mock.patch.object(evaluation.policy, "record_manager_call"),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def _row(task_id: int = 1) -> dict:
        return {"task_id": task_id, "pass_criteria": "Must provide sources."}

    def test_same_provider_is_needs_review_without_a_model_call(self) -> None:
        usage: dict = {}
        with mock.patch.object(evaluation.execution, "ollama_chat") as chat:
            verdict, detail = evaluation.run_critic(
                self._row(), "deliverable", {
                    "critic": {"provider": "ollama", "model": "critic-model"},
                }, False, usage_out=usage,
                worker_config={"provider": "ollama", "model": "worker-model"})

        self.assertEqual(verdict, "needs_review")
        self.assertIn("different providers", detail)
        self.assertEqual(chat.call_count, 0)
        self.assertEqual(usage["api_calls"], 0)

    def test_different_provider_can_reach_the_critic_transport(self) -> None:
        usage: dict = {}

        def fake_chat(_model: str, _prompt: str, **kwargs: object) -> str:
            kwargs["usage_out"].update({"input_tokens": 3, "output_tokens": 2})
            return "VERDICT: PASS\nIndependent evaluation complete."

        with mock.patch.object(evaluation.execution, "ollama_chat", side_effect=fake_chat) as chat:
            verdict, _ = evaluation.run_critic(
                self._row(2), "deliverable", {
                    "critic": {"provider": "byteplus_coding", "model": "ark-code-latest"},
                }, False, usage_out=usage,
                worker_config={"provider": "ollama", "model": "worker-model"})

        self.assertEqual(verdict, "pass")
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(usage["api_calls"], 1)
        self.assertEqual(usage["total_tokens"], 5)

    def test_missing_provider_fails_closed(self) -> None:
        self.assertFalse(evaluation.critic_is_independent(
            {"model": "worker-model"},
            {"provider": "byteplus_coding", "model": "ark-code-latest"}))


if __name__ == "__main__":
    unittest.main()
