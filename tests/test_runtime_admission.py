"""tests/test_runtime_admission.py — Model-free tests for fail-closed runtime admission.

Validates that:
1. Profiles resolve correctly ('development' by default, 'release' when explicit or env-set).
2. Development profile admits when unpaused, and fails closed when ESTOP is engaged.
3. Release profile fails closed when any release prerequisite fails:
   - ESTOP engaged
   - Egress boundary unattested / invalid token
   - Off-machine audit replication unverified
   - Dependency hashes missing from bootstrap or requirements lock
   - Worker and critic use the same LLM provider (lack of independence)
4. Release profile admits when all prerequisites are verified.
5. batch_runner and run_task fail closed before dispatching tasks under release profile.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))

import runtime_admission  # noqa: E402
import batch_runner  # noqa: E402
import run_task  # noqa: E402


class RuntimeAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        # Create minimal repo structure in temp_dir
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)

        # Write valid models.yaml with independent critic
        (self.root / "config" / "models.yaml").write_text(
            """roles:
  worker:
    provider: ollama
    model: qwen2.5:7b-instruct
  critic:
    provider: anthropic
    model: claude-3-5-sonnet
  manager:
    provider: gemini
    model: gemini-2.5-pro
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_profile_resolution(self) -> None:
        self.assertEqual(runtime_admission.get_harness_profile({}), "development")
        self.assertEqual(
            runtime_admission.get_harness_profile({"HARNESS_PROFILE": "release"}),
            "release",
        )
        self.assertEqual(
            runtime_admission.get_harness_profile({"HARNESS_PROFILE": "unknown"}),
            "development",
        )
        self.assertEqual(
            runtime_admission.get_harness_profile(explicit_profile="release"),
            "release",
        )

    @mock.patch("execution_pause.verify_pause_integrity")
    @mock.patch("execution_pause.pause_engaged", return_value=False)
    def test_development_profile_admits_when_not_paused(
        self, _mock_pause, _mock_integrity
    ) -> None:
        decision = runtime_admission.check_admission(profile="development", root=self.root)
        self.assertTrue(decision.admitted)
        self.assertEqual(decision.profile, "development")
        self.assertEqual(decision.blockers, ())

        enforced = runtime_admission.enforce_runtime_admission(
            profile="development", root=self.root
        )
        self.assertTrue(enforced.admitted)

    @mock.patch("execution_pause.verify_pause_integrity")
    @mock.patch("execution_pause.pause_engaged", return_value=True)
    def test_development_profile_refuses_when_estop_engaged(
        self, _mock_pause, _mock_integrity
    ) -> None:
        decision = runtime_admission.check_admission(profile="development", root=self.root)
        self.assertFalse(decision.admitted)
        self.assertIn("estop_engaged", decision.blockers)

        with self.assertRaises(runtime_admission.RuntimeAdmissionError):
            runtime_admission.enforce_runtime_admission(profile="development", root=self.root)

    @mock.patch("execution_pause.verify_pause_integrity")
    @mock.patch("execution_pause.pause_engaged", return_value=False)
    @mock.patch("egress_policy.boundary_state", return_value={"ok": False, "error": "unattested"})
    @mock.patch("audit_replication.audit_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.bootstrap_hash_enforcement_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.requirements_lock_state", return_value={"ok": True})
    def test_release_profile_fails_closed_on_egress(
        self, _lock, _boot, _audit, _egress, _pause, _integrity
    ) -> None:
        decision = runtime_admission.check_admission(profile="release", root=self.root)
        self.assertFalse(decision.admitted)
        self.assertTrue(any("egress_boundary_unattested" in b for b in decision.blockers))

        with self.assertRaises(runtime_admission.RuntimeAdmissionError):
            runtime_admission.enforce_runtime_admission(profile="release", root=self.root)

    @mock.patch("execution_pause.verify_pause_integrity")
    @mock.patch("execution_pause.pause_engaged", return_value=False)
    @mock.patch("egress_policy.boundary_state", return_value={"ok": True})
    @mock.patch("audit_replication.audit_state", return_value={"ok": False, "error": "unreachable"})
    @mock.patch("dependency_integrity.bootstrap_hash_enforcement_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.requirements_lock_state", return_value={"ok": True})
    def test_release_profile_fails_closed_on_audit(
        self, _lock, _boot, _audit, _egress, _pause, _integrity
    ) -> None:
        decision = runtime_admission.check_admission(profile="release", root=self.root)
        self.assertFalse(decision.admitted)
        self.assertTrue(any("audit_retention_unverified" in b for b in decision.blockers))

        with self.assertRaises(runtime_admission.RuntimeAdmissionError):
            runtime_admission.enforce_runtime_admission(profile="release", root=self.root)

    @mock.patch("execution_pause.verify_pause_integrity")
    @mock.patch("execution_pause.pause_engaged", return_value=False)
    @mock.patch("egress_policy.boundary_state", return_value={"ok": True})
    @mock.patch("audit_replication.audit_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.bootstrap_hash_enforcement_state", return_value={"ok": False})
    @mock.patch("dependency_integrity.requirements_lock_state", return_value={"ok": True})
    def test_release_profile_fails_closed_on_dependency_hashes(
        self, _lock, _boot, _audit, _egress, _pause, _integrity
    ) -> None:
        decision = runtime_admission.check_admission(profile="release", root=self.root)
        self.assertFalse(decision.admitted)
        self.assertIn("dependency_bootstrap_hashes_missing", decision.blockers)

        with self.assertRaises(runtime_admission.RuntimeAdmissionError):
            runtime_admission.enforce_runtime_admission(profile="release", root=self.root)

    @mock.patch("execution_pause.verify_pause_integrity")
    @mock.patch("execution_pause.pause_engaged", return_value=False)
    @mock.patch("egress_policy.boundary_state", return_value={"ok": True})
    @mock.patch("audit_replication.audit_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.bootstrap_hash_enforcement_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.requirements_lock_state", return_value={"ok": True})
    def test_release_profile_fails_closed_on_dependent_critic(
        self, _lock, _boot, _audit, _egress, _pause, _integrity
    ) -> None:
        # Write models.yaml with same provider for worker and critic
        (self.root / "config" / "models.yaml").write_text(
            """roles:
  worker:
    provider: ollama
    model: qwen2.5:7b-instruct
  critic:
    provider: ollama
    model: mistral:7b-instruct
  manager:
    provider: gemini
    model: gemini-2.5-pro
""",
            encoding="utf-8",
        )
        decision = runtime_admission.check_admission(profile="release", root=self.root)
        self.assertFalse(decision.admitted)
        self.assertIn("critic_provider_not_independent", decision.blockers)

        with self.assertRaises(runtime_admission.RuntimeAdmissionError):
            runtime_admission.enforce_runtime_admission(profile="release", root=self.root)

    @mock.patch("execution_pause.verify_pause_integrity")
    @mock.patch("execution_pause.pause_engaged", return_value=False)
    @mock.patch("egress_policy.boundary_state", return_value={"ok": True})
    @mock.patch("audit_replication.audit_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.bootstrap_hash_enforcement_state", return_value={"ok": True})
    @mock.patch("dependency_integrity.requirements_lock_state", return_value={"ok": True})
    def test_release_profile_admits_when_all_prerequisites_met(
        self, _lock, _boot, _audit, _egress, _pause, _integrity
    ) -> None:
        decision = runtime_admission.check_admission(profile="release", root=self.root)
        self.assertTrue(decision.admitted)
        self.assertEqual(decision.blockers, ())

        enforced = runtime_admission.enforce_runtime_admission(profile="release", root=self.root)
        self.assertTrue(enforced.admitted)

    @mock.patch("runtime_admission.enforce_runtime_admission", side_effect=runtime_admission.RuntimeAdmissionError("mock egress failure"))
    @mock.patch("batch_runner.preflight", return_value=True)
    def test_batch_runner_release_refuses_on_admission_error(self, _preflight, _enforce) -> None:
        args = argparse.Namespace(
            scorecard=False,
            canaries=False,
            resume=False,
            dry_run=False,
            mission="001-shopify-competitor-intel",
            max_tasks=1,
            deliver=False,
            release=True,
        )
        rc = batch_runner._run(args)
        self.assertEqual(rc, 75)

    @mock.patch("run_task.resolve_mission_path", return_value=Path("mission.md"))
    @mock.patch("run_task.parse_mission", return_value={"id": "safe"})
    @mock.patch("run_task.pass_criteria_for", return_value="criteria")
    @mock.patch("run_task.execution_pause.verify_pause_integrity")
    @mock.patch("run_task.execution_pause.pause_engaged", return_value=False)
    @mock.patch("run_task.integrity.preflight", return_value=True)
    @mock.patch("runtime_admission.enforce_runtime_admission", side_effect=runtime_admission.RuntimeAdmissionError("mock egress failure"))
    def test_run_task_release_refuses_on_admission_error(
        self, _enforce, _preflight, _pause, _integrity, _crit, _parse, _resolve
    ) -> None:
        with mock.patch("run_task.runlock.acquire") as _lock:
            _lock.return_value.__enter__.return_value = None
            _lock.return_value.__exit__.return_value = None
            rc = run_task.main(["--mission", "safe", "--release"])
            self.assertEqual(rc, 5)


if __name__ == "__main__":
    unittest.main()
