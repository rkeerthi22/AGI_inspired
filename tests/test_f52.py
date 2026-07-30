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
import batch_runner as br  # noqa: E402
import policy  # noqa: E402

fails = []
br.log = lambda *a, **k: None          # silence the F47 mask warning during the run


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


H = ROOT / ".claude" / "HANDOFF.md"

print("=== 1. the surface declares .claude ===")
check(".claude is in PROTECTED_PATHS", ".claude" in br.PROTECTED_PATHS, True)
check("policy.validate_paths still consistent",
      policy.validate_paths(br.PROTECTED_PATHS) or "consistent", "consistent")

print("\n=== 2. HANDOFF.md is tracked AND hashed ===")
tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", ".claude"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout.split()
check("HANDOFF.md is tracked by git", ".claude/HANDOFF.md" in tracked, True)
snap = br.fs_integrity_snapshot()
check("...and carried in the tracked-hash set",
      ".claude/HANDOFF.md" in snap["hashes"], True)

print("\n=== 3. the nested worktree stays collapsed (DECIDED, not an oversight) ===")
check("worktree files are NOT hashed",
      any("worktrees" in k for k in snap["hashes"]), False)
wt = {k: v for k, v in snap["untracked"].items() if "worktrees" in k}
check("it appears as exactly ONE entry, not enumerated file-by-file", len(wt), 1)
check("...and that entry is the collapsed marker, not a content hash",
      list(wt.values()), ["<nested-repo>"])

print("\n=== 4. a tamper is DETECTED (the actual regression) ===")
if not H.exists():
    print("  [SKIP] HANDOFF.md absent (bare clone) — nothing to tamper with")
else:
    safe = Path(tempfile.mkdtemp()) / "HANDOFF.safe.md"
    shutil.copy2(H, safe)
    original = H.read_text(encoding="utf-8")
    try:
        before = br.fs_integrity_snapshot()
        H.write_text(original + "\n<!-- simulated tamper -->\n", encoding="utf-8")
        after = br.fs_integrity_snapshot()
        changed = sorted(p for p in set(before["hashes"]) | set(after["hashes"])
                         if before["hashes"].get(p) != after["hashes"].get(p))
        porcelain = sorted(after["porcelain"] - before["porcelain"])
        check("tracked-hash channel catches it", changed, [".claude/HANDOFF.md"])
        check("porcelain channel catches it too (defence in depth)",
              any("HANDOFF" in p for p in porcelain), True)

        # validated against the defect: the pre-F52 surface, same tamper
        pre = [p for p in br.PROTECTED_PATHS if p != ".claude"]
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", *pre],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout.splitlines()
        check("PRE-F52 surface did not cover it at all",
              any("HANDOFF" in t for t in out), False)
    finally:
        shutil.copy2(safe, H)
        assert H.read_text(encoding="utf-8") == original
        print("         (restored by content, never git checkout — F36)")

print("\n=== 5. the residual F52 creates, stated rather than hidden ===")
# .git/info/exclude masks .claude/worktrees/, which is now UNDER a protected path, so
# F47's mask detector fires on every snapshot. That is F47 working, not failing.
masked = br._masked_under_protected() if hasattr(br, "_masked_under_protected") else []
check("F47 reports the worktree as a masked protected path",
      any("worktrees" in m for m in masked), True)
print("         (expected: the collapse is DECIDED. `git worktree remove` clears it.)")

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
