"""Versioned contract between AGI_like retrieval control and installed Hermes."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Protocol, runtime_checkable
import uuid

from retrieval_progress import RetrievalProgressController


CONTRACT_VERSION = 2
AUDIT_BASE_FIELDS = frozenset({
    "sequence", "event", "profile", "required_strategy", "executed_calls",
})
AUDIT_EVENT_FIELDS = {
    "redirect": frozenset({
        "tool", "count_violation", "redirect_violations", "rejected_calls",
        "terminal", "terminal_reason",
    }),
    "observation": frozenset({
        "tool", "stage", "novel", "new_urls", "new_words", "failed", "stale",
        "result_chars", "result_class",
    }),
    "research_finished": frozenset({
        "api_calls", "input_tokens", "output_tokens", "total_tokens",
        "executed_retrieval_calls", "rejected_calls",
    }),
    "finalization_started": frozenset({"evidence_items", "evidence_chars"}),
    "finalization_finished": frozenset({
        "success", "input_tokens", "output_tokens", "reason",
    }),
}


@runtime_checkable
class RetrievalAdapterProtocol(Protocol):
    """Capabilities Hermes may call during one controlled research turn."""

    def begin_tool_batch(self) -> None: ...
    def end_tool_batch(self) -> None: ...
    def finalization_started(self) -> None: ...
    def finalization_finished(self, *, success: bool, input_tokens: int = 0,
                              output_tokens: int = 0, reason: str = "") -> None: ...
    def research_finished(self, *, api_calls: int, input_tokens: int,
                          output_tokens: int, total_tokens: int) -> None: ...
    def finalization_prompt(self, mission: str) -> str: ...
    def bounded_failure(self, reason: str) -> str: ...


@dataclass(frozen=True)
class ContractReport:
    contract_version: int
    hermes_root: Path
    hermes_revision: str
    capabilities: tuple[str, ...]


class ContractViolation(RuntimeError):
    pass


def _method(tree: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    return item
    raise ContractViolation(f"installed Hermes lacks {class_name}.{method_name}")


def _calls(method: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute):
            parts = [target.attr]
            value = target.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            found.append(".".join(reversed(parts)))
        elif isinstance(target, ast.Name):
            found.append(target.id)
    return found


def locate_installed_hermes() -> Path:
    executable = shutil.which("hermes")
    if not executable:
        raise ContractViolation("installed Hermes executable was not found on PATH")
    candidate = Path(executable).resolve().parents[2]
    if not (candidate / "run_agent.py").is_file():
        raise ContractViolation(f"cannot resolve Hermes checkout from {executable}")
    return candidate


def _revision(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def validate_harness_adapter() -> None:
    controller = RetrievalProgressController()
    if not isinstance(controller, RetrievalAdapterProtocol):
        raise ContractViolation(
            f"RetrievalProgressController does not satisfy contract v{CONTRACT_VERSION}"
        )

    audit = (Path(__file__).resolve().parents[1] / "workspace" /
             f"contract_{uuid.uuid4().hex}.jsonl")
    try:
        controller = RetrievalProgressController(audit_path=audit)
        controller.state.stage = 3
        controller.begin_tool_batch()
        decisions = [controller.before("web_search", {"query": str(i)}) for i in range(4)]
        controller.end_tool_batch()
        if controller.state.redirect_violations != 1:
            raise ContractViolation(
                "one installed-Hermes tool batch must consume exactly one redirect violation"
            )
        if any(d is None or d["terminal"] for d in decisions):
            raise ContractViolation("parallel siblings were not treated as one feedback batch")
        controller.research_finished(api_calls=4, input_tokens=10,
                                     output_tokens=5, total_tokens=15)
        controller.finalization_started()
        controller.finalization_finished(success=True, input_tokens=3, output_tokens=2)
        try:
            controller.finalization_started()
        except RuntimeError:
            pass
        else:
            raise ContractViolation("retrieval finalization is not limited to one call")
        records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
        for record in records:
            missing = AUDIT_BASE_FIELDS - record.keys()
            if missing:
                raise ContractViolation(f"audit {record.get('event')} missing {sorted(missing)}")
            required = AUDIT_EVENT_FIELDS.get(record["event"], frozenset())
            missing = required - record.keys()
            if missing:
                raise ContractViolation(f"audit {record['event']} missing {sorted(missing)}")
        events = [record["event"] for record in records]
        for required in ("redirect", "research_finished", "finalization_started",
                         "finalization_finished"):
            if required not in events:
                raise ContractViolation(f"audit output lacks required event {required}")
    finally:
        audit.unlink(missing_ok=True)


def validate_installed_hermes(root: Path | None = None) -> ContractReport:
    """Fail loudly when installed Hermes no longer satisfies the active contract."""
    root = (root or locate_installed_hermes()).resolve()
    run_tree = ast.parse((root / "run_agent.py").read_text(encoding="utf-8"))
    executor_tree = ast.parse(
        (root / "agent" / "tool_executor.py").read_text(encoding="utf-8")
    )
    loop_tree = ast.parse(
        (root / "agent" / "conversation_loop.py").read_text(encoding="utf-8")
    )

    execute = _method(run_tree, "AIAgent", "_execute_tool_calls")
    execute_calls = _calls(execute)
    if "retrieval_progress.begin_tool_batch" not in execute_calls:
        raise ContractViolation("Hermes does not begin the retrieval tool-batch lifecycle")
    if "retrieval_progress.end_tool_batch" not in execute_calls:
        raise ContractViolation("Hermes does not end the retrieval tool-batch lifecycle")
    if not any(isinstance(node, ast.Try) and node.finalbody for node in ast.walk(execute)):
        raise ContractViolation("Hermes tool-batch cleanup is not protected by finally")

    block = _method(run_tree, "AIAgent", "_guardrail_block_result")
    if "self._set_tool_guardrail_halt" not in _calls(block):
        raise ContractViolation("Hermes blocked-result path does not propagate halt")

    executor_calls = _calls(executor_tree)
    if "agent._guardrail_block_result" not in executor_calls:
        raise ContractViolation("Hermes pre-call block bypasses the halt-propagating result path")

    loop_source = ast.unparse(loop_tree)
    if "agent._tool_guardrail_halt_decision" not in loop_source or "guardrail_halt" not in loop_source:
        raise ContractViolation("Hermes conversation loop does not terminate on propagated halt")

    validate_harness_adapter()
    return ContractReport(
        contract_version=CONTRACT_VERSION,
        hermes_root=root,
        hermes_revision=_revision(root),
        capabilities=("batch_lifecycle", "halt_propagation", "audit_jsonl_v2",
                      "retrieval_finalization_v1"),
    )
