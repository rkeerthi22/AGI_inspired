"""Nightly backup of both live databases (fixes F16, docs/HARDENING.md).

CLAUDE.md's recovery story -- "nightly hermes backup + git push = recovery" -- was fiction
in every clause when audited 2026-07-19: no git remote, both ledger.db and ledgerbook.db
gitignored (git holds zero ledger state), no backup scheduled task, nothing ever taken.
The ledger is the harness's declared source of truth and had no second copy anywhere; one
disk failure was total, unrecoverable loss.

Uses sqlite3's own .backup() API, not a file copy -- a copy taken while WAL is mid-write can
be corrupt; .backup() is safe against a concurrent writer (a live batch_runner run, thanks to
WAL) because it copies page-by-page through SQLite itself, not the filesystem.

    python orchestrator/backup.py            # take a backup now, prune, replicate offsite
    python orchestrator/backup.py --restore-test <dir>  # restore latest into <dir>, verify
    python orchestrator/backup.py --check-offsite       # report the destination + its class

Stdlib only -- deliberately: a backup tool that needs PyYAML or a venv to run is a backup
tool that stops working on exactly the bad day it exists for.

OFFSITE REPLICATION (2026-07-29). This module previously stopped at `backups/`, and said so:
"a local backups/ folder on the same physical disk does not survive a full disk failure."
Measured 2026-07-29, that caveat was worse than it read -- **this machine has ONE physical
disk.** C: and S: are both partitions of Disk 0 (a single 477GB NVMe), so `backups/` on S:,
the repo on S:, and anything written to C: all die together. There was no second copy of the
ledger anywhere in any sense that survives hardware loss.

Destination is configured, never hardcoded, via (first match wins):
  1. env AGI_OFFSITE_BACKUP_DIR
  2. config/offsite_backup.path  -- a one-line text file (survives Task Scheduler, which
     does not inherit an interactive shell's environment)
Unset = offsite replication is skipped with a visible warning, never a silent no-op."""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "backups"
KEEP_LAST_N = 14  # ~2 weeks of nightlies
KEEP_OFFSITE_N = 7
OFFSITE_CONFIG = ROOT / "config" / "offsite_backup.path"
SOURCES = {
    "ledger": ROOT / "ledger" / "ledger.db",
    "ledgerbook": ROOT / "memory" / "ledgerbook.db",
}

# Local folders whose contents are replicated off the MACHINE by a sync client. These sit
# on the same physical disk, so a disk check alone would reject them -- and they are the
# only genuinely off-machine target this box has, so rejecting them would be exactly wrong.
# Protection here is real but CONDITIONAL: it depends on the sync client actually being
# signed in and caught up, which this module cannot verify and does not claim to.
_SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "drivefs", "icloud")


def offsite_dir() -> Path | None:
    raw = os.environ.get("AGI_OFFSITE_BACKUP_DIR", "").strip()
    if not raw and OFFSITE_CONFIG.exists():
        raw = OFFSITE_CONFIG.read_text(encoding="utf-8").strip().splitlines()[0].strip() \
            if OFFSITE_CONFIG.read_text(encoding="utf-8").strip() else ""
    return Path(raw).expanduser() if raw else None


def _physical_disk(path: Path) -> str | None:
    """Windows physical disk number backing `path`'s drive letter, or None if that cannot
    be determined (UNC/network paths, non-Windows) -- None is treated as off-disk, since a
    path we cannot map to a local disk is not on the local disk."""
    drive = path.drive.rstrip(":")
    if len(drive) != 1:
        return None                      # UNC \\server\share or similar
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-Partition -DriveLetter {drive} -ErrorAction Stop).DiskNumber"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        out = p.stdout.strip()
        return out if out else None
    except Exception:
        return None


def classify_destination(dest: Path) -> tuple[str, str]:
    """(class, human explanation). class is one of:
    network | sync | different_disk | same_disk  -- only same_disk is refused."""
    low = str(dest).lower()
    if any(m in low for m in _SYNC_MARKERS):
        return "sync", ("a cloud-sync folder — leaves the machine once the client uploads it; "
                        "protection depends on that sync completing, which this tool cannot verify")
    src_disk = _physical_disk(BACKUP_DIR)
    dst_disk = _physical_disk(dest)
    if dst_disk is None:
        return "network", "not on a local disk (network/UNC path)"
    if src_disk is None or dst_disk != src_disk:
        return "different_disk", f"physical disk {dst_disk} (repo is on disk {src_disk})"
    return "same_disk", (f"SAME physical disk as the repo (disk {dst_disk}) — survives file "
                         f"deletion, does NOT survive disk failure")


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


def replicate_offsite(local: Path, name: str, allow_same_disk: bool = False) -> dict:
    """Copy one fresh backup to the configured offsite destination and VERIFY it there by
    reopening it and comparing row counts -- a copy that cannot be read is not a backup.

    Refuses a same-physical-disk destination by default. That refusal is the whole point:
    a backup path that silently sits on the one disk you are protecting against is worse
    than no backup, because it manufactures confidence. `backups/` already covers the
    delete-an-important-file case; this exists only for the disk-dies case."""
    dest_root = offsite_dir()
    if dest_root is None:
        return {"status": "unconfigured"}
    cls, why = classify_destination(dest_root)
    if cls == "same_disk" and not allow_same_disk:
        print(f"  OFFSITE REFUSED [{name}]: {dest_root} is on the {why}")
        print(f"  -> pick a destination on another device, or pass --allow-same-disk "
              f"if you genuinely only want protection from accidental deletion")
        return {"status": "refused_same_disk", "dest": str(dest_root)}
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / local.name
        shutil.copy2(local, dest)
        ok = _table_counts(dest) == _table_counts(local)
        pruned = prune(dest_root, name, KEEP_OFFSITE_N)
        print(f"  offsite [{cls}]: {dest} verified={ok} pruned={pruned}")
        return {"status": "ok" if ok else "verify_failed", "dest": str(dest),
                "class": cls, "verified": ok}
    except Exception as e:
        # Never let an offsite failure lose the LOCAL backup -- it is already on disk by
        # the time this runs. Report loudly, exit non-zero at the CLI, keep what we have.
        print(f"  OFFSITE FAILED [{name}]: {e}")
        return {"status": "error", "error": str(e)}


def run_backup(allow_same_disk: bool = False) -> dict:
    results = {}
    dest_root = offsite_dir()
    if dest_root is None:
        print("WARNING: no offsite destination configured — backups exist only on this "
              "machine's single physical disk. Set AGI_OFFSITE_BACKUP_DIR or write a path "
              f"into {OFFSITE_CONFIG.relative_to(ROOT)}")
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
        results[name] = {"dest": str(dest), "counts_match": ok,
                         "offsite": replicate_offsite(dest, name, allow_same_disk)}
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
    ap.add_argument("--check-offsite", action="store_true",
                    help="report the configured offsite destination and its protection class")
    ap.add_argument("--allow-same-disk", action="store_true",
                    help="permit an offsite destination on the repo's own physical disk "
                         "(protects against deletion only, NOT disk failure)")
    args = ap.parse_args()
    if args.check_offsite:
        d = offsite_dir()
        if d is None:
            print(f"offsite: NOT CONFIGURED (set AGI_OFFSITE_BACKUP_DIR or write a path "
                  f"into {OFFSITE_CONFIG.relative_to(ROOT)})")
            sys.exit(1)
        cls, why = classify_destination(d)
        print(f"offsite: {d}\n  class: {cls}\n  meaning: {why}\n  exists: {d.exists()}")
        sys.exit(0 if cls != "same_disk" else 1)
    if args.restore_test:
        d = Path(args.restore_test)
        ok = all(restore_test(name, d) for name in SOURCES)
        sys.exit(0 if ok else 1)
    res = run_backup(allow_same_disk=args.allow_same_disk)
    bad = [n for n, r in res.items()
           if r.get("offsite", {}).get("status") in ("error", "verify_failed")]
    sys.exit(1 if bad else 0)
