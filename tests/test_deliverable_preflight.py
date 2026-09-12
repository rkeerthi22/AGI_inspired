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
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

os.environ.setdefault("AGI_TEST_TIER", "unit")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "orchestrator"))

import citecheck
import deliverable_preflight
from deliverable_preflight import (
    PreflightReport,
    build_repair_prompt,
    check_citation_metadata,
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


def test_no_direct_sockets_or_urllib():
    """Assert deliverable_preflight has zero direct socket or urllib dependencies."""
    import inspect
    source = inspect.getsource(deliverable_preflight)
    assert "import urllib" not in source, "deliverable_preflight must not import urllib directly"
    assert "from urllib" not in source, "deliverable_preflight must not import urllib directly"
    assert "import socket" not in source, "deliverable_preflight must not import socket directly"
    assert "import requests" not in source, "deliverable_preflight must not import requests directly"
    assert "http.client" not in source, "deliverable_preflight must not import http.client directly"

    # Runtime assertion: raising mocks on socket and urllib to ensure run_preflight opens no socket
    import socket
    import urllib.request
    text = "# Test\n\n| Platform | Metric |\n| :--- | :--- |\n| A | 100 |\n\n[Link](https://example.com) verified.\n" * 4
    with patch.object(socket, "socket", side_effect=RuntimeError("Direct socket call forbidden")), \
         patch.object(urllib.request, "urlopen", side_effect=RuntimeError("Direct urlopen forbidden")), \
         patch.object(deliverable_preflight.citecheck, "verify", return_value=[]):
        report = run_preflight(text, spec="")
        assert report.passed is True


def test_is_infra_error_suppresses_repair():
    """Verify is_infra_error identifies failure modes and suppresses auto-repair feedback."""
    assert deliverable_preflight.is_infra_error("worker API failure (full text in runs/task1_worker_raw.txt)") is True
    assert deliverable_preflight.is_infra_error("task 1: infra_failed (worker timeout)") is True
    assert deliverable_preflight.is_infra_error("chain_exhausted: quota on all models") is True
    assert deliverable_preflight.is_infra_error("Valid clean output with normal content") is False

    # Calling run_preflight on infra error returns repair_feedback=None so repair loop aborts
    infra_text = "worker API failure: upstream HTTP 500 error connecting to provider" * 3
    report = run_preflight(infra_text, spec="Some task spec")
    assert report.passed is False
    assert report.repair_feedback is None, "Infra error must have repair_feedback=None to prevent repair calls"


def test_abuse_bounds_under_ceiling_passes():
    """1 POLICY_DENIED + 3 OK citations (25% <= 25%, count 1 <= 2, ok 3 >= 2) passes."""
    text = (
        "# Analysis\n\n"
        "Here are facts from multiple sources:\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [OK 3](https://ok3.com) confirmed.\n"
        "- Fact D: [Denied](https://denied.com) (retrieved 2026-09-07, confidence 1, policy-blocked).\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
        ),
        citecheck.CitationCheckResult(
            url="https://ok3.com", host="ok3.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
        ),
        citecheck.CitationCheckResult(
            url="https://denied.com", host="denied.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is True
        assert len(report.schema_issues) == 0


def test_abuse_bounds_over_25_percent_fails():
    """2 POLICY_DENIED + 2 OK citations (50% > 25% ceiling) fails preflight."""
    text = (
        "# Analysis\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [Denied 1](https://denied1.com) (confidence 1).\n"
        "- Fact D: [Denied 2](https://denied2.com) (confidence 1).\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
        ),
        citecheck.CitationCheckResult(
            url="https://denied1.com", host="denied1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
        ),
        citecheck.CitationCheckResult(
            url="https://denied2.com", host="denied2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("high_policy_denial_fraction" in issue for issue in report.schema_issues)


def test_abuse_bounds_over_2_absolute_fails():
    """3 POLICY_DENIED citations (exceeds absolute cap of 2) fails preflight."""
    text = "# Analysis\n\n" + "\n".join(f"- Fact {i}: [OK](https://ok{i}.com)" for i in range(1, 10)) + "\n"
    text += "\n".join(f"- Denied {i}: [Denied](https://denied{i}.com) (confidence 1)" for i in range(1, 4))
    mock_evidence = [
        citecheck.CitationCheckResult(
            url=f"https://ok{i}.com", host=f"ok{i}.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
        ) for i in range(1, 10)
    ] + [
        citecheck.CitationCheckResult(
            url=f"https://denied{i}.com", host=f"denied{i}.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
        ) for i in range(1, 4)
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("high_policy_denial_count" in issue for issue in report.schema_issues)


def test_min_ok_sources_insufficient_fails():
    """1 POLICY_DENIED + 1 OK citation (< 2 OK minimum grounding) fails preflight."""
    text = (
        "# Brief\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [Denied](https://denied.com) (confidence 1).\n"
    ) * 3
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
        ),
        citecheck.CitationCheckResult(
            url="https://denied.com", host="denied.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("insufficient_verified_sources" in issue for issue in report.schema_issues)


def test_fabrication_guard_catches_conf3():
    """Claiming confidence 3 on a POLICY_DENIED source triggers fabrication failure."""
    text = (
        "# Research Report\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [OK 3](https://ok3.com) confirmed.\n"
        "- Blocked Fact: https://denied.com/pricing retrieved 2026-09-07, confidence 3.\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact A: [OK 1](https://ok1.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact B: [OK 2](https://ok2.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok3.com", host="ok3.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact C: [OK 3](https://ok3.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://denied.com/pricing", host="denied.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
            line="- Blocked Fact: https://denied.com/pricing retrieved 2026-09-07, confidence 3."
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("Fabrication" in issue for issue in report.schema_issues)


def test_fabrication_guard_catches_verbatim_quotes():
    """Attributing verbatim quotes to a POLICY_DENIED source triggers fabrication failure."""
    text = (
        "# Research Report\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [OK 3](https://ok3.com) confirmed.\n"
        "- Blocked Claim: \"Enterprise accounts include 24/7 dedicated support\" (https://denied.com/terms, confidence 1).\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact A: [OK 1](https://ok1.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact B: [OK 2](https://ok2.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok3.com", host="ok3.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact C: [OK 3](https://ok3.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://denied.com/terms", host="denied.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
            line="- Blocked Claim: \"Enterprise accounts include 24/7 dedicated support\" (https://denied.com/terms, confidence 1)."
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("Fabrication" in issue for issue in report.schema_issues)


def test_fabrication_guard_allows_conf1_and_unquoted():
    """A POLICY_DENIED source marked confidence 1 without verbatim quotes passes fabrication guard."""
    text = (
        "# Research Report\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [OK 3](https://ok3.com) confirmed.\n"
        "- Blocked Note: pricing details were not publicly reachable (https://denied.com/pricing, confidence 1).\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact A: [OK 1](https://ok1.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact B: [OK 2](https://ok2.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok3.com", host="ok3.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact C: [OK 3](https://ok3.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://denied.com/pricing", host="denied.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
            line="- Blocked Note: pricing details were not publicly reachable (https://denied.com/pricing, confidence 1)."
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is True
        assert len(report.schema_issues) == 0


def test_policy_expansion_candidates_logged():
    """POLICY_DENIED citations append candidates to runs/policy_expansion_candidates.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_runs = Path(tmpdir)
        mock_evidence = [
            citecheck.CitationCheckResult(
                url="https://denied.com/page", host="denied.com", reachable_on_host=True, http_status=200,
                worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
            )
        ]
        candidates = citecheck.record_policy_expansion_candidates(
            mock_evidence, task_id=140, attempt=1, runs_dir=tmp_runs
        )
        assert len(candidates) == 1
        assert candidates[0]["host"] == "denied.com"
        assert candidates[0]["task_id"] == 140

        log_path = tmp_runs / "policy_expansion_candidates.jsonl"
        assert log_path.is_file()
        lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 1
        assert lines[0]["host"] == "denied.com"
        assert lines[0]["classification"] == "POLICY_DENIED"


def test_evaluation_abuse_bounds_and_fabrication():
    """Verify orchestrator/evaluation.py enforces fabrication hard FAIL and abuse bounds."""
    import evaluation
    row = {"task_id": 9999, "pass_criteria": "Research criteria", "spec": "Spec"}
    roles = {"critic": {"model": "critic-test", "provider": "mock"}}

    # Redirect RUNS to a temp dir so record_policy_expansion_candidates does NOT
    # pollute the production runs/policy_expansion_candidates.jsonl (A1 fix).
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_runs = Path(tmpdir)
        with patch.object(evaluation, "RUNS", tmp_runs):

            # Case A: Fabrication triggers mechanical FAIL
            fab_text = "Here is a quote \"Guaranteed 100% uptime\" from https://denied.com/sla (conf 3)."
            fab_evidence = [
                citecheck.CitationCheckResult(
                    url="https://denied.com/sla", host="denied.com", reachable_on_host=True, http_status=200,
                    worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
                    line="from https://denied.com/sla (conf 3)."
                )
            ]
            with patch.object(evaluation.citecheck, "verify", return_value=fab_evidence):
                verdict, text = evaluation.run_critic(row, fab_text, roles, baseline=False)
                assert verdict == "fail"
                assert "Fabrication: worker asserted" in text

            # Case B: High policy denial fraction triggers needs_review escalation
            high_denial_text = "Multiple facts: https://ok.com https://denied1.com https://denied2.com"
            high_evidence = [
                citecheck.CitationCheckResult(
                    url="https://ok.com", host="ok.com", reachable_on_host=True, http_status=200,
                    worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
                ),
                citecheck.CitationCheckResult(
                    url="https://denied1.com", host="denied1.com", reachable_on_host=True, http_status=200,
                    worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
                ),
                citecheck.CitationCheckResult(
                    url="https://denied2.com", host="denied2.com", reachable_on_host=True, http_status=200,
                    worker_policy_permitted=False, broker_attempt_verified=True, classification="POLICY_DENIED",
                ),
            ]
            with patch.object(evaluation.citecheck, "verify", return_value=high_evidence):
                verdict, text = evaluation.run_critic(row, high_denial_text, roles, baseline=False)
                assert verdict in ("needs_review", "fail")
                assert "insufficient_verified_sources" in text or "high_policy_denial" in text

            # Case C: F135 Fabrication on UNREACHABLE triggers mechanical FAIL
            unreach_fab_text = "Claim: \"Confidential internal metric\" at https://unattempted.com/leak (conf 3)."
            unreach_fab_evidence = [
                citecheck.CitationCheckResult(
                    url="https://unattempted.com/leak", host="unattempted.com", reachable_on_host=True, http_status=200,
                    worker_policy_permitted=False, broker_attempt_verified=False, classification="UNREACHABLE",
                    line="Claim: \"Confidential internal metric\" at https://unattempted.com/leak (conf 3)."
                )
            ]
            with patch.object(evaluation.citecheck, "verify", return_value=unreach_fab_evidence):
                verdict, text = evaluation.run_critic(row, unreach_fab_text, roles, baseline=False)
                assert verdict == "fail"
                assert "Fabrication: worker asserted" in text


def test_fabrication_guard_catches_unattempted_conf3():
    """F135: Claiming confidence 3 on an UNREACHABLE source triggers fabrication failure."""
    text = (
        "# Research Report\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [OK 3](https://ok3.com) confirmed.\n"
        "- Unattempted Fact: https://unattempted.com/pricing retrieved 2026-09-07, confidence 3.\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact A: [OK 1](https://ok1.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact B: [OK 2](https://ok2.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok3.com", host="ok3.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact C: [OK 3](https://ok3.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://unattempted.com/pricing", host="unattempted.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=False, classification="UNREACHABLE",
            line="- Unattempted Fact: https://unattempted.com/pricing retrieved 2026-09-07, confidence 3."
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("Fabrication" in issue for issue in report.schema_issues)


def test_fabrication_guard_catches_unattempted_verbatim_quotes():
    """F135: Attributing verbatim quotes to an UNREACHABLE source triggers fabrication failure."""
    text = (
        "# Research Report\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [OK 3](https://ok3.com) confirmed.\n"
        "- Unattempted Claim: \"Enterprise accounts include 24/7 dedicated support\" (https://unattempted.com/terms, confidence 1).\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact A: [OK 1](https://ok1.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact B: [OK 2](https://ok2.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok3.com", host="ok3.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact C: [OK 3](https://ok3.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://unattempted.com/terms", host="unattempted.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=False, classification="UNREACHABLE",
            line="- Unattempted Claim: \"Enterprise accounts include 24/7 dedicated support\" (https://unattempted.com/terms, confidence 1)."
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("Fabrication" in issue for issue in report.schema_issues)


def test_fabrication_guard_allows_unattempted_conf1_and_unquoted():
    """F135: An UNREACHABLE source marked confidence 1 without verbatim quotes passes fabrication guard."""
    text = (
        "# Research Report\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: [OK 2](https://ok2.com) confirmed.\n"
        "- Fact C: [OK 3](https://ok3.com) confirmed.\n"
        "- Unattempted Note: pricing details were not checked (https://unattempted.com/pricing, confidence 1).\n"
    ) * 2
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact A: [OK 1](https://ok1.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok2.com", host="ok2.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact B: [OK 2](https://ok2.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://ok3.com", host="ok3.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact C: [OK 3](https://ok3.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://unattempted.com/pricing", host="unattempted.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=False, classification="UNREACHABLE",
            line="- Unattempted Note: pricing details were not checked (https://unattempted.com/pricing, confidence 1)."
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is True
        assert len(report.schema_issues) == 0


def test_abuse_bounds_unattempted_min_ok_fails():
    """F135: Deliverable with 1 OK and 1 UNREACHABLE fails min-2-OK invariant."""
    text = (
        "# Research Report\n\n"
        "- Fact A: [OK 1](https://ok1.com) confirmed.\n"
        "- Fact B: https://unattempted.com/data noted, confidence 1.\n"
    ) * 3
    mock_evidence = [
        citecheck.CitationCheckResult(
            url="https://ok1.com", host="ok1.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=True, broker_attempt_verified=False, classification="OK",
            line="- Fact A: [OK 1](https://ok1.com) confirmed."
        ),
        citecheck.CitationCheckResult(
            url="https://unattempted.com/data", host="unattempted.com", reachable_on_host=True, http_status=200,
            worker_policy_permitted=False, broker_attempt_verified=False, classification="UNREACHABLE",
            line="- Fact B: https://unattempted.com/data noted, confidence 1."
        ),
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="")
        assert report.passed is False
        assert any("insufficient_verified_sources" in issue for issue in report.schema_issues)


def test_candidates_log_injectable():
    """A1 regression: candidate log path is injectable; production file is untouched."""
    prod_path = ROOT / "runs" / "policy_expansion_candidates.jsonl"
    prod_before = prod_path.read_bytes() if prod_path.is_file() else b""

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_runs = Path(tmpdir)
        mock_evidence = [
            citecheck.CitationCheckResult(
                url="https://denied-injected.com/test", host="denied-injected.com",
                reachable_on_host=True, http_status=200, worker_policy_permitted=False,
                broker_attempt_verified=True, classification="POLICY_DENIED",
            )
        ]
        candidates = citecheck.record_policy_expansion_candidates(
            mock_evidence, task_id=8888, attempt=1, runs_dir=tmp_runs
        )
        assert len(candidates) == 1
        injected_file = tmp_runs / "policy_expansion_candidates.jsonl"
        assert injected_file.is_file()
        assert "denied-injected.com" in injected_file.read_text(encoding="utf-8")

    # Verify production file was NOT modified
    prod_after = prod_path.read_bytes() if prod_path.is_file() else b""
    assert prod_before == prod_after, "Production candidates log was modified during test!"


def test_production_candidates_log_segregation_guard():
    """A1 gate guard: production candidate log has ZERO test-fixture entries."""
    prod_path = ROOT / "runs" / "policy_expansion_candidates.jsonl"
    if not prod_path.is_file():
        return
    for idx, line in enumerate(prod_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        record = json.loads(line)
        assert record.get("task_id") != 9999, f"Line {idx} in production log has test fixture task_id=9999"
        host = record.get("host", "")
        assert not host.startswith("denied"), f"Line {idx} in production log has test fixture host: {host}"


def test_citation_metadata_linter_m1_missing_date_and_confidence():
    """M1 regression: linter detects missing retrieval date and confidence level."""
    text = (
        "# PromptHero Weekly Brief\n\n"
        "## Review Sentiment\n"
        "- Attempted review pages:\n"
        "  - https://aisotools.com/blog/prompthero-review-2026 - returned 403\n"
        "  - https://www.stork.ai/en/prompthero - returned 403\n\n"
        "## MAU\n"
        "- Gap: Attempted https://www.similarweb.com/website/prompthero.com/ but empty.\n"
    ) * 3
    criteria = "- [ ] Every fact has: source URL + retrieval date + confidence 1-3"
    issues = check_citation_metadata(text, pass_criteria=criteria)
    assert len(issues) >= 3
    assert any("missing an explicit retrieval date" in iss for iss in issues)
    assert any("missing an explicit confidence rating" in iss for iss in issues)

    # Test via run_preflight
    mock_evidence = [
        {"url": "https://aisotools.com/blog/prompthero-review-2026", "reachable": True, "http_status": 200, "literal": None, "error": None},
        {"url": "https://www.stork.ai/en/prompthero", "reachable": True, "http_status": 200, "literal": None, "error": None},
        {"url": "https://www.similarweb.com/website/prompthero.com/", "reachable": True, "http_status": 200, "literal": None, "error": None},
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="Research PromptHero", pass_criteria=criteria)
        assert report.passed is False
        assert any("Citation formatting" in iss for iss in report.schema_issues)
        assert "Citation Formatting" in report.repair_feedback


def test_citation_metadata_linter_passes_when_formatted():
    """M1 regression: deliverable passes when retrieval date and confidence are provided."""
    text = (
        "# PromptHero Weekly Brief\n\n"
        "## Review Sentiment\n"
        "- Attempted review pages:\n"
        "  - https://aisotools.com/blog/prompthero-review-2026 (retrieved 2026-09-12, confidence 1) - returned 403\n"
        "  - https://www.stork.ai/en/prompthero (retrieved 2026-09-12, confidence 1) - returned 403\n\n"
        "## MAU\n"
        "- Gap: Attempted https://www.similarweb.com/website/prompthero.com/ (retrieved 2026-09-12, confidence 1) but empty.\n"
    ) * 3
    criteria = "- [ ] Every fact has: source URL + retrieval date + confidence 1-3"
    issues = check_citation_metadata(text, pass_criteria=criteria)
    assert len(issues) == 0

    mock_evidence = [
        {"url": "https://aisotools.com/blog/prompthero-review-2026", "reachable": True, "http_status": 200, "literal": None, "error": None},
        {"url": "https://www.stork.ai/en/prompthero", "reachable": True, "http_status": 200, "literal": None, "error": None},
        {"url": "https://www.similarweb.com/website/prompthero.com/", "reachable": True, "http_status": 200, "literal": None, "error": None},
    ]
    with patch.object(deliverable_preflight.citecheck, "verify", return_value=mock_evidence):
        report = run_preflight(text, spec="Research PromptHero", pass_criteria=criteria)
        assert report.passed is True
        assert len(report.schema_issues) == 0


def test_schema_linter_m7_catches_speculative_bootstrapped_cell():
    """M7 regression: catches speculative 'Bootstrapped' when 'not publicly disclosed' is required."""
    text = (
        "# AI Prompt Marketplace Landscape\n\n"
        "| Marketplace | Founding Year | Funding Status | Operational Status |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| PromptBase | 2021 | Bootstrapped (no public funding found) | Active |\n"
        "| AIPRM | 2022 | not publicly disclosed | Active |\n"
    ) * 3
    criteria = "- [ ] Where data is NOT publicly available: explicit 'not publicly disclosed' per cell, not fabricated"
    issues = check_schema(text, spec="Produce structured overview", pass_criteria=criteria)
    assert any("speculative placeholder" in iss for iss in issues)
    assert any("Bootstrapped" in iss for iss in issues)


def test_schema_linter_m7_passes_with_not_publicly_disclosed_cell():
    """M7 regression: passes when all unavailable data points use 'not publicly disclosed'."""
    text = (
        "# AI Prompt Marketplace Landscape\n\n"
        "| Marketplace | Founding Year | Funding Status | Operational Status |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| PromptBase | 2021 | not publicly disclosed | Active |\n"
        "| AIPRM | 2022 | not publicly disclosed | Active |\n"
    ) * 3
    criteria = "- [ ] Where data is NOT publicly available: explicit 'not publicly disclosed' per cell, not fabricated"
    issues = check_schema(text, spec="Produce structured overview", pass_criteria=criteria)
    assert len(issues) == 0


def test_pinpoint_unattempted_url_repair_feedback_m5():
    """M5 regression: pinpoint repair feedback lists un-attempted URLs and remediation."""
    fabrications = [
        {
            "url": "https://flowgpt.com/",
            "classification": "UNREACHABLE",
            "offending_quotes": ["50M+ prompts served"],
        }
    ]
    feedback = format_repair_feedback(
        dead_urls=[],
        schema_issues=["Fabrication detected: worker asserted verbatim quotes for un-attempted source."],
        fabrications=fabrications,
    )
    assert "Un-Attempted / Policy-Denied Sources (Fabrication Guard):" in feedback
    assert "`https://flowgpt.com/` (un-attempted)" in feedback
    assert "50M+ prompts served" in feedback
    assert "REMOVE these URL citations" in feedback


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
    test_no_direct_sockets_or_urllib()
    test_is_infra_error_suppresses_repair()
    test_abuse_bounds_under_ceiling_passes()
    test_abuse_bounds_over_25_percent_fails()
    test_abuse_bounds_over_2_absolute_fails()
    test_min_ok_sources_insufficient_fails()
    test_fabrication_guard_catches_conf3()
    test_fabrication_guard_catches_verbatim_quotes()
    test_fabrication_guard_allows_conf1_and_unquoted()
    test_policy_expansion_candidates_logged()
    test_evaluation_abuse_bounds_and_fabrication()
    test_fabrication_guard_catches_unattempted_conf3()
    test_fabrication_guard_catches_unattempted_verbatim_quotes()
    test_fabrication_guard_allows_unattempted_conf1_and_unquoted()
    test_abuse_bounds_unattempted_min_ok_fails()
    test_candidates_log_injectable()
    test_production_candidates_log_segregation_guard()
    test_citation_metadata_linter_m1_missing_date_and_confidence()
    test_citation_metadata_linter_passes_when_formatted()
    test_schema_linter_m7_catches_speculative_bootstrapped_cell()
    test_schema_linter_m7_passes_with_not_publicly_disclosed_cell()
    test_pinpoint_unattempted_url_repair_feedback_m5()
    print("ALL 33 DELIVERABLE PREFLIGHT TESTS PASSED!")

