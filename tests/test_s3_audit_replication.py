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

        if "IfMatch" in kwargs:
            current = self.objects.get(key)
            if not current or current.get("ETag") != kwargs["IfMatch"]:
                raise ClientError(
                    {"Error": {"Code": "PreconditionFailed", "Message": "Precondition failed"}},
                    "PutObject"
                )
        if kwargs.get("IfNoneMatch") == "*":
            if key in self.objects:
                raise ClientError(
                    {"Error": {"Code": "PreconditionFailed", "Message": "Precondition failed"}},
                    "PutObject"
                )

        import hashlib
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        self.objects[key] = {
            "Body": body,
            "ContentLength": len(body),
            "Metadata": kwargs.get("Metadata", {}),
            "ObjectLockMode": kwargs.get("ObjectLockMode"),
            "ObjectLockRetainUntilDate": kwargs.get("ObjectLockRetainUntilDate"),
            "ETag": etag,
        }
        return {"ETag": etag}

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
            "ETag": self.objects[key].get("ETag", '"mock-etag"'),
            "Metadata": self.objects[key].get("Metadata", {}),
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
            "Metadata": self.objects[key].get("Metadata", {}),
            "ETag": self.objects[key].get("ETag", '"mock-etag"'),
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

            # Verify checkpoint record exists in S3 and has ObjectLock
            checkpoint_put = next(c for c in put_calls if c["Key"] == self.config.checkpoint_key)
            self.assertEqual(checkpoint_put["ObjectLockMode"], "COMPLIANCE")
            self.assertIsNotNone(checkpoint_put["ObjectLockRetainUntilDate"])

            checkpoints = s3_rep.fetch_s3_checkpoints(self.mock_client, self.config)
            self.assertEqual(len(checkpoints), 1)

            # Verify latest-checkpoint manifest exists in S3 and has ObjectLock
            manifest_put = next(c for c in put_calls if c["Key"] == self.config.manifest_key)
            self.assertEqual(manifest_put["ObjectLockMode"], "COMPLIANCE")
            self.assertIsNotNone(manifest_put["ObjectLockRetainUntilDate"])
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

    def test_s3_audit_error_subclass(self):
        import audit_replication
        self.assertTrue(issubclass(s3_rep.S3AuditReplicationError, audit_replication.AuditReplicationError))
        err = s3_rep.S3AuditReplicationError("test")
        self.assertIsInstance(err, audit_replication.AuditReplicationError)

    def test_load_s3_config_invalid_retention_mode(self):
        with self.assertRaises(s3_rep.S3AuditReplicationError) as ctx:
            s3_rep.load_s3_config_from_env({
                "HARNESS_AUDIT_S3_BUCKET": "my-bucket",
                "HARNESS_AUDIT_RETENTION_MODE": "invalid_mode",
            })
        self.assertIn("invalid_retention_mode:INVALID_MODE", str(ctx.exception))

    def test_audit_replication_does_not_route_without_backend_s3(self):
        import audit_replication
        with tempfile.TemporaryDirectory() as td:
            # Set HARNESS_AUDIT_S3_BUCKET alone without HARNESS_AUDIT_BACKEND=s3
            env = {
                "HARNESS_AUDIT_S3_BUCKET": "my-vault-bucket",
                "HARNESS_AUDIT_ROOT": td,
                "HARNESS_AUDIT_ENFORCE": "1",
            }
            # Calling audit_state should NOT route to S3
            with mock.patch("s3_audit_replication.s3_audit_state") as mock_s3_state:
                res = audit_replication.audit_state(environment=env)
                mock_s3_state.assert_not_called()

    def test_replicate_concurrency_conflict_raises_error(self):
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
                "event_hash": "mockhash",
            }
            t_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            signed_map = {}
            def mock_sign(payload):
                sig = f"sig:{len(signed_map)}:{payload.get('checkpoint_hash', 'p')}"
                signed_map[sig] = payload
                return sig

            with mock.patch("trajectory.verify_chain", return_value=True):
                s3_rep.replicate_trajectory_s3(
                    t_path,
                    self.config,
                    s3_client=self.mock_client,
                    sign_checkpoint=mock_sign,
                    verify_checkpoint=lambda sig: signed_map.get(sig),
                    now=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
                )

                # Simulate concurrency conflict: fetch_s3_checkpoints returns a stale ETag
                # because another writer updated the object before put_object
                t_path2 = Path(td) / "task101.trajectory.jsonl"
                t_path2.write_text(json.dumps(event) + "\n", encoding="utf-8")
                def fake_fetch(client, config, return_etag=False):
                    if return_etag:
                        return [], '"stale-etag"'
                    return []

                with mock.patch("s3_audit_replication.fetch_s3_checkpoints", side_effect=fake_fetch):
                    with self.assertRaises(s3_rep.S3AuditReplicationError) as ctx:
                        s3_rep.replicate_trajectory_s3(
                            t_path2,
                            self.config,
                            s3_client=self.mock_client,
                            sign_checkpoint=mock_sign,
                            verify_checkpoint=lambda sig: signed_map.get(sig),
                            now=datetime(2026, 9, 12, 12, 1, 0, tzinfo=timezone.utc),
                        )
                    self.assertEqual(str(ctx.exception), "s3_checkpoint_concurrency_conflict")

    def test_verify_tampered_artifact_body_hash_mismatch(self):
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
                "event_hash": "mockhash",
            }
            raw = json.dumps(event) + "\n"
            t_path.write_text(raw, encoding="utf-8")

            signed_map = {}
            def mock_sign(payload):
                sig = f"sig:{len(signed_map)}:{payload.get('checkpoint_hash', 'p')}"
                signed_map[sig] = payload
                return sig

            with mock.patch("trajectory.verify_chain", return_value=True):
                res = s3_rep.replicate_trajectory_s3(
                    t_path,
                    self.config,
                    s3_client=self.mock_client,
                    sign_checkpoint=mock_sign,
                    verify_checkpoint=lambda sig: signed_map.get(sig),
                    now=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
                )

            # Confirm artifact passes untampered
            chain = s3_rep.verify_s3_checkpoint_chain(
                self.mock_client,
                self.config,
                lambda sig: signed_map.get(sig),
                verify_artifacts=True,
            )
            self.assertTrue(chain["ok"])

            # Tamper with the artifact in S3 keeping the EXACT same length
            art_key = res["artifact_relative_path"]
            orig_body = self.mock_client.objects[art_key]["Body"]
            tampered_body = b"X" + orig_body[1:]
            self.assertEqual(len(tampered_body), len(orig_body))
            self.mock_client.objects[art_key]["Body"] = tampered_body

            # Verification should fail with replica_artifact_hash_mismatch
            chain_tampered = s3_rep.verify_s3_checkpoint_chain(
                self.mock_client,
                self.config,
                lambda sig: signed_map.get(sig),
                verify_artifacts=True,
            )
            self.assertFalse(chain_tampered["ok"])
            self.assertEqual(chain_tampered["error"], "replica_artifact_hash_mismatch")

    def test_s3_audit_state_retention_floor_violation(self):
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
                "event_hash": "mockhash",
            }
            t_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            signed_map = {}
            def mock_sign(payload):
                sig = f"sig:{len(signed_map)}:{payload.get('checkpoint_hash', 'p')}"
                signed_map[sig] = payload
                return sig

            with mock.patch("trajectory.verify_chain", return_value=True):
                s3_rep.replicate_trajectory_s3(
                    t_path,
                    self.config,
                    s3_client=self.mock_client,
                    sign_checkpoint=mock_sign,
                    verify_checkpoint=lambda sig: signed_map.get(sig),
                    now=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
                )

            # Single checkpoint has chain_days = 0.0. With minimum_retention_days=30, it must report retention_floor_violation
            env = {
                "HARNESS_AUDIT_S3_BUCKET": self.config.bucket,
                "HARNESS_AUDIT_BACKEND": "s3",
                "HARNESS_AUDIT_MINIMUM_RETENTION_DAYS": "30",
            }
            state = s3_rep.s3_audit_state(
                environment=env,
                verify_checkpoint=lambda sig: signed_map.get(sig),
                signing_state=lambda: {"ok": True, "service": "AGI_AuditSigner"},
                now=datetime(2026, 9, 12, 12, 30, 0, tzinfo=timezone.utc),
                s3_client=self.mock_client,
            )
            self.assertFalse(state["ok"])
            self.assertEqual(state["error"], "retention_floor_violation")


if __name__ == "__main__":
    unittest.main()
