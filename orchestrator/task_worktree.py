"""Safe, local Git-worktree lifecycle for ledger-backed implementation tasks.

This module deliberately is *not* part of the read-only ``agi`` operator CLI.
Its commands mutate Git and the active-work registry, so they require an
explicit invocation and are covered only with disposable repositories in tests.
It never contacts providers or changes ESTOP/isolation state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TaskWorktreeError(RuntimeError):
    """A claim or release could not be completed safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TaskWorktreeManager:
    """Manage task branches without assuming the caller's current directory."""

    def __init__(self, root: Path = ROOT, *, registry_path: Path | None = None,
                 ledger_path: Path | None = None, lock_path: Path | None = None,
                 runner=subprocess.run):
        self.root = Path(root).resolve()
        self.registry_path = Path(registry_path or self.root / "docs" / "ACTIVE_WORK.json")
        self.ledger_path = Path(ledger_path or self.root / "ledger" / "ledger.db")
        self.batch_lock_path = Path(lock_path or self.root / "runs" / ".batch.lock")
        self.runner = runner

    def worktree_path(self, task_id: int) -> Path:
        """Return the deterministic sibling path, rejecting unsafe task IDs."""
        if not isinstance(task_id, int) or task_id <= 0:
            raise TaskWorktreeError("task id must be a positive integer")
        candidate = (self.root.parent / f"{self.root.name}-task-{task_id}").resolve()
        if candidate == self.root or self.root in candidate.parents:
            raise TaskWorktreeError("refusing unsafe worktree destination")
        return candidate

    def _git(self, args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
        result = self.runner(["git", *args], cwd=str(cwd or self.root),
                             capture_output=True, text=True)
        if result.returncode:
            detail = (result.stderr or result.stdout or "git command failed").strip()
            raise TaskWorktreeError(detail)
        return result

    def _assert_batch_lock_free(self) -> None:
        # Any extant lock is unsafe here.  In particular, a corrupt lock must
        # not be guessed stale and removed by a task lifecycle command.
        if self.batch_lock_path.exists():
            raise TaskWorktreeError(f"batch lock present: {self.batch_lock_path}")

    def _assert_task_exists(self, task_id: int) -> None:
        if not self.ledger_path.is_file():
            raise TaskWorktreeError(f"task registry unavailable: {self.ledger_path}")
        conn = None
        try:
            conn = sqlite3.connect(self.ledger_path)
            with conn:
                row = conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        except sqlite3.Error as exc:
            raise TaskWorktreeError("task registry could not be read") from exc
        finally:
            if conn is not None:
                conn.close()
        if row is None:
            raise TaskWorktreeError(f"unknown task id: {task_id}")

    def _registry_bytes(self) -> bytes:
        try:
            return self.registry_path.read_bytes()
        except OSError as exc:
            raise TaskWorktreeError(f"active-work registry unavailable: {self.registry_path}") from exc

    @staticmethod
    def _parse_registry(raw: bytes) -> dict:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskWorktreeError("active-work registry is invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("active_agents"), list):
            raise TaskWorktreeError("active-work registry has no active_agents list")
        return data

    @contextmanager
    def _registry_lock(self):
        """Serialize writers; content-hash checks still detect external edits."""
        lock = self.registry_path.with_suffix(self.registry_path.suffix + ".claim.lock")
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise TaskWorktreeError("active-work registry is being updated") from exc
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(fd)
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

    def _cas_registry(self, expected_hash: str, transform) -> None:
        """Write a registry update only if its preimage is unchanged."""
        with self._registry_lock():
            raw = self._registry_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_hash:
                raise TaskWorktreeError("active-work registry changed concurrently")
            data = self._parse_registry(raw)
            transform(data)
            data["last_updated"] = _utc_now()
            encoded = (json.dumps(data, indent=2) + "\n").encode("utf-8")
            with tempfile.NamedTemporaryFile(dir=self.registry_path.parent, delete=False) as temp:
                temp.write(encoded)
                replacement = Path(temp.name)
            try:
                os.replace(replacement, self.registry_path)
            finally:
                replacement.unlink(missing_ok=True)

    @staticmethod
    def _claim_conflicts(data: dict, task_id: int, owned_paths: list[str]) -> bool:
        requested = set(owned_paths)
        for item in data["active_agents"]:
            if item.get("status") not in {"in_progress", "active"}:
                continue
            if item.get("task_id") == str(task_id) or item.get("task_id") == task_id:
                return True
            if requested.intersection(item.get("owned_paths", [])):
                return True
        return False

    def claim(self, task_id: int, *, agent: str, role: str = "task implementer",
              owned_paths: list[str] | None = None, base_branch: str = "master") -> Path:
        """Create a task branch/worktree then atomically record its ownership."""
        self._assert_batch_lock_free()
        self._assert_task_exists(task_id)
        if not agent.strip():
            raise TaskWorktreeError("agent is required")
        paths = list(owned_paths or [])
        before = self._registry_bytes()
        registry = self._parse_registry(before)
        if self._claim_conflicts(registry, task_id, paths):
            raise TaskWorktreeError("task or requested paths already have an active owner")
        expected_hash = hashlib.sha256(before).hexdigest()
        target = self.worktree_path(task_id)
        if target.exists():
            raise TaskWorktreeError(f"worktree path already exists: {target}")
        branch = f"task-{task_id}"
        # ``show-ref`` returns 1 for a branch that does not yet exist; inspect
        # that expected result directly rather than treating it as a Git error.
        result = self.runner(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                             cwd=str(self.root), capture_output=True, text=True)
        if result.returncode == 0:
            raise TaskWorktreeError(f"task branch already exists: {branch}")
        if result.returncode not in {1}:
            raise TaskWorktreeError((result.stderr or "cannot inspect task branch").strip())
        self._git(["worktree", "add", "-b", branch, str(target), base_branch])
        try:
            def add_claim(data: dict) -> None:
                if self._claim_conflicts(data, task_id, paths):
                    raise TaskWorktreeError("task or requested paths already have an active owner")
                data["active_agents"].append({
                    "agent": agent, "role": role, "task_id": str(task_id),
                    "mode": "implementation", "owned_paths": paths,
                    "started_at": _utc_now(), "last_update": _utc_now(),
                    "status": "in_progress", "blocked_by": None,
                    "worktree": str(target),
                })
            self._cas_registry(expected_hash, add_claim)
        except Exception:
            # This worktree was created by this invocation and has no user work.
            self._git(["worktree", "remove", str(target)])
            raise
        return target

    def release(self, task_id: int, *, agent: str,
                gate_command: list[str] | None = None) -> None:
        """Gate, commit, fast-forward merge, release ownership, and remove worktree."""
        self._assert_batch_lock_free()
        target = self.worktree_path(task_id)
        if not target.is_dir():
            raise TaskWorktreeError(f"task worktree does not exist: {target}")
        before = self._registry_bytes()
        expected_hash = hashlib.sha256(before).hexdigest()
        registry = self._parse_registry(before)
        matches = [x for x in registry["active_agents"]
                   if str(x.get("task_id")) == str(task_id)
                   and x.get("agent") == agent and x.get("status") == "in_progress"]
        if len(matches) != 1:
            raise TaskWorktreeError("no matching active task claim")

        command = gate_command or [sys.executable, "-B", "tests/run_all.py"]
        gate = self.runner(command, cwd=str(target), capture_output=True, text=True)
        if gate.returncode:
            raise TaskWorktreeError("model-free gate failed; worktree retained for inspection")
        self._git(["add", "-A"], cwd=target)
        staged = self.runner(["git", "diff", "--cached", "--quiet"], cwd=str(target),
                             capture_output=True, text=True)
        if staged.returncode == 1:
            self._git(["commit", "-m", f"task {task_id}: completed by {agent}"], cwd=target)
        elif staged.returncode != 0:
            raise TaskWorktreeError("cannot inspect staged task changes")
        dirty = self._git(["status", "--porcelain=v1"], cwd=target).stdout.strip()
        if dirty:
            raise TaskWorktreeError("refusing to remove worktree with uncommitted changes")

        branch = f"task-{task_id}"
        with self._registry_lock():
            current = self._registry_bytes()
            if hashlib.sha256(current).hexdigest() != expected_hash:
                raise TaskWorktreeError("active-work registry changed concurrently; merge not attempted")
            self._git(["merge", "--ff-only", branch], cwd=self.root)
            def release_claim(data: dict) -> None:
                data["active_agents"] = [x for x in data["active_agents"]
                                         if not (str(x.get("task_id")) == str(task_id)
                                                 and x.get("agent") == agent)]
            # Lock is already held; inline CAS avoids attempting recursive lock.
            data = self._parse_registry(current)
            release_claim(data)
            data["last_updated"] = _utc_now()
            encoded = (json.dumps(data, indent=2) + "\n").encode("utf-8")
            with tempfile.NamedTemporaryFile(dir=self.registry_path.parent, delete=False) as temp:
                temp.write(encoded)
                replacement = Path(temp.name)
            try:
                os.replace(replacement, self.registry_path)
            finally:
                replacement.unlink(missing_ok=True)
        self._git(["worktree", "remove", str(target)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task worktree lifecycle (mutating; separate from agi)")
    command = parser.add_subparsers(dest="command", required=True)
    claim = command.add_parser("claim")
    claim.add_argument("task_id", type=int)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--role", default="task implementer")
    claim.add_argument("--path", action="append", default=[])
    claim.add_argument("--base-branch", default="master")
    release = command.add_parser("release")
    release.add_argument("task_id", type=int)
    release.add_argument("--agent", required=True)
    args = parser.parse_args(argv)
    manager = TaskWorktreeManager()
    try:
        if args.command == "claim":
            print(f"INSPECT WORKTREE PATH: {manager.worktree_path(args.task_id)}")
            path = manager.claim(args.task_id, agent=args.agent, role=args.role,
                                 owned_paths=args.path, base_branch=args.base_branch)
            print(f"CLAIMED WORKTREE: {path}")
        else:
            manager.release(args.task_id, agent=args.agent)
            print(f"RELEASED TASK: {args.task_id}")
    except TaskWorktreeError as exc:
        print(f"TASK WORKTREE REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
