"""F49: synthesis received a silently truncated brief and reported the missing part
as a data gap.

`run_synthesis()` built its input with `p.read_text()[:6000] for p in briefs[:6]` -- two
silent caps, no marker. Task 29's brief is 12,464 chars and "## Topic Opportunity 3"
begins at char 6,060, so the cut missed it by sixty characters; task 30 then declared a
data gap and told the operator to source material the harness already had.

The fix states every omission. It does NOT recover the text -- so these assertions check
that the loss is reportable, not that it is gone.

Validated against the defect: section 6 reproduces the pre-fix expression on the very
same file and shows it emits no marker at all.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import batch_runner as br  # noqa: E402

fails = []
tmp = Path(tempfile.mkdtemp())


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got}\n        want={want}")


FILL = "Q"   # must not occur in the marker prose, or the body count double-counts it
              # (first draft used "x" and collided with "exists" in the marker)


def brief(name: str, n: int) -> Path:
    p = tmp / name
    p.write_text(FILL * n, encoding="utf-8")
    return p


print("=== 1. a brief UNDER the cap is passed through untouched ===")
small = brief("2026-W31_small.md", 500)
blk = br.build_brief_block([small])
check("no truncation marker", "TRUNCATED BY THE HARNESS" in blk, False)
check("full content present", blk.count(FILL), 500)
check("filename is still labelled", "### 2026-W31_small.md" in blk, True)

print("\n=== 2. a brief OVER the cap is marked, with exact figures ===")
big = brief("2026-W31_big.md", 10_000)
blk = br.build_brief_block([big], cap=6000)
check("marker present", "[TRUNCATED BY THE HARNESS:" in blk, True)
check("states the omitted count", "4000 of 10000" in blk, True)
check("supplies exactly cap chars of body", blk.count(FILL), 6000)
check("explicitly denies 'absent'", "not absent" in blk, True)
check("explicitly denies 'data gap'", "NOT a data gap" in blk, True)

print("\n=== 3. boundary: exactly at the cap is NOT truncated ===")
exact = brief("2026-W31_exact.md", 6000)
blk = br.build_brief_block([exact], cap=6000)
check("no marker at exactly cap", "TRUNCATED BY THE HARNESS" in blk, False)
one_over = brief("2026-W31_oneover.md", 6001)
blk = br.build_brief_block([one_over], cap=6000)
check("marker at cap+1, omitting 1 char", "1 of 6001" in blk, True)

print("\n=== 4. the SECOND silent cap: briefs beyond max_briefs ===")
many = [brief(f"2026-W31_m{i}.md", 100) for i in range(8)]
blk = br.build_brief_block(many, max_briefs=6)
check("omitted-briefs section present", "[BRIEFS OMITTED BY THE HARNESS]" in blk, True)
check("states how many were dropped", "2 further brief(s)" in blk, True)
check("names the dropped files", "2026-W31_m6.md" in blk and "2026-W31_m7.md" in blk, True)
check("the 6 supplied are still there", blk.count("### 2026-W31_m"), 6)

print("\n=== 5. empty input still degrades cleanly ===")
check("no briefs -> '(none)'", br.build_brief_block([]), "(none)")

print("\n=== 6. THE REAL FILE that caused F49 ===")
real = (ROOT / "workspace" / "content" /
        "2026-W31_2026-w31-seed-2-ai-productivity-channel-new-from-blueprint.md")
if not real.exists():
    print("  [SKIP] source brief no longer on disk")
else:
    txt = real.read_text(encoding="utf-8")
    topic3 = txt.find("## Topic Opportunity 3")
    check("Topic 3 really does fall past the OLD 6000 cut", topic3 > 6000, True)
    # (a) at the historical cap, the marker fires with the true figures
    blk_old = br.build_brief_block([real], cap=6000)
    check("at cap=6000 the brief that broke task 30 is marked",
          "[TRUNCATED BY THE HARNESS:" in blk_old, True)
    check("marker states the true omitted count",
          f"{len(txt) - 6000} of {len(txt)}" in blk_old, True)
    # (b) validated against the defect: the pre-fix expression on the SAME file
    old = f"### {real.name}\n{txt[:6000]}"
    check("PRE-FIX expression emits no marker (this was the bug)",
          "TRUNCATED" in old, False)
    check("...and silently drops Topic 3 with no trace",
          "Topic Opportunity 3" in old, False)
    # (c) at the RAISED default cap the case that caused F49 no longer truncates at all
    blk_now = br.build_brief_block([real])
    check("at the shipped cap it is NOT truncated", "TRUNCATED BY THE HARNESS" in blk_now, False)
    check("...and Topic 3 now actually reaches the model",
          "Topic Opportunity 3" in blk_now, True)
    check("...along with the metaintro evidence task 30 was denied",
          "metaintro.com" in blk_now, True)

print("\n=== 7. the shipped cap clears every brief on disk ===")
allb = [p for d in ("content", "shopify", "onboarding")
        for p in sorted((ROOT / "workspace" / d).glob("*.md"))
        if (ROOT / "workspace" / d).exists() and "synthesis" not in p.name]
biggest = max(len(p.read_text(encoding="utf-8")) for p in allb)
over_old = sum(1 for p in allb if len(p.read_text(encoding="utf-8")) > 6000)
print(f"  {len(allb)} briefs on disk, largest {biggest} chars, {over_old} exceeded the old 6000")
check("the old cap really was overflowed by most briefs", over_old > len(allb) // 2, True)
check("the shipped cap clears the largest of them",
      biggest < br.SYNTHESIS_BRIEF_CHARS, True)
check("cap did not silently regress below the largest brief",
      br.SYNTHESIS_BRIEF_CHARS >= 16000, True)

print("\n=== 8. the prompt teaches the model what the marker means ===")
# A marker the model cannot interpret would still be read as absence.
import inspect  # noqa: E402
src = inspect.getsource(br.run_synthesis)
check("prompt distinguishes truncation from a data gap",
      "TRUNCATION IS NOT A DATA GAP" in src, True)
check("prompt forbids re-tasking the operator",
      "they already have it" in src, True)

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
