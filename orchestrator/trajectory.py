"""Structured append-only trajectory writer for task execution.

Produces ``runs/task<ID>.trajectory.jsonl`` — one JSON object per line,
monotonic sequence numbers, ISO-8601 UTC timestamps, no secrets.

Integration points (all additive, never destructive to existing artifacts):

- ``task_runner.py``: task_started, task_completed, task_failed
- ``execution.py``: provider_selected, failover_attempted, provider_failed
- ``retrieval_progress.py``: tool_call_finished, strategy_transition
- ``evaluation.py``: citecheck_completed, critic_evaluated, facts_extracted

Design constraints:

- schema_version = 1 (stable, increment only on breaking shape changes)
- strictly monotonic sequence numbers (int, no gaps, per-writer)
- no secrets, API keys, auth headers, or env-var values in payloads
- no full raw model outputs — reference artifact paths instead
- normal Python file flush/close semantics (no fsync per event)
- standard library only
"""

from __future__ import annotations

import json
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENESIS_HASH = "GENESIS"


# ── Secret redaction ──────────────────────────────────────────────────────
# The sanitizer runs at the trajectory boundary so no secret can leak through
# any event payload regardless of which module calls emit().  It targets the
# known secret env-var names and structural patterns (Authorization headers,
# Bearer tokens) without relying on call-site discipline.

_SECRET_ENV_NAMES: set[str] = set(
    filter(None, os.environ.get("AGI_TRAJECTORY_REDACT_NAMES", "").split(","))
)
_SECRET_ENV_NAMES.update(
    {"ARK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY",
     "GOOGLE_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
     "TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL",
     "HERMES_SECRET", "HERMES_TOKEN"}
)

# Patterns that look like Bearer tokens / API key values.
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
# Generic long base64-looking hex/alphanumeric secrets (40+ chars, typical API key length).
_API_KEY_VALUE_RE = re.compile(r'((?:sk|pk|api[_-]?key|token|secret|key)["\s:=]+)([A-Za-z0-9+/=]{32,})', re.IGNORECASE)


def _redact_string(value: str) -> str:
    """Strip known secret patterns from a single string value."""
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    value = _API_KEY_VALUE_RE.sub(r'\1"[REDACTED]"', value)
    return value


def _redact(obj: Any, depth: int = 0) -> Any:
    """Deep-redact secrets from an arbitrary JSON-serializable object."""
    if depth > 8:
        return "[MAX_DEPTH]"
    if isinstance(obj, str):
        return _redact_string(obj)
    if isinstance(obj, dict):
        return {k: _redact(v, depth + 1) for k, v in obj.items()
                if k not in _SECRET_ENV_NAMES and k.lower() not in _SECRET_ENV_NAMES}
    if isinstance(obj, (list, tuple, set)):
        return [_redact(item, depth + 1) for item in obj]
    return obj


def _canonical_event_bytes(event: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in event.items() if key != "event_hash"}
    return json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_event_bytes(event)).hexdigest()


def verify_chain(path: Path) -> bool:
    """Verify hashed events and legacy-to-hashed transition anchors."""
    if not path.is_file():
        return True
    previous = GENESIS_HASH
    saw_hashed = False
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                event = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return False
            if not isinstance(event, dict):
                return False
            stored = event.get("event_hash")
            if stored is None:
                if saw_hashed:
                    return False
                previous = hashlib.sha256(raw).hexdigest()
                continue
            if (event.get("prev_event_hash") != previous or
                    stored != _event_hash(event)):
                return False
            previous = stored
            saw_hashed = True
    return True


# ── Trajectory writer ─────────────────────────────────────────────────────

class TrajectoryWriter:
    """Append-only JSONL writer for a single task's execution trace.

    Thread-safe for the single-writer case (the harness orchestrator is
    single-threaded per task).  Not designed for concurrent writes from
    multiple threads or processes — that would require external locking.
    """

    def __init__(self, trajectory_path: Path, task_id: int, mission_id: str):
        self._path = trajectory_path
        self._task_id = task_id
        self._mission_id = mission_id
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence, self._needs_separator = self._resume_state()

    def _resume_state(self) -> tuple[int, bool]:
        """Return the highest valid sequence and whether an append needs a newline.

        A crash may leave a truncated final JSON object.  Valid earlier records
        remain authoritative; malformed records are ignored, and the next append
        is separated from an unterminated tail without rewriting existing bytes.
        """
        self._previous_hash = GENESIS_HASH
        if not self._path.is_file() or self._path.stat().st_size == 0:
            return 0, False

        highest = 0
        previous_hash = GENESIS_HASH
        with self._path.open("rb") as handle:
            for raw_bytes in handle:
                raw_line = raw_bytes.decode("utf-8", errors="replace")
                try:
                    event = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(event, dict) or event.get("task_id") != self._task_id:
                    continue
                sequence = event.get("sequence")
                if type(sequence) is int and sequence > highest:
                    highest = sequence
                    previous_hash = event.get("event_hash") or hashlib.sha256(raw_bytes).hexdigest()

        with self._path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            needs_separator = handle.read(1) not in (b"\n", b"\r")
        self._previous_hash = previous_hash
        return highest, needs_separator

    # ── read-only properties ──────────────────────────────────────────

    @property
    def task_id(self) -> int:
        return self._task_id

    @property
    def mission_id(self) -> str:
        return self._mission_id

    @property
    def sequence(self) -> int:
        return self._sequence

    @property
    def path(self) -> Path:
        return self._path

    # ── emit ──────────────────────────────────────────────────────────

    def emit(
        self,
        stage: str,
        event_type: str,
        *,
        actor: str = "orchestrator",
        payload: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one structured event to the trajectory file.

        Args:
            stage: Lifecycle phase (e.g. ``"execution"``, ``"evaluation"``).
            event_type: Stable event name (e.g. ``"task_started"``,
                        ``"provider_selected"``, ``"critic_evaluated"``).
            actor: Component that produced the event.
            payload: Structured event data (secrets are redacted).
            metrics: Optional numeric counters (tokens, latency, counts).

        Returns:
            The event dict that was written (for inspection in tests).
        """
        self._sequence += 1
        event: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": f"evt-{self._task_id}-{self._sequence:04d}",
            "sequence": self._sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": self._task_id,
            "mission_id": self._mission_id,
            "stage": stage,
            "event_type": event_type,
            "actor": actor,
            "payload": _redact(payload) if payload else {},
            "prev_event_hash": self._previous_hash,
        }
        if metrics:
            event["metrics"] = {k: v for k, v in metrics.items()
                               if k not in _SECRET_ENV_NAMES}
        event["event_hash"] = _event_hash(event)
        self._previous_hash = event["event_hash"]
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with open(self._path, "a", encoding="utf-8") as fh:
            if self._needs_separator:
                fh.write("\n")
                self._needs_separator = False
            fh.write(line)
        return event

    # ── convenience helpers ───────────────────────────────────────────

    def task_started(self, spec: str, worker_model: str,
                     worker_provider: str) -> dict[str, Any]:
        return self.emit("lifecycle", "task_started",
            payload={"spec": spec[:500], "worker_model": worker_model,
                     "worker_provider": worker_provider})

    def task_completed(self, verdict: str, status: str,
                       facts_extracted: int = 0) -> dict[str, Any]:
        return self.emit("lifecycle", "task_completed",
            payload={"verdict": verdict, "status": status,
                     "facts_extracted": facts_extracted})

    def task_failed(self, reason: str, failure_stage: str = "",
                    detail: str = "") -> dict[str, Any]:
        return self.emit("lifecycle", "task_failed",
            payload={"reason": reason, "failure_stage": failure_stage,
                     "detail": detail[:500]})

    def provider_selected(self, provider: str, model: str,
                          rung: int = 1, total_rungs: int = 1) -> dict[str, Any]:
        return self.emit("execution", "provider_selected",
            payload={"provider": provider, "model": model,
                     "rung": rung, "total_rungs": total_rungs})

    def provider_failed(self, provider: str, model: str,
                        reason: str, rung: int = 1,
                        total_rungs: int = 1) -> dict[str, Any]:
        return self.emit("execution", "provider_failed",
            payload={"provider": provider, "model": model,
                     "reason": reason, "rung": rung,
                     "total_rungs": total_rungs})

    def failover_attempted(self, from_provider: str, from_model: str,
                           to_provider: str, to_model: str,
                           reason: str, rung: int = 2) -> dict[str, Any]:
        return self.emit("execution", "failover_attempted",
            payload={"from_provider": from_provider, "from_model": from_model,
                     "to_provider": to_provider, "to_model": to_model,
                     "reason": reason, "rung": rung})

    def provider_skip(self, provider: str, model: str,
                      reason: str, rung: int = 1,
                      total_rungs: int = 1) -> dict[str, Any]:
        return self.emit("execution", "provider_skip",
            payload={"provider": provider, "model": model,
                     "reason": reason, "rung": rung,
                     "total_rungs": total_rungs})

    def citecheck_completed(self, total_urls: int, dead_urls: int,
                            dead_frac: float, hard_fail: bool = False
                            ) -> dict[str, Any]:
        return self.emit("evaluation", "citecheck_completed",
            payload={"total_urls": total_urls, "dead_urls": dead_urls,
                     "dead_frac": dead_frac, "hard_fail": hard_fail})

    def critic_evaluated(self, verdict: str, model: str = "",
                         provider: str = "") -> dict[str, Any]:
        return self.emit("evaluation", "critic_evaluated",
            payload={"verdict": verdict, "model": model,
                     "provider": provider})

    def facts_extracted(self, count: int, model: str = "",
                        provider: str = "") -> dict[str, Any]:
        return self.emit("evaluation", "facts_extracted",
            payload={"count": count, "model": model,
                     "provider": provider})

    def tool_call_finished(self, tool_name: str, stage_name: str,
                           call_index: int, novel: bool,
                           urls_found: int) -> dict[str, Any]:
        return self.emit("execution", "tool_call_finished",
            payload={"tool": tool_name, "stage": stage_name,
                     "call_index": call_index, "novel": novel,
                     "urls_found": urls_found})

    def strategy_transition(self, from_stage: str, to_stage: str,
                            reason: str) -> dict[str, Any]:
        return self.emit("execution", "strategy_transition",
            payload={"from": from_stage, "to": to_stage,
                     "reason": reason})

    def tool_redirect(self, tool_name: str, required_stage: str,
                      count_violation: bool) -> dict[str, Any]:
        return self.emit("execution", "tool_redirect_violation",
            payload={"tool": tool_name, "required_stage": required_stage,
                     "count_violation": count_violation})

    def finalization_started(self, evidence_items: int,
                             evidence_chars: int) -> dict[str, Any]:
        return self.emit("execution", "finalization_started",
            payload={"evidence_items": evidence_items,
                     "evidence_chars": evidence_chars})

    def finalization_finished(self, success: bool,
                              reason: str = "") -> dict[str, Any]:
        return self.emit("execution", "finalization_finished",
            payload={"success": success, "reason": reason})

    def retrieval_exhausted(self, calls_executed: int,
                            rejected_calls: int) -> dict[str, Any]:
        return self.emit("execution", "retrieval_exhausted",
            payload={"calls_executed": calls_executed,
                     "rejected_calls": rejected_calls})


# ── Module-level access ──────────────────────────────────────────────────
# The harness is single-task-at-a-time, so one module-level active writer
# is simpler than threading a TrajectoryWriter through every call signature.
# Callers that need explicit control (tests, subprocess boundaries) can
# instantiate directly.

_active: TrajectoryWriter | None = None


def active() -> TrajectoryWriter | None:
    return _active


def begin(task_id: int, mission_id: str) -> TrajectoryWriter:
    """Create (or replace) the active trajectory writer for the current task."""
    global _active
    from runtime_context import RUNS
    _active = TrajectoryWriter(RUNS / f"task{task_id}.trajectory.jsonl",
                               task_id, mission_id)
    return _active


def end() -> None:
    """Clear the active writer (call after task completion)."""
    global _active
    _active = None
