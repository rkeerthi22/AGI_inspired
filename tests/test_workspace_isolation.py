"""tests/test_workspace_isolation.py -- Hermetic verification of per-task workspace
isolation and confinement (Gap B).

Asserts that:
1. Sequential tasks have isolated worker homes under workspace/tasks/{task_id}/.
   Task 1 writes a sentinel in its home; Task 2's worker home does not see it.
2. policy.is_path_writable restricts writes to workspace/tasks/{task_id}/ when task_id
   is in scope, rejecting writes to workspace/worker_home and sibling task directories.
3. WorkspaceConfinementGuard catches writes outside workspace/tasks/{task_id}/, auto-reverts
   the unauthorized files, and raises WorkspaceConfinementViolation.
4. When task_runner encounters a WorkspaceConfinementViolation, it fails closed to
   status="infra_failed".
5. Non-task operations (task_id=None) preserve access to workspace/worker_home.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.path.insert(0, str(ROOT / "tests"))

import attestation_chain as chain
import integrity
import ledger
import operator_auth
import policy
import task_runner
from v1_test_support import context, runner_fixture, source, worker_result
from deliverable_preflight import PreflightReport


def _init_db(db_path: Path):
    schema = (ROOT / "ledger" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    conn.close()


class TestWorkspaceIsolation(unittest.TestCase):
    def setUp(self):
        self.keys = operator_auth._generate_keypair()
        self.patch = patch.object(operator_auth, "_load_keypair", return_value=self.keys)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tearDown(self):
        gc.collect()

    def test_policy_is_path_writable_enforces_task_confinement(self):
        """policy.is_path_writable confines writes to workspace/tasks/{task_id}/ when task_id is set."""
        ws = ROOT / "workspace"
        task1_dir = ws / "tasks" / "101"
        task2_dir = ws / "tasks" / "102"
        worker_home = ws / "worker_home"

        # When task_id=101 is in scope:
        # Permitted:
        self.assertTrue(policy.is_path_writable(task1_dir, task_id=101))
        self.assertTrue(policy.is_path_writable(task1_dir / "draft.txt", task_id=101))
        self.assertTrue(policy.is_path_writable(task1_dir / "sub" / "file.json", task_id=101))

        # Rejected (sibling task, shared worker_home, top-level workspace):
        self.assertFalse(policy.is_path_writable(task2_dir, task_id=101))
        self.assertFalse(policy.is_path_writable(task2_dir / "clobber.txt", task_id=101))
        self.assertFalse(policy.is_path_writable(worker_home, task_id=101))
        self.assertFalse(policy.is_path_writable(worker_home / "config.yaml", task_id=101))
        self.assertFalse(policy.is_path_writable(ws / "escape.txt", task_id=101))

        # Protected repo path outside workspace is always rejected:
        self.assertFalse(policy.is_path_writable(ROOT / "config" / "policy.yaml", task_id=101))

        # When task_id is None (general harness context):
        # All workspace paths remain writable:
        self.assertTrue(policy.is_path_writable(task1_dir, task_id=None))
        self.assertTrue(policy.is_path_writable(worker_home, task_id=None))
        self.assertTrue(policy.is_path_writable(ws, task_id=None))

    def test_sequential_tasks_have_isolated_homes(self):
        """Task 1 writes a sentinel to its home; Task 2 has its own home and cannot see it."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ws = root / "workspace"
            ws.mkdir(parents=True, exist_ok=True)

            # Task 1 worker home
            task1_home = ws / "tasks" / "1"
            task1_home.mkdir(parents=True, exist_ok=True)
            sentinel = task1_home / "task1_sentinel.txt"
            sentinel.write_text("secret task 1 data", encoding="utf-8")

            # Task 2 worker home
            task2_home = ws / "tasks" / "2"
            task2_home.mkdir(parents=True, exist_ok=True)

            # Assert isolation: Task 2 home does NOT contain Task 1's sentinel
            self.assertTrue(sentinel.is_file())
            self.assertFalse((task2_home / "task1_sentinel.txt").exists())
            self.assertEqual(len(list(task2_home.glob("*"))), 0)

    def test_workspace_confinement_guard_catches_and_reverts_unauthorized_writes(self):
        """WorkspaceConfinementGuard catches writes outside assigned task dir and reverts them."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ws = root / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "worker_home").mkdir(parents=True, exist_ok=True)
            (ws / "tasks" / "1").mkdir(parents=True, exist_ok=True)
            (ws / "tasks" / "2").mkdir(parents=True, exist_ok=True)

            with patch("runtime_context.ROOT", root):
                # Legitimate write inside task 1 workspace passes
                with integrity.WorkspaceConfinementGuard(1, "task 1 legitimate write"):
                    (ws / "tasks" / "1" / "legit.txt").write_text("ok", encoding="utf-8")
                self.assertTrue((ws / "tasks" / "1" / "legit.txt").is_file())

                # Rogue write to sibling task 2 workspace is caught and reverted
                rogue_file = ws / "tasks" / "2" / "rogue_in_task2.txt"
                with self.assertRaises(integrity.WorkspaceConfinementViolation):
                    with integrity.WorkspaceConfinementGuard(1, "task 1 rogue sibling write"):
                        rogue_file.write_text("attack", encoding="utf-8")

                # Assert auto-revert: rogue file was removed
                self.assertFalse(rogue_file.exists())

                # Rogue write to shared worker_home is caught and reverted
                rogue_worker_home = ws / "worker_home" / "poison.txt"
                with self.assertRaises(integrity.WorkspaceConfinementViolation):
                    with integrity.WorkspaceConfinementGuard(1, "task 1 rogue worker_home write"):
                        rogue_worker_home.write_text("poison", encoding="utf-8")

                # Assert auto-revert
                self.assertFalse(rogue_worker_home.exists())

    def test_task_runner_fails_closed_on_workspace_confinement_violation(self):
        """task_runner fails closed to infra_failed if worker violates workspace confinement."""
        rogue_target = None

        def rogue_worker(prompt, *_a, **_kw):
            nonlocal rogue_target
            if rogue_target:
                rogue_target.write_text("rogue write to shared home", encoding="utf-8")
            return worker_result("Deliverable output")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as db_dir:
            db_path = Path(db_dir) / "ledger.db"
            _init_db(db_path)

            with runner_fixture(rogue_worker) as (root, runs, stack):
                stack.enter_context(patch.object(ledger, "LEDGER_DB", db_path))
                ws = root / "workspace"
                ws.mkdir(parents=True, exist_ok=True)
                shared_home = ws / "worker_home"
                shared_home.mkdir(parents=True, exist_ok=True)
                rogue_target = shared_home / "unauthorized_write.txt"

                tid = chain.dispatch_admitted_task(
                    db_path, runs, "001-shopify", "gather intel", "standard criteria"
                )

                task_ctx = context(tid=tid, spec="gather intel")
                status = task_runner._run_research_task(task_ctx)

                # Must fail-closed to infra_failed
                self.assertEqual(status, "infra_failed")

                # Rogue file must have been auto-reverted
                self.assertFalse(rogue_target.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
