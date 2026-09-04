"""Model-free regressions for the worker egress broker policy."""
from __future__ import annotations

import json
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import egress_policy  # noqa: E402


checks = 0
failures: list[str] = []


def check(label: str, condition: bool) -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def config_data() -> dict:
    return {
        "schema_version": 1,
        "mode": "broker_required",
        "broker": {"host": "127.0.0.1", "port": 8787, "connect_port": 443,
                   "idle_timeout_seconds": 30, "max_connection_bytes": 10485760,
                   "allowed_hosts": ["api.example.test"]},
        "attestation": {
            "environment_variable": "TEST_EGRESS_ATTESTATION",
            "purpose": "egress-boundary-v1",
            "max_age_hours": 24,
            "required_evidence": ["restricted_worker_identity", "deny_direct_egress",
                                  "broker_only_egress", "raw_socket_bypass_test",
                                  "private_address_test"],
            "required_claims": ["worker_identity", "worker_program_sha256",
                                "broker_program_sha256", "boundary_policy_id"],
        },
        "audit_log_environment_variable": "TEST_EGRESS_AUDIT",
    }


with tempfile.TemporaryDirectory(dir=ROOT / "workspace", ignore_cleanup_errors=True) as raw:
    root = Path(raw)
    config = root / "policy.yaml"
    config.write_text(json.dumps(config_data()), encoding="utf-8")
    policy = egress_policy.load_policy(config)

    def public_resolver(_host, _port, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    host, addresses = egress_policy.authorize_destination(
        "api.example.test", 443, policy, resolver=public_resolver)
    check("allowlisted global destination is authorized",
          host == "api.example.test" and addresses == ("8.8.8.8",))

    try:
        egress_policy.authorize_destination("untrusted.example", 443, policy,
                                            resolver=public_resolver)
        denied_host = False
    except egress_policy.EgressPolicyError:
        denied_host = True
    check("unallowlisted host is denied", denied_host)

    def private_resolver(_host, _port, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    try:
        egress_policy.authorize_destination("api.example.test", 443, policy,
                                            resolver=private_resolver)
        denied_private = False
    except egress_policy.EgressPolicyError:
        denied_private = True
    check("private DNS answer is denied", denied_private)

    attestation = root / "attestation.token"
    attestation.write_text("signed-token", encoding="utf-8")
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    payload = {
        "purpose": "egress-boundary-v1",
        "policy_sha256": policy.digest,
        "broker_endpoint": "127.0.0.1:8787",
        "issued_at": now.isoformat(),
        "evidence": sorted(policy.required_evidence),
        "claims": {
            "worker_identity": "AGI_like_worker",
            "worker_program_sha256": "a" * 64,
            "broker_program_sha256": "b" * 64,
            "boundary_policy_id": "AGI-like-worker-deny-direct-egress",
        },
    }
    environment = {"TEST_EGRESS_ATTESTATION": str(attestation)}
    state = egress_policy.boundary_state(config, environment,
                                         verify_token=lambda _token: payload, now=now)
    check("signed complete boundary attestation passes", state["ok"])
    payload["evidence"] = ["restricted_worker_identity"]
    state = egress_policy.boundary_state(config, environment,
                                         verify_token=lambda _token: payload, now=now)
    check("missing raw-socket evidence fails closed", not state["ok"])

    payload["evidence"] = sorted(policy.required_evidence)
    payload["claims"].pop("worker_identity")
    state = egress_policy.boundary_state(config, environment,
                                         verify_token=lambda _token: payload, now=now)
    check("missing worker-identity claim fails closed", not state["ok"])

    child = egress_policy.worker_environment(
        {"HTTP_PROXY": "http://attacker.invalid", "SAFE": "value"}, config,
        state_loader=lambda _path: {"ok": True})
    check("worker proxy is overwritten by verified broker",
          child["HTTPS_PROXY"] == "http://127.0.0.1:8787" and
          child["HTTP_PROXY"] == "http://127.0.0.1:8787")
    check("untrusted inherited proxy is not retained", "attacker.invalid" not in child.values())

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    raise SystemExit("FAILURES: " + ", ".join(failures))
