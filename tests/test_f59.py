"""F59: Compact Brief continuity layer is small, atomic, safe, and subordinate to Git."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import continuity  # noqa: E402
import integrity  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as raw:
    repo = Path(raw)
    git(repo, "init")
    git(repo, "config", "user.email", "continuity@test.invalid")
    git(repo, "config", "user.name", "Continuity Test")
    (repo / "DESIGN.md").write_text("durable design\n", encoding="utf-8")
    git(repo, "add", "DESIGN.md")
    git(repo, "commit", "-m", "initial")
    # A local recovery checkpoint must also work before an upstream is configured.
    check("temporary repo has no upstream",
          continuity.inspect_repository(repo)["upstream"], None)

    current = repo / ".harness" / "continuity" / "current.json"
    common = dict(
        task={"id": "T1", "phase": "editing", "status": "in_progress"},
        next_action="Run the focused test",
        completed=["initial checkpoint"],
        locked_constraints=["live state wins"],
        gate={"status": "green", "detail": "focused test passed"},
        references=[{"path": "DESIGN.md", "purpose": "decision record"}],
        root=repo, path=current,
    )

    first = continuity.write_current(**common)
    check("schema version", first["schema_version"], 1)
    check("first revision", first["brief_revision"], 1)
    check("file within hard cap", current.stat().st_size <= 4096, True)
    check("reference verified", continuity.validate_brief(first, repo)[0]["exists"], True)
    check("reference integrity verified",
          continuity.validate_brief(first, repo)[0]["sha256_matches"], True)

    second = continuity.write_current(**common)
    check("replace increments revision", second["brief_revision"], 2)
    check("no temporary file remains", list(current.parent.glob("current.*.tmp")), [])

    # The brief observed a clean tree before writing itself. A subsequent real edit must
    # be reported as a discrepancy, and the report explicitly chooses live state.
    (repo / "DESIGN.md").write_text("changed design\n", encoding="utf-8")
    report = continuity.recover(current, repo)
    fields = {item["field"] for item in report["discrepancies"]}
    check("live changed paths detected", "changed_paths" in fields, True)
    changed_delta = next(x for x in report["discrepancies"]
                         if x["field"] == "changed_paths")
    check("recovery reports actual edited durable file",
          any("DESIGN.md" in path for path in changed_delta["live"]), True)
    check("changed referenced content detected",
          "reference_integrity" in fields, True)
    check("all discrepancies choose live", {x["winner"] for x in report["discrepancies"]},
          {"live"})

    bad = dict(second)
    bad["task"] = dict(bad["task"], api_key="not-allowed")
    try:
        continuity.validate_brief(bad, repo)
        rejected_secret = False
    except continuity.ContinuityError:
        rejected_secret = True
    check("sensitive field rejected", rejected_secret, True)

    bad = dict(second)
    bad["references"] = [{"path": "../outside.txt"}]
    try:
        continuity.validate_brief(bad, repo)
        rejected_escape = False
    except continuity.ContinuityError:
        rejected_escape = True
    check("escaping reference rejected", rejected_escape, True)

    oversized = dict(second)
    oversized["completed"] = ["x" * 5000]
    try:
        continuity.validate_brief(oversized, repo)
        rejected_size = False
    except continuity.ContinuityError:
        rejected_size = True
    check("oversized brief rejected", rejected_size, True)

check("continuity directory protected", ".harness" in integrity.PROTECTED_PATHS, True)
live = continuity.load_current()
check("tracked current brief validates", live["schema_version"], 1)

# ── AGENTS.md: cross-agent discovery pointer ──────────────────────────

# AGENTS.md is the industry-convention file at the project root that Aider,
# Codex, Cursor, and Continue auto-discover. After it is committed (rather
# than only referenced in CLAUDE.md) it becomes a supervising-agent
# instruction surface, so the same argument F52 made for `.claude` applies:
# it must be in PROTECTED_PATHS and its hash must be tracked.

agents_path = ROOT / "AGENTS.md"
check("AGENTS.md exists at repo root", agents_path.is_file(), True)
check("AGENTS.md is Git-tracked",
      bool(subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch",
                           "AGENTS.md"], capture_output=True).returncode == 0), True)
check("AGENTS.md in PROTECTED_PATHS",
      "AGENTS.md" in integrity.PROTECTED_PATHS, True)
# _tracked_hashes() walks PROTECTED_PATHS so AGENTS.md must appear.
tracked = integrity._tracked_hashes()
check("_tracked_hashes() includes AGENTS.md",
      "AGENTS.md" in tracked, True)
check("AGENTS.md <= 2 KB",
      agents_path.stat().st_size <= 2048, True)

# Content checks: must name the real recover command, must say live wins,
# must NOT advertise `continuity.py write` (the CLI has only `validate` and
# `recover`; `write` was never implemented and would mislead the next agent).
content = agents_path.read_text(encoding="utf-8")
check("AGENTS.md names python orchestrator/continuity.py recover",
      "python orchestrator/continuity.py recover" in content, True)
check("AGENTS.md says live state wins",
      any(needle in content.lower() for needle in
          ("live state wins", "live wins", "live always wins")), True)
check("AGENTS.md does NOT advertise continuity.py write",
      "continuity.py write" not in content, True)
check("AGENTS.md does NOT duplicate the full protocol",
      "validate_brief" not in content
      and "write_current" not in content
      and "inspect_repository" not in content, True)
check("AGENTS.md points to the canonical Compact Brief path",
      ".harness/continuity/current.json" in content, True)

print("\n=== FAILURES ===")
if fails:
    for failure in fails:
        print(f"  - {failure}")
    raise SystemExit(1)
print("  none")
