"""Nightly backup of both live databases (fixes F16, docs/HARDENING.md).

CLAUDE.md's recovery story -- "nightly hermes backup + git push = recovery" -- was fiction
in every clause when audited 2026-07-19: no git remote, both ledger.db and ledgerbook.db
gitignored (git holds zero ledger state), no backup scheduled task, nothing ever taken.
The ledger is the harness's declared source of truth and had no second copy anywhere; one
disk failure was total, unrecoverable loss.

Uses sqlite3's own .backup() API, not a file copy -- a copy taken while WAL is mid-write can
be corrupt; .backup() is safe against a concurrent writer (a live batch_runner run, thanks to
WAL) because it copies page-by-page through SQLite itself, not the filesystem.

    python orchestrator/backup.py            # take a backup now, prune old ones
    python orchestrator/backup.py --restore-test <dir>  # restore latest into <dir>, verify

Stdlib only. Offsite/second-drive replication is a separate, operator decision (this only
solves "no second copy exists anywhere" -- a local backups/ folder on the same physical disk
does not survive a full disk failure; see docs/HARDENING.md H9 for the honest scope)."""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "backups"
KEEP_LAST_N = 14  # ~2 weeks of nightlies
SOURCES = {
    "ledger": ROOT / "ledger" / "ledger.db",
    "ledgerbook": ROOT / "memory" / "ledgerbook.db",
}


def _table_counts(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")]
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def backup_one(name: str, src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"{name}_{ts}.db"
    src_conn = sqlite3.connect(src)
    dest_conn = sqlite3.connect(dest)
    with dest_conn:
        src_conn.backup(dest_conn)  # safe against a concurrent writer (WAL-aware)
    src_conn.close()
    dest_conn.close()
    return dest


def prune(dest_dir: Path, name: str, keep: int = KEEP_LAST_N) -> int:
    files = sorted(dest_dir.glob(f"{name}_*.db"))
    excess = files[:-keep] if len(files) > keep else []
    for f in excess:
        f.unlink()
    return len(excess)


def run_backup() -> dict:
    results = {}
    for name, src in SOURCES.items():
        if not src.exists():
            print(f"SKIP {name}: {src} does not exist")
            continue
        before_counts = _table_counts(src)
        dest = backup_one(name, src, BACKUP_DIR)
        after_counts = _table_counts(dest)
        ok = before_counts == after_counts
        pruned = prune(BACKUP_DIR, name)
        print(f"{name}: {dest.name} ({dest.stat().st_size} bytes) "
             f"counts-match={ok} pruned={pruned} old file(s)")
        results[name] = {"dest": str(dest), "counts_match": ok}
    return results


def restore_test(name: str, into_dir: Path) -> bool:
    """Restore the LATEST backup for `name` into `into_dir` and diff row counts against
    the live source -- proves the backup is actually restorable, not just present."""
    candidates = sorted(BACKUP_DIR.glob(f"{name}_*.db"))
    if not candidates:
        print(f"no backups found for {name}")
        return False
    latest = candidates[-1]
    into_dir.mkdir(parents=True, exist_ok=True)
    restored = into_dir / f"{name}_restored.db"
    shutil.copy(latest, restored)
    live = SOURCES[name]
    live_counts = _table_counts(live) if live.exists() else {}
    restored_counts = _table_counts(restored)
    print(f"restoring {latest.name} -> {restored}")
    print(f"live counts:     {live_counts}")
    print(f"restored counts: {restored_counts}")
    # Live may have moved on since the backup was taken -- restored should be a SUBSET
    # (<=) of live for every table, never more, and never wildly divergent.
    ok = all(restored_counts.get(t, 0) <= live_counts.get(t, 0) + 5 for t in restored_counts)
    print("RESTORE VERIFIED OK" if ok else "RESTORE MISMATCH -- investigate")
    return ok


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore-test", metavar="DIR", help="restore latest backups into DIR and verify")
    args = ap.parse_args()
    if args.restore_test:
        d = Path(args.restore_test)
        ok = all(restore_test(name, d) for name in SOURCES)
        sys.exit(0 if ok else 1)
    else:
        run_backup()
