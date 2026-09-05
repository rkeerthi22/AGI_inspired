"""Hermetic process-level evidence for checkpoint transaction serialization."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "orchestrator"))

import test_audit_replication as fixtures


audit = fixtures.audit_replication


def _replicate_process(source, config, environment, attempted, signing, release,
                       disable_lock=False):
    original = audit._checkpoint_lock

    @contextmanager
    def observed_lock(path):
        attempted.set()
        with (nullcontext() if disable_lock else original(path)):
            yield

    def paused_sign(checkpoint):
        signing.set()
        if not release.wait(15):
            raise RuntimeError("test signer synchronization timed out")
        return fixtures._sign(checkpoint)

    with mock.patch.object(audit, "_checkpoint_lock", observed_lock):
        audit.replicate_trajectory(Path(source), Path(config), environment,
                                   paused_sign, fixtures._verify)


def _lock_then_exit(path, acquired, release):
    with audit._checkpoint_lock(Path(path)):
        acquired.set()
        if not release.wait(15):
            os._exit(24)
        os._exit(23)  # Deliberately bypass finally; only this test child exits.


class AuditSerializationTests(unittest.TestCase):
    setUp = fixtures.AuditReplicationTests.setUp
    tearDown = fixtures.AuditReplicationTests.tearDown
    _trajectory = fixtures.AuditReplicationTests._trajectory
    _state = fixtures.AuditReplicationTests._state

    @property
    def checkpoint(self):
        return self.replica / "trajectory-checkpoints.jsonl"

    def replicate(self, source, signer=fixtures._sign):
        return audit.replicate_trajectory(source, self.config, self.environment,
                                          signer, fixtures._verify)

    def _concurrent_pair(self, disable_lock=False):
        context = multiprocessing.get_context("spawn")
        releases = [context.Event(), context.Event()]
        attempts = [context.Event(), context.Event()]
        signing = [context.Event(), context.Event()]
        sources = [self._trajectory(1), self._trajectory(2)]
        processes = [context.Process(target=_replicate_process, args=(
            str(sources[i]), str(self.config), self.environment, attempts[i],
            signing[i], releases[i], disable_lock)) for i in range(2)]
        started = []
        try:
            processes[0].start()
            started.append(processes[0])
            self.assertTrue(signing[0].wait(10), "first writer never reached signer")
            processes[1].start()
            started.append(processes[1])
            self.assertTrue(attempts[1].wait(10), "second writer never attempted lock")
            second_entered = signing[1].wait(3 if disable_lock else 0.3)
            # Release writer 0 to append its checkpoint
            releases[0].set()
            processes[0].join(15)
            self.assertEqual(processes[0].exitcode, 0, "first child failed or hung")
            if not disable_lock:
                self.assertTrue(signing[1].wait(10), "second writer never reached signer")
            # Release writer 1 to append its checkpoint
            releases[1].set()
            processes[1].join(15)
            self.assertEqual(processes[1].exitcode, 0, "second child failed or hung")
            self.assertEqual(second_entered, disable_lock,
                             "second signer entered the first writer's transaction")
        finally:
            for r in releases:
                r.set()
            for process in started:
                if process.is_alive():
                    process.terminate()
                process.join(5)
                process.close()
        return self._state()

    def test_process_writers_form_one_chain(self):
        state = self._concurrent_pair()
        self.assertTrue(state["ok"], state)
        self.assertEqual(state["checkpoints"], 2)

    def test_unlocked_negative_control_reproduces_fork(self):
        # Same deterministic schedule, test-only lock bypass, temporary replica.
        state = self._concurrent_pair(disable_lock=True)
        self.assertFalse(state["ok"])
        self.assertEqual(state["error"], "checkpoint_link_invalid")

    def test_contention_fails_before_copy_or_sign(self):
        source = self._trajectory()
        signer = mock.Mock(side_effect=fixtures._sign)
        with audit._checkpoint_lock(self.checkpoint):
            with mock.patch.object(audit, "CHECKPOINT_LOCK_TIMEOUT_SECONDS", 0.1):
                with self.assertRaisesRegex(audit.AuditReplicationError,
                                            "replica_lock_unavailable"):
                    self.replicate(source, signer)
        signer.assert_not_called()
        self.assertFalse(self.checkpoint.exists())
        self.assertFalse((self.replica / "trajectories").exists())
        self.replicate(source)
        self.assertTrue(self._state()["ok"])

    def test_process_exit_releases_lock_without_file_deletion(self):
        context = multiprocessing.get_context("spawn")
        acquired, release = context.Event(), context.Event()
        process = context.Process(target=_lock_then_exit,
                                  args=(str(self.checkpoint), acquired, release))
        process.start()
        try:
            self.assertTrue(acquired.wait(10))
            release.set()
            process.join(10)
            self.assertEqual(process.exitcode, 23)
            sidecar = self.checkpoint.with_name(self.checkpoint.name + ".lock")
            self.assertTrue(sidecar.is_file())
            self.replicate(self._trajectory())
            self.assertTrue(sidecar.is_file())
            self.assertTrue(self._state()["ok"])
        finally:
            release.set()
            if process.is_alive():
                process.terminate()
            process.join(5)
            process.close()

    def test_signer_and_append_exceptions_release_lock(self):
        source = self._trajectory()
        with self.assertRaisesRegex(RuntimeError, "sign failed"):
            self.replicate(source, mock.Mock(side_effect=RuntimeError("sign failed")))
        with mock.patch.object(audit, "_append_checkpoint", side_effect=OSError("append failed")):
            with self.assertRaisesRegex(OSError, "append failed"):
                self.replicate(source)
        self.replicate(source)
        self.assertEqual(self._state()["checkpoints"], 1)

    def test_lock_open_failure_never_falls_back_to_unlocked_write(self):
        import portalocker
        source = self._trajectory()
        with mock.patch.object(portalocker.Lock, "acquire", side_effect=PermissionError):
            with self.assertRaisesRegex(audit.AuditReplicationError,
                                        "replica_lock_unavailable:PermissionError"):
                self.replicate(source)
        self.assertEqual(list(self.replica.iterdir()), [])

    def test_same_source_threads_do_not_collide_on_temporary_copy(self):
        source = self._trajectory()
        barrier = threading.Barrier(4, timeout=10)

        def run():
            barrier.wait()
            return self.replicate(source)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(run) for _ in range(4)]
            for future in futures:
                future.result(timeout=15)
        self.assertTrue(self._state()["ok"])
        self.assertEqual(self._state()["checkpoints"], 4)
        self.assertEqual(len(list((self.replica / "trajectories").iterdir())), 1)

    def test_partial_append_is_preserved_and_retry_fails_closed(self):
        source = self._trajectory()

        def partial_append(path, checkpoint, signature):
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"checkpoint":')
            raise OSError("interrupted append")

        with mock.patch.object(audit, "_append_checkpoint", partial_append):
            with self.assertRaisesRegex(OSError, "interrupted append"):
                self.replicate(source)
        before = self.checkpoint.read_bytes()
        with self.assertRaisesRegex(audit.AuditReplicationError, "checkpoint_json_invalid"):
            self.replicate(source)
        self.assertEqual(self.checkpoint.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
