"""F110 (RC-1): citecheck must distinguish BLOCKED (server responded, page exists,
bot refused: 403/429/5xx) from DEAD (resource genuinely gone: 404/410/DNS/timeout).

Root cause: citecheck._fetch_one set reachable=False for ANY HTTPError, and summarize()
counted `not reachable` as dead -- so a live, bot-protected page (403 WAF-block, 429
rate-limit) was counted identically to a fabricated URL. This inflated dead_frac and
mechanically hard-failed tasks whose citations were to live sites.

Primary evidence (2026-09-03 cohort, M5/task116): runs/task116_citation_evidence.json
showed 4/8 cited URLs "dead" (dead_frac=0.50 > DEAD_FRAC_HARD_FAIL=0.34), but 3 of those 4
were 403/429 -- flowgpt.com returned 403 (the mission's OWN subject site). True dead_frac
was 1/8 = 0.125. M5 should have passed. The same pattern killed M6-rerun (task118, HN
throttling) and produced M7's "factual error" (model said FlowGPT loads; citecheck said
UNREACHABLE 403 -- the page loads fine, it 403s the bot).

This was promoted from tests/red/test_citecheck_waf_false_negative.py once the gap closed.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # S:\AGI_like
sys.path.insert(0, str(ROOT / "orchestrator"))

import citecheck  # noqa: E402


def _ev(url, status, error=None):
    """Build an evidence row the way _fetch_one does (reachable mirrors the real rule:
    200-399 is reachable; everything else is not, but is_dead distinguishes why)."""
    return {"url": url, "reachable": 200 <= (status or 0) < 400,
            "http_status": status, "error": error,
            "literal": None, "literal_found": None}


def test_blocked_only_is_not_a_hard_fail():
    """The task116/M5 pattern: 4 of 8 unreachable, but 3 of those 4 are 403/429 (live
    pages that refused the bot). Only postunreel (410) is genuinely gone. A responding
    server (403/429/503) means the page EXISTS; the citation is not fabricated. dead_frac
    must count GENUINELY-DEAD, not blocked."""
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
    """Guard: truly fabricated/gone citations (404/410/DNS, no usable server response)
    MUST still hard-fail. Prevents an over-lenient 'fix' that disables the dead check."""
    evidence = [
        _ev("https://example.invalid/fabricated-1", None, error="dns resolution failed"),
        _ev("https://example.invalid/fabricated-2", None, error="dns resolution failed"),
        _ev("https://gone.example/", 410),
        _ev("https://missing.example/", 404),
        _ev("https://live.example/", 200),
    ]
    summary = citecheck.summarize(evidence)
    assert citecheck.is_hard_fail(summary), (
        f"Over-lenient: genuinely-dead citations (404/410/DNS) must still hard-fail. "
        f"dead_frac={summary['dead_frac']}"
    )


def test_dead_classification_per_url():
    """Direct checks of citecheck.is_dead classification across the status spectrum."""
    assert citecheck.is_dead(_ev("https://ok.example/", 200)) is False       # reachable
    assert citecheck.is_dead(_ev("https://ok.example/", 302)) is False      # redirect-OK
    assert citecheck.is_dead(_ev("https://blocked.example/", 403)) is False  # WAF-block, live
    assert citecheck.is_dead(_ev("https://blocked.example/", 429)) is False  # rate-limited, live
    assert citecheck.is_dead(_ev("https://blocked.example/", 503)) is False  # server error, host exists
    assert citecheck.is_dead(_ev("https://gone.example/", 404)) is True      # not found
    assert citecheck.is_dead(_ev("https://gone.example/", 410)) is True      # gone
    assert citecheck.is_dead(_ev("https://nohost.example/", None,
                                error="dns resolution failed")) is True      # host doesn't exist
    assert citecheck.is_dead(_ev("https://nohost.example/", None,
                                error="timed out")) is True                 # unreachable


class TestF110(unittest.TestCase):
    def test_blocked_only_is_not_a_hard_fail(self):
        test_blocked_only_is_not_a_hard_fail()

    def test_genuinely_dead_still_hard_fails(self):
        test_genuinely_dead_still_hard_fails()

    def test_dead_classification_per_url(self):
        test_dead_classification_per_url()


if __name__ == "__main__":
    cases = [("A_blocked_only", test_blocked_only_is_not_a_hard_fail),
             ("B_genuinely_dead", test_genuinely_dead_still_hard_fails),
             ("C_classification", test_dead_classification_per_url)]
    failures = 0
    for name, fn in cases:
        try:
            fn()
            print(f"[GREEN] {name}")
        except AssertionError as e:
            failures += 1
            print(f"[RED]   {name}: {e}")
    print(f"\n{'ALL GREEN' if not failures else 'FAILURES'}: "
          f"{failures} red / {len(cases) - failures} green")
    sys.exit(1 if failures else 0)
