"""Fail-closed worker egress policy and signed boundary attestation.

The local broker is useful only when the worker is also restricted by an OS
boundary.  A proxy environment variable alone is not containment: an agent
could clear it or open a raw socket.  This module therefore refuses to launch
a worker until an operator-signed, time-bounded provisioning attestation names
the exact policy digest and records the required OS-level evidence.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "egress_policy.yaml"


class EgressPolicyError(RuntimeError):
    """The worker cannot be launched under a verified egress boundary."""


@dataclass(frozen=True)
class EgressPolicy:
    host: str
    port: int
    connect_port: int
    idle_timeout_seconds: int
    max_connection_bytes: int
    allowed_hosts: frozenset[str]
    attestation_env: str
    attestation_purpose: str
    max_age_hours: int
    required_evidence: frozenset[str]
    required_claims: frozenset[str]
    audit_log_env: str
    digest: str


def _canonical_policy_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normal_host(host: str) -> str:
    try:
        value = host.strip().rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, AttributeError):
        raise EgressPolicyError("invalid_host") from None
    if not value or any(part == "" for part in value.split(".")):
        raise EgressPolicyError("invalid_host")
    return value


def load_policy(path: Path = POLICY_PATH) -> EgressPolicy:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EgressPolicyError(f"policy_unavailable:{type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise EgressPolicyError("invalid_policy_schema")
    if data.get("mode") != "broker_required":
        raise EgressPolicyError("broker_mode_required")
    broker = data.get("broker")
    attestation = data.get("attestation")
    if not isinstance(broker, dict) or not isinstance(attestation, dict):
        raise EgressPolicyError("invalid_policy_sections")
    host = str(broker.get("host") or "")
    if host != "127.0.0.1":
        raise EgressPolicyError("broker_must_bind_loopback")
    try:
        port = int(broker.get("port"))
        connect_port = int(broker.get("connect_port"))
        idle_timeout = int(broker.get("idle_timeout_seconds"))
        max_connection_bytes = int(broker.get("max_connection_bytes"))
        max_age = int(attestation.get("max_age_hours"))
    except (TypeError, ValueError) as exc:
        raise EgressPolicyError("invalid_policy_numbers") from exc
    if not (1 <= port <= 65535 and connect_port == 443 and max_age > 0 and
            1 <= idle_timeout <= 300 and 1024 <= max_connection_bytes <= 104857600):
        raise EgressPolicyError("unsafe_policy_numbers")
    raw_hosts = broker.get("allowed_hosts")
    if not isinstance(raw_hosts, list) or not raw_hosts:
        raise EgressPolicyError("allowlist_required")
    allowed = frozenset(_normal_host(str(hostname)) for hostname in raw_hosts)
    evidence = attestation.get("required_evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise EgressPolicyError("invalid_attestation_evidence")
    claims = attestation.get("required_claims")
    if not isinstance(claims, list) or not all(isinstance(item, str) for item in claims):
        raise EgressPolicyError("invalid_attestation_claims")
    env_name = str(attestation.get("environment_variable") or "")
    purpose = str(attestation.get("purpose") or "")
    audit_env = str(data.get("audit_log_environment_variable") or "")
    if not env_name or not purpose or not audit_env:
        raise EgressPolicyError("invalid_attestation_configuration")
    return EgressPolicy(
        host=host, port=port, connect_port=connect_port,
        idle_timeout_seconds=idle_timeout, max_connection_bytes=max_connection_bytes,
        allowed_hosts=allowed,
        attestation_env=env_name, attestation_purpose=purpose, max_age_hours=max_age,
        required_evidence=frozenset(evidence), required_claims=frozenset(claims),
        audit_log_env=audit_env,
        digest=hashlib.sha256(_canonical_policy_bytes(data)).hexdigest(),
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _claims_complete(payload: dict[str, Any], policy: EgressPolicy) -> bool:
    claims = payload.get("claims")
    if not isinstance(claims, dict):
        return False
    for name in policy.required_claims:
        value = claims.get(name)
        if name.endswith("_sha256"):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                return False
        elif not isinstance(value, str) or not value.strip():
            return False
    return True


def boundary_state(
    policy_path: Path = POLICY_PATH,
    environment: dict[str, str] | None = None,
    verify_token: Callable[[str], dict[str, Any] | None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the signed out-of-repo OS-boundary attestation, read-only."""
    try:
        policy = load_policy(policy_path)
    except EgressPolicyError as exc:
        return {"ok": False, "error": str(exc)}
    env = os.environ if environment is None else environment
    raw_path = str(env.get(policy.attestation_env) or "").strip()
    if not raw_path:
        return {"ok": False, "error": "attestation_missing", "policy_digest": policy.digest}
    path = Path(raw_path)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return {"ok": False, "error": f"attestation_unreadable:{type(exc).__name__}",
                "policy_digest": policy.digest}
    if verify_token is None:
        try:
            import operator_auth
            verify_token = operator_auth.verify_marker
        except Exception:
            return {"ok": False, "error": "operator_verifier_unavailable",
                    "policy_digest": policy.digest}
    payload = verify_token(token)
    if not isinstance(payload, dict):
        return {"ok": False, "error": "attestation_untrusted", "policy_digest": policy.digest}
    issued_at = _parse_timestamp(payload.get("issued_at"))
    present_evidence = payload.get("evidence")
    current = now or datetime.now(timezone.utc)
    valid_age = (issued_at is not None and issued_at <= current and
                 current - issued_at <= timedelta(hours=policy.max_age_hours))
    evidence_ok = (isinstance(present_evidence, list) and
                   policy.required_evidence.issubset(set(present_evidence)))
    claims_ok = _claims_complete(payload, policy)
    endpoint_ok = payload.get("broker_endpoint") == f"{policy.host}:{policy.port}"
    ok = (payload.get("purpose") == policy.attestation_purpose and
          payload.get("policy_sha256") == policy.digest and valid_age and
          endpoint_ok and evidence_ok and claims_ok)
    return {
        "ok": ok,
        "policy_digest": policy.digest,
        "broker_endpoint": f"{policy.host}:{policy.port}",
        "issued_at": issued_at.isoformat() if issued_at else None,
        "evidence": sorted(present_evidence) if isinstance(present_evidence, list) else [],
        "claims": sorted((payload.get("claims") or {}).keys())
        if isinstance(payload.get("claims"), dict) else [],
        "error": None if ok else "attestation_mismatch",
    }


def worker_environment(
    base: dict[str, str],
    policy_path: Path = POLICY_PATH,
    state_loader: Callable[..., dict[str, Any]] = boundary_state,
) -> dict[str, str]:
    """Return child-only proxy settings or fail before launching the worker."""
    state = state_loader(policy_path)
    if state.get("ok") is not True:
        raise EgressPolicyError(f"boundary_unverified:{state.get('error', 'unknown')}")
    policy = load_policy(policy_path)
    proxy = f"http://{policy.host}:{policy.port}"
    env = dict(base)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
                 "all_proxy", "NO_PROXY", "no_proxy"):
        env.pop(name, None)
    env.update({
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "ALL_PROXY": proxy,
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "HARNESS_EGRESS_POLICY_SHA256": policy.digest,
    })
    return env


def authorize_destination(
    host: str,
    port: int,
    policy: EgressPolicy,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> tuple[str, tuple[str, ...]]:
    """Resolve a permitted hostname once and reject all non-global addresses."""
    normalized = _normal_host(host)
    if normalized not in policy.allowed_hosts:
        raise EgressPolicyError("host_not_allowlisted")
    if port != policy.connect_port:
        raise EgressPolicyError("port_not_allowed")
    try:
        records = resolver(normalized, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressPolicyError("dns_resolution_failed") from exc
    addresses: list[str] = []
    for record in records:
        address = str(record[4][0])
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise EgressPolicyError("invalid_dns_answer") from exc
        if not parsed.is_global:
            raise EgressPolicyError("non_global_destination")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise EgressPolicyError("dns_resolution_empty")
    return normalized, tuple(addresses)
