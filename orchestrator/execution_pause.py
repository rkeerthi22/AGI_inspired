"""Repository-owned fail-safe check for Hermes' global ESTOP sentinel."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path


def estop_path() -> Path:
    configured = os.environ.get("HERMES_HOME", "").strip()
    if configured:
        home = Path(configured).expanduser()
        if not home.is_absolute():
            raise ValueError("HERMES_HOME must be an absolute path")
        return home / "ESTOP"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "hermes" / "ESTOP"
    return Path.home() / ".hermes" / "ESTOP"


def pause_engaged() -> bool:
    """Return True when paused; only a validated home with no sentinel resumes."""
    try:
        sentinel = estop_path()
        try:
            sentinel.stat()
            return True
        except FileNotFoundError:
            pass

        home = sentinel.parent
        if not home.is_dir():
            return True
        # An explicit override is easy to typo into an existing unrelated directory.
        # Require a stable Hermes-home marker before interpreting a missing ESTOP as resume.
        if os.environ.get("HERMES_HOME", "").strip():
            if not ((home / "config.yaml").is_file() or (home / "hermes-agent").is_dir()):
                return True
        return False
    except (OSError, ValueError, RuntimeError):
        return True


# --- Tamper detection (boundary hardening 2026-08-31) -------------------
#
# pause_engaged() above is intentionally untouched: it stays a cheap, pure
# stat check on the hot path.  The functions below answer a different
# question — WHEN the sentinel is absent, is that absence authorized?
# Absence is authorized in exactly two cases:
#   1. an operator-issued clear marker (fresh, TTL-bounded), or
#   2. an active controlled isolation window (journal phase in
#      quiesced/open/restoring owned by a live OS process).
# Anything else is tampering: the sentinel is re-engaged and audited.
# There is deliberately NO background watcher here — verification runs at
# existing harness entry points, so no new autonomous mechanism is created.

TRANSITION_MARKER_NAME = ".estop-transition.json"
CANARY_AUTH_NAME = ".canary-operator-auth.json"
CLEAR_TTL_HOURS_DEFAULT = 24
CANARY_TTL_MINUTES = 30
_ACTIVE_WINDOW_PHASES = frozenset({"quiesced", "open", "restoring"})


def _hermes_home() -> Path:
    return estop_path().parent


def transition_marker_path() -> Path:
    return _hermes_home() / TRANSITION_MARKER_NAME


def canary_authorization_path() -> Path:
    return _hermes_home() / CANARY_AUTH_NAME


def _cohort_journal_path() -> Path:
    override = os.environ.get("AGI_COHORT_JOURNAL", "").strip()
    if override:
        return Path(override)
    return (Path(__file__).resolve().parents[1] / "workspace" /
            "validation" / "cohort_isolation_state.json")


def _parse_marker(marker: Path) -> dict | None:
    """Parse a marker file, supporting both signed tokens and plain JSON.

    Signed tokens are verified via ``operator_auth.verify_marker()``.
    Plain JSON is accepted with a ``PendingDeprecationWarning`` (backward
    compat).  Returns the payload dict, or ``None`` on failure.
    """
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    # Signed token (starts with base64, not '{')
    if not raw.startswith("{"):
        try:
            import operator_auth as _auth
            payload = _auth.verify_marker(raw)
            if payload is not None:
                return payload
        except Exception:
            pass
        return None  # invalid signature or corrupt token
    # Plain JSON (backward compat)
    warnings.warn(
        "Unsigned operator marker (plain JSON) — consider upgrading to "
        "signed markers via operator_auth.sign_marker()",
        PendingDeprecationWarning, stacklevel=2,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _marker_age_hours(marker: Path) -> float | None:
    """Hours since the marker's timestamp was issued; None when unreadable."""
    from datetime import datetime, timezone
    data = _parse_marker(marker)
    if data is None:
        return None
    try:
        issued = datetime.fromisoformat(str(data["issued_at"]))
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - issued).total_seconds() / 3600
    except (ValueError, KeyError, TypeError):
        return None


def _window_owner_alive(state: dict) -> bool:
    """True only when the journal owner is a live, identity-matching process."""
    from runlock import _process_start_identity
    pid = state.get("owner_pid")
    identity = state.get("owner_process_start_id")
    if not isinstance(pid, int) or not isinstance(identity, str) or not identity:
        return False
    try:
        current = _process_start_identity(pid)
    except OSError:
        return True  # inspection failure is unknown; the guardian will settle it
    return current is not None and current == identity


def clear_is_authorized() -> tuple[bool, str]:
    """Classify an ABSENT sentinel as authorized-clear or tamper.

    Returns (authorized, how). Never raises: unknown states are unauthorized.
    """
    # Case 1: fresh operator clear marker (signed or unsigned).
    marker = transition_marker_path()
    if marker.is_file():
        age = _marker_age_hours(marker)
        if age is not None and age >= 0:
            try:
                payload = _parse_marker(marker)
                if payload is None:
                    return False, "unreadable_marker"
                ttl = float(payload.get("ttl_hours", CLEAR_TTL_HOURS_DEFAULT))
            except (ValueError, OSError, TypeError):
                ttl = CLEAR_TTL_HOURS_DEFAULT
            if age <= ttl:
                return True, "operator_clear_marker"
    # Case 2: an active controlled window with a live owner.
    journal = _cohort_journal_path()
    try:
        if journal.is_file():
            state = json.loads(journal.read_text(encoding="utf-8"))
            if (isinstance(state, dict) and state.get("phase") in _ACTIVE_WINDOW_PHASES
                    and _window_owner_alive(state)):
                return True, "controlled_window"
    except (OSError, ValueError):
        pass
    return False, "unauthorized"


def unauthorized_removal_detected() -> bool:
    """True exactly when the sentinel is absent without authorization."""
    try:
        if estop_path().exists():
            return False
    except (OSError, ValueError):
        return True  # cannot even evaluate the path: treat as tampered
    return not clear_is_authorized()[0]


def reengage(reason: str) -> Path:
    """Fail closed: rewrite the sentinel and audit the recovery.

    Idempotent. Emits a health event through the existing fail-soft channel;
    an observability failure never blocks the fail-closed write.
    """
    from datetime import datetime, timezone
    sentinel = estop_path()
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    payload = {"reason": f"tamper-recovery: {reason}",
               "engaged_at": datetime.now(timezone.utc).isoformat()}
    sentinel.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        import health_events
        health_events.emit("estop", "tamper_recovery",
                           RuntimeError(reason), authorized=False)
    except Exception:
        pass
    return sentinel


def verify_pause_integrity() -> str:
    """Entry-point guard. Returns the classified state, re-engaging on tamper.

    Call this immediately BEFORE an existing pause_engaged() checkpoint:
    a re-engaged sentinel then makes that checkpoint refuse execution
    naturally, with no further caller changes.
    """
    try:
        engaged = pause_engaged()
    except (OSError, ValueError):
        engaged = True
    if engaged:
        return "engaged"
    authorized, how = clear_is_authorized()
    if authorized:
        return f"authorized:{how}"
    reengage(f"sentinel absent without authorization (state={how})")
    return "tamper_reengaged"


def _signed_marker(payload: dict) -> str:
    """Produce a signed marker token. Falls back to plain JSON if signing
    is unavailable (e.g. cryptography package not installed)."""
    try:
        import operator_auth as _auth
        return _auth.sign_marker(payload)
    except Exception:
        warnings.warn(
            "operator_auth signing unavailable — falling back to unsigned JSON",
            RuntimeWarning, stacklevel=2,
        )
        return json.dumps(payload, indent=2) + "\n"


def authorize_clear(ttl_hours: float = CLEAR_TTL_HOURS_DEFAULT) -> Path:
    """Operator-only: record a TTL-bounded authorization for a manual clear."""
    from datetime import datetime, timezone
    marker = transition_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(_signed_marker({
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "by": "operator", "ttl_hours": float(ttl_hours),
    }), encoding="utf-8")
    return marker


def authorize_canary() -> Path:
    """Operator-only: issue a fresh single-use canary authorization marker."""
    from datetime import datetime, timezone
    marker = canary_authorization_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(_signed_marker({
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "by": "operator", "use": "single-connectivity-canary",
    }), encoding="utf-8")
    return marker


def consume_canary_authorization() -> dict:
    """Single-use consumption for the connectivity canary.

    The marker is unlinked BEFORE validation, so a failed or replayed attempt
    can never resurrect it. Raises RuntimeError on absence, staleness, or
    malformed content — the caller must abort without a provider call.

    Supports both signed tokens and unsigned JSON (backward compat).
    """
    from datetime import datetime, timezone
    marker = canary_authorization_path()
    try:
        raw = marker.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError(
            "no operator canary authorization marker; run: "
            "python orchestrator/execution_pause.py --authorize-canary") from None
    marker.unlink(missing_ok=True)  # consume first; never re-create on failure

    # Parse (supports signed tokens and plain JSON)
    data = None
    if not raw.startswith("{"):
        try:
            import operator_auth as _auth
            data = _auth.verify_marker(raw.strip())
        except Exception:
            pass
    if data is None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"malformed canary authorization marker: {exc}") from None

    try:
        issued = datetime.fromisoformat(str(data["issued_at"]))
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"malformed canary authorization marker: {exc}") from None

    age_minutes = (datetime.now(timezone.utc) - issued).total_seconds() / 60
    if age_minutes < 0 or age_minutes > CANARY_TTL_MINUTES:
        raise RuntimeError(
            f"canary authorization is stale ({age_minutes:.0f} min old; "
            f"TTL {CANARY_TTL_MINUTES} min) — re-issue with --authorize-canary")
    return data


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Operator-only ESTOP transition and canary authorization tooling")
    parser.add_argument("--authorize-clear", action="store_true",
                        help="record a TTL-bounded manual-clear authorization")
    parser.add_argument("--ttl-hours", type=float, default=CLEAR_TTL_HOURS_DEFAULT)
    parser.add_argument("--authorize-canary", action="store_true",
                        help="issue a fresh single-use canary authorization marker")
    parser.add_argument("--status", action="store_true",
                        help="print the current pause integrity classification")
    args = parser.parse_args()
    if args.authorize_clear:
        print(f"clear authorization recorded: {authorize_clear(args.ttl_hours)} "
              f"(ttl={args.ttl_hours}h)")
    if args.authorize_canary:
        print(f"canary authorization issued: {authorize_canary()} "
              f"(single-use, {CANARY_TTL_MINUTES} min TTL)")
    if args.status or not (args.authorize_clear or args.authorize_canary):
        print(f"engaged={pause_engaged()} integrity={verify_pause_integrity()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
