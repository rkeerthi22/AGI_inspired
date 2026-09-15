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
from research_notebook import verified_metadata

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
_HOST_RE = re.compile(r"https?://([^/\s:?#]+)", re.I)
_STATUS_KW = r"(?:blocked|unavailable|rating-obtained|unobtainable|failed|denied)"


@dataclass
class PreflightReport:
    passed: bool
    dead_urls: list[dict[str, Any]] = field(default_factory=list)
    schema_issues: list[str] = field(default_factory=list)
    repair_feedback: str | None = None
    verified_sources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return not self.passed


def count_distinct_sources(text: str) -> int:
    """Count distinct sources cited or declared in deliverable text.
    
    Counts distinct URLs/hosts, and also credits declared blocked or unavailable
    sources (e.g. in bounded-failure sections or status declarations).
    A declared blocked source counts as an attempt; a silently-omitted source does not.
    """
    sources: set[str] = set()
    source_stems: set[str] = set()

    # 1. Distinct URLs cited in text
    urls = _URL_RE.findall(text)
    for u in urls:
        m = _HOST_RE.match(u)
        if m:
            host = m.group(1).lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                sources.add(host)
                stem = host.split(".")[0]
                if len(stem) > 2:
                    source_stems.add(stem)

    # 2. Markdown bullet / table declarations: - G2: blocked, - **Trustpilot**: blocked, | G2 | blocked |
    bullet_re = re.compile(
        rf"^\s*[-*|]\s*(?:\*\*)?([A-Za-z0-9\s\.\-_/]+?)(?:\*\*)?\s*[:\-–|]\s*(?:status\s*:\s*)?{_STATUS_KW}",
        re.IGNORECASE | re.MULTILINE
    )
    for m in bullet_re.finditer(text):
        name = m.group(1).strip().strip("*").strip()
        name_clean = re.sub(r"^(?:source\s*:?|attempt\s*\d*\s*:?)", "", name, flags=re.IGNORECASE).strip()
        if name_clean and len(name_clean) < 40 and name_clean.lower() not in ("source", "status", "attempt", "notes", "platform"):
            nl = name_clean.lower()
            if not any(nl == s or nl in s or s in nl for s in source_stems):
                sources.add(name_clean)
                source_stems.add(nl)

    # 3. Parenthetical style: - G2 (blocked), Trustpilot (blocked)
    paren_re = re.compile(
        rf"(?:[-*]\s*)?([A-Za-z0-9\s\.\-_]+?)\s*\({_STATUS_KW}\)",
        re.IGNORECASE
    )
    for m in paren_re.finditer(text):
        name = m.group(1).strip()
        if name and len(name) < 40 and name.lower() not in ("source", "status", "attempt", "notes", "platform"):
            nl = name.lower()
            if not any(nl == s or nl in s or s in nl for s in source_stems):
                sources.add(name)
                source_stems.add(nl)

    # 4. Known common platforms if declared blocked/unavailable: G2, Trustpilot, Chrome Web Store
    named_re = re.compile(
        rf"\b(G2|Trustpilot|Chrome\s+Web\s+Store)\b[^\n.]{{0,30}}\b{_STATUS_KW}\b",
        re.IGNORECASE
    )
    for m in named_re.finditer(text):
        name = m.group(1).strip()
        nl = name.lower()
        if not any(nl == s or nl in s or s in nl for s in source_stems):
            sources.add(name)
            source_stems.add(nl)

    return len(sources)


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
        "not publicly disclosed", "disclose"
    ])
    if requires_npd:
        has_npd = "not publicly disclosed" in text_lower
        if not has_npd:
            issues.append(
                "Missing mandatory 'not publicly disclosed' cell entries for unavailable data points in table."
            )

    # Allowed placeholders: detect phrases mandated by the criteria
    allowed_placeholders: list[str] = []
    if "not publicly disclosed" in combined_lower or requires_npd:
        allowed_placeholders.append("not publicly disclosed")
    if "not available" in combined_lower:
        allowed_placeholders.append("not available")

    # If any specific placeholder is mandated, scan table rows for speculative fillers or empty cells
    if allowed_placeholders:
        table_lines = [l for l in text.splitlines() if _TABLE_ROW_RE.match(l) and not _TABLE_SEPARATOR_RE.match(l)]
        if len(table_lines) > 1:
            for r_idx, row in enumerate(table_lines[1:]):
                cells = [c.strip() for c in row.split('|')[1:-1]]
                for c in cells:
                    c_lower = c.lower()
                    is_whitelisted = any(ph in c_lower for ph in allowed_placeholders) or "http" in c_lower
                    m = _SPECULATIVE_CELL_RE.match(c)
                    if m and not is_whitelisted:
                        primary_ph = allowed_placeholders[0]
                        issues.append(
                            f"Table cell '{c}' uses speculative placeholder '{m.group(0)}'. Where data is not publicly available, pass criteria mandates explicit '{primary_ph}' per cell, not fabricated or guessed."
                        )
                        break
                    elif c in ("", "-") and not is_whitelisted:
                        primary_ph = allowed_placeholders[0]
                        issues.append(
                            f"Table contains empty or dash cell. For unavailable data points, explicitly enter '{primary_ph}'."
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

    # 4. Spec-declared minimum source count check (Claude Directive 2026-09-13)
    min_src_match = re.search(
        r"\b(?:at\s+least|minimum)\s+(\d+)\s+(?:(?:distinct|independent|third-party|review)\s+)*sources?(?:\s+(?:cited|attempted|consulted|total))?\b(?!\s+per\b)",
        combined_lower
    )
    if min_src_match:
        required_sources = int(min_src_match.group(1))
        actual_sources = count_distinct_sources(text)
        if actual_sources < required_sources:
            issues.append(
                f"Insufficient source count: deliverable cites {actual_sources} sources, spec requires at least {required_sources}."
            )

    # 5. Missing bounded-failure / sources-attempted section check (Claude Directive 2026-09-13)
    requires_bf = bool(re.search(
        r"\b(?:bounded-failure(?:\s+section)?|name\s+every\s+attempt|note\s+each\s+as\s+.*?blocked|sources?\s+attempted(?:\s+section)?)\b",
        combined_lower
    ))
    if requires_bf:
        has_bf = bool(re.search(
            r"(?:^#{1,6}\s+|^\*\*)(?:[^\n]*\b)?(?:bounded[\s-]failure|sources?[\s-]attempted|attempted[\s-]sources?|unavailable[\s-]sources?|source[\s-]attempts?)\b",
            text,
            re.IGNORECASE | re.MULTILINE
        ))
        if not has_bf:
            issues.append(
                "Missing bounded-failure section: spec requires a section naming every source attempt with status (rating-obtained/blocked/unavailable); none detected."
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
    try:
        summary = citecheck.summarize(evidence, text=text)
    except TypeError:
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
        passed_bounds, bounds_reason = citecheck.check_abuse_bounds(summary, text=text, evidence=evidence)
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
        if bounds_reason.startswith("insufficient_verified_sources:"):
            schema_issues.append(f"Insufficient verified sources: {bounds_reason}")
        else:
            schema_issues.append(f"Policy denial bounds exceeded: {bounds_reason}")

    passed = not (cite_failed or schema_issues or len(fabrications) > 0 or not passed_bounds)
    repair_feedback = None

    if not passed:
        repair_feedback = format_repair_feedback(dead_urls, schema_issues, fabrications=fabrications)

    return PreflightReport(
        passed=passed,
        dead_urls=dead_urls,
        schema_issues=schema_issues,
        repair_feedback=repair_feedback,
        verified_sources=verified_metadata(evidence),
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
        lines.append("These sources were unreachable or blocked (HTTP errors listed below). Do NOT retry the same URLs — search for DIFFERENT, independent sources that provide the necessary evidence:")
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
    has_insufficient_sources = any("insufficient_verified_sources" in s for s in schema_issues)
    has_citation_metadata = any("Citation formatting" in s for s in schema_issues)
    has_speculative = any("speculative" in s.lower() or "not publicly disclosed" in s.lower() for s in schema_issues)
    has_insufficient_source_count = any("insufficient source count:" in s.lower() for s in schema_issues)
    has_missing_bounded_failure = any("missing bounded-failure section:" in s.lower() for s in schema_issues)

    if has_fabrication:
        lines.append("- For Fabrication / Un-attempted URLs: You MUST remove all quotation marks (including double quotes \"\", curly quotes “”, single quotes '', and blockquotes >) around any text citing sources that were policy-denied, un-attempted, or search snippets. Express the facts entirely in your own words without quotation marks, or remove the un-attempted URL citations.")
    if has_policy_bounds:
        lines.append("- For Policy Denial bounds: You MUST cite at most 2 policy-denied / aggregator sources. Remove extraneous aggregator links to satisfy the <=25% and <=2 policy denial ceiling.")
    if has_insufficient_sources:
        n, m = 0, 2
        for s in schema_issues:
            if "insufficient_verified_sources" in s:
                match = re.search(r"found\s+(\d+)\s+OK(?:\s+citations)?,?\s*minimum\s+(\d+)", s)
                if match:
                    n = int(match.group(1))
                    m = int(match.group(2))
                    break
        needed = max(1, m - n)
        lines.append(f"- For Insufficient Verified Sources: Your deliverable has {n} verified (OK) source(s) but the minimum is {m}. You must conduct ADDITIONAL research NOW — use the web tools to search for and fetch at least {needed} NEW independent source(s) that corroborate the claim, then cite each with its URL, retrieval date, and confidence. Do NOT merely restate or reformat the sources you already have. Do NOT remove sources to lower the bar — find more. If after a genuine additional search no further independent source exists, state that explicitly with confidence 1 and which queries you tried.")
    if has_insufficient_source_count:
        actual, required = 0, 3
        for s in schema_issues:
            if "insufficient source count:" in s.lower():
                m = re.search(r"cites\s+(\d+)\s+sources?,\s+spec\s+requires\s+at\s+least\s+(\d+)", s, re.IGNORECASE)
                if m:
                    actual = int(m.group(1))
                    required = int(m.group(2))
                    break
        needed = max(1, required - actual)
        lines.append(
            f"- For Insufficient Source Count: Your deliverable cites {actual} source(s) but the spec requires at least {required}. You must attempt and DECLARE at least {needed} MORE independent third-party sources — use the web tools to search for them, attempt each, and cite each with its URL, retrieval date, and confidence. If a specific source named in the spec (e.g. G2, Trustpilot, Chrome Web Store) was blocked or returned no data, you MUST still declare it by name with status 'blocked' or 'unavailable' — a declared blocked source counts as an attempt; a silently-omitted source does not."
        )
    if has_missing_bounded_failure:
        lines.append(
            "- For Missing Bounded-Failure Section: You MUST include a 'Bounded Failure' (or 'Sources Attempted') section that names EVERY source you attempted and its status: rating-obtained (with the rating), blocked (with the HTTP error), or unavailable. The spec explicitly requires this — its absence is a spec-compliance failure regardless of how many sources you cited."
        )
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
