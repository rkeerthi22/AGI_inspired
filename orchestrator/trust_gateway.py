"""AGI_like Gateway: MCP Server & Thin CLI (Phase 2).

Provides the unified programmatic and agentic front door for the AGI_like
hardened trust kernel:
- Model Context Protocol (MCP) stdio server (JSON-RPC 2.0) compatible with
  Claude Desktop, Cursor, and agentic workflows.
- Thin CLI wrapper for direct operator command-line dispatch and inspection.
- Validates per-task budget parameters ($1.00 USD / 100k tokens max).
  These queue-time caps do not enforce runtime spending.
- Exposes tools: dispatch_task, check_status, get_deliverable,
  get_attestation, propose_domains, approve_domain, reject_domain.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import egress_policy
import ledger
import operator_auth
import attestation_chain
from execution_pause import pause_engaged
from policy_manager import PolicyManager

DEFAULT_LEDGER_DB = ROOT / "ledger" / "ledger.db"
DEFAULT_RUNS_DIR = ROOT / "runs"
DEFAULT_ATTEST_PATH = ROOT / ".harness" / "egress_attestation.signed"

# Admission parameter caps, not runtime spend enforcement.
MAX_PER_TASK_BUDGET_USD = 1.0
DEFAULT_PER_TASK_BUDGET_USD = 1.0
MAX_PER_TASK_TOKENS = 100000
DEFAULT_PER_TASK_TOKENS = 100000


class GatewayBudgetExceeded(Exception):
    """Raised when requested parameters exceed admission budget caps."""


class Gateway:
    """Core programmatic gateway managing dispatch, ledger inspection, and governance."""

    def __init__(
        self,
        ledger_db: Path = DEFAULT_LEDGER_DB,
        runs_dir: Path = DEFAULT_RUNS_DIR,
        attest_path: Path = DEFAULT_ATTEST_PATH,
    ):
        self.ledger_db = ledger_db
        self.runs_dir = runs_dir
        self.attest_path = attest_path
        self.policy_mgr = PolicyManager()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.ledger_db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    def dispatch_task(
        self,
        spec: str,
        pass_criteria: str = "",
        mission_id: str = "custom",
        max_budget_usd: float = DEFAULT_PER_TASK_BUDGET_USD,
        max_tokens: int = DEFAULT_PER_TASK_TOKENS,
    ) -> dict[str, Any]:
        """Validate safety bounds and queue task into the SQLite ledger."""
        if pause_engaged():
            raise RuntimeError("ESTOP engaged - dispatch refused; use the controlled-window CLI")
        if not spec.strip():
            raise ValueError("Task specification cannot be empty.")

        if (isinstance(max_budget_usd, bool) or not isinstance(max_budget_usd, (int, float))
                or not math.isfinite(max_budget_usd) or not 0 < max_budget_usd <= MAX_PER_TASK_BUDGET_USD):
            raise GatewayBudgetExceeded(
                f"Budget parameter must be finite, positive, and at most ${MAX_PER_TASK_BUDGET_USD:.2f}"
            )
        if type(max_tokens) is not int or not 0 < max_tokens <= MAX_PER_TASK_TOKENS:
            raise GatewayBudgetExceeded(
                f"Token parameter must be a positive integer at most {MAX_PER_TASK_TOKENS}"
            )

        criteria = pass_criteria.strip() or "Standard research analyst deliverable criteria."
        with self._conn() as c:
            task_id = attestation_chain.dispatch_admitted_task(
                c, self.runs_dir, mission_id, spec, criteria,
                max_budget_usd=max_budget_usd, max_tokens=max_tokens,
                budget_enforcement="admission_parameters_only",
            )


        return {
            "task_id": task_id,
            "mission_id": mission_id,
            "status": "queued",
            "max_budget_usd": max_budget_usd,
            "max_tokens": max_tokens,
            "budget_enforcement": "admission_parameters_only",
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_status(self, task_id: int) -> dict[str, Any]:
        """Fetch task execution state, token spend, and critic verdict from ledger."""
        with self._conn() as c:
            row = c.execute(
                "SELECT task_id, mission_id, spec, status, critic_verdict, "
                "critic_notes, tokens_in, tokens_out, "
                "cost_usd, model_used, started_at, finished_at "
                "FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()

        if not row:
            return {"task_id": task_id, "found": False}

        data = dict(row)
        data["found"] = True
        data["completed_at"] = data.get("finished_at")
        duration = None
        if data.get("started_at") and data.get("finished_at"):
            try:
                t0 = datetime.fromisoformat(str(data["started_at"]).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(data["finished_at"]).replace("Z", "+00:00"))
                duration = round((t1 - t0).total_seconds(), 2)
            except Exception:
                duration = None
        data["duration_seconds"] = duration

        # Check for correlated broker activity
        audit_file = self.runs_dir / f"task{task_id}_a1_broker.audit.jsonl"
        data["broker_audit_exists"] = audit_file.is_file()
        if audit_file.is_file():
            try:
                lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
                data["broker_requests_count"] = len(lines)
                data["broker_allows"] = sum(1 for line in lines if line.get("decision") == "allow")
                data["broker_denies"] = sum(1 for line in lines if line.get("decision") == "deny")
            except Exception:
                pass

        return data

    def get_deliverable(self, task_id: int) -> dict[str, Any]:
        """Retrieve the finalized or latest raw deliverable for a given task."""
        status_info = self.check_status(task_id)
        if not status_info.get("found"):
            return {"task_id": task_id, "found": False, "deliverable": None}

        # Check candidate artifact files in priority order
        candidates = [
            self.runs_dir / f"task{task_id}_a2_worker_raw.txt",
            self.runs_dir / f"task{task_id}_a1_worker_raw.txt",
            self.runs_dir / f"task{task_id}_worker_raw.txt",
        ]
        # Also search for markdown deliverables in deliverables/ directory
        deliv_dir = ROOT / "deliverables"
        if deliv_dir.is_dir():
            for p in deliv_dir.glob("*.md"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                    if f"task {task_id}" in txt:
                        candidates.insert(0, p)
                        break
                except OSError:
                    pass

        content = None
        source_path = None
        for path in candidates:
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    try:
                        source_path = str(path.relative_to(ROOT))
                    except ValueError:
                        source_path = str(path)
                    break
                except OSError:
                    pass

        return {
            "task_id": task_id,
            "found": True,
            "status": status_info.get("status"),
            "critic_verdict": status_info.get("critic_verdict"),
            "source_path": source_path,
            "deliverable": content,
        }

    def get_attestation(self, task_id: int | None = None) -> dict[str, Any]:
        """Verify and return signed attestation and worker policy snapshot."""
        # 1. System-wide active signed attestation
        token_valid = False
        signature_valid = False
        token_payload: dict[str, Any] = {}
        verification_error = "attestation_missing"
        if self.attest_path.is_file():
            try:
                # Native format is base64(payload).base64(signature).v1, not JWT.
                # The embedded key must match the locally trusted operator key.
                signed_token = self.attest_path.read_text(encoding="utf-8").strip()
                verified = operator_auth.verify_marker(signed_token)
                signature_valid = isinstance(verified, dict)
                verification_error = "attestation_untrusted"
                if signature_valid:
                    token_payload = verified
                    policy = egress_policy.load_policy(self.policy_mgr.policy_path)
                    boundary = egress_policy.boundary_state(
                        self.policy_mgr.policy_path,
                        environment={policy.attestation_env: str(self.attest_path)},
                        # Validate exactly the token whose signature we checked.
                        verify_token=lambda raw: verified if raw == signed_token else None,
                    )
                    token_valid = bool(boundary.get("ok"))
                    verification_error = boundary.get("error")
            except Exception:
                token_valid = False
                verification_error = "attestation_verification_failed"

        claims = token_payload.get("claims")
        claims = claims if isinstance(claims, dict) else {}
        worker_id = (
            claims.get("worker_identity")
            or token_payload.get("worker_identity")
        )

        result: dict[str, Any] = {
            "attestation_file_exists": self.attest_path.is_file(),
            "attestation_token_valid": token_valid,
            "attestation_signature_valid": signature_valid,
            "attestation_error": verification_error,
            "active_policy_digest": token_payload.get("policy_sha256"),
            "claims": claims,
            "issued_at": token_payload.get("issued_at"),
            "expires_at": token_payload.get("expires_at"),
            "worker_identity": worker_id,
        }

        # 2. Task-specific attestation snapshot if requested
        if task_id is not None:
            result.update(attestation_chain.status(self.runs_dir, task_id))
            usage_path = self.runs_dir / f"task{task_id}_a1_worker.usage.json"
            if not usage_path.is_file():
                usage_path = self.runs_dir / f"task{task_id}_worker.usage.json"

            task_snapshot = {}
            if usage_path.is_file():
                try:
                    usage_data = json.loads(usage_path.read_text(encoding="utf-8"))
                    task_snapshot = {
                        "policy_digest": usage_data.get("policy_digest"),
                        "allowlisted_hosts": usage_data.get("allowlisted_hosts", []),
                        "matches_active_attestation": (
                            token_valid and bool(usage_data.get("policy_digest"))
                            and usage_data.get("policy_digest") == token_payload.get("policy_sha256")
                        ),
                    }
                except Exception:
                    pass
            result["task_snapshot"] = task_snapshot

        return result

    def propose_domains(self, unreviewed_only: bool = True) -> list[dict[str, Any]]:
        """Return candidate domains requiring operator approval with syntax and DNS checks."""
        return self.policy_mgr.propose(unreviewed_only=unreviewed_only, resolve_dns=True)

    def approve_domain(self, domain: str, operator: str = "operator", rationale: str = "") -> dict[str, Any]:
        """Operator-gated admission: append domain to egress policy and re-sign attestation."""
        return self.policy_mgr.approve(domain, operator=operator, rationale=rationale)

    def reject_domain(self, domain: str, operator: str = "operator", reason: str = "") -> dict[str, Any]:
        """Operator-gated rejection: record rejection in audit trail."""
        return self.policy_mgr.reject(domain, operator=operator, reason=reason)


# ---------------------------------------------------------------------------
# MCP Server Implementation (JSON-RPC 2.0 over stdio)
# ---------------------------------------------------------------------------

class McpServer:
    """Model Context Protocol (MCP) server exposing AGI_like tools."""

    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.tools = [
            {
                "name": "dispatch_task",
                "description": "Queue a research or verification task under Windows Restricted Token containment.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "spec": {"type": "string", "description": "The task specification / prompt to execute."},
                        "pass_criteria": {"type": "string", "description": "Criteria required for critic verification."},
                        "mission_id": {"type": "string", "default": "custom", "description": "Mission identifier."},
                        "max_budget_usd": {"type": "number", "default": 1.0, "maximum": 1.0, "exclusiveMinimum": 0, "description": "Admission budget parameter in USD; no runtime spend enforcement."},
                        "max_tokens": {"type": "integer", "default": 100000, "minimum": 1, "maximum": 100000, "description": "Admission token parameter; no runtime spend enforcement."},
                    },
                    "required": ["spec"],
                },
            },
            {
                "name": "check_status",
                "description": "Inspect task lifecycle state, token spend, and critic verdict.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "The task ID to inspect."},
                    },
                    "required": ["task_id"],
                },
            },
            {
                "name": "get_deliverable",
                "description": "Retrieve the generated deliverable text and verdict for a completed task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "The task ID to retrieve."},
                    },
                    "required": ["task_id"],
                },
            },
            {
                "name": "get_attestation",
                "description": "Inspect cryptographic Ed25519 attestation, policy digest, and containment evidence.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "Optional task ID to check specific snapshot."},
                    },
                },
            },
            {
                "name": "propose_domains",
                "description": "List unreviewed candidate domains requested by worker tasks with DNS & risk checks.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "unreviewed_only": {"type": "boolean", "default": True},
                    },
                },
            },
            {
                "name": "approve_domain",
                "description": "Operator authorization: append research domain to egress allowlist and re-sign token.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Domain to allowlist."},
                        "rationale": {"type": "string", "description": "Operator safety rationale."},
                    },
                    "required": ["domain"],
                },
            },
            {
                "name": "reject_domain",
                "description": "Operator rejection: reject candidate domain and record in audit log.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Domain to reject."},
                        "reason": {"type": "string", "description": "Operator rejection reason."},
                    },
                    "required": ["domain"],
                },
            },
        ]

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch JSON-RPC 2.0 request."""
        method = req.get("method")
        msg_id = req.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agi-like-gateway", "version": "1.0.0"},
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": self.tools},
            }

        if method == "tools/call":
            params = req.get("params") or {}
            tool_name = params.get("name")
            args = params.get("arguments") or {}

            try:
                if tool_name == "dispatch_task":
                    res = self.gateway.dispatch_task(
                        spec=args["spec"],
                        pass_criteria=args.get("pass_criteria", ""),
                        mission_id=args.get("mission_id", "custom"),
                        max_budget_usd=args.get("max_budget_usd", 1.0),
                        max_tokens=args.get("max_tokens", DEFAULT_PER_TASK_TOKENS),
                    )
                elif tool_name == "check_status":
                    res = self.gateway.check_status(int(args["task_id"]))
                elif tool_name == "get_deliverable":
                    res = self.gateway.get_deliverable(int(args["task_id"]))
                elif tool_name == "get_attestation":
                    tid = int(args["task_id"]) if args.get("task_id") is not None else None
                    res = self.gateway.get_attestation(task_id=tid)
                elif tool_name == "propose_domains":
                    res = self.gateway.propose_domains(unreviewed_only=bool(args.get("unreviewed_only", True)))
                elif tool_name == "approve_domain":
                    res = self.gateway.approve_domain(
                        domain=args["domain"],
                        rationale=args.get("rationale", ""),
                    )
                elif tool_name == "reject_domain":
                    res = self.gateway.reject_domain(
                        domain=args["domain"],
                        reason=args.get("reason", ""),
                    )
                else:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                    }

                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(res, indent=2)}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def run_stdio(self) -> None:
        """Run standard I/O loop for MCP client communication."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except Exception as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLI Command Dispatcher
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AGI_like Trust Gateway & MCP Server (Phase 2)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: dispatch
    disp_p = subparsers.add_parser("dispatch", help="Queue a new contained task")
    disp_p.add_argument("spec", help="Task specification")
    disp_p.add_argument("--criteria", default="", help="Pass criteria")
    disp_p.add_argument("--mission", default="custom", help="Mission ID")
    disp_p.add_argument("--budget", type=float, default=1.0, help="Budget parameter in USD (max $1; admission only)")

    # Subcommand: status
    stat_p = subparsers.add_parser("status", help="Check status of a task")
    stat_p.add_argument("task_id", type=int, help="Task ID")

    # Subcommand: deliverable
    deliv_p = subparsers.add_parser("deliverable", help="Get deliverable for a task")
    deliv_p.add_argument("task_id", type=int, help="Task ID")

    # Subcommand: attestation
    att_p = subparsers.add_parser("attestation", help="Inspect signed attestation")
    att_p.add_argument("--task-id", type=int, default=None, help="Optional task ID")

    # Subcommand: mcp
    subparsers.add_parser("mcp", help="Run MCP stdio server")

    args = parser.parse_args(argv)
    gw = Gateway()

    if args.command == "dispatch":
        res = gw.dispatch_task(
            spec=args.spec,
            pass_criteria=args.criteria,
            mission_id=args.mission,
            max_budget_usd=args.budget,
        )
        print(f"[SUCCESS] Queued Task #{res['task_id']} (Budget parameter: ${res['max_budget_usd']:.2f}; admission only)")
        print(f"  Status: {res['status']}")
        return 0

    if args.command == "status":
        res = gw.check_status(args.task_id)
        if not res.get("found"):
            print(f"[NOT FOUND] Task #{args.task_id} does not exist in ledger.")
            return 1
        print(f"Task #{res['task_id']} [{res['mission_id']}] - Status: {res['status']}")
        print(f"  Verdict: {res.get('critic_verdict')} | Model: {res.get('model_used')}")
        print(f"  Tokens: in={res.get('tokens_in', 0):,} out={res.get('tokens_out', 0):,}")
        if res.get("critic_notes"):
            print(f"  Critic Notes: {res['critic_notes'][:200]}...")
        return 0

    if args.command == "deliverable":
        res = gw.get_deliverable(args.task_id)
        if not res.get("found") or not res.get("deliverable"):
            print(f"[NOT FOUND] No deliverable found for Task #{args.task_id}.")
            return 1
        print(f"--- Deliverable for Task #{args.task_id} ({res.get('source_path')}) ---")
        print(res["deliverable"])
        return 0

    if args.command == "attestation":
        res = gw.get_attestation(task_id=args.task_id)
        print("--- AGI_like Cryptographic Attestation ---")
        print(f"  Token Valid: {res['attestation_token_valid']}")
        print(f"  Policy Digest: {res['active_policy_digest']}")
        print(f"  Worker Identity: {res['worker_identity']}")
        print(f"  Expires At: {res['expires_at']}")
        if args.task_id is not None:
            print(f"  Lifecycle Chain Verified: {res['attestation_chain_valid']} ({res['attestation_chain_steps']} steps)")
            print(f"  Lifecycle Result: {res['attestation_chain_error'] or 'verified'}")
        if res.get("task_snapshot"):
            snap = res["task_snapshot"]
            print(f"  Task Snapshot Matches Active: {snap.get('matches_active_attestation')}")
        return 0

    if args.command == "mcp":
        server = McpServer(gw)
        server.run_stdio()
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
