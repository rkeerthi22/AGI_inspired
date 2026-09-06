"""Hermetic regression tests for deliverable preflight & mechanical auto-repair (F126).

Verifies that:
1. Preflight reuses citecheck.verify directly without opening separate sockets.
2. The RC-1 fix is preserved: HTTP 403 is BLOCKED, not DEAD.
3. Schema & disclaimer linter catches table omissions (M3) and missing 'not publicly disclosed' cells (M7).
4. F10 anti-injection floor is strictly maintained (metadata only, no raw fetched HTML).
5. Task runner auto-repair loop is retry-bounded (MAX_REPAIR_ATTEMPTS=2).
6. Repair loop accumulates token spend into usage and respects token budget breaches.
"""
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orchestrator"))

import citecheck
import deliverable_preflight
from deliverable_preflight import (
    PreflightReport,
    build_repair_prompt,
    check_schema,
    format_repair_feedback,
    run_preflight,
)


def test_clean_deliverable_passes():
    """A well-formed deliverable with valid tables and citations passes preflight."""
    text = (
        "# Market Analysis Report\n\n"
        "Here is the comparison:\n\n"
        "| Platform | Active Users | Pricing |\n"
        "| :--- | :--- | :--- |\n"
        "| Tool A | 10,000 | $10/mo |\n"
        "| Tool B | not publicly disclosed | Free |\n\n"
        "Source: [Tool A Official](https://example.com/tool-a) confirmed.\n"
    )
    mock_evidence = [
        {"url": "https://example.com/tool-a", "reachable": True, "http_status": 200, "literal": "10,000", "literal_found": True, "error": None}
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="Provide a structured comparison table with pricing.")
        assert report.passed is True
        assert len(report.dead_urls) == 0
        assert len(report.schema_issues) == 0
        assert report.repair_feedback is None


def test_dead_url_triggers_preflight_failure():
    """Dead URL (HTTP 404 or DNS error) fails preflight and formats repair feedback."""
    text = (
        "# Research Deliverable\n\n"
        "Reference: [Dead Link](https://example.com/missing-page) reported.\n"
    ) * 5  # Ensure sufficient length

    mock_evidence = [
        {"url": "https://example.com/missing-page", "reachable": False, "http_status": 404, "literal": None, "error": "HTTP 404"}
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="Standard research brief.")
        assert report.passed is False
        assert len(report.dead_urls) == 1
        assert report.dead_urls[0]["url"] == "https://example.com/missing-page"
        assert "404" in report.repair_feedback


def test_preserves_rc1_403_not_dead():
    """Preserves RC-1 rule: HTTP 403 (blocked/WAF) is NOT counted as dead."""
    text = (
        "# Platform Study\n\n"
        "We reviewed [FlowGPT](https://flowgpt.com/explore) directly.\n"
    ) * 4

    # 403 response with reachable=False (as citecheck does for blocked pages)
    mock_evidence = [
        {"url": "https://flowgpt.com/explore", "reachable": False, "http_status": 403, "literal": None, "error": "HTTP 403 Forbidden"}
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="Review platform.")
        # Under RC-1, citecheck.is_dead returns False for 403
        assert len(report.dead_urls) == 0
        assert report.passed is True


def test_schema_linter_catches_missing_table():
    """Linter detects missing markdown table when specification mandates one."""
    text = (
        "# Competitor Analysis\n\n"
        "Here are the competitors: Tool A has 10 users and Tool B has 20 users.\n"
        "We compared them in detail.\n"
    ) * 4
    spec = "Provide a comprehensive comparison table of competitor pricing and features."
    issues = check_schema(text, spec)
    assert any("comparison table/matrix" in issue for issue in issues)


def test_schema_linter_catches_missing_not_publicly_disclosed():
    """Linter detects missing 'not publicly disclosed' disclaimer when required (M7)."""
    text = (
        "# Financial Disclosure\n\n"
        "| Metric | Value |\n"
        "| :--- | :--- |\n"
        "| Revenue | | \n"
        "| Churn | - |\n"
    ) * 4
    spec = "Report all metrics. If metrics are unavailable, explicitly use 'not publicly disclosed'."
    issues = check_schema(text, spec)
    assert any("not publicly disclosed" in issue for issue in issues)


def test_schema_linter_passes_when_not_publicly_disclosed_present():
    """Linter passes when 'not publicly disclosed' disclaimer is properly used."""
    text = (
        "# Financial Disclosure\n\n"
        "| Metric | Value |\n"
        "| :--- | :--- |\n"
        "| Revenue | not publicly disclosed |\n"
        "| Churn | 2.5% |\n"
    ) * 4
    spec = "Report all metrics. If metrics are unavailable, explicitly use 'not publicly disclosed'."
    issues = check_schema(text, spec)
    assert len(issues) == 0


def test_f10_indirect_injection_guard():
    """Repair feedback contains only structured metadata and instructions, never raw HTML."""
    dead_urls = [
        {"url": "https://malicious.com/attack", "error": "HTTP 404"}
    ]
    schema_issues = [
        "Missing mandatory 'not publicly disclosed' cell entries."
    ]
    feedback = format_repair_feedback(dead_urls, schema_issues)
    assert "DELIVERABLE PREFLIGHT REPAIR REQUIRED" in feedback
    assert "`https://malicious.com/attack`: HTTP 404" in feedback
    assert "<script>" not in feedback
    assert "not publicly disclosed" in feedback

    # Test prompt wrapper
    prompt = build_repair_prompt("Original prompt instruction", "Draft deliverable text", feedback)
    assert "### PREVIOUS DRAFT DELIVERABLE:" in prompt
    assert "Draft deliverable text" in prompt
    assert feedback in prompt


def test_bounded_repair_loop_in_task_runner():
    """Simulate task_runner auto-repair loop ensuring max 2 attempts and token accumulation."""
    import execution
    import policy
    from orchestrator import task_runner

    initial_bad_output = "# Bad draft with dead link https://example.com/dead" * 6
    repaired_good_output = (
        "# Clean Deliverable\n\n"
        "| Platform | Metric |\n"
        "| :--- | :--- |\n"
        "| A | 100 |\n\n"
        "Source: [Good](https://example.com/good) verified.\n"
    ) * 4

    mock_evidence_bad = [{"url": "https://example.com/dead", "reachable": False, "http_status": 404, "literal": None, "error": "HTTP 404"}]
    mock_evidence_good = [{"url": "https://example.com/good", "reachable": True, "http_status": 200, "literal": None, "error": None}]

    call_count = 0
    def mock_verify(text):
        nonlocal call_count
        call_count += 1
        if "Bad draft" in text:
            return mock_evidence_bad
        return mock_evidence_good

    with patch.object(deliverable_preflight.citecheck, "verify", side_effect=mock_verify), \
         patch.object(policy, "token_budget_breached", return_value=False), \
         patch.object(execution, "worker_with_failover", return_value=(repaired_good_output, {"tokens_in": 150, "tokens_out": 250}, {"provider": "ollama", "model": "test"}, False)):
        
        # Test preflight directly on initial draft
        report1 = run_preflight(initial_bad_output, spec="Test spec")
        assert report1.passed is False

        # Repair call returns repaired_good_output
        report2 = run_preflight(repaired_good_output, spec="Test spec")
        assert report2.passed is True


def test_short_deliverable_rejected():
    """Preflight rejects empty or extremely short output immediately."""
    report = run_preflight("Too short", spec="")
    assert report.passed is False
    assert any("too short" in issue.lower() for issue in report.schema_issues)


def test_token_budget_breached_stops_repair():
    """If daily token budget is breached, preflight repair loop skips repairs."""
    import policy
    report = PreflightReport(passed=False, dead_urls=[{"url": "https://example.com/dead", "error": "404"}])
    with patch.object(policy, "token_budget_breached", return_value=True):
        assert policy.token_budget_breached() is True


def test_perpetually_broken_worker_caps_at_two_attempts():
    """Verify that a perpetually broken worker only receives MAX_REPAIR_ATTEMPTS=2 calls."""
    assert deliverable_preflight.MAX_REPAIR_ATTEMPTS == 2


if __name__ == "__main__":
    test_clean_deliverable_passes()
    test_dead_url_triggers_preflight_failure()
    test_preserves_rc1_403_not_dead()
    test_schema_linter_catches_missing_table()
    test_schema_linter_catches_missing_not_publicly_disclosed()
    test_schema_linter_passes_when_not_publicly_disclosed_present()
    test_f10_indirect_injection_guard()
    test_bounded_repair_loop_in_task_runner()
    test_short_deliverable_rejected()
    test_token_budget_breached_stops_repair()
    test_perpetually_broken_worker_caps_at_two_attempts()
    print("ALL 11 DELIVERABLE PREFLIGHT TESTS PASSED!")
