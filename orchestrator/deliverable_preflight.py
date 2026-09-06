"""Deliverable preflight & mechanical auto-repair linter (F126, docs/HARDENING.md).

Addresses the real-world yield bottleneck (live cohort 1/6 pass rate) by catching
mechanical failures BEFORE the authoritative evaluator and citecheck gate grade the deliverable.

Core Architectural Rules (Incorporating Independent Peer Review):
1. REUSES citecheck.py DIRECTLY: Never reimplements URL fetching or opens raw sockets.
   Preserves the RC-1 fix (HTTP 403 is BLOCKED, not DEAD; only 404/410/DNS failures are DEAD),
   SSRF protection, and the 8-second timeout.
2. Genuinely Additive Schema & Disclaimer Linter: Validates markdown table structure,
   flags empty cells when mandatory disclaimers ('not publicly disclosed') are required,
   and verifies platform coverage against task specifications.
3. F10 Indirect-Injection Floor: Feedback passed to the worker contains ONLY structured
   metadata (status codes, URLs, missing elements), NEVER raw fetched web content.
4. Bounded Execution: Integrated into task_runner with a hard MAX_REPAIR_ATTEMPTS=2 cap,
   budget checks, and token spend accumulation.
"""
from dataclasses import dataclass, field
import re
from typing import Any

try:
    import citecheck
except ImportError:
    from orchestrator import citecheck


MAX_REPAIR_ATTEMPTS = 2

# Regex patterns for markdown tables
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$", re.MULTILINE)


@dataclass
class PreflightReport:
    passed: bool
    dead_urls: list[dict[str, Any]] = field(default_factory=list)
    schema_issues: list[str] = field(default_factory=list)
    repair_feedback: str | None = None

    @property
    def has_errors(self) -> bool:
        return not self.passed


def check_schema(text: str, spec: str = "") -> list[str]:
    """Examine text against mission specification for mechanical schema omissions.
    
    Targeting M3 & M7 failure modes:
    - M3: Omitting required sections / platforms specified in the task prompt.
    - M7: Omitting mandatory disclaimers ('not publicly disclosed') in data tables.
    """
    issues: list[str] = []
    spec_lower = spec.lower() if spec else ""
    text_lower = text.lower()

    # 1. Check for required markdown tables if spec mandates a table / matrix / comparison
    requires_table = any(kw in spec_lower for kw in [
        "table", "matrix", "comparison grid", "tabular", "structured comparison"
    ])
    has_table = bool(_TABLE_SEPARATOR_RE.search(text))
    if requires_table and not has_table:
        issues.append("Specification requires a comparison table/matrix, but no valid markdown table was found.")

    # 2. Check for mandatory 'not publicly disclosed' disclaimer when spec requires it
    requires_npd = "not publicly disclosed" in spec_lower or "disclose" in spec_lower
    if requires_npd and "not publicly disclosed" not in text_lower:
        # Check if table rows have empty cells '| |' or placeholder dashes '| - |'
        # which should explicitly say 'not publicly disclosed'
        empty_cell_match = re.search(r"\|\s*(?:|-|n/a|\?)\s*\|", text, re.IGNORECASE)
        if empty_cell_match or "not publicly disclosed" not in text_lower:
            issues.append(
                "Missing mandatory 'not publicly disclosed' cell entries for unavailable data points in table."
            )

    # 3. Check for specific platform / competitor mentions if explicitly enumerated in spec
    # Targets M3 failure where specific required platforms were dropped
    if "platform" in spec_lower or "review" in spec_lower:
        # If spec explicitly asks to review or compare multiple named subjects
        matches = re.findall(r"\b(?:compare|review|analyze|platforms?)\s*:\s*([^.]+)", spec, re.IGNORECASE)
        if matches:
            candidates = [c.strip() for c in re.split(r"[,;/]|and\b", matches[0]) if len(c.strip()) > 2]
            missing_candidates = [c for c in candidates if c.lower() not in text_lower]
            if missing_candidates:
                issues.append(
                    f"Required subjects/platforms from specification not addressed: {', '.join(missing_candidates[:3])}"
                )

    return issues


def run_preflight(text: str, spec: str = "") -> PreflightReport:
    """Run mechanical preflight checks on deliverable text.
    
    Reuses citecheck.py for URL liveness and runs schema checks.
    Does NOT modify text, open separate sockets, or execute LLM calls.
    """
    if not text or len(text.strip()) < 100:
        return PreflightReport(
            passed=False,
            schema_issues=["Deliverable is empty or too short (under 100 characters)."],
            repair_feedback="Deliverable is empty or too short. Provide the complete final deliverable."
        )

    # 1. URL Liveness via authoritative citecheck.verify (reusing existing RC-1 logic)
    evidence = citecheck.verify(text)
    summary = citecheck.summarize(evidence)
    dead_urls: list[dict[str, Any]] = []

    for e in evidence:
        if citecheck.is_dead(e):
            dead_urls.append({
                "url": e.get("url", ""),
                "http_status": e.get("http_status"),
                "error": e.get("error") or ("HTTP " + str(e.get("http_status")) if e.get("http_status") else "unreachable")
            })

    # Trigger URL repair if hard fail threshold met OR any URLs are provably dead (404/410/DNS failure)
    cite_failed = citecheck.is_hard_fail(summary) or len(dead_urls) > 0

    # 2. Schema & Disclaimer Linter
    schema_issues = check_schema(text, spec)

    passed = not (cite_failed or schema_issues)
    repair_feedback = None

    if not passed:
        repair_feedback = format_repair_feedback(dead_urls, schema_issues)

    return PreflightReport(
        passed=passed,
        dead_urls=dead_urls,
        schema_issues=schema_issues,
        repair_feedback=repair_feedback
    )


def format_repair_feedback(dead_urls: list[dict[str, Any]], schema_issues: list[str]) -> str:
    """Build structured, injection-safe feedback for worker auto-repair.
    
    Adheres strictly to F10 rule: Only structured metadata and instructions,
    NEVER raw fetched web content or redirect targets.
    """
    lines = [
        "### DELIVERABLE PREFLIGHT REPAIR REQUIRED",
        "Your draft deliverable failed mechanical pre-submission verification. Correct the following issues:"
    ]

    if dead_urls:
        lines.append("\n**Dead Citation URLs (HTTP 404/410 or Unreachable):**")
        lines.append("The following cited URLs could not be reached. Replace them with verified active URLs or remove the dead link and state the fact clearly:")
        for d in dead_urls:
            url = d.get("url", "")
            err = d.get("error", "unreachable")
            lines.append(f"- `{url}`: {err}")

    if schema_issues:
        lines.append("\n**Specification / Formatting Deficiencies:**")
        for s in schema_issues:
            lines.append(f"- {s}")

    lines.append("\n**Action Required:**")
    lines.append("Regenerate the COMPLETE, corrected final deliverable addressing every item above. Ensure all tables are complete and all citations point to active pages.")

    return "\n".join(lines)


def build_repair_prompt(base_prompt: str, current_deliverable: str, feedback: str) -> str:
    """Construct the follow-up prompt for the worker to revise its deliverable."""
    return (
        f"{base_prompt}\n\n"
        f"---\n"
        f"### PREVIOUS DRAFT DELIVERABLE:\n"
        f"{current_deliverable}\n\n"
        f"---\n"
        f"{feedback}\n"
    )
