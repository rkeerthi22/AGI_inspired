"""F61: Move 5e leaves batch_runner as CLI composition plus intentional shims."""
import ast
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import batch_runner as br  # noqa: E402
import evaluation  # noqa: E402
import execution  # noqa: E402
import task_runner  # noqa: E402
import workflow  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(name)
    print(f"  [{'PASS' if got == want else 'FAIL'}] {name}: got={got!r} want={want!r}")


def snapshot():
    paths = (ROOT / "ledger" / "ledger.db", ROOT / "workspace" / "ESCALATIONS.md",
             ROOT / "config" / "policy.yaml")
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                        text=True).strip(),
        "status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                          text=True),
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in paths if p.is_file()},
    }


before = snapshot()
source = (ROOT / "orchestrator" / "batch_runner.py").read_text(encoding="utf-8")
tree = ast.parse(source)
functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
check("only CLI/composition functions remain", functions, {"load_roles", "main", "_run"})
check("run_task compatibility identity", br.run_task is task_runner.run_task, True)

owners = {
    "run_critic": evaluation.run_critic,
    "extract_facts": evaluation.extract_facts,
    "run_synthesis": workflow.run_synthesis,
    "run_canaries": workflow.run_canaries,
    "retry_failed_this_fire": workflow.retry_failed_this_fire,
    "_strip_tool_chatter": execution._strip_tool_chatter,
}
for name, owner in owners.items():
    check(f"intentional shim {name}", getattr(br, name) is owner, True)

for removed in ("worker_with_failover", "fs_integrity_snapshot", "accumulated_tokens",
                "build_brief_block", "SYNTHESIS_BRIEF_CHARS", "FACT_LEDGER_CAP"):
    check(f"legacy alias removed: {removed}", hasattr(br, removed), False)

run_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "_run")
run_names = {node.id for node in ast.walk(run_node) if isinstance(node, ast.Name)}
for required in ("run_task", "retry_failed_this_fire", "run_canaries"):
    check(f"_run preserves wiring: {required}", required in run_names, True)
check("retry injection remains explicit", "run_task_fn=run_task" in source, True)

promote = (ROOT / "orchestrator" / "promote.py").read_text(encoding="utf-8")
spotcheck = (ROOT / "orchestrator" / "spotcheck.py").read_text(encoding="utf-8")
check("promote uses execution owner", "from execution import ollama_chat" in promote, True)
check("spotcheck uses evaluation owner", "from evaluation import retract_facts" in spotcheck, True)

after = snapshot()
check("zero live-state drift", after, before)

if fails:
    print("\nFAILURES:", *fails, sep="\n  - ")
    raise SystemExit(1)
print("\nF61 PASS — CLI composition and compatibility surface verified")
