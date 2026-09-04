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
import json
import re
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

MAX_CITATIONS = 15
FETCH_TIMEOUT_S = 8
MAX_WORKERS = 4
MAX_REDIRECTS = 5
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

# RC-1 (2026-09-04): HTTP statuses that mean the resource is genuinely GONE (the URL
# points at no real page) -- as opposed to 403/429/5xx where the server RESPONDED and the
# page exists but the bot was refused. Only these count toward `dead`/dead_frac; a
# responding-but-refused page is "blocked", not dead. See is_dead().
DEAD_HTTP_STATUSES = frozenset({404, 410})

# F23c (docs/HARDENING.md): `<` must be excluded or an inline HTML tag is swallowed into
# the URL. Workers emit markdown containing `<br>`, so `...fun-3d-icons<br>` extracted as
# `https://...fun-3d-icons<br`, which of course 404s. Measured 2026-07-28: 4 of the 6
# "unreachable" citations that MECHANICALLY hard-failed task 27 were this corruption --
# dead_frac 0.40 (over the 0.34 line) instead of the true ~0.13. A hard fail needs no LLM
# call, so a regex bug alone was rejecting deliverables outright.
#
# F29 (docs/HARDENING.md), 2026-07-29: the SAME bug, third instance -- this time the
# backtick. Task 30's deliverable wrote every citation inside a markdown code span, so
# ALL 8 extracted URLs ended in '`' and 4 were reported unreachable (dead_frac=0.50),
# mechanically failing a synthesis whose citations were fine. Fixing one character at a
# time is what produced three separate incidents, so this is now handled as a class:
# every structural markdown/HTML delimiter is excluded from the URL body, AND trailing
# sentence punctuation is stripped afterwards (a URL is routinely the last thing before
# a comma or full stop). Note what this deliberately does NOT rescue: task 30 also cited
# a literal `https://www.youtube.com/watch?v=...` placeholder, which still fails after
# the strip. That is a real fabricated citation, and it must keep failing.
_URL_RE = re.compile(r'https?://[^\s\)\]\}<>"\'`*|\\^]+')
_URL_TRAIL_RE = re.compile(r'[.,;:!?]+$')


def _clean_url(u: str) -> str:
    """Strip trailing sentence punctuation a URL never legitimately ends in."""
    return _URL_TRAIL_RE.sub("", u)
_NUM_RE = re.compile(r'\$?\d[\d,]*\.?\d*%?')
_PROPER_RE = re.compile(r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b')
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)

_PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "0.0.0.0/8", "::1/128", "fc00::/7", "fe80::/10")]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Return 30x responses to the caller instead of following them blindly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


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
    seen = set()
    for line in text.splitlines():
        urls = _URL_RE.findall(line)
        if not urls:
            continue
        claim_text = _URL_RE.sub(" ", line)  # strip URLs before literal-hunting
        for url in urls:
            # F29: single definition of "trailing junk" (_clean_url) rather than an
            # inline rstrip set that drifts out of sync with _URL_RE's exclusions.
            cleaned = _clean_url(url)
            # F66: several claims may cite one page; fetch that page once and
            # preserve the first claim as its representative evidence context.
            if cleaned in seen:
                continue
            seen.add(cleaned)
            out.append({"url": cleaned, "line": line.strip()[:200],
                        "literal": _key_literal(claim_text)})
            if len(out) >= MAX_CITATIONS:
                return out
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

    F25 (docs/HARDENING.md): numeric literals additionally require a TOKEN boundary.
    F23's normalisation fixed false negatives but bought false positives with them: "19"
    is a substring of "$194", so a claimed "Starter $19/mo" verified happily against a
    page whose only prices are $16/$24/$194/$296. Caught by the 2026-07-28 spot-check on
    tasks 24/25, where several confidence-3 prices "verified" against pages that do not
    contain them. A digit run adjacent to more digits is a different number, never
    evidence for this one.

    Still advisory evidence rather than proof, even so: a standalone number can appear on
    a page for unrelated reasons, and no substring test can tell "the Starter plan costs
    $19" from "19 appears here". That is acceptable because is_hard_fail() keys on
    unreachable citations only -- the literal signal informs the critic's judgment and
    never fails a deliverable by itself. Confirming a number means the claim is
    SUPPORTABLE, not that it is true; only a reader comparing claim to page can do that,
    which is exactly what the operator spot-check is for."""
    low, lit = body.lower(), literal.lower().strip()
    core = _NORM_RE.sub("", lit)
    if re.fullmatch(r"\d[\d.]*%?", core):
        # Collapse thousands separators inside numbers only ("42,000" -> "42000") so
        # rendering differences still match, without gluing neighbouring numbers together
        # the way whole-string normalisation would ("$16 $24" -> "1624").
        return re.search(rf"(?<![\d.]){re.escape(core)}(?![\d])",
                         re.sub(r"(?<=\d),(?=\d)", "", low)) is not None
    if lit in low:
        return True
    return bool(core) and core in _NORM_RE.sub("", low)


def _flatten_jsonld(obj) -> list[str]:
    """Every leaf scalar in a parsed JSON-LD structure, as strings. Keys are dropped
    (they're schema.org field names like 'price'/'ratingValue', not claim content);
    dicts and lists are walked generically so `@graph` arrays and nested `offers`/
    `aggregateRating` blocks are covered without knowing the schema in advance."""
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(_flatten_jsonld(v))
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out.extend(_flatten_jsonld(v))
        return out
    if isinstance(obj, (str, int, float)) and not isinstance(obj, bool):
        return [str(obj)]
    return []


def _jsonld_text(body: str) -> str:
    """F26 (docs/HARDENING.md): many real prices/ratings are never in a page's visible
    text at all -- they arrive via client-side rendering from structured data the server
    DOES send. Measured live 2026-07-28: notion.com/templates/ultimate-brain's rendered
    text has no '129' anywhere, but its `<script type="application/ld+json">` block
    contains `"offers":{"...","price":129}` verbatim. The prior citecheck treated that
    page as not supporting a real, true, worker-verified $129 claim -- indistinguishable
    from an actual fabrication in the evidence table.

    Best-effort by design: many real pages ship JSON-LD that isn't quite valid JSON
    (trailing commas, unescaped quotes); a block that fails to parse is skipped, never
    raised, so one malformed block can't take down the whole citation check. Values only,
    joined with spaces -- safe to concatenate onto body text before the existing
    _literal_present() token-boundary check without risking two numbers gluing together
    (F25)."""
    values = []
    for m in _JSONLD_RE.finditer(body):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        values.extend(_flatten_jsonld(data))
    return " ".join(values)


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
    result = {**cite, "reachable": False, "http_status": None, "literal_found": None,
              "final_url": cite["url"], "redirects_followed": 0}

    def _validated_target(target: str) -> tuple[str | None, str | None]:
        parsed_target = urlparse(target)
        if parsed_target.scheme not in ("http", "https") or not parsed_target.hostname:
            return None, "unsupported scheme"
        unsafe = _resolve_safety(parsed_target.hostname)
        if unsafe:
            return None, unsafe
        return target, None

    current_url, error = _validated_target(cite["url"])
    if error:
        result["error"] = error
        return result

    try:
        for _ in range(MAX_REDIRECTS + 1):
            req = urllib.request.Request(
                current_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AGI-harness-citecheck/1.0)"},
            )
            try:
                resp = _NO_REDIRECT_OPENER.open(req, timeout=FETCH_TIMEOUT_S)
                break
            except urllib.error.HTTPError as e:
                if 300 <= e.code < 400:
                    location = e.headers.get("Location")
                    if not location:
                        result["error"] = "redirect missing location"
                        return result
                    next_url, error = _validated_target(urljoin(current_url, location))
                    if error:
                        result["error"] = f"blocked redirect target: {error}"
                        return result
                    current_url = next_url
                    result["redirects_followed"] += 1
                    continue
                raise
        else:
            result["error"] = f"too many redirects (>{MAX_REDIRECTS})"
            return result

        with resp:
            result["final_url"] = getattr(resp, "geturl", lambda: current_url)()
            final_url, error = _validated_target(result["final_url"])
            if error:
                result["error"] = f"blocked final target: {error}"
                return result
            result["final_url"] = final_url
            result["http_status"] = resp.status
            result["reachable"] = 200 <= resp.status < 400
            if cite["literal"] and result["reachable"]:
                body = resp.read(MAX_BYTES).decode("utf-8", errors="replace")
                # F26: search visible/raw text AND structured JSON-LD data together, so
                # a value that only exists in a page's <script type="application/ld+json">
                # block (client-rendered, never in the HTML text) still counts as support.
                result["literal_found"] = _literal_present(
                    cite["literal"], body + " " + _jsonld_text(body))
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


def is_dead(e: dict) -> bool:
    """RC-1 (2026-09-04): True only when a citation is fabricated or the resource is
    genuinely gone -- NOT when a live page merely refused the bot.

    A server that RESPONDED with 4xx/5xx (403 WAF-block, 429 rate-limit, 5xx) means the
    page EXISTS and the citation is real; the bot was just refused. Counting those as
    'dead' inflated dead_frac and mechanically hard-failed tasks whose citations were to
    live, bot-protected sites: M5/task116 had flowgpt.com return 403 (the mission's OWN
    subject site) and counted it dead -- true dead_frac 0.125 vs the gate's 0.50, a false
    negative that flipped a passing deliverable to fail. Only 404/410 and host-doesn't-
    exist connection errors (DNS failure, timeout, connection refused) are dead.

    Safety refusals (SSRF private-net guard, unsupported scheme, redirect problems) are
    NOT dead: the harness refused the fetch; the URL may still point at a real page.
    """
    if e.get("reachable"):
        return False
    status = e.get("http_status")
    if status is not None:
        return status in DEAD_HTTP_STATUSES
    err = e.get("error") or ""
    if err.startswith("blocked") or err == "unsupported scheme" or "redirect" in err:
        return False
    # No HTTP response and no safety refusal: DNS failure / timeout / connection refused
    # -> host unreachable -> treat as dead (likely fabricated or genuinely gone).
    return True


def summarize(evidence: list[dict]) -> dict:
    checked = len(evidence)
    dead = sum(1 for e in evidence if is_dead(e))
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
        if e["reachable"]:
            status = "OK"
        elif is_dead(e):
            status = f"DEAD ({e.get('http_status') or e.get('error')})"
        else:
            # RC-1: server responded (403/429/5xx) but the bot was refused -- the page
            # likely EXISTS, so this is "blocked", not "dead"/fabricated. Telling the
            # critic a blocked page is "UNREACHABLE" made a true claim look like a lie
            # (M7/task119: model said FlowGPT "loads", gate said UNREACHABLE 403).
            status = f"BLOCKED ({e.get('http_status') or e.get('error')})"
        lit = f", claimed value '{e['literal']}' found on page: {e['literal_found']}" \
            if e["literal"] and e["reachable"] else ""
        lines.append(f"- {e['url']}: {status}{lit}")
    return "\n".join(lines)
