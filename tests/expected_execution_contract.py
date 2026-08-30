"""Test-side specification for the production contracts built in the next step.

This module deliberately lives under tests/. Production must implement an equivalent
contract; the red suites compare the live implementation to this specification.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class OutcomeKind(str, Enum):
    PASS = "pass"
    FAILED = "failed"
    INFRA_FAILED = "infra_failed"
    QUOTA_WAIT = "quota_wait"
    PAUSED = "paused"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ExecutionOutcome:
    kind: OutcomeKind
    exit_code: int
    retryable: bool = False
    provider: str | None = None
    model: str | None = None
    error_category: str | None = None
    message: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()


class OnboardingPhase(str, Enum):
    ADMITTED = "admitted"
    COLLECTING = "collecting"
    PREPARED = "prepared"
    REVIEWED = "reviewed"
    DOMAIN_COMMITTED = "domain_committed"
    ARTIFACTS_PUBLISHED = "artifacts_published"
    TASK_FINALIZED = "task_finalized"


class DatabaseMutationKind(str, Enum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    SCHEMA = "schema"
    PRAGMA = "pragma"

