"""Mechanical citation validator (H4, docs/HARDENING.md — fixes F3).

The critic is deliberately tool-free (§2.4) and therefore cannot verify that a
cited URL exists, resolves, or supports the claim attached to it — it can only
check that a URL-shaped string is present. A worker that fabricates plausible
citations passes the automated gate unconditionally (F3, confirmed in practice
2026-07-18: PromptBase facts had to be verified by hand in a browser).

This module fetches every cited URL (SSRF-guarded, bounded concurrency, small
byte cap) and returns an EVIDENCE TABLE — reachability + whether the claim's key
literal (a price/number/name near the URL) appears in the fetched text. Only
that structured table is ever handed to the critic prompt, never raw fetched
page content — F10 (docs/HARDENING.md) already flags "fetched web content feeding
straight into a future prompt" as an indirect prompt-injection path; this keeps
that surface closed while still getting real, non-LLM-judged truth signal.
"""
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

MAX_CITATIONS = 15
FETCH_TIMEOUT_S = 8
MAX_WORKERS = 4
# F23 (docs/HARDENING.md): 20_000 silently made the literal check a coin flip on any
# real page. Measured 2026-07-28 against the pages this harness actually cites:
# promptbase.com/apps is 232,645 chars and the claimed "4.9" sits at char 85,999 --
# the old cap read 9% of the page, missed it, and reported the fact as unsupported.
# That false evidence went to the critic as "claimed value not found on page", which
# reads as fabrication, and helped FAIL tasks 24 and 25. Cost of the raise is bounded
# memory per citation (<=15 citations x 400KB worst case) on a text-only scan.
MAX_BYTES = 400_000
DEAD_FRAC_HARD_FAIL = 0.34   # >1/3 of checked citations unreachable -> mechanical fail
MIN_CHECKED_FOR_HARD_FAIL = 3  # don't hard-fail on a tiny, noisy sample

# F23c (docs/HARDENING.md): `<` must be excluded or an inline HTML tag is swallowed into
# the URL. Workers emit markdown containing `<br>`, so `...fun-3d-icons<br>` extracted as
# `https://...fun-3d-icons<br`, which of course 404s. Measured 2026-07-28: 4 of the 6
# "unreachable" citations that MECHANICALLY hard-failed task 27 were this corruption --
# dead_frac 0.40 (over the 0.34 line) instead of the true ~0.13. A hard fail needs no LLM
# call, so a regex bug alone was rejecting deliverables outright.
_URL_RE = re.compile(r'https?://[^\s\)\]\}<>"\']+')
_NUM_RE = re.compile(r'\$?\d[\d,]*\.?\d*%?')
_PROPER_RE = re.compile(r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b')

_PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "0.0.0.0/8", "::1/128", "fc00::/7", "fe80::/10")]


def _key_literal(claim_text: str) -> str | None:
    """Best-effort 'the one thing this citation is supposed to prove' — a price
    or number if present (facts in this harness are instructed to carry one),
    else a proper-noun phrase. `claim_text` must already have any URL stripped
    out (see extract_citations) — otherwise digits/capitals inside the URL
    itself (e.g. a domain like "abc123xyz.com" or a bare IP) get picked up as
    the "literal", checking the fetched page for a fragment of its own address
    instead of the actual claim. Heuristic, not NLP; false negatives (returns
    None) are safe — those citations just skip the literal check and are judged
    on reachability alone."""
    m = _NUM_RE.search(claim_text)
    if m and len(m.group(0)) >= 2:
        return m.group(0)
    m = _PROPER_RE.search(claim_text)
    return m.group(0) if m else None


def extract_citations(text: str) -> list[dict]:
    """One entry per URL found on a line. Workers are instructed (batch_runner's
    prompt) to put the source URL on the same line as the fact it supports, so
    the line itself is a good-enough claim context without a full NLP parse."""
    out = []
    for line in text.splitlines():
        urls = _URL_RE.findall(line)
        if not urls:
            continue
        claim_text = _URL_RE.sub(" ", line)  # strip URLs before literal-hunting
        for url in urls:
            out.append({"url": url.rstrip('.,;:)'), "line": line.strip()[:200],
                       "literal": _key_literal(claim_text)})
    return out[:MAX_CITATIONS]


_NORM_RE = re.compile(r"[\s,$ ]+")


def _literal_present(literal: str, body: str) -> bool:
    """Is the claimed value actually on the page? Exact match first, then a
    format-tolerant retry.

    F23 (docs/HARDENING.md): the old check was a bare `literal.lower() in body.lower()`,
    which fails on presentation differences that carry no meaning. Measured live
    2026-07-28: the worker claimed "$14" and the page contains the price, but not as
    the contiguous string "$14" -- markup routinely separates a currency symbol from
    its number (`<span>$</span>14`), and thousands separators differ ("42,000" vs
    "42000"). Every such mismatch was reported to the critic as the claimed value being
    absent from its own source, which reads as fabrication rather than as formatting.
    Normalising away whitespace, commas, currency symbols and NBSP on BOTH sides keeps
    the check meaningful while removing that whole class of false accusation.

    Deliberately still a substring test, so it stays advisory evidence rather than
    proof: a bare number can coincidentally appear elsewhere on a page. That is
    acceptable because is_hard_fail() keys on unreachable citations only -- the literal
    signal informs the critic's judgment, it never fails a deliverable by itself."""
    low, lit = body.lower(), literal.lower().strip()
    if lit in low:
        return True
    norm_lit = _NORM_RE.sub("", lit)
    return bool(norm_lit) and norm_lit in _NORM_RE.sub("", low)


def _resolve_safety(hostname: str) -> str | None:
    """Returns None if the host is safe to fetch, else a short reason string.
    Kept distinct from a plain bool so callers can tell a genuinely dead/
    unresolvable domain (DNS failure -- a normal dead-citation case) apart from
    an actual SSRF-guard block (resolves to a private/loopback/link-local
    address) -- conflating the two mislabels every ordinary dead link as a
    blocked attack in the evidence table, which is both confusing to the
    operator and would drown out a real SSRF attempt in the noise."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return "dns resolution failed"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if any(ip in net for net in _PRIVATE_NETS):
            return "blocked: resolves to a private/loopback/link-local address"
    return None


def _fetch_one(cite: dict) -> dict:
    result = {**cite, "reachable": False, "http_status": None, "literal_found": None}
    parsed = urlparse(cite["url"])
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        result["error"] = "unsupported scheme"
        return result
    unsafe_reason = _resolve_safety(parsed.hostname)
    if unsafe_reason:
        result["error"] = unsafe_reason
        return result
    req = urllib.request.Request(
        cite["url"], headers={"User-Agent": "Mozilla/5.0 (compatible; AGI-harness-citecheck/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            result["http_status"] = resp.status
            result["reachable"] = 200 <= resp.status < 400
            if cite["literal"] and result["reachable"]:
                body = resp.read(MAX_BYTES).decode("utf-8", errors="replace")
                result["literal_found"] = _literal_present(cite["literal"], body)
    except urllib.error.HTTPError as e:
        result["http_status"] = e.code
        result["reachable"] = False
    except Exception as e:
        result["error"] = str(e)[:100]
    return result


def verify(text: str) -> list[dict]:
    """Fetch+verify every citation in `text` (bounded). Returns the evidence
    table — never raises; a total failure surfaces as reachable=False rows."""
    cites = extract_citations(text)
    if not cites:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_fetch_one, c): c for c in cites}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                cite = futs[fut]
                results.append({**cite, "reachable": False, "http_status": None,
                               "literal_found": None, "error": str(e)[:100]})
    return results


def summarize(evidence: list[dict]) -> dict:
    checked = len(evidence)
    dead = sum(1 for e in evidence if not e["reachable"])
    lit_checked = [e for e in evidence if e["literal"] and e["reachable"]]
    lit_missing = sum(1 for e in lit_checked if e["literal_found"] is False)
    return {"checked": checked, "dead": dead,
            "dead_frac": round(dead / checked, 2) if checked else 0.0,
            "literal_checked": len(lit_checked), "literal_missing": lit_missing}


def is_hard_fail(summary: dict) -> bool:
    return (summary["checked"] >= MIN_CHECKED_FOR_HARD_FAIL
            and summary["dead_frac"] > DEAD_FRAC_HARD_FAIL)


def evidence_block(evidence: list[dict]) -> str:
    """Compact text for the critic prompt — structured facts only, never raw
    fetched page content (see module docstring / F10)."""
    if not evidence:
        return "(no citations found to verify)"
    lines = []
    for e in evidence[:MAX_CITATIONS]:
        status = "OK" if e["reachable"] else f"UNREACHABLE ({e.get('error') or e.get('http_status')})"
        lit = f", claimed value '{e['literal']}' found on page: {e['literal_found']}" \
            if e["literal"] and e["reachable"] else ""
        lines.append(f"- {e['url']}: {status}{lit}")
    return "\n".join(lines)
