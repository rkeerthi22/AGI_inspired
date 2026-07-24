"""Exclusive run lock for the batch orchestrator.

Fixes F1 (docs/HARDENING.md): with no mutual exclusion, an overlapping run (e.g.
Sunday's canaries at 03:30 running long into the 04:00 scorecard fire, or any
manual command issued during a cron window) triggered db_integrity_check() to
delete the OTHER run's legitimate rows and raise a false "worker wrote directly
to a database" alarm — proven via a probe on DB copies, 2026-07-19.

Stdlib only, portable: os.O_CREAT|O_EXCL is atomic on both Windows and POSIX.
A stale lock (owner crashed/killed, never released) is reclaimed after
STALE_AFTER_SECONDS rather than wedging the harness forever — a coarse
crash-recovery aid; H3 handles per-task recovery precisely.
"""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

STALE_AFTER_SECONDS = 3600  # generous: longest observed real run ≈ 9 min/task,
                             # worst-case 5-task canary sweep ≈ 45 min


class AlreadyRunning(Exception):
    """Another batch_runner instance holds the lock and it is not stale."""


def _read_lock(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_stale(path: Path) -> bool:
    started = _read_lock(path).get("started_at")
    if started is None:
        return True  # unreadable/corrupt lock — don't wedge forever
    return (time.time() - started) > STALE_AFTER_SECONDS


@contextmanager
def acquire(path: Path):
    """Acquire the exclusive run lock or raise AlreadyRunning. Reclaims a stale
    lock once, then retries acquisition."""
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    for attempt in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(),
                                     "started_at": time.time()}).encode("utf-8"))
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            if attempt == 0 and _is_stale(path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue  # one retry after reclaiming
            raise AlreadyRunning(f"lock held: {_read_lock(path)}")
    try:
        yield
    finally:
        if acquired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
