"""F52: committing HANDOFF.md silently removed it from the containment surface.

`.claude/HANDOFF.md` was covered by _untracked_files()'s hashes (F46/F47) precisely
BECAUSE it was untracked. Tracking it removed it from that set without adding it to
_tracked_hashes(), which only walks PROTECTED_PATHS -- and `.claude` was not in the list.
Measured at the time: a simulated tamper produced an empty delta on all four detection
channels.

`.claude` is Claude Code's own config tree (agents, skills, settings, hooks), i.e. the one
place a written file steers the SUPERVISING agent -- which is exactly why F46 refused to
gitignore it.

Detection-only. fs_integrity_check() is never invoked, because its revert path has
destroyed uncommitted work three times (F36); the file is restored from a content copy.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import integrity  # noqa: E402
import policy  # noqa: E402
from _silence import silence_log  # noqa: E402

fails = []
# F56: silence ALL orchestrator log streams -- the F47 mask warning fires
# from inside integrity.fs_integrity_check(), not from batch_runner.log.
_silence_ctx = silence_log()
_silence_ctx.__enter__()


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


H = ROOT / ".claude" / "HANDOFF.md"

print("=== 1. the surface declares .claude ===")
check(".claude is in PROTECTED_PATHS", ".claude" in integrity.PROTECTED_PATHS, True)
check("policy.validate_paths still consistent",
      policy.validate_paths(integrity.PROTECTED_PATHS) or "consistent", "consistent")

print("\n=== 2. HANDOFF.md is tracked AND hashed ===")
tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", ".claude"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout.split()
check("HANDOFF.md is tracked by git", ".claude/HANDOFF.md" in tracked, True)
snap = integrity.fs_integrity_snapshot()
check("...and carried in the tracked-hash set",
      ".claude/HANDOFF.md" in snap["hashes"], True)

print("\n=== 3. IF a nested worktree exists, it stays collapsed (DECIDED, not an oversight) ===")
# 2026-07-30: the jolly-gauss-8e52cb worktree that motivated this test was removed
# (`git worktree remove`) once its F47 snapshot warning had done its job. The property
# under test was never "a worktree is present" -- it was "IF one is present, it must
# collapse to one marker entry and never be hashed". Asserting that unconditionally
# would silently stop testing anything the moment the tree is worktree-free, so this
# branches on live git state instead of hardcoding either world.
live_worktrees = [p for p in integrity._untracked_files() if "worktrees/" in p]
check("worktree files are NOT hashed",
      any("worktrees" in k for k in snap["hashes"]), False)
wt = {k: v for k, v in snap["untracked"].items() if "worktrees" in k}
if live_worktrees:
    check("it appears as exactly ONE entry, not enumerated file-by-file", len(wt), 1)
    check("...and that entry is the collapsed marker, not a content hash",
          list(wt.values()), ["<nested-repo>"])
else:
    check("no nested worktree present -> zero entries in either channel", len(wt), 0)

print("\n=== 4. a tamper is DETECTED (the actual regression) ===")
if not H.exists():
    print("  [SKIP] HANDOFF.md absent (bare clone) — nothing to tamper with")
else:
    safe = Path(tempfile.mkdtemp()) / "HANDOFF.safe.md"
    shutil.copy2(H, safe)
    original = H.read_text(encoding="utf-8")
    try:
        before = integrity.fs_integrity_snapshot()
        H.write_text(original + "\n<!-- simulated tamper -->\n", encoding="utf-8")
        after = integrity.fs_integrity_snapshot()
        changed = sorted(p for p in set(before["hashes"]) | set(after["hashes"])
                         if before["hashes"].get(p) != after["hashes"].get(p))
        porcelain = sorted(after["porcelain"] - before["porcelain"])
        check("tracked-hash channel catches it", changed, [".claude/HANDOFF.md"])
        check("porcelain channel catches it too (defence in depth)",
              any("HANDOFF" in p for p in porcelain), True)

        # validated against the defect: the pre-F52 surface, same tamper
        pre = [p for p in integrity.PROTECTED_PATHS if p != ".claude"]
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", *pre],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout.splitlines()
        check("PRE-F52 surface did not cover .claude/HANDOFF.md at all",
              ".claude/HANDOFF.md" in out, False)
    finally:
        shutil.copy2(safe, H)
        assert H.read_text(encoding="utf-8") == original
        print("         (restored by content, never git checkout — F36)")

print("\n=== 5. the residual F52 creates, stated rather than hidden (only WHILE a worktree exists) ===")
# .git/info/exclude masks .claude/worktrees/, which is under a protected path, so F47's
# mask detector fires on every snapshot -- but only while there is something under that
# directory for it to mask. 2026-07-30: jolly-gauss-8e52cb was removed via
# `git worktree remove`, which is the documented resolution, not a regression -- so the
# residual must now be conditional on live state, same as section 3.
masked = (integrity._masked_under_protected()
          if hasattr(integrity, "_masked_under_protected") else [])
if live_worktrees:
    check("F47 reports the worktree as a masked protected path",
          any("worktrees" in m for m in masked), True)
    print("         (expected: the collapse is DECIDED. `git worktree remove` clears it.)")
else:
    check("no worktree present -> nothing masked under .claude", sorted(masked), [])
    print("         (jolly-gauss-8e52cb removed 2026-07-30; warning silenced as designed.)")

print("\nFAILURES:", fails if fails else "none")
_silence_ctx.__exit__(None, None, None)
sys.exit(1 if fails else 0)
