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
import os
from pathlib import Path
import re
from typing import Any

try:
    import citecheck
except ImportError:
    from orchestrator import citecheck


MAX_REPAIR_ATTEMPTS = 2

# Regex patterns for markdown tables and citations
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$", re.MULTILINE)
_SPECULATIVE_CELL_RE = re.compile(
    r"^\s*(?:bootstrapped(?:\s*\(.*?\))?|self-funded|unknown|undisclosed|n/a|\?|tbd)\s*$",
    re.I
)
_DATE_RE = re.compile(
    r"\b(?:20\d\d-\d\d-\d\d|retrieved\s+20\d\d|attempted\s+20\d\d|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+20\d\d)\b",
    re.I
)
_CONFIDENCE_RE = re.compile(
    r"\bconfidence(?:\s*(?::|\*\*|\*|-|\b)\s*(?:high|medium|low|[1-3])|\s+level\s*(?::|\*\*|\*|-|\b)\s*(?:high|medium|low|[1-3]))\b",
    re.I
)
_URL_RE = re.compile(r"https?://[^\s)\]>\"'`]+")


@dataclass
class PreflightReport:
    passed: bool
    dead_urls: list[dict[str, Any]] = field(default_factory=list)
    schema_issues: list[str] = field(default_factory=list)
    repair_feedback: str | None = None

    @property
    def has_errors(self) -> bool:
        return not self.passed


def check_schema(text: str, spec: str = "", pass_criteria: str = "") -> list[str]:
    """Examine text against mission specification and pass_criteria for mechanical omissions (M3, M7).
    
    Targeting M3 & M7 failure modes:
    - M3: Omitting required sections / platforms specified in the task prompt or criteria.
    - M7: Omitting mandatory disclaimers ('not publicly disclosed') in data tables, or
          using speculative placeholders ('Bootstrapped', 'Unknown', 'N/A', empty cells).
    """
    issues: list[str] = []
    combined = f"{spec}\n{pass_criteria}"
    combined_lower = combined.lower()
    text_lower = text.lower()

    # 1. Check for required markdown tables if spec/criteria mandates a table / matrix / comparison
    requires_table = any(kw in combined_lower for kw in [
        "table", "matrix", "comparison grid", "tabular", "structured comparison"
    ])
    has_table = bool(_TABLE_SEPARATOR_RE.search(text))
    if requires_table and not has_table:
        issues.append("Specification requires a comparison table/matrix, but no valid markdown table was found.")

    # 2. Check for mandatory 'not publicly disclosed' disclaimer when spec/criteria requires it
    requires_npd = any(kw in combined_lower for kw in [
        "not publicly disclosed", "disclose", "not available"
    ])
    if requires_npd:
        has_npd = "not publicly disclosed" in text_lower
        if not has_npd:
            issues.append(
                "Missing mandatory 'not publicly disclosed' cell entries for unavailable data points in table."
            )

        # Scan table rows for speculative fillers or empty cells (M7)
        table_lines = [l for l in text.splitlines() if _TABLE_ROW_RE.match(l) and not _TABLE_SEPARATOR_RE.match(l)]
        if len(table_lines) > 1:
            for r_idx, row in enumerate(table_lines[1:]):
                cells = [c.strip() for c in row.split('|')[1:-1]]
                for c in cells:
                    m = _SPECULATIVE_CELL_RE.match(c)
                    if m and "not publicly disclosed" not in c.lower() and "http" not in c.lower():
                        issues.append(
                            f"Table cell '{c}' uses speculative placeholder '{m.group(0)}'. Where data is not publicly available, pass criteria mandates explicit 'not publicly disclosed' per cell, not fabricated or guessed."
                        )
                        break
                    elif c in ("", "-"):
                        issues.append(
                            "Table contains empty or dash cell. For unavailable data points, explicitly enter 'not publicly disclosed'."
                        )
                        break

    # 3. Check for specific platform / competitor mentions if explicitly enumerated in spec
    # Targets M3 failure where specific required platforms were dropped
    if "platform" in combined_lower or "review" in combined_lower:
        matches = re.findall(r"\b(?:compare|review|analyze|platforms?)\s*:\s*([^.]+)", combined, re.IGNORECASE)
        if matches:
            candidates = [c.strip() for c in re.split(r"[,;/]|and\b", matches[0]) if len(c.strip()) > 2]
            missing_candidates = [c for c in candidates if c.lower() not in text_lower]
            if missing_candidates:
                issues.append(
                    f"Required subjects/platforms from specification not addressed: {', '.join(missing_candidates[:3])}"
                )

    return issues


def check_citation_metadata(text: str, spec: str = "", pass_criteria: str = "") -> list[str]:
    """Examine deliverable for citation metadata completeness (M1).
    
    Verifies that cited sources and source attempts include:
    - Explicit retrieval / attempt date (YYYY-MM-DD or standard date format)
    - Explicit confidence level (confidence 1-3, high/medium/low)
    when mandated by pass_criteria or mission specification.
    """
    combined = f"{spec}\n{pass_criteria}".lower()
    requires_dates = bool(
        re.search(r"\b(?:retrieval\s+date|retrieved|date\s+per\s+fact|every\s+fact\s+has.*?date)\b", combined)
    )
    requires_confidence = bool(
        re.search(r"\b(?:confidence(?:\s+level|\s+1-3|\s+per\s+cell|\s+rating)?|every\s+fact\s+has.*?confidence)\b", combined)
    )

    if not requires_dates and not requires_confidence:
        return []

    lines = text.splitlines()
    issues: list[str] = []
    seen_urls: set[str] = set()

    for i, line in enumerate(lines):
        found_urls = _URL_RE.findall(line)
        if not found_urls:
            continue

        # Look at line context and immediate siblings in bullet/table block
        context = line
        if i > 0 and lines[i - 1].strip().startswith(("-", "*", "|")):
            context = lines[i - 1] + " " + context
        if i < len(lines) - 1 and (
            lines[i + 1].strip().startswith(("-", "*", "|", "Source:", "Confidence:"))
            or not lines[i + 1].strip().startswith("#")
        ):
            context = context + " " + lines[i + 1]

        has_date = bool(_DATE_RE.search(context))
        has_conf = bool(_CONFIDENCE_RE.search(context))

        for u in found_urls:
            u_clean = u.rstrip(".,;:)]>")
            if u_clean in seen_urls:
                continue
            seen_urls.add(u_clean)

            if requires_dates and not has_date:
                issues.append(
                    f"Citation formatting: source '{u_clean}' is missing an explicit retrieval date (e.g. 'retrieved YYYY-MM-DD')."
                )
            if requires_confidence and not has_conf:
                issues.append(
                    f"Citation formatting: source '{u_clean}' is missing an explicit confidence rating (e.g. 'confidence: 1-3' or 'confidence: high/medium/low')."
                )

    return issues[:10]


def is_infra_error(text: str) -> bool:
    """Trap 2 guard: Check if output represents an infrastructure failure,
    quota exhaustion, or process abort that should never trigger an auto-repair call."""
    if not text:
        return True
    low = text.lower()
    return any(sig in low for sig in [
        "infra_failed", "worker api failure", "quota_wait",
        "chain_exhausted", "worker launch failure", "worker timeout",
        "database containment violation"
    ])


def run_preflight(
    text: str,
    spec: str = "",
    task_id: int | None = None,
    attempt: int | None = 1,
    runs_dir: Path | None = None,
    pass_criteria: str = "",
) -> PreflightReport:
    """Run mechanical preflight checks on deliverable text (F126, F134).
    
    Reuses citecheck.py for URL liveness, policy denial abuse bounds, fabrication guard,
    and schema checks.
    Does NOT modify text, open separate sockets, or execute LLM calls.
    """
    if not text or len(text.strip()) < 100:
        return PreflightReport(
            passed=False,
            schema_issues=["Deliverable is empty or too short (under 100 characters)."],
            repair_feedback="Deliverable is empty or too short. Provide the complete final deliverable."
        )

    if is_infra_error(text):
        return PreflightReport(
            passed=False,
            schema_issues=["Output indicates infrastructure failure; auto-repair suppressed."],
            repair_feedback=None
        )

    # 1. URL Liveness & Policy Evaluation via authoritative citecheck.verify
    try:
        evidence = citecheck.verify(text, task_id=task_id, attempt=attempt, runs_dir=runs_dir)
    except TypeError:
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

    # 2. F134: Record policy expansion candidates for operator review (append-only)
    # In test tiers, only record if an explicit runs_dir was injected to prevent polluting production log (A1).
    if summary.get("policy_denied", 0) > 0:
        if runs_dir is not None or not os.environ.get("AGI_TEST_TIER"):
            try:
                citecheck.record_policy_expansion_candidates(
                    evidence, task_id=task_id, attempt=attempt, runs_dir=runs_dir
                )
            except Exception:
                pass

    # 3. F134: Strict Mechanical Fabrication Guard
    try:
        fabrications = citecheck.detect_fabrication(text, evidence)
    except Exception:
        fabrications = []

    # 4. F134: Abuse Bounds on POLICY_DENIED citations
    try:
        passed_bounds, bounds_reason = citecheck.check_abuse_bounds(summary)
    except Exception:
        passed_bounds, bounds_reason = True, None

    # 5. Schema & Disclaimer Linter (M3, M7)
    schema_issues = check_schema(text, spec, pass_criteria=pass_criteria)

    # 6. Citation Metadata Linter (M1)
    metadata_issues = check_citation_metadata(text, spec, pass_criteria=pass_criteria)
    schema_issues.extend(metadata_issues)

    if fabrications:
        for fab in fabrications:
            desc = "policy-denied" if fab.get("classification") == citecheck.CLASSIFICATION_POLICY_DENIED else "un-attempted"
            quotes_info = f" Quotes found: {', '.join(fab.get('offending_quotes', [])[:3])}." if fab.get("offending_quotes") else ""
            schema_issues.append(
                f"Fabrication detected: worker asserted high confidence or verbatim quotes for {desc} source ({fab.get('url')}) which was not loaded at the network layer.{quotes_info}"
            )

    if not passed_bounds and bounds_reason:
        schema_issues.append(f"Policy denial bounds exceeded: {bounds_reason}")

    passed = not (cite_failed or schema_issues or len(fabrications) > 0 or not passed_bounds)
    repair_feedback = None

    if not passed:
        repair_feedback = format_repair_feedback(dead_urls, schema_issues, fabrications=fabrications)

    return PreflightReport(
        passed=passed,
        dead_urls=dead_urls,
        schema_issues=schema_issues,
        repair_feedback=repair_feedback
    )


def format_repair_feedback(
    dead_urls: list[dict[str, Any]],
    schema_issues: list[str],
    fabrications: list[dict[str, Any]] | None = None,
) -> str:
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

    if fabrications:
        lines.append("\n**Un-Attempted / Policy-Denied Sources (Fabrication Guard):**")
        lines.append("The following URLs were NEVER loaded via the network proxy during this session:")
        for fab in fabrications:
            url = fab.get("url", "")
            desc = "policy-denied" if fab.get("classification") == citecheck.CLASSIFICATION_POLICY_DENIED else "un-attempted"
            quotes_info = f" Offending quotes: {', '.join(fab.get('offending_quotes', [])[:2])}." if fab.get("offending_quotes") else ""
            lines.append(f"- `{url}` ({desc}).{quotes_info}")
        lines.append("CRITICAL: You must either (1) REMOVE these URL citations completely, or (2) rewrite the text in your own words without ANY quotation marks (\"\", '', “”, >) and set confidence to 1, stating that the page was not directly fetched.")

    if schema_issues:
        lines.append("\n**Specification / Formatting Deficiencies:**")
        for s in schema_issues:
            lines.append(f"- {s}")

    lines.append("\n**Action Required:**")
    has_fabrication = any("Fabrication detected" in s for s in schema_issues) or bool(fabrications)
    has_policy_bounds = any("Policy denial bounds exceeded" in s for s in schema_issues)
    has_citation_metadata = any("Citation formatting" in s for s in schema_issues)
    has_speculative = any("speculative" in s.lower() or "not publicly disclosed" in s.lower() for s in schema_issues)

    if has_fabrication:
        lines.append("- For Fabrication / Un-attempted URLs: You MUST remove all quotation marks (including double quotes \"\", curly quotes “”, single quotes '', and blockquotes >) around any text citing sources that were policy-denied, un-attempted, or search snippets. Express the facts entirely in your own words without quotation marks, or remove the un-attempted URL citations.")
    if has_policy_bounds:
        lines.append("- For Policy Denial bounds: You MUST cite at most 2 policy-denied / aggregator sources. Remove extraneous aggregator links to satisfy the <=25% and <=2 policy denial ceiling.")
    if has_citation_metadata:
        lines.append("- For Citation Formatting: Ensure EVERY cited source, URL, and fetch attempt includes BOTH an explicit retrieval date (e.g. 'retrieved YYYY-MM-DD') and an explicit confidence level (e.g. 'confidence: 2' or 'confidence: high/medium/low').")
    if has_speculative:
        lines.append("- For Missing / Speculative Data: For unavailable data points (especially funding or financial metrics), you MUST explicitly write 'not publicly disclosed' per cell. Do NOT guess 'Bootstrapped', 'Unknown', 'N/A', or leave cells blank.")
    lines.append("- Regenerate the COMPLETE, corrected final deliverable addressing every item above. Ensure all tables are complete and all citations point to active pages.")

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
