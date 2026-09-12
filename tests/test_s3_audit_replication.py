"""Hermetic unit tests for S3 / Backblaze B2 Object Lock WORM audit replication."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import s3_audit_replication as s3_rep


class MockS3Client:
    """In-memory mock of boto3 S3 client supporting Object Lock PutObject / GetObject / HeadObject."""

    def __init__(self):
        self.objects: dict[str, dict[str, Any]] = {}
        self.put_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        body = kwargs["Body"]
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.objects[key] = {
            "Body": body,
            "ContentLength": len(body),
            "Metadata": kwargs.get("Metadata", {}),
            "ObjectLockMode": kwargs.get("ObjectLockMode"),
            "ObjectLockRetainUntilDate": kwargs.get("ObjectLockRetainUntilDate"),
        }
        return {"ETag": '"mock-etag"'}

    def get_object(self, **kwargs):
        key = kwargs["Key"]
        if key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
                "GetObject"
            )
        return {
            "Body": io.BytesIO(self.objects[key]["Body"]),
            "ContentLength": self.objects[key]["ContentLength"],
        }

    def head_object(self, **kwargs):
        key = kwargs["Key"]
        if key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
                "HeadObject"
            )
        return {
            "ContentLength": self.objects[key]["ContentLength"],
            "Metadata": self.objects[key]["Metadata"],
        }


class TestS3AuditReplication(unittest.TestCase):

    def setUp(self):
        self.config = s3_rep.S3AuditConfig(
            bucket="test-audit-bucket",
            endpoint_url="https://s3.us-west-004.backblazeb2.com",
            region_name="us-west-004",
            access_key_id="test-key-id",
            secret_access_key="test-secret-key",
            retention_mode="COMPLIANCE",
            retention_days=365,
        )
        self.mock_client = MockS3Client()

    def test_load_s3_config_from_env(self):
        env = {
            "HARNESS_AUDIT_S3_BUCKET": "my-vault-bucket",
            "HARNESS_AUDIT_S3_ENDPOINT": "https://s3.example.com",
            "HARNESS_AUDIT_RETENTION_DAYS": "90",
            "HARNESS_AUDIT_RETENTION_MODE": "COMPLIANCE",
        }
        cfg = s3_rep.load_s3_config_from_env(env)
        self.assertEqual(cfg.bucket, "my-vault-bucket")
        self.assertEqual(cfg.endpoint_url, "https://s3.example.com")
        self.assertEqual(cfg.retention_days, 90)
        self.assertEqual(cfg.retention_mode, "COMPLIANCE")

    def test_load_s3_config_missing_bucket(self):
        with self.assertRaises(s3_rep.S3AuditReplicationError):
            s3_rep.load_s3_config_from_env({})

    def test_verify_empty_chain(self):
        result = s3_rep.verify_s3_checkpoint_chain(
            self.mock_client, self.config, lambda sig: None
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_replicate_trajectory_s3_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            t_path = Path(td) / "task100.trajectory.jsonl"
            # Write a valid dummy event
            event = {
                "event_id": "evt-100-0001",
                "sequence": 1,
                "timestamp": "2026-09-12T00:00:00+00:00",
                "stage": "lifecycle",
                "event_type": "task_started",
                "payload": {},
                "prev_event_hash": "GENESIS",
            }
            event["event_hash"] = "mockhash"
            t_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            def mock_verify_chain(p):
                return True

            with mock.patch("trajectory.verify_chain", side_effect=mock_verify_chain):
                res = s3_rep.replicate_trajectory_s3(
                    t_path,
                    self.config,
                    s3_client=self.mock_client,
                    sign_checkpoint=lambda cp: f"sig:{cp.get('checkpoint_hash', 'm')}",
                    verify_checkpoint=lambda sig: None,
                    now=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
                )

            self.assertIn("trajectories/task100-", res["artifact_relative_path"])
            self.assertTrue(res["checkpoint_hash"])

            # Verify that ObjectLock was requested in COMPLIANCE mode
            put_calls = self.mock_client.put_calls
            artifact_put = next(c for c in put_calls if "trajectories/task100-" in c["Key"])
            self.assertEqual(artifact_put["ObjectLockMode"], "COMPLIANCE")
            self.assertIsNotNone(artifact_put["ObjectLockRetainUntilDate"])

            # Verify checkpoint record exists in S3
            checkpoints = s3_rep.fetch_s3_checkpoints(self.mock_client, self.config)
            self.assertEqual(len(checkpoints), 1)

            # Verify latest-checkpoint manifest exists in S3
            self.assertIn(self.config.manifest_key, self.mock_client.objects)

    def test_replicate_fails_closed_on_invalid_local_trajectory(self):
        with tempfile.TemporaryDirectory() as td:
            t_path = Path(td) / "task100.trajectory.jsonl"
            t_path.write_text("corrupted", encoding="utf-8")

            with mock.patch("trajectory.verify_chain", return_value=False):
                with self.assertRaises(s3_rep.S3AuditReplicationError):
                    s3_rep.replicate_trajectory_s3(t_path, self.config, s3_client=self.mock_client)

    def test_s3_audit_state_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            t_path = Path(td) / "task100.trajectory.jsonl"
            event = {
                "event_id": "evt-100-0001",
                "sequence": 1,
                "timestamp": "2026-09-12T00:00:00+00:00",
                "stage": "lifecycle",
                "event_type": "task_started",
                "payload": {},
                "prev_event_hash": "GENESIS",
                "event_hash": "hash1",
            }
            t_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            signed_map = {}

            def mock_sign(payload):
                sig = f"sig:{len(signed_map)}:{payload.get('checkpoint_hash', 'p')}"
                signed_map[sig] = payload
                return sig

            def mock_verify(sig):
                return signed_map.get(sig)

            with mock.patch("trajectory.verify_chain", return_value=True):
                s3_rep.replicate_trajectory_s3(
                    t_path,
                    self.config,
                    s3_client=self.mock_client,
                    sign_checkpoint=mock_sign,
                    verify_checkpoint=mock_verify,
                    now=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
                )

            # Run s3_audit_state
            env = {
                "HARNESS_AUDIT_S3_BUCKET": self.config.bucket,
                "HARNESS_AUDIT_BACKEND": "s3",
            }
            state = s3_rep.s3_audit_state(
                environment=env,
                verify_checkpoint=mock_verify,
                signing_state=lambda: {"ok": True, "service": "AGI_AuditSigner"},
                now=datetime(2026, 9, 12, 12, 30, 0, tzinfo=timezone.utc),
                s3_client=self.mock_client,
            )
            self.assertTrue(state["ok"])
            self.assertEqual(state["backend"], "s3")
            self.assertEqual(state["checkpoints"], 1)
            self.assertEqual(state["immutability_guarantee"], "s3_object_lock_compliance")


if __name__ == "__main__":
    unittest.main()
