"""RED TEST — proves gap RC-1: citecheck treats HTTP 403/429 (bot-blocked but LIVE)
as "dead citation", the same as 404/410 (genuinely gone). This inflates dead_frac and
mechanically hard-fails tasks whose citations are to live, WAF-protected / rate-limited
sites. See blueprint doc docs/CODEX_HANDOFF_2026-09-04_REDTEAM_AND_BLUEPRINT.md (RC-1).

NOT in the gate suite (lives in tests/red/; excluded from tests/tiers.json). Codex:
make BOTH cases GREEN to close RC-1, then promote this file into tests/ + tiers.json.

ROOT CAUSE (verified 2026-09-04 against code + run artifacts):
  orchestrator/citecheck.py:297  ->  result["reachable"] = 200 <= resp.status < 400
  orchestrator/citecheck.py:305-307 ->  HTTPError (incl. 403, 429) sets reachable=False
  orchestrator/citecheck.py:334  ->  dead = sum(1 for e in evidence if not e["reachable"])
  -> a 403/429 (server RESPONDED, page exists, bot was refused) is counted as 'dead',
     identical to 404/410/DNS-failure (fabricated / genuinely gone citation).

PRIMARY EVIDENCE — task116 (mission M5, "verify FlowGPT's 50M+ prompts claim"):
  runs/task116_citation_evidence.json shows 4/8 citations unreachable (dead_frac=0.50
  > DEAD_FRAC_HARD_FAIL=0.34 at citecheck.py:38) -> mechanical hard-fail. But of those 4:
    https://flowgpt.com/              403  <- the mission's OWN subject site (live, WAF)
    https://www.capterra.com/.../     403  <- live, WAF-blocked
    https://megatek.ai/.../flowgpt/  429  <- rate-limited, not dead
    https://postunreel.com/...        410  <- genuinely gone (the ONLY true dead one)
  TRUE dead_frac = 1/8 = 0.125 (well under threshold). M5 should have PASSED.
  Same pattern killed task118 (M6 rerun): 10/13 HN item URLs 'unreachable', many 429/403
  from news.ycombinator.com bot throttling. The model cited real sources; the gate called
  them dead.

This is a core-thesis attack: "mechanical gates force cheap models to behave reliably"
is undermined when the gate produces FALSE NEGATIVES on legitimate citations to
bot-protected sites. The harness was measuring network/bot tolerance, not model quality.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # S:\AGI_like
sys.path.insert(0, str(ROOT / "orchestrator"))

import citecheck  # noqa: E402


def _ev(url, status, error=None):
    """Build an evidence row the way _fetch_one does (reachable mirrors the real rule)."""
    return {"url": url, "reachable": 200 <= (status or 0) < 400,
            "http_status": status, "error": error,
            "literal": None, "literal_found": None}


def test_blocked_only_is_not_a_hard_fail():
    """GAP CASE: the task116/M5 pattern — 4 of 8 unreachable, but 3 of those 4 are
    403/429 (live pages that refused the bot). Only postunreel (410) is genuinely gone.
    A responding server (403/429/503) means the page EXISTS; the citation is not
    fabricated. dead_frac must count GENUINELY-DEAD, not blocked."""
    evidence = [
        _ev("https://flowgpt.com/", 403),
        _ev("https://www.capterra.com/p/10015310/FlowGPT/", 403),
        _ev("https://megatek.ai/en/prompts/flowgpt/", 429),
        _ev("https://postunreel.com/blog/flowgpt-review", 410),
        _ev("https://aigearbase.com/tool/flowgpt", 200),
        _ev("https://www.similarweb.com/ai-traffic/flowgpt.com/", 200),
        _ev("https://topai.tools/t/flowgpt", 200),
        _ev("https://techcrunch.com/2024/02/26/flowgpt", 200),
    ]
    summary = citecheck.summarize(evidence)
    assert not citecheck.is_hard_fail(summary), (
        f"FALSE NEGATIVE: {summary['dead']}/{summary['checked']} counted 'dead' but only "
        f"1 is genuinely gone (410); the rest are 403/429 (live, bot-blocked). dead_frac="
        f"{summary['dead_frac']} crossed the hard-fail line on BLOCKED, not DEAD, citations."
    )


def test_genuinely_dead_still_hard_fails():
    """GUARD CASE: truly fabricated/gone citations (404/410/DNS, no usable server
    response / resource removed) MUST still hard-fail. Prevents an over-lenient 'fix'
    that just disables the dead check entirely."""
    evidence = [
        _ev("https://example.invalid/fabricated-1", None, error="DNS: name not resolved"),
        _ev("https://example.invalid/fabricated-2", None, error="DNS: name not resolved"),
        _ev("https://gone.example/", 410),
        _ev("https://missing.example/", 404),
        _ev("https://live.example/", 200),
    ]
    summary = citecheck.summarize(evidence)
    assert citecheck.is_hard_fail(summary), (
        f"Over-lenient: genuinely-dead citations (404/410/DNS) must still hard-fail. "
        f"dead_frac={summary['dead_frac']}"
    )


if __name__ == "__main__":
    cases = [("A_blocked_only", test_blocked_only_is_not_a_hard_fail),
             ("B_genuinely_dead", test_genuinely_dead_still_hard_fails)]
    failures = 0
    for name, fn in cases:
        try:
            fn()
            print(f"[GREEN] {name}")
        except AssertionError as e:
            failures += 1
            print(f"[RED]   {name}: {e}")
    print(f"\n{'GAP OPEN (red)' if failures else 'GAP CLOSED (green)'}: "
          f"{failures} red / {len(cases) - failures} green")
    sys.exit(1 if failures else 0)
