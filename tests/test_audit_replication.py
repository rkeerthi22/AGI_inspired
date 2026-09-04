"""Model-free regression coverage for signed off-machine trajectory retention."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH))

import audit_replication  # noqa: E402
import task_runner  # noqa: E402
import trajectory  # noqa: E402


def _sign(checkpoint: dict) -> str:
    return json.dumps({"checkpoint": checkpoint}, sort_keys=True)


def _verify(token: str) -> dict | None:
    try:
        payload = json.loads(token)
    except json.JSONDecodeError:
        return None
    return payload.get("checkpoint") if isinstance(payload, dict) else None


class AuditReplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.replica = self.root / "replica"
        self.replica.mkdir()
        self.config = self.root / "audit_retention.yaml"
        self.config.write_text(
            """schema_version: 1
mode: signed_hash_chain
replica:
  root_environment_variable: HARNESS_TEST_AUDIT_ROOT
  require_unc: false
  artifact_subdirectory: trajectories
  checkpoint_filename: trajectory-checkpoints.jsonl
enforcement_environment_variable: HARNESS_TEST_AUDIT_ENFORCE
checkpoint_max_age_hours: 24
minimum_retention_days: 365
""", encoding="utf-8")
        self.environment = {
            "HARNESS_TEST_AUDIT_ROOT": str(self.replica),
            "HARNESS_TEST_AUDIT_ENFORCE": "1",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _trajectory(self, task_id: int = 1) -> Path:
        path = self.root / f"task{task_id}.trajectory.jsonl"
        writer = trajectory.TrajectoryWriter(path, task_id, "audit-test")
        writer.task_started("audit test", "worker", "ollama")
        writer.task_completed("pass", "done")
        return path

    def _state(self) -> dict:
        return audit_replication.audit_state(
            self.config, self.environment, verify_checkpoint=_verify,
            signing_state=lambda: {"ok": True})

    def test_replication_is_signed_chained_and_fresh(self) -> None:
        source = self._trajectory()
        first = audit_replication.replicate_trajectory(
            source, self.config, self.environment, _sign, _verify)
        writer = trajectory.TrajectoryWriter(source, 1, "audit-test")
        writer.task_completed("pass", "done")
        second = audit_replication.replicate_trajectory(
            source, self.config, self.environment, _sign, _verify)

        self.assertNotEqual(first["checkpoint_hash"], second["checkpoint_hash"])
        self.assertTrue((self.replica / second["artifact_relative_path"]).is_file())
        state = self._state()
        self.assertTrue(state["ok"])
        self.assertEqual(state["checkpoints"], 2)
        self.assertTrue(state["artifact_ok"])

    def test_tampered_past_checkpoint_fails_closed(self) -> None:
        source = self._trajectory()
        audit_replication.replicate_trajectory(
            source, self.config, self.environment, _sign, _verify)
        checkpoint_path = self.replica / "trajectory-checkpoints.jsonl"
        record = json.loads(checkpoint_path.read_text(encoding="utf-8").splitlines()[0])
        record["checkpoint"]["source_bytes"] = 0
        checkpoint_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        state = self._state()
        self.assertFalse(state["ok"])
        self.assertEqual(state["error"], "checkpoint_signature_invalid")

    def test_missing_replica_root_fails_closed(self) -> None:
        state = audit_replication.audit_state(
            self.config, {"HARNESS_TEST_AUDIT_ENFORCE": "1"},
            verify_checkpoint=_verify, signing_state=lambda: {"ok": True})
        self.assertFalse(state["ok"])
        self.assertEqual(state["error"], "replica_root_missing")

    def test_trajectory_end_delegates_to_enforced_replication(self) -> None:
        previous = trajectory.active()
        writer = trajectory.TrajectoryWriter(self._trajectory(2), 2, "audit-test")
        trajectory._active = writer  # Explicit lifecycle test; begin() uses runtime RUNS.
        try:
            with mock.patch.object(audit_replication, "replicate_if_enforced") as replicate:
                trajectory.end()
            replicate.assert_called_once_with(writer.path)
            self.assertIsNone(trajectory.active())
        finally:
            trajectory._active = previous

    def test_task_runner_marks_enforced_replication_failure_as_infra_failed(self) -> None:
        writer = mock.Mock()
        with mock.patch.object(task_runner.trajectory, "begin", return_value=writer), \
             mock.patch.object(task_runner.trajectory, "end",
                               side_effect=RuntimeError("remote replica offline")), \
             mock.patch.object(task_runner, "_load_task", return_value={"spec": "test"}), \
             mock.patch.object(task_runner, "_prepare_task_input", return_value=object()), \
             mock.patch.object(task_runner, "_run_research_task", return_value="done"), \
             mock.patch.object(task_runner.ledger, "finish_task") as finish_task, \
             mock.patch.object(task_runner.integrity, "escalate") as escalate:
            with self.assertRaisesRegex(RuntimeError, "remote replica offline"):
                task_runner.run_task(
                    77, {"id": "audit-test"},
                    {"worker": {"provider": "ollama", "model": "worker"}})

        finish_task.assert_called_once()
        self.assertEqual(finish_task.call_args.kwargs["status"], "infra_failed")
        self.assertTrue(finish_task.call_args.kwargs["append_note"])
        self.assertEqual(escalate.call_args.kwargs["trigger"],
                         "remote_audit_replication_failure")


if __name__ == "__main__":
    unittest.main()
