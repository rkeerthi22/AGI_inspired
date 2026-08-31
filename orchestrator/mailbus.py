"""Asynchronous file-based agent mailbox with 500ms os.scandir polling.

Implements Subsystem Specification 1 of the Munder Blueprint
(``docs/MUNDER_BLUEPRINT.md`` Section 2), grounded by the empirical findings in
``docs/HERMES_RESEARCH_MUNDER.md``.

Design constraints (all measured on this machine, Windows 11 / NTFS / Python 3.11):

- Single-volume affinity: the entire mailbox tree lives under
  ``S:\\AGI_like\\mailboxes``. Cross-volume temp directories are prohibited;
  ``os.replace`` is the atomic same-volume delivery primitive.
- 500ms ``os.scandir`` polling (not ``watchdog``): cost measured at ~1.1ms per
  1000 files, immune to ``ERROR_NOTIFY_ENUM_DIR`` silent event loss, no
  persistent directory handles.
- 10/50/200ms exponential-backoff retry ladder with jitter for ``os.replace``
  and ``os.unlink`` sharing violations (winerror 5 / 32).
- Zero execution backdoor: this module never imports ``run_task``,
  ``batch_runner``, ``execution``, or ``controlled_hermes`` (Invariant I6).
- Message bodies are strictly untrusted data — never ``eval()``, ``exec()``, or
  subshell-invoked.

Canonical directory layout::

    mailboxes/
      router.log.jsonl           # Append-only audit trail
      deferred.jsonl             # Buffered input queue for drain loops
      <agent-id>/
        inbox/                   # Messages addressed to agent
        inbox/.done/             # Archived / processed messages
        inbox/.tmp/              # Atomic delivery staging
        outbox/                  # Agent's private outbound messages (single-writer)
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from health_events import emit as emit_health_event
from runtime_context import MAILBOXES, log

# ── Constants ─────────────────────────────────────────────────────────────────

# Canonical message ID prefix: mbx-<8 hex chars>
MSG_ID_RE = re.compile(r"^mbx-[a-f0-9]{8}\.json$")

# Allowed message verbs (act field).
ALLOWED_VERBS: frozenset[str] = frozenset({
    "request", "inform", "propose", "query", "agree", "refuse", "done",
})

# Verbs that are terminal (no reply permitted).
TERMINAL_VERBS: frozenset[str] = frozenset({"inform", "done"})

# Verbs that expect a reply.
REPLY_EXPECTANT_VERBS: frozenset[str] = frozenset({"request", "propose", "query"})

# Maximum number of hops before a message is terminated.
MAX_HOPS: int = 10

# Maximum messages in a recipient's inbox before backpressure is applied.
INBOX_CAP: int = 50

# Retry ladder delays in milliseconds (with jitter).
RETRY_LADDER_MS: tuple[int, int, int] = (10, 50, 200)

# Maximum jitter added to each retry delay (ms).
RETRY_JITTER_MS: int = 5

# ── Proposal interception (Invariant I6) ─────────────────────────────────────

# Patterns that indicate a message body contains mission execution commands or
# ESTOP bypass requests — these are intercepted and rewritten as passive
# proposals to the Operator.
_INTERCEPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brun_task\s*\(", re.IGNORECASE),
    re.compile(r"\bexec(?:ute)?_mission\s*\(", re.IGNORECASE),
    re.compile(r"\bdispatch\s*\(", re.IGNORECASE),
    re.compile(r"\bESTOP\s*[=:]\s*(?:off|false|0|disable)", re.IGNORECASE),
    re.compile(r"\bauthorize_single_paused_canary\s*\(", re.IGNORECASE),
    re.compile(r"--controlled-window", re.IGNORECASE),
    re.compile(r"\bDatabaseMutationGuard\s*[=:]\s*(?:off|false|0|disable)", re.IGNORECASE),
)


def _contains_execution_command(body: str) -> bool:
    """Return True if *body* contains a mission-execution or ESTOP-bypass pattern."""
    return any(p.search(body) for p in _INTERCEPT_PATTERNS)


# ── Directory helpers ────────────────────────────────────────────────────────


def _ensure_dir(path: Path) -> None:
    """Create directory tree if it does not exist."""
    path.mkdir(parents=True, exist_ok=True)


def _agent_inbox(agent_id: str) -> Path:
    return MAILBOXES / agent_id / "inbox"


def _agent_inbox_tmp(agent_id: str) -> Path:
    return MAILBOXES / agent_id / "inbox" / ".tmp"


def _agent_inbox_done(agent_id: str) -> Path:
    return MAILBOXES / agent_id / "inbox" / ".done"


def _agent_outbox(agent_id: str) -> Path:
    return MAILBOXES / agent_id / "outbox"


# ── Message validation ───────────────────────────────────────────────────────


def validate_message(msg: dict[str, Any]) -> list[str]:
    """Validate a message dict against the canonical schema.

    Returns a list of validation error strings (empty = valid).
    """
    errors: list[str] = []

    # Required fields
    for field in ("id", "from", "to", "act", "subject", "body", "conversation", "hops", "created_at"):
        if field not in msg:
            errors.append(f"missing required field: {field}")

    if errors:
        return errors

    # id format
    if not isinstance(msg["id"], str) or not re.match(r"^mbx-[a-f0-9]{8}$", msg["id"]):
        errors.append(f"invalid message id: {msg.get('id')!r}")

    # act must be an allowed verb
    if msg.get("act") not in ALLOWED_VERBS:
        errors.append(f"invalid act verb: {msg.get('act')!r}; allowed: {sorted(ALLOWED_VERBS)}")

    # hops must be int and <= MAX_HOPS
    if not isinstance(msg.get("hops"), int) or msg["hops"] < 0:
        errors.append(f"invalid hops: {msg.get('hops')!r}")
    elif msg["hops"] > MAX_HOPS:
        errors.append(f"hop limit exceeded: {msg['hops']} > {MAX_HOPS}")

    # from and to must be non-empty strings
    for field in ("from", "to"):
        if not isinstance(msg.get(field), str) or not msg[field].strip():
            errors.append(f"invalid {field}: {msg.get(field)!r}")

    # subject and body must be strings (body may be empty for terminal messages)
    if not isinstance(msg.get("subject"), str):
        errors.append(f"invalid subject: {msg.get('subject')!r}")
    if not isinstance(msg.get("body"), str):
        errors.append(f"invalid body: {msg.get('body')!r}")

    # conversation must be non-empty string
    if not isinstance(msg.get("conversation"), str) or not msg["conversation"].strip():
        errors.append(f"invalid conversation: {msg.get('conversation')!r}")

    # created_at must be ISO-ish
    if isinstance(msg.get("created_at"), str):
        try:
            datetime.fromisoformat(msg["created_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            errors.append(f"invalid created_at timestamp: {msg.get('created_at')!r}")

    # in_reply_to if present must be a valid message id or null
    irt = msg.get("in_reply_to")
    if irt is not None and (not isinstance(irt, str) or not re.match(r"^mbx-[a-f0-9]{8}$", irt)):
        errors.append(f"invalid in_reply_to: {irt!r}")

    return errors


# ── Inbox / outbox operations ────────────────────────────────────────────────


def _inbox_count(agent_id: str) -> int:
    """Return the number of pending messages in an agent's inbox."""
    inbox = _agent_inbox(agent_id)
    if not inbox.is_dir():
        return 0
    return sum(1 for entry in os.scandir(inbox)
               if entry.is_file() and MSG_ID_RE.match(entry.name))


def _retry_with_backoff(operation: callable, description: str) -> bool:
    """Execute *operation* with the 10/50/200ms retry ladder for sharing violations.

    Returns True if the operation eventually succeeded, False if the ladder was
    exhausted.
    """
    for attempt, delay_ms in enumerate(RETRY_LADDER_MS, start=1):
        try:
            operation()
            return True
        except OSError as exc:
            # WinError 5 = ACCESS_DENIED, WinError 32 = SHARING_VIOLATION
            if exc.winerror not in (5, 32):
                raise
            if attempt >= len(RETRY_LADDER_MS):
                emit_health_event(
                    "mailbus",
                    description,
                    exc,
                    winerror=exc.winerror,
                    attempts=attempt,
                )
                return False
            jitter = (time.time_ns() % RETRY_JITTER_MS) / 1000.0
            time.sleep((delay_ms + jitter) / 1000.0)
    return False


# ── Atomic delivery ──────────────────────────────────────────────────────────


def _stage_and_deliver(msg_path: Path, recipient_id: str, msg_id: str) -> bool:
    """Stage, flush, fsync, and atomically ``os.replace`` a message into the
    recipient's inbox.

    This is the core delivery primitive (Blueprint §2.3, steps 3-7).
    """
    inbox = _agent_inbox(recipient_id)
    tmp_dir = _agent_inbox_tmp(recipient_id)
    _ensure_dir(tmp_dir)

    # Stage: copy bytes to a temp file under .tmp/
    tmp_name = f"{uuid.uuid4().hex}.tmp"
    tmp_path = tmp_dir / tmp_name

    try:
        # Read source message
        with msg_path.open("rb") as src:
            data = src.read()

        # Write to temp
        with tmp_path.open("wb") as dst:
            dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
    except OSError as exc:
        emit_health_event("mailbus", "stage_write", exc, msg_id=msg_id, recipient=recipient_id)
        return False

    # Atomic replace into inbox
    dest_path = inbox / f"{msg_id}.json"

    def _do_replace() -> None:
        os.replace(tmp_path, dest_path)

    if not _retry_with_backoff(_do_replace, f"deliver_replace:{msg_id}"):
        # Leave staged file; next poll cycle retries (idempotent).
        return False

    return True


def _archive_message(msg_path: Path, recipient_id: str) -> bool:
    """Move a processed inbox message to .done/ (same-volume atomic)."""
    done_dir = _agent_inbox_done(recipient_id)
    _ensure_dir(done_dir)
    dest = done_dir / msg_path.name

    def _do_move() -> None:
        os.replace(msg_path, dest)

    return _retry_with_backoff(_do_move, f"archive:{msg_path.name}")


# ── Routing log ──────────────────────────────────────────────────────────────


def _log_routing_event(event_type: str, **fields: Any) -> None:
    """Append a structured routing event to the router audit log."""
    log_path = MAILBOXES / "router.log.jsonl"
    _ensure_dir(log_path.parent)
    record = {
        "schema": "agi.router_event.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **fields,
    }
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
    except OSError as exc:
        emit_health_event("mailbus", "router_log_write", exc)


# ── Message construction helpers ─────────────────────────────────────────────


def new_message_id() -> str:
    """Generate a canonical message ID: ``mbx-<8 hex chars>``."""
    return f"mbx-{uuid.uuid4().hex[:8]}"


def compose(
    sender: str,
    recipient: str,
    act: str,
    subject: str,
    body: str,
    *,
    conversation: str | None = None,
    in_reply_to: str | None = None,
    hops: int = 0,
) -> dict[str, Any]:
    """Build a message dict conforming to the canonical schema.

    Args:
        sender: Agent ID of the sender.
        recipient: Agent ID of the recipient.
        act: Allowed verb (request, inform, propose, query, agree, refuse, done).
        subject: One-line summary.
        body: Plain-text message body (untrusted data).
        conversation: Optional conversation thread ID. Auto-generated if omitted.
        in_reply_to: Optional message ID this is replying to.
        hops: Current hop count (0 for new messages).

    Returns:
        A dict ready for JSON serialization.
    """
    if act not in ALLOWED_VERBS:
        raise ValueError(f"Invalid act verb: {act!r}; allowed: {sorted(ALLOWED_VERBS)}")

    if hops < 0 or hops > MAX_HOPS:
        raise ValueError(f"hops {hops} out of range [0, {MAX_HOPS}]")

    msg_id = new_message_id()
    conv = conversation or f"conv-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    return {
        "id": msg_id,
        "from": sender,
        "to": recipient,
        "act": act,
        "subject": subject,
        "body": body,
        "conversation": conv,
        "in_reply_to": in_reply_to,
        "hops": hops,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }


def send(msg: dict[str, Any]) -> Path | None:
    """Write a composed message into the sender's outbox.

    The outbox is single-writer: only the sending agent writes to its own
    outbox. The router polls outboxes for delivery.

    Returns the Path of the written message file, or None on failure.
    """
    errors = validate_message(msg)
    if errors:
        log(f"mailbus: refusing to send invalid message {msg.get('id', '?')}: {'; '.join(errors)}")
        return None

    sender = msg["from"]
    outbox = _agent_outbox(sender)
    _ensure_dir(outbox)

    # Invariant I6: intercept execution commands in proposal messages.
    body = msg.get("body", "")
    if _contains_execution_command(body):
        intercepted = dict(msg)
        intercepted["body"] = (
            f"[INTERCEPTED — original body contained execution commands]\n\n"
            f"The following message was flagged as containing mission-execution or "
            f"ESTOP-bypass patterns and has been rewritten as a passive proposal to "
            f"the Operator for review:\n\n"
            f"Original subject: {msg.get('subject', '')}\n\n"
            f"Original body (first 500 chars):\n{body[:500]}"
        )
        intercepted["subject"] = f"[INTERCEPTED] {msg.get('subject', '')}"
        intercepted["to"] = "operator"
        emit_health_event(
            "mailbus",
            "intercept_execution_command",
            RuntimeError("message body contained execution commands"),
            msg_id=msg["id"],
            sender=sender,
            original_recipient=msg["to"],
        )
        msg = intercepted
        sender = "operator"  # won't be used — sent to operator's inbox directly
        outbox = _agent_outbox(sender)

    msg_path = outbox / f"{msg['id']}.json"
    try:
        with msg_path.open("w", encoding="utf-8") as handle:
            json.dump(msg, handle, ensure_ascii=False, sort_keys=True)
    except OSError as exc:
        emit_health_event("mailbus", "send_write", exc, msg_id=msg["id"], sender=msg["from"])
        return None

    return msg_path


# ── Router ───────────────────────────────────────────────────────────────────


def _list_outbox_files() -> list[tuple[str, Path]]:
    """Scan all agent outboxes for pending .json messages.

    Returns a list of (agent_id, file_path) tuples sorted by agent then filename
    for deterministic delivery order.
    """
    results: list[tuple[str, Path]] = []
    if not MAILBOXES.is_dir():
        return results

    for agent_entry in os.scandir(MAILBOXES):
        if not agent_entry.is_dir():
            continue
        agent_id = agent_entry.name
        outbox = _agent_outbox(agent_id)
        if not outbox.is_dir():
            continue
        for file_entry in os.scandir(outbox):
            if not file_entry.is_file():
                continue
            if not MSG_ID_RE.match(file_entry.name):
                continue
            results.append((agent_id, Path(file_entry.path)))
    results.sort(key=lambda x: (x[0], x[1].name))
    return results


def route_cycle() -> int:
    """Execute one full routing cycle.

    Scans all agent outboxes via ``os.scandir``, validates each message,
    delivers it atomically to the recipient's inbox, and unlinks from the
    sender's outbox.

    This is the function called every 500ms under runlock by the harness
    dispatch loop.

    Returns the number of messages successfully delivered.
    """
    delivered = 0
    pending = _list_outbox_files()

    for sender_id, msg_path in pending:
        # Parse the message
        try:
            msg_raw = msg_path.read_text(encoding="utf-8")
            msg = json.loads(msg_raw)
        except (json.JSONDecodeError, OSError) as exc:
            emit_health_event("mailbus", "parse_outbox", exc,
                             path=str(msg_path), sender=sender_id)
            # Move unparseable files aside so they don't block the outbox forever.
            _quarantine_unparseable(msg_path)
            continue

        if not isinstance(msg, dict):
            _quarantine_unparseable(msg_path)
            continue

        # Validate schema
        errors = validate_message(msg)
        if errors:
            emit_health_event("mailbus", "validate_outbox", ValueError("; ".join(errors)),
                             msg_id=msg.get("id", "?"), sender=sender_id)
            _quarantine_unparseable(msg_path)
            continue

        recipient_id = msg["to"]
        msg_id = msg["id"]

        # Check hop limit
        hops = msg.get("hops", 0)
        if hops > MAX_HOPS:
            emit_health_event("mailbus", "hop_limit_exceeded", OverflowError(f"hops={hops}"),
                             msg_id=msg_id, sender=sender_id, recipient=recipient_id, hops=hops)
            _unlink_with_retry(msg_path, f"hop_limit_unlink:{msg_id}")
            _log_routing_event("hop_limit_terminated", msg_id=msg_id,
                              sender=sender_id, recipient=recipient_id, hops=hops)
            continue

        # Check inbox backpressure
        if _inbox_count(recipient_id) >= INBOX_CAP:
            emit_health_event("mailbus", "inbox_full", OverflowError(f"inbox cap {INBOX_CAP}"),
                             msg_id=msg_id, recipient=recipient_id)
            # Hold in outbox; retry next cycle.
            _log_routing_event("backpressure_held", msg_id=msg_id,
                              sender=sender_id, recipient=recipient_id)
            continue

        # Invariant I6: intercept execution commands at the router boundary too
        # (defense in depth — send() already checks, but router re-checks).
        body = msg.get("body", "")
        if _contains_execution_command(body):
            msg["body"] = (
                f"[INTERCEPTED at router boundary]\n\n"
                f"Original body (first 500 chars):\n{body[:500]}"
            )
            msg["subject"] = f"[INTERCEPTED] {msg.get('subject', '')}"
            msg["to"] = "operator"
            recipient_id = "operator"
            emit_health_event(
                "mailbus", "intercept_router", RuntimeError("execution commands in body"),
                msg_id=msg_id, sender=sender_id,
            )
            # Re-write the message in-place with intercepted content.
            with msg_path.open("w", encoding="utf-8") as handle:
                json.dump(msg, handle, ensure_ascii=False, sort_keys=True)

        # Ensure recipient inbox directories exist
        _ensure_dir(_agent_inbox(recipient_id))

        # Deliver atomically
        if not _stage_and_deliver(msg_path, recipient_id, msg_id):
            _log_routing_event("delivery_deferred", msg_id=msg_id,
                              sender=sender_id, recipient=recipient_id)
            continue

        # Unlink from sender's outbox
        if not _unlink_with_retry(msg_path, f"unlink_outbox:{msg_id}"):
            # Delivery succeeded but unlink failed; message will be re-delivered
            # on the next poll cycle. Idempotent handlers absorb duplicates.
            _log_routing_event("delivered_unlink_deferred", msg_id=msg_id,
                              sender=sender_id, recipient=recipient_id)
            delivered += 1
            continue

        _log_routing_event("delivered", msg_id=msg_id, sender=sender_id,
                          recipient=recipient_id, act=msg.get("act", ""))
        delivered += 1

    return delivered


def _unlink_with_retry(msg_path: Path, description: str) -> bool:
    """Unlink *msg_path* with the sharing-violation retry ladder."""
    def _do_unlink() -> None:
        msg_path.unlink()

    return _retry_with_backoff(_do_unlink, description)


def _quarantine_unparseable(msg_path: Path) -> None:
    """Move an unparseable outbox file to a .quarantine subdirectory."""
    quarantine_dir = msg_path.parent / ".quarantine"
    _ensure_dir(quarantine_dir)
    dest = quarantine_dir / f"{msg_path.name}.{uuid.uuid4().hex[:6]}"
    try:
        os.replace(msg_path, dest)
    except OSError:
        # Best effort — if we can't move it, leave it.
        pass


# ── Inbox reader (agent-side) ────────────────────────────────────────────────


def read_inbox(agent_id: str, mark_done: bool = False) -> list[dict[str, Any]]:
    """Read all pending messages from an agent's inbox.

    Args:
        agent_id: The agent reading its inbox.
        mark_done: If True, archive each message to .done/ after reading.

    Returns:
        List of message dicts, sorted by creation time.
    """
    inbox = _agent_inbox(agent_id)
    if not inbox.is_dir():
        return []

    messages: list[dict[str, Any]] = []
    for entry in sorted(os.scandir(inbox), key=lambda e: e.name):
        if not entry.is_file():
            continue
        if not MSG_ID_RE.match(entry.name):
            continue
        msg_path = Path(entry.path)
        try:
            msg = json.loads(msg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(msg, dict):
            messages.append(msg)
            if mark_done:
                _archive_message(msg_path, agent_id)

    messages.sort(key=lambda m: m.get("created_at", ""))
    return messages


def inbox_pending_count(agent_id: str) -> int:
    """Return the number of unread messages in an agent's inbox."""
    return _inbox_count(agent_id)


# ── Deferred queue (operator input buffer) ───────────────────────────────────


def _deferred_path() -> Path:
    return MAILBOXES / "deferred.jsonl"


def defer_input(command: dict[str, Any]) -> bool:
    """Append a deferred operator command to the deferred input buffer.

    Returns False if the buffer is at capacity (100 entries).
    """
    dpath = _deferred_path()
    _ensure_dir(dpath.parent)

    # Count existing entries for capacity check
    existing = 0
    if dpath.is_file():
        with dpath.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in handle:
                existing += 1

    if existing >= 100:
        emit_health_event("mailbus", "deferred_buffer_full",
                         OverflowError("deferred buffer at capacity 100"),
                         count=existing)
        return False

    entry = {
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "ttl": 3600,
        **command,
    }
    try:
        with dpath.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")
    except OSError as exc:
        emit_health_event("mailbus", "deferred_write", exc)
        return False

    return True


def drain_deferred() -> list[dict[str, Any]]:
    """Read and clear all entries from the deferred input buffer.

    Expired entries (TTL > 3600s) are filtered out.

    Returns list of valid (non-expired) command dicts.
    """
    dpath = _deferred_path()
    if not dpath.is_file():
        return []

    now = datetime.now(timezone.utc)
    kept: list[dict[str, Any]] = []
    expired = 0

    # Read all entries
    entries: list[dict[str, Any]] = []
    try:
        with dpath.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    # Partition into expired and valid
    for entry in entries:
        queued_str = entry.get("queued_at", "")
        try:
            queued = datetime.fromisoformat(queued_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            expired += 1
            continue
        ttl = entry.get("ttl", 3600)
        if (now - queued).total_seconds() > ttl:
            expired += 1
            continue
        kept.append(entry)

    # Clear the file
    try:
        dpath.unlink()
    except OSError:
        pass

    if expired:
        _log_routing_event("deferred_expired", expired_count=expired, kept_count=len(kept))

    return kept


# ── Mailbox initialization ───────────────────────────────────────────────────


def init_mailboxes(agent_ids: list[str]) -> None:
    """Create the full mailbox directory tree for a list of agent IDs.

    Idempotent — safe to call multiple times.

    Also creates the shared ``deferred.jsonl`` and ``router.log.jsonl`` files
    if they do not exist.
    """
    _ensure_dir(MAILBOXES)

    for agent_id in agent_ids:
        _ensure_dir(_agent_inbox(agent_id))
        _ensure_dir(_agent_inbox_tmp(agent_id))
        _ensure_dir(_agent_inbox_done(agent_id))
        _ensure_dir(_agent_outbox(agent_id))

    # Touch router log and deferred files
    router_log = MAILBOXES / "router.log.jsonl"
    if not router_log.is_file():
        router_log.write_text("", encoding="utf-8")

    deferred = _deferred_path()
    if not deferred.is_file():
        deferred.write_text("", encoding="utf-8")


# ── Mailbox health ───────────────────────────────────────────────────────────


def mailbox_stats() -> dict[str, Any]:
    """Return aggregate mailbox statistics for monitoring.

    Returns counts per agent (inbox, outbox, done) plus total deferred entries.
    """
    stats: dict[str, Any] = {"agents": {}, "deferred_entries": 0, "router_log_bytes": 0}

    if not MAILBOXES.is_dir():
        return stats

    router_log = MAILBOXES / "router.log.jsonl"
    if router_log.is_file():
        stats["router_log_bytes"] = router_log.stat().st_size

    dpath = _deferred_path()
    if dpath.is_file():
        with dpath.open("r", encoding="utf-8", errors="replace") as handle:
            stats["deferred_entries"] = sum(1 for _ in handle)

    for agent_entry in os.scandir(MAILBOXES):
        if not agent_entry.is_dir():
            continue
        agent_id = agent_entry.name

        inbox = _agent_inbox(agent_id)
        inbox_count = 0
        if inbox.is_dir():
            inbox_count = sum(1 for e in os.scandir(inbox)
                            if e.is_file() and MSG_ID_RE.match(e.name))

        outbox = _agent_outbox(agent_id)
        outbox_count = 0
        if outbox.is_dir():
            outbox_count = sum(1 for e in os.scandir(outbox)
                             if e.is_file() and MSG_ID_RE.match(e.name))

        done_dir = _agent_inbox_done(agent_id)
        done_count = 0
        if done_dir.is_dir():
            done_count = sum(1 for e in os.scandir(done_dir)
                           if e.is_file() and MSG_ID_RE.match(e.name))

        stats["agents"][agent_id] = {
            "inbox": inbox_count,
            "outbox": outbox_count,
            "done": done_count,
        }

    return stats
