"""DSSE v1 Ed25519 task lifecycle records under the existing operator key.

This is the AGI-like lifecycle profile, not an in-toto layout verifier. DSSE
authenticates payloadType and exact payload bytes using standard PAE. Links hash
canonical JSON of the decoded payload (UTF-8, sorted keys, compact separators).
Only the locally configured key is trusted; envelope keyid is an identifier.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path

import operator_auth
import runlock

PAYLOAD_TYPE = "application/vnd.agi-like.task-lifecycle.v1+json"
GATEWAY_RUN_ID = "gateway-dsse-v1"


class ChainError(RuntimeError):
    pass


class Step(str, Enum):
    DISPATCH = "DISPATCH"
    WORKER = "WORKER"
    PREFLIGHT = "PREFLIGHT"
    CRITIC = "CRITIC"
    DELIVERABLE = "DELIVERABLE"


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pae(payload: bytes, payload_type: str = PAYLOAD_TYPE) -> bytes:
    kind = payload_type.encode("utf-8")
    return b"DSSEv1 " + str(len(kind)).encode("ascii") + b" " + kind + b" " + str(len(payload)).encode("ascii") + b" " + payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def _decode(raw: str) -> bytes:
    return base64.b64decode(raw, altchars=b"-_", validate=True)


def compute_digest(statement: dict) -> str:
    """SHA256 of canonical decoded payload JSON, independent of envelope formatting."""
    return hashlib.sha256(_canonical(_json(_decode(statement["payload"])))).hexdigest()


def emit_step(step, task_id, attempt, claims: dict, prior_digest: str | None) -> dict:
    keys = operator_auth._load_keypair()
    if keys is None:
        raise ChainError("operator signing key missing; initialize through the installer")
    if type(task_id) is not int or task_id < 1 or type(attempt) is not int or attempt < 1:
        raise ChainError("invalid lifecycle task or attempt")
    if not isinstance(claims, dict):
        raise ChainError("claims must be an object")
    payload = _canonical({"step": Step(step).value, "task_id": task_id, "attempt": attempt,
        "issued_at": datetime.now(timezone.utc).isoformat(), "prior_step_digest": prior_digest,
        "claims": claims})
    private, public = keys
    sig = operator_auth._reconstruct_signer(private).sign(pae(payload))
    return {"payloadType": PAYLOAD_TYPE, "payload": base64.b64encode(payload).decode("ascii"),
            "signatures": [{"keyid": hashlib.sha256(public).hexdigest(),
                            "sig": base64.b64encode(sig).decode("ascii")}]}


def _verified_payload(statement, public: bytes) -> dict:
    if statement.get("payloadType") != PAYLOAD_TYPE:
        raise ChainError("unsupported payload type")
    payload = _decode(statement["payload"])
    signatures = statement.get("signatures")
    if not isinstance(signatures, list) or len(signatures) != 1:
        raise ChainError("expected one operator signature")
    sig = signatures[0]
    if sig.get("keyid") != hashlib.sha256(public).hexdigest():
        raise ChainError("untrusted key identifier")
    operator_auth._reconstruct_verifier(public).verify(_decode(sig["sig"]), pae(payload))
    body = _json(payload)
    if set(body) != {"step", "task_id", "attempt", "issued_at", "prior_step_digest", "claims"}:
        raise ChainError("invalid lifecycle payload schema")
    if (type(body["task_id"]) is not int or body["task_id"] < 1
            or type(body["attempt"]) is not int or body["attempt"] < 1
            or not isinstance(body["claims"], dict)):
        raise ChainError("invalid task, attempt or claims")
    if datetime.fromisoformat(body["issued_at"]).utcoffset() is None:
        raise ChainError("issued_at must include UTC offset")
    Step(body["step"])
    return body


def verify_chain(statements: list[dict], *, require_complete: bool = True,
                 task_id: int | None = None) -> tuple[bool, str | None]:
    try:
        keys = operator_auth._load_keypair()
        if keys is None:
            return False, "operator_key_missing"
        if not statements:
            return False, "chain_missing"
        prior_digest = None
        previous = None
        transitions = {"DISPATCH": {"WORKER", "DELIVERABLE"}, "WORKER": {"PREFLIGHT", "DELIVERABLE"},
                       "PREFLIGHT": {"WORKER", "CRITIC", "DELIVERABLE"},
                       "CRITIC": {"DELIVERABLE"}, "DELIVERABLE": {"WORKER"}}
        for statement in statements:
            body = _verified_payload(statement, keys[1])
            if task_id is not None and body["task_id"] != task_id:
                return False, "task_id_mismatch"
            if body["prior_step_digest"] != prior_digest:
                return False, "broken_prior_digest"
            if previous is None:
                if body["step"] != "DISPATCH":
                    return False, "missing_dispatch"
            else:
                if body["task_id"] != previous["task_id"] or body["step"] not in transitions[previous["step"]]:
                    return False, "invalid_step_order"
                if previous["step"] == "DELIVERABLE":
                    if body["attempt"] <= previous["attempt"]:
                        return False, "attempt_did_not_advance"
                elif body["attempt"] != previous["attempt"]:
                    return False, "attempt_changed_mid_execution"
                if (body["step"] == "DELIVERABLE" and previous["step"] != "CRITIC"
                        and body["claims"].get("status") not in ("infra_failed", "failed", "quota_exhausted")):
                    return False, "success_without_critic"
            prior_digest = compute_digest(statement)
            previous = body
        if require_complete and previous["step"] != "DELIVERABLE":
            return False, "chain_incomplete"
        return True, None
    except Exception as exc:
        # Never reflect payload content, keys or provider text into errors.
        return False, "verification_failed:" + type(exc).__name__


def chain_path(runs: Path, task_id: int) -> Path:
    if type(task_id) is not int or task_id < 1:
        raise ChainError("invalid task ID")
    return Path(runs) / f"task{task_id}_attestation_chain.jsonl"


def load(path: Path) -> list[dict]:
    if path.is_symlink():
        raise ChainError("chain must be a regular harness file")
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise ChainError("empty or partially written chain")
    return [_json(line) for line in raw.splitlines()]


def append_step(runs: Path, step, task_id, attempt, claims: dict) -> dict:
    path = chain_path(runs, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with runlock.acquire(path.with_suffix(".lock")):
        statements = load(path)
        if statements:
            ok, error = verify_chain(statements, require_complete=False, task_id=task_id)
            if not ok:
                raise ChainError(error)
        statement = emit_step(step, task_id, attempt, claims,
                              compute_digest(statements[-1]) if statements else None)
        ok, error = verify_chain(statements + [statement], require_complete=False, task_id=task_id)
        if not ok:
            raise ChainError(error)
        with path.open("ab") as stream:
            stream.write(_canonical(statement) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return statement


def existing(runs: Path, task_id: int, row: dict) -> bool:
    """Legacy tasks predate gateway lifecycle admission; never invent their history."""
    path = chain_path(runs, task_id)
    required = row.get("run_id") == GATEWAY_RUN_ID
    if not path.exists():
        if required:
            raise ChainError("required lifecycle chain missing")
        return False
    statements = load(path)
    ok, error = verify_chain(statements, require_complete=False, task_id=task_id)
    if not ok:
        raise ChainError(error)
    bodies = [_json(_decode(s["payload"])) for s in statements]
    if int(row.get("attempt_count") or 0) > bodies[-1]["attempt"]:
        raise ChainError("chain older than ledger attempt")
    # Bind persisted notebook to its most recent signed preflight, so edits made
    # between leases cannot manufacture research evidence either.
    for body in reversed(bodies):
        digest = body["claims"].get("notebook_sha256")
        if digest:
            notebook = Path(runs) / f"task{task_id}_research_notebook.json"
            if not notebook.is_file() or hashlib.sha256(notebook.read_bytes()).hexdigest() != digest:
                raise ChainError("research notebook does not match signed preflight")
            break
    return True


def finalize_failure(runs: Path, task_id: int, status: str) -> None:
    """Close a started chain on an aborted task without inventing skipped stages."""
    path = chain_path(runs, task_id)
    if not path.exists():
        return
    statements = load(path)
    ok, error = verify_chain(statements, require_complete=False, task_id=task_id)
    if not ok:
        raise ChainError(error)
    last = _json(_decode(statements[-1]["payload"]))
    if last["step"] != "DELIVERABLE":
        append_step(runs, Step.DELIVERABLE, task_id, last["attempt"],
                    {"status": status, "aborted": True})


def status(runs: Path, task_id: int) -> dict:
    steps = 0
    try:
        statements = load(chain_path(runs, task_id))
        steps = len(statements)
        ok, error = verify_chain(statements, task_id=task_id)
    except Exception as exc:
        ok, error = False, "chain_unreadable:" + type(exc).__name__
    return {"attestation_chain_valid": ok, "attestation_chain_steps": steps,
            "attestation_chain_error": error}


def dispatch_admitted_task(
    db_or_conn,
    runs_dir: Path,
    mission_id: str,
    spec: str,
    pass_criteria: str,
    *,
    client_id: str | None = None,
    max_budget_usd: float | None = None,
    max_tokens: int | None = None,
    budget_enforcement: str = "admission_parameters_only",
) -> int:
    """Admit and queue a task with genuine DSSE DISPATCH provenance before commit.

    Guarantees that run_id is GATEWAY_RUN_ID and DISPATCH step is appended and
    verified before the database transaction commits. Accepts either a sqlite3.Connection
    or a Path / str to the ledger database.
    """
    import sqlite3
    if isinstance(db_or_conn, (str, Path)):
        with sqlite3.connect(str(db_or_conn), timeout=30) as conn:
            return dispatch_admitted_task(
                conn, runs_dir, mission_id, spec, pass_criteria,
                client_id=client_id,
                max_budget_usd=max_budget_usd, max_tokens=max_tokens,
                budget_enforcement=budget_enforcement,
            )

    conn = db_or_conn
    cur = conn.execute(
        "INSERT INTO tasks (mission_id, spec, pass_criteria, status, run_id) "
        "VALUES (?, ?, ?, 'queued', ?)",
        (mission_id, spec, pass_criteria, GATEWAY_RUN_ID),
    )
    task_id = cur.lastrowid
    claims = {
        "spec_sha256": text_digest(spec),
        "criteria_sha256": text_digest(pass_criteria),
        "mission_id": mission_id,
        "client_id": client_id,
        "max_budget_usd": max_budget_usd,
        "max_tokens": max_tokens,
        "budget_enforcement": budget_enforcement,
    }
    append_step(Path(runs_dir), Step.DISPATCH, task_id, 1, claims)
    conn.commit()
    return task_id


def read_chain(runs: Path, task_id: int) -> list[dict]:
    return load(chain_path(runs, task_id))


def read_payloads(runs: Path, task_id: int) -> list[dict]:
    return [_json(_decode(s["payload"])) for s in read_chain(runs, task_id)]

