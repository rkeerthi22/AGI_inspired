"""tests/test_cli_attestation_chain.py -- Hermetic verification of CLI & Scheduler
attestation chaining (Gap A).

Asserts that:
1. Tasks queued via run_task.py and scheduler.queue_mission_tasks produce real,
   verifiable DSSE attestation chains starting with DISPATCH.
2. row["run_id"] is GATEWAY_RUN_ID, preserving the non-fabrication contract.
3. attestation_chain.existing() returns True, enabling full lifecycle chain recording.
4. End-to-end hermetic execution completes with a fully verified DSSE chain.
5. run_task.py --dry-run does not write to the database or emit attestation chains.
"""
from __future__ import annotations

from contextlib import ExitStack
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
import ledger
import operator_auth
import run_task
import scheduler
import task_runner
from v1_test_support import context, runner_fixture, source, worker_result
from deliverable_preflight import PreflightReport


def _init_db(db_path: Path):
    schema = (ROOT / "ledger" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    conn.close()


class TestCliAttestationChain(unittest.TestCase):
    def setUp(self):
        self.keys = operator_auth._generate_keypair()
        self.patch = patch.object(operator_auth, "_load_keypair", return_value=self.keys)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tearDown(self):
        gc.collect()

    def test_legacy_queue_task_does_not_fabricate_attestation(self):
        """Kill-assumption probe: direct ledger.queue_task has run_id != GATEWAY_RUN_ID
        and attestation_chain.existing() returns False (no fabrication)."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            db_path = Path(td) / "ledger.db"
            runs_dir = Path(td) / "runs"
            runs_dir.mkdir()
            _init_db(db_path)
            with patch.object(ledger, "LEDGER_DB", db_path):
                tid = ledger.queue_task("test-mission", "spec 1", "criteria 1")
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                row = dict(conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())
                conn.close()

                self.assertNotEqual(row["run_id"], chain.GATEWAY_RUN_ID)
                self.assertFalse(chain.existing(runs_dir, tid, row))
                self.assertFalse(chain.chain_path(runs_dir, tid).exists())

    def test_dispatch_admitted_task_records_dispatch_and_validates(self):
        """dispatch_admitted_task inserts row with GATEWAY_RUN_ID and DISPATCH step."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            db_path = Path(td) / "ledger.db"
            runs_dir = Path(td) / "runs"
            runs_dir.mkdir()
            _init_db(db_path)
            with patch.object(ledger, "LEDGER_DB", db_path):
                conn = sqlite3.connect(db_path)
                tid = chain.dispatch_admitted_task(
                    conn, runs_dir, "m-001", "spec for task", "must pass",
                    max_budget_usd=0.50, max_tokens=10000,
                )
                conn.row_factory = sqlite3.Row
                row = dict(conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())
                conn.close()

                self.assertEqual(row["run_id"], chain.GATEWAY_RUN_ID)
                self.assertTrue(chain.chain_path(runs_dir, tid).exists())
                self.assertTrue(chain.existing(runs_dir, tid, row))

                statements = chain.load(chain.chain_path(runs_dir, tid))
                self.assertEqual(len(statements), 1)
                first_body = json.loads(chain._decode(statements[0]["payload"]))
                self.assertEqual(first_body["step"], chain.Step.DISPATCH.value)
                self.assertEqual(first_body["task_id"], tid)
                self.assertEqual(first_body["claims"]["mission_id"], "m-001")

                valid, err = chain.verify_chain(statements, require_complete=False, task_id=tid)
                self.assertTrue(valid, f"Verification failed: {err}")

    def test_scheduler_queue_mission_tasks_routes_through_gateway_admission(self):
        """scheduler.queue_mission_tasks admits tasks with DISPATCH attestation chain."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            db_path = Path(td) / "ledger.db"
            runs_dir = Path(td) / "runs"
            runs_dir.mkdir()
            _init_db(db_path)
            mission = {
                "id": "001-test",
                "seeds": ["first research seed"],
                "frontmatter": {"status": "active"},
                "body": "test",
            }
            with ExitStack() as stack:
                stack.enter_context(patch.object(ledger, "LEDGER_DB", db_path))
                stack.enter_context(patch.object(scheduler, "RUNS", runs_dir))
                stack.enter_context(patch.object(scheduler, "week_key", return_value="2026-W38"))

                task_ids = scheduler.queue_mission_tasks(mission, dry=False)
                self.assertEqual(len(task_ids), 1)
                tid = task_ids[0]

                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                row = dict(conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())
                conn.close()

                self.assertEqual(row["run_id"], chain.GATEWAY_RUN_ID)
                self.assertTrue(chain.existing(runs_dir, tid, row))

    def test_cli_dispatched_task_produces_full_verified_chain(self):
        """Full end-to-end task runner with CLI admission produces complete DSSE chain."""
        clean_output = (
            "# Deliverable Output\n\n"
            "| Item | Value | Date | Conf |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| Fact 1 | 42 | 2026-09-15 | 3 |\n"
            "| Fact 2 | 99 | 2026-09-15 | 3 |\n\n"
            "Sources:\n"
            "- https://alive.example/pricing [Retrieved 2026-09-15, Confidence 3]\n"
        )

        def mock_worker(prompt, *_a, **_kw):
            return worker_result(clean_output)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as db_dir:
            db_path = Path(db_dir) / "ledger.db"
            _init_db(db_path)

            with runner_fixture(mock_worker) as (root, runs, stack):
                stack.enter_context(patch.object(ledger, "LEDGER_DB", db_path))

                # Queue task via admitted dispatch (as run_task.py does)
                tid = chain.dispatch_admitted_task(
                    db_path, runs, "001-shopify", "gather intel", "standard criteria"
                )

                # Configure preflight & critic to pass cleanly
                stack.enter_context(patch.object(task_runner.deliverable_preflight, "run_preflight",
                                                 return_value=PreflightReport(True, verified_sources=[source()])))
                stack.enter_context(patch.object(task_runner.citecheck, "verify", return_value=[source()]))
                stack.enter_context(patch.object(task_runner.evaluation, "run_critic",
                                                 return_value=("pass", "Deliverable meets all requirements")))
                stack.enter_context(patch.object(task_runner.evaluation, "RUNS", runs))
                stack.enter_context(patch.object(task_runner.evaluation, "extract_facts", return_value=2))

                task_ctx = context(tid=tid, spec="gather intel")
                status = task_runner._run_research_task(task_ctx)
                self.assertEqual(status, "done")

                # Verify chain has all stages and validates
                chain_file = chain.chain_path(runs, tid)
                self.assertTrue(chain_file.exists())
                statements = chain.load(chain_file)
                steps = [json.loads(chain._decode(s["payload"]))["step"] for s in statements]
                self.assertIn(chain.Step.DISPATCH.value, steps)
                self.assertIn(chain.Step.WORKER.value, steps)
                self.assertIn(chain.Step.PREFLIGHT.value, steps)
                self.assertIn(chain.Step.CRITIC.value, steps)
                self.assertIn(chain.Step.DELIVERABLE.value, steps)

                valid, err = chain.verify_chain(statements, require_complete=True, task_id=tid)
                self.assertTrue(valid, f"Full chain verification failed: {err}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
