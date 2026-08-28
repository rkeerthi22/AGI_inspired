"""The prediction machine is the sole active prediction implementation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
fails = []


def check(name, condition):
    ok = bool(condition)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        fails.append(name)


check("legacy simulate module removed", not (ROOT / "orchestrator" / "simulate.py").exists())
runner = (ROOT / "orchestrator" / "task_runner.py").read_text(encoding="utf-8")
check("task execution uses canonical prediction integration",
      "prediction_machine.integrations.batch_runner_hook" in runner)

references = []
for base in (ROOT / "orchestrator", ROOT / "prediction_machine", ROOT / "tests"):
    for path in base.rglob("*.py"):
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "orchestrator/simulate.py" in text or "import simulate" in text:
            references.append(str(path.relative_to(ROOT)))
check("active Python has no legacy simulate callers", references == [])

if fails:
    raise SystemExit("prediction interface failures: " + ", ".join(fails))
