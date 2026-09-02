"""Task worktree lifecycle tests against disposable Git repositories only."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

from task_worktree import TaskWorktreeError, TaskWorktreeManager  # noqa: E402

failures: list[str] = []
checks = 0


def check(name: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(name)
    print(f"  [{'PASS' if got == want else 'FAIL'}] {name}")


def run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)


def fixture(base: Path) -> tuple[Path, TaskWorktreeManager]:
    root = base / "repo"
    root.mkdir()
    run(["git", "init", "-b", "master"], root)
    run(["git", "config", "user.email", "test@example.invalid"], root)
    run(["git", "config", "user.name", "Task Test"], root)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "runs").mkdir()
    (root / "ledger").mkdir()
    registry = {"schema_version": 1, "last_updated": "2026-09-02T00:00:00Z",
                "active_agents": [], "coordination_rules": []}
    (root / "docs" / "ACTIVE_WORK.json").write_text(json.dumps(registry) + "\n", encoding="utf-8")
    with sqlite3.connect(root / "ledger" / "ledger.db") as conn:
        conn.execute("CREATE TABLE tasks (task_id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO tasks VALUES (7)")
    run(["git", "add", "-A"], root)
    run(["git", "commit", "-m", "fixture"], root)
    return root, TaskWorktreeManager(root)


print("=== task worktree safety ===")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    root, manager = fixture(Path(raw))
    check("worktree destination is a sibling", manager.worktree_path(7).parent, root.parent)
    try:
        manager.claim(99, agent="worker")
        unknown_refused = False
    except TaskWorktreeError:
        unknown_refused = True
    check("unknown ledger task is refused", unknown_refused)

    (root / "runs" / ".batch.lock").write_text("corrupt", encoding="utf-8")
    try:
        manager.claim(7, agent="worker")
        lock_refused = False
    except TaskWorktreeError:
        lock_refused = True
    check("present or corrupt batch lock is refused", lock_refused)
    (root / "runs" / ".batch.lock").unlink()

    path = manager.claim(7, agent="worker", owned_paths=["orchestrator/example.py"])
    check("claim creates disposable worktree", path.is_dir())
    data = json.loads((root / "docs" / "ACTIVE_WORK.json").read_text(encoding="utf-8"))
    check("claim records ownership", data["active_agents"][0]["agent"], "worker")
    try:
        manager.claim(7, agent="other")
        duplicate_refused = False
    except TaskWorktreeError:
        duplicate_refused = True
    check("duplicate task claim is refused", duplicate_refused)

    # A failed gate leaves the worktree and ownership intact for inspection.
    try:
        manager.release(7, agent="worker", gate_command=[sys.executable, "-c", "raise SystemExit(1)"])
        failed_gate_refused = False
    except TaskWorktreeError:
        failed_gate_refused = True
    check("failed gate retains worktree", failed_gate_refused and path.is_dir())

    (path / "implementation.txt").write_text("completed\n", encoding="utf-8")
    manager.release(7, agent="worker", gate_command=[sys.executable, "-c", "raise SystemExit(0)"])
    check("release removes clean merged worktree", not path.exists())
    data = json.loads((root / "docs" / "ACTIVE_WORK.json").read_text(encoding="utf-8"))
    check("release removes only matching ownership", data["active_agents"], [])
    check("release fast-forwarded task commit", (root / "implementation.txt").is_file())

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES: " + ", ".join(failures))
    raise SystemExit(1)
