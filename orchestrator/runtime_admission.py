"""orchestrator/runtime_admission.py — Fail-closed runtime admission contract for release prerequisites.

Diagnostic preflight (operator_cli.py) surfaces release readiness to operators,
but runtime execution engines (batch_runner.py, task_runner.py, run_task.py)
must enforce admission at dispatch time.

Profiles:
- 'development' (default): Local development, unit/containment/integration test gates.
  Enforces local integrity (ESTOP pause integrity, database guard recovery, local Ollama).
  Release prerequisites (signed egress tokens, remote UNC audit roots) are diagnostic.
- 'release': Strict production/release execution.
  Fails closed before claiming tasks or dispatching workers if any release prerequisite
  (egress attestation, remote audit replication, dependency hash locking, independent critic)
  is missing, expired, or invalid.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]


class RuntimeAdmissionError(RuntimeError):
    """The runtime refused task or worker dispatch due to failed admission prerequisites."""


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    profile: str
    evaluated_at: str
    blockers: tuple[str, ...] = field(default_factory=tuple)
    checks: dict[str, Any] = field(default_factory=dict)


def get_harness_profile(
    env: Mapping[str, str] | None = None,
    explicit_profile: str | None = None,
) -> str:
    """Resolve the active execution profile ('release' or 'development')."""
    if explicit_profile:
        return str(explicit_profile).strip().lower()
    environment = os.environ if env is None else env
    raw = str(environment.get("HARNESS_PROFILE") or "").strip().lower()
    return raw if raw in ("release", "development", "canary", "test") else "development"


def check_admission(
    profile: str | None = None,
    env: Mapping[str, str] | None = None,
    root: Path = ROOT,
) -> AdmissionDecision:
    """Evaluate runtime admission checks against environment and repository state."""
    environment = os.environ if env is None else env
    target_profile = get_harness_profile(environment, profile)
    evaluated_at = datetime.now(timezone.utc).isoformat()
    checks: dict[str, Any] = {}
    blockers: list[str] = []

    # 1. ESTOP (universal across all profiles)
    try:
        import execution_pause
        execution_pause.verify_pause_integrity()
        estop_engaged = execution_pause.pause_engaged()
        checks["estop"] = {"ok": not estop_engaged, "engaged": estop_engaged}
        if estop_engaged:
            blockers.append("estop_engaged")
    except Exception as exc:
        checks["estop"] = {"ok": False, "error": str(exc)}
        blockers.append(f"estop_integrity_failed:{type(exc).__name__}")

    # If profile is not "release", admit as long as ESTOP is not engaged
    if target_profile != "release":
        return AdmissionDecision(
            admitted=len(blockers) == 0,
            profile=target_profile,
            evaluated_at=evaluated_at,
            blockers=tuple(blockers),
            checks=checks,
        )

    # In "release" profile, enforce full release prerequisites:

    # 2. Worker Egress Boundary Attestation
    try:
        import egress_policy
        egress_state = egress_policy.boundary_state(environment=environment)
        checks["egress_boundary"] = egress_state
        if egress_state.get("ok") is not True:
            blockers.append(f"egress_boundary_unattested:{egress_state.get('error', 'unknown')}")
    except Exception as exc:
        checks["egress_boundary"] = {"ok": False, "error": str(exc)}
        blockers.append(f"egress_boundary_error:{type(exc).__name__}")

    # 3. Off-Machine Remote Audit Retention
    try:
        import audit_replication
        audit_state = audit_replication.audit_state(environment=environment)
        checks["audit_retention"] = audit_state
        if audit_state.get("ok") is not True:
            blockers.append(f"audit_retention_unverified:{audit_state.get('error', 'unknown')}")
    except Exception as exc:
        checks["audit_retention"] = {"ok": False, "error": str(exc)}
        blockers.append(f"audit_retention_error:{type(exc).__name__}")

    # 4. Dependency Hash Enforcement
    try:
        import dependency_integrity
        bootstrap_state = dependency_integrity.bootstrap_hash_enforcement_state(
            root / "scripts" / "bootstrap.ps1"
        )
        lock_state = dependency_integrity.requirements_lock_state(
            root / "scripts" / "requirements.txt"
        )
        checks["dependency_hashes"] = {
            "ok": bootstrap_state.get("ok") is True and lock_state.get("ok") is True,
            "bootstrap": bootstrap_state,
            "lock": lock_state,
        }
        if bootstrap_state.get("ok") is not True:
            blockers.append("dependency_bootstrap_hashes_missing")
        if lock_state.get("ok") is not True:
            blockers.append("dependency_lock_hashes_missing")
    except Exception as exc:
        checks["dependency_hashes"] = {"ok": False, "error": str(exc)}
        blockers.append(f"dependency_hash_error:{type(exc).__name__}")

    # 5. Independent Critic Routing
    try:
        import yaml
        models_cfg = root / "config" / "models.yaml"
        cfg_data = yaml.safe_load(models_cfg.read_text(encoding="utf-8"))
        roles = cfg_data.get("roles") if isinstance(cfg_data, dict) else {}
        worker_cfg = roles.get("worker") if isinstance(roles, dict) else {}
        critic_cfg = roles.get("critic") if isinstance(roles, dict) else {}
        manager_cfg = roles.get("manager") if isinstance(roles, dict) else {}
        worker_p = str((worker_cfg or {}).get("provider") or "").strip().lower()
        critic_p = str((critic_cfg or {}).get("provider") or "").strip().lower()
        manager_p = str((manager_cfg or {}).get("provider") or "").strip().lower()
        independent = bool(worker_p and critic_p and manager_p and
                           worker_p != critic_p and critic_p != manager_p)
        checks["independent_critic"] = {"ok": independent, "worker": worker_p, "critic": critic_p}
        if not independent:
            blockers.append("critic_provider_not_independent")
    except Exception as exc:
        checks["independent_critic"] = {"ok": False, "error": str(exc)}
        blockers.append(f"critic_config_error:{type(exc).__name__}")

    admitted = len(blockers) == 0
    return AdmissionDecision(
        admitted=admitted,
        profile=target_profile,
        evaluated_at=evaluated_at,
        blockers=tuple(blockers),
        checks=checks,
    )


def enforce_runtime_admission(
    profile: str | None = None,
    env: Mapping[str, str] | None = None,
    root: Path = ROOT,
) -> AdmissionDecision:
    """Enforce runtime admission, raising RuntimeAdmissionError if refused."""
    decision = check_admission(profile=profile, env=env, root=root)
    if not decision.admitted:
        reasons = "; ".join(decision.blockers)
        raise RuntimeAdmissionError(
            f"runtime admission refused for profile '{decision.profile}': {reasons}"
        )
    return decision
