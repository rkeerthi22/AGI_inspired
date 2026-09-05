"""Synthetic keys and temporary local IPC only; no Credential Manager writes."""
from __future__ import annotations

import base64
import contextlib
import io
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import audit_replication as replication
import audit_signer_protocol as protocol
import audit_signer_pipe as pipe
import audit_signer_service as service
import audit_signing as client
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class AuditSignerTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.config = protocol.SignerConfig(
            r"\\.\pipe\AGI_like_audit_test_" + uuid.uuid4().hex,
            "S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1002", "S-1-5-21-1-2-3-1003",
            service.public_bytes(self.key))
        self.signer = service.AuditSigner(self.config, self.key, self.config.signer_sid)
        digest = "a" * 64
        self.checkpoint = {
            "schema_version": 1, "task_id": 1,
            "artifact_relative_path": f"trajectories/task1-{digest}.trajectory.jsonl",
            "trajectory_sha256": digest, "source_bytes": 42,
            "replicated_at": "2026-09-05T00:00:00+00:00",
            "previous_checkpoint_hash": "GENESIS",
        }
        self.checkpoint["checkpoint_hash"] = replication._checkpoint_hash(self.checkpoint)

    def response(self, config, request):
        return self.signer.handle(request, self.config.controller_sid)

    def token(self, payload, key=None, version=protocol.VERSION):
        raw = protocol.canonical(payload)
        return ".".join((base64.b64encode(raw).decode(),
                         base64.b64encode((key or self.key).sign(raw)).decode(), version))

    def test_round_trip_and_health_use_no_operator_key(self):
        with mock.patch.object(client, "load_config", return_value=self.config), \
             mock.patch.object(pipe, "request", side_effect=self.response), \
             mock.patch.dict(sys.modules, {"operator_auth": None, "win32cred": None}):
            token = client.sign_checkpoint(self.checkpoint)
            self.assertEqual(client.verify_checkpoint(token), self.checkpoint)
            self.assertTrue(client.signer_state()["ok"])

    def test_wrong_key_and_mutated_token_rejected(self):
        payload = {"action": "audit_checkpoint", "checkpoint": self.checkpoint}
        foreign = self.token(payload, Ed25519PrivateKey.generate())
        self.assertIsNone(protocol.verify_token(foreign, self.config))
        token = self.token(payload)
        parts = token.split(".")
        parts[0] = base64.b64encode(b'{}').decode()
        self.assertIsNone(protocol.verify_token(".".join(parts), self.config))

    def test_signer_must_match_identity_and_public_pin(self):
        with self.assertRaises(protocol.SignerError):
            service.AuditSigner(self.config, self.key, self.config.controller_sid)
        with self.assertRaises(protocol.SignerError):
            service.AuditSigner(self.config, Ed25519PrivateKey.generate(), self.config.signer_sid)
        with self.assertRaises(protocol.SignerError):
            service.AuditSigner(replace(self.config, worker_sid=self.config.controller_sid),
                                self.key, self.config.signer_sid)

    def test_worker_foreign_and_restricted_callers_denied(self):
        request = {"op": "sign", "checkpoint": self.checkpoint}
        for sid, restricted in [(self.config.worker_sid, False), (self.config.signer_sid, False),
                                ("S-1-5-18", False), (self.config.controller_sid, True)]:
            with self.subTest(sid=sid, restricted=restricted):
                with self.assertRaisesRegex(protocol.SignerError, "caller_denied"):
                    self.signer.handle(request, sid, restricted)

    def test_service_is_not_general_operator_signing_oracle(self):
        for request in [{"op": "sign", "payload": {"action": "clear_estop"}},
                        {"op": "export_key"}, {"op": "health", "nonce": "bad"},
                        {"op": "sign", "checkpoint": {"action": "clear_estop"}}]:
            with self.assertRaises(protocol.SignerError):
                self.signer.handle(request, self.config.controller_sid)

    def test_checkpoint_shape_and_hash_checked(self):
        for key, value in [("source_bytes", True), ("task_id", None),
                           ("artifact_relative_path", "../secret"),
                           ("checkpoint_hash", "b" * 64), ("replicated_at", "bad")]:
            with self.subTest(key=key), self.assertRaises(protocol.SignerError):
                protocol.validate_checkpoint({**self.checkpoint, key: value})

    def test_wrong_payload_and_replayed_health_fail_closed(self):
        reply = {"ok": True, "token": self.token({"action": "audit_signer_health", "nonce": "0" * 64})}
        with mock.patch.object(client, "load_config", return_value=self.config), \
             mock.patch.object(pipe, "request", return_value=reply):
            with self.assertRaises(client.AuditSigningError):
                client.sign_checkpoint(self.checkpoint)
            self.assertFalse(client.signer_state()["ok"])

    def test_missing_config_and_offline_service_never_fall_back(self):
        with mock.patch.dict(os.environ, {"HARNESS_AUDIT_SIGNER_CONFIG": ""}):
            self.assertFalse(client.signer_state()["ok"])
            self.assertIsNone(client.verify_checkpoint("bad"))
            with self.assertRaises(client.AuditSigningError):
                client.sign_checkpoint(self.checkpoint)
        with mock.patch.object(client, "load_config", return_value=self.config), \
             mock.patch.object(pipe, "request", side_effect=OSError("offline")):
            self.assertFalse(client.signer_state()["ok"])
            with self.assertRaises(client.AuditSigningError):
                client.sign_checkpoint(self.checkpoint)

    def test_legacy_verification_requires_explicit_public_pin(self):
        legacy_key = Ed25519PrivateKey.generate()
        public = service.public_bytes(legacy_key)
        payload = {"action": "audit_checkpoint", "checkpoint": self.checkpoint,
                   "_public_key": base64.b64encode(public).decode()}
        token = self.token(payload, legacy_key, "v1")
        self.assertIsNone(protocol.verify_token(token, self.config))
        trusted = replace(self.config, legacy_public_keys=(public,),
                          legacy_checkpoint_hashes=frozenset({self.checkpoint["checkpoint_hash"]}))
        self.assertEqual(protocol.verify_token(token, trusted)["checkpoint"], self.checkpoint)
        changed = {**self.checkpoint, "source_bytes": 900}
        changed["checkpoint_hash"] = replication._checkpoint_hash(changed)
        forged = {**payload, "checkpoint": changed}
        self.assertIsNone(protocol.verify_token(self.token(forged, legacy_key, "v1"), trusted))
        payload["action"] = "clear_estop"
        self.assertIsNone(protocol.verify_token(self.token(payload, legacy_key, "v1"), trusted))

    def test_public_config_requires_local_pipe_and_distinct_accounts(self):
        data = {"schema_version": 1, "pipe": self.config.pipe,
                "signer_sid": self.config.signer_sid, "controller_sid": self.config.controller_sid,
                "worker_sid": self.config.worker_sid,
                "public_key": base64.b64encode(self.config.public_key).decode()}
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "public.json"
            with mock.patch.dict(os.environ, {"HARNESS_AUDIT_SIGNER_CONFIG": str(path)}):
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(protocol.load_config(), self.config)
                for changes in [{"pipe": r"\\remote\pipe\AGI_like_audit_test"},
                                {"worker_sid": self.config.controller_sid},
                                {"controller_sid": "S-1-1-0"}]:
                    path.write_text(json.dumps({**data, **changes}), encoding="utf-8")
                    with self.assertRaises(protocol.SignerError):
                        protocol.load_config()

    def test_pipe_acl_does_not_grant_controller_server_creation(self):
        self.assertEqual(protocol.CLIENT_ACCESS & 4, 0)
        sddl = pipe.pipe_sddl(self.config)
        self.assertIn(f"(D;;GA;;;{self.config.worker_sid})", sddl)
        self.assertNotIn(";;;WD)", sddl)
        self.assertNotIn(";;;AN)", sddl)

    def test_invalid_tokens_are_total_nonthrowing_verification(self):
        for token in (None, "", "{}.bad.v1", "x" * (protocol.MAX_MESSAGE + 1),
                      "e30=.eA==.audit-v2", "a.b.c.d"):
            self.assertIsNone(protocol.verify_token(token, self.config))

    def test_service_start_rejects_wrong_identity_before_loading_key(self):
        with mock.patch.object(sys, "argv", ["signer", "serve"]), \
             mock.patch.object(pipe, "current_sid", return_value=self.config.worker_sid), \
             mock.patch.object(service, "load_config", return_value=self.config), \
             mock.patch.object(service, "_load_key") as key:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(service.main(), 75)
            key.assert_not_called()

    def test_service_error_output_never_includes_exception_payload(self):
        output = io.StringIO()
        with mock.patch.object(service, "_main", side_effect=ValueError("private-canary-do-not-print")), \
             contextlib.redirect_stdout(output):
            self.assertEqual(service.main(), 75)
        self.assertNotIn("private-canary", output.getvalue())

    def test_impersonation_revert_failure_is_fatal(self):
        api, security = mock.Mock(), mock.Mock()
        security.GetTokenInformation.return_value = (object(), 0)
        security.ConvertSidToStringSid.return_value = self.config.controller_sid
        security.IsTokenRestricted.return_value = False
        security.RevertToSelf.side_effect = OSError("revert failed")
        windows = (None, api, mock.Mock(), None, None, mock.Mock(), security)
        with mock.patch.object(pipe, "_windows", return_value=windows):
            with self.assertRaises(pipe.PeerIdentityError):
                pipe._peer(object())
        # Must escape the service loop's recoverable OSError/SignerError catches.
        self.assertFalse(issubclass(pipe.PeerIdentityError, (OSError, protocol.SignerError)))

    @unittest.skipUnless(os.name == "nt", "Windows key provisioning boundary")
    def test_key_initialization_never_overwrites_unreadable_key(self):
        import win32cred
        with mock.patch.object(sys, "argv", ["signer", "initialize-key", "--expected-sid", self.config.signer_sid]), \
             mock.patch.object(pipe, "current_sid", return_value=self.config.signer_sid), \
             mock.patch.object(service, "_load_key", side_effect=ValueError("malformed")), \
             mock.patch.object(win32cred, "CredWrite") as write, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(service.main(), 75)
            write.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows IPC transport")
    def test_real_local_pipe_transport_and_peer_identity(self):
        # Transport-only same-user test, not proof of deployed account separation.
        actual_sid = pipe.current_sid()
        config = replace(self.config, controller_sid=actual_sid)
        stop = threading.Event()
        errors = []
        peers = []

        def handler(request, sid, restricted):
            peers.append(sid)
            return {"ok": True, "nonce": request["nonce"], "sid": sid, "restricted": restricted}

        def run():
            try:
                pipe.serve_pipe(config, handler, stop)
            except Exception as exc:
                errors.append(exc)

        # Only this temporary pipe grants the current test account server access.
        with mock.patch.object(pipe, "pipe_sddl", return_value=f"D:P(A;;GA;;;{actual_sid})"):
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                import win32pipe
                while True:
                    try:
                        win32pipe.WaitNamedPipe(config.pipe, 100)
                        break
                    except Exception:
                        if errors or time.monotonic() >= deadline:
                            self.fail(f"pipe not ready: {errors}")
                        time.sleep(0.02)
                reply = pipe.request(config, {"op": "health", "nonce": "0" * 64})
                self.assertEqual(reply["nonce"], "0" * 64)
                self.assertEqual(reply["sid"], actual_sid)
                self.assertIsInstance(reply["restricted"], bool)
                self.assertEqual(peers, [actual_sid])
                with self.assertRaises(Exception):
                    pipe.serve_pipe(config, handler, threading.Event())
            finally:
                stop.set()
                thread.join(7)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
