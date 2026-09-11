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
import os
import re
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    from runtime_context import ROOT
except ImportError:
    ROOT = Path(__file__).resolve().parent.parent

CLASSIFICATION_OK = "OK"
CLASSIFICATION_DEAD = "DEAD"
CLASSIFICATION_POLICY_DENIED = "POLICY_DENIED"
CLASSIFICATION_UNREACHABLE = "UNREACHABLE"
VALID_CLASSIFICATIONS = frozenset({
    CLASSIFICATION_OK,
    CLASSIFICATION_DEAD,
    CLASSIFICATION_POLICY_DENIED,
    CLASSIFICATION_UNREACHABLE,
})

# Phase 3 (F134) Abuse Bounds & Invariants
MAX_POLICY_DENIED_FRAC = 0.25   # <= 25% of total citations can be POLICY_DENIED
MAX_POLICY_DENIED_COUNT = 2     # <= 2 absolute POLICY_DENIED citations allowed
MIN_OK_CITATIONS = 2            # >= 2 OK citations required when policy relief is claimed


@dataclass(frozen=True)
class CitationCheckResult:
    """Two-tier citation check result schema (G5 Revision 2.0, F133)."""
    url: str
    host: str
    reachable_on_host: bool
    http_status: int | None
    worker_policy_permitted: bool      # Evaluated against worker-run attestation digest
    broker_attempt_verified: bool      # Verified in per-attempt broker audit log
    classification: str                # 'OK' | 'DEAD' | 'POLICY_DENIED' | 'UNREACHABLE'
    error: str | None = None
    line: str = ""
    literal: str | None = None
    literal_found: bool | None = None
    final_url: str = ""
    redirects_followed: int = 0
    snapshot_source: str = "frozen"    # 'frozen' | 'live_fallback' | 'none' (A2)

    @property
    def reachable(self) -> bool:
        return self.reachable_on_host

    def __getitem__(self, item: str) -> Any:
        if item == "reachable":
            return self.reachable_on_host
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        if item == "reachable":
            return self.reachable_on_host
        return getattr(self, item, default)

    def __contains__(self, item: str) -> bool:
        if item == "reachable":
            return True
        return hasattr(self, item)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "host": self.host,
            "reachable": self.reachable_on_host,
            "reachable_on_host": self.reachable_on_host,
            "http_status": self.http_status,
            "worker_policy_permitted": self.worker_policy_permitted,
            "broker_attempt_verified": self.broker_attempt_verified,
            "classification": self.classification,
            "error": self.error,
            "line": self.line,
            "literal": self.literal,
            "literal_found": self.literal_found,
            "final_url": self.final_url,
            "redirects_followed": self.redirects_followed,
            "snapshot_source": self.snapshot_source,
        }


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


def _normal_host(host: str) -> str:
    """Normalize hostname for policy and denial cross-checking."""
    try:
        value = host.strip().rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, AttributeError):
        return host.strip().rstrip(".").lower()
    return value


def load_broker_attempt_denials(
    task_id: int | None,
    attempt: int | None = 1,
    runs_dir: Path | None = None,
) -> set[str]:
    """Return set of normalized hosts denied by the egress broker for a task attempt (F132/F133)."""
    if task_id is None:
        return set()
    runs = runs_dir or Path("runs")
    paths = []
    if attempt is not None:
        paths.append(runs / f"task{task_id}_a{attempt}_broker.audit.jsonl")
    paths.append(runs / f"task{task_id}_broker.audit.jsonl")

    denied = set()
    for p in paths:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("decision") == "deny" or rec.get("reason") == "host_not_allowlisted":
                        host = rec.get("host")
                        if host:
                            cleaned = _normal_host(str(host).split(":")[0])
                            denied.add(cleaned)
                            if cleaned.startswith("www."):
                                denied.add(cleaned[4:])
            except Exception:
                pass
            if denied:
                break
    return denied


def load_worker_policy_snapshot(
    task_id: int | None,
    attempt: int | None = 1,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    """Load the frozen egress policy snapshot recorded at worker dispatch time (F133).

    Reads policy_digest and allowlisted_hosts from task{tid}_a{attempt}_worker.usage.json.
    Never checks the live config/egress_policy.yaml (Gap 1).
    """
    if task_id is None:
        return {"policy_digest": None, "allowlisted_hosts": []}
    runs = runs_dir or Path("runs")
    paths = []
    if attempt is not None:
        paths.append(runs / f"task{task_id}_a{attempt}_worker.usage.json")
        for r in range(5, 0, -1):
            paths.append(runs / f"task{task_id}_a{attempt}_worker_repair_{r}.usage.json")
    paths.append(runs / f"task{task_id}_worker.usage.json")

    for p in paths:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if "allowlisted_hosts" in data or "policy_digest" in data:
                    raw_hosts = data.get("allowlisted_hosts") or []
                    cleaned = []
                    for h in raw_hosts:
                        norm = _normal_host(str(h))
                        cleaned.append(norm)
                        if norm.startswith("www."):
                            cleaned.append(norm[4:])
                    return {
                        "policy_digest": data.get("policy_digest"),
                        "allowlisted_hosts": sorted(list(set(cleaned))),
                        "snapshot_source": "frozen",
                    }
            except Exception:
                pass

    runs_canonical = (runs_dir.resolve() if runs_dir is not None else (ROOT / "runs").resolve())
    harness_runs = (ROOT / "runs").resolve()
    if runs_canonical == harness_runs:
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "Gap-1 safety net: worker usage artifact absent for task_id=%s attempt=%s; "
                "falling back to live egress policy snapshot (snapshot_source='live_fallback')",
                task_id, attempt,
            )
            from egress_policy import snapshot_egress_policy
            snap = snapshot_egress_policy()
            raw_hosts = snap.get("allowlisted_hosts") or []
            cleaned = []
            for h in raw_hosts:
                norm = _normal_host(str(h))
                cleaned.append(norm)
                if norm.startswith("www."):
                    cleaned.append(norm[4:])
            return {
                "policy_digest": snap.get("policy_digest"),
                "allowlisted_hosts": sorted(list(set(cleaned))),
                "snapshot_source": "live_fallback",
            }
        except Exception:
            pass

    return {"policy_digest": None, "allowlisted_hosts": [], "snapshot_source": "none"}


def classify_citation(
    reachable_on_host: bool,
    http_status: int | None,
    is_dead_resource: bool,
    worker_policy_permitted: bool,
    broker_attempt_verified: bool,
) -> str:
    """Determine classification according to G5 Rev 2.0 two-tier verification model (§4.2).

    1. Reachable on host (e.g. HTTP 200 on direct probe):
       - If worker_policy_permitted -> OK
       - If not worker_policy_permitted:
         - If broker_attempt_verified -> POLICY_DENIED
         - Else -> UNREACHABLE (un-attempted URL on blocked host receives no relief)
    2. Unreachable / Error on host (404, 5xx, NXDOMAIN, timeout, etc.):
       - If dead/fabricated (404/410/DNS failure) -> DEAD
       - Else -> UNREACHABLE
    """
    if reachable_on_host:
        if worker_policy_permitted:
            return CLASSIFICATION_OK
        if broker_attempt_verified:
            return CLASSIFICATION_POLICY_DENIED
        return CLASSIFICATION_UNREACHABLE
    else:
        if is_dead_resource:
            return CLASSIFICATION_DEAD
        return CLASSIFICATION_UNREACHABLE


def _fetch_one(
    cite: dict,
    policy_snapshot: dict | None = None,
    broker_denied_hosts: set[str] | None = None,
) -> CitationCheckResult:
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
    else:
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
                            break
                        next_url, error = _validated_target(urljoin(current_url, location))
                        if error:
                            result["error"] = f"blocked redirect target: {error}"
                            break
                        current_url = next_url
                        result["redirects_followed"] += 1
                        continue
                    raise
            else:
                result["error"] = f"too many redirects (>{MAX_REDIRECTS})"

            if not result.get("error") and "resp" in locals():
                with resp:
                    result["final_url"] = getattr(resp, "geturl", lambda: current_url)()
                    final_url, error = _validated_target(result["final_url"])
                    if error:
                        result["error"] = f"blocked final target: {error}"
                    else:
                        result["final_url"] = final_url
                        result["http_status"] = resp.status
                        result["reachable"] = 200 <= resp.status < 400
                        if cite["literal"] and result["reachable"]:
                            body = resp.read(MAX_BYTES).decode("utf-8", errors="replace")
                            result["literal_found"] = _literal_present(
                                cite["literal"], body + " " + _jsonld_text(body))
        except urllib.error.HTTPError as e:
            result["http_status"] = e.code
            result["reachable"] = False
        except Exception as e:
            result["error"] = str(e)[:100]

    final_host = urlparse(result.get("final_url") or cite["url"]).hostname or ""
    host = _normal_host(final_host)
    stripped = host[4:] if host.startswith("www.") else host

    # Determine worker_policy_permitted against the frozen snapshot (Gap 1)
    if policy_snapshot and "allowlisted_hosts" in policy_snapshot:
        allowed = set(policy_snapshot.get("allowlisted_hosts") or [])
        worker_policy_permitted = (host in allowed or stripped in allowed)
    else:
        # Fallback when no snapshot provided: default permitted
        worker_policy_permitted = True

    # Determine broker_attempt_verified against per-attempt broker denials (Gap 2)
    if broker_denied_hosts is not None:
        broker_attempt_verified = (host in broker_denied_hosts or stripped in broker_denied_hosts)
    else:
        broker_attempt_verified = False

    reachable_on_host = bool(result.get("reachable", False))
    http_status = result.get("http_status")
    is_dead_resource = is_dead(result)
    classification = classify_citation(
        reachable_on_host=reachable_on_host,
        http_status=http_status,
        is_dead_resource=is_dead_resource,
        worker_policy_permitted=worker_policy_permitted,
        broker_attempt_verified=broker_attempt_verified,
    )

    return CitationCheckResult(
        url=cite["url"],
        host=host,
        reachable_on_host=reachable_on_host,
        http_status=http_status,
        worker_policy_permitted=worker_policy_permitted,
        broker_attempt_verified=broker_attempt_verified,
        classification=classification,
        error=result.get("error"),
        line=cite.get("line", ""),
        literal=cite.get("literal"),
        literal_found=result.get("literal_found"),
        final_url=result.get("final_url", cite["url"]),
        redirects_followed=result.get("redirects_followed", 0),
        snapshot_source=(policy_snapshot.get("snapshot_source") or "frozen") if policy_snapshot else "none",
    )


def verify(
    text: str,
    task_id: int | None = None,
    attempt: int | None = 1,
    runs_dir: Path | None = None,
    policy_snapshot: dict | None = None,
    broker_denied_hosts: set[str] | None = None,
) -> list[CitationCheckResult]:
    """Fetch+verify every citation in `text` (bounded). Returns CitationCheckResult list."""
    cites = extract_citations(text)
    if not cites:
        return []

    if policy_snapshot is None and task_id is not None:
        policy_snapshot = load_worker_policy_snapshot(task_id, attempt, runs_dir)

    if broker_denied_hosts is None and task_id is not None:
        broker_denied_hosts = load_broker_attempt_denials(task_id, attempt, runs_dir)

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {
            ex.submit(_fetch_one, c, policy_snapshot, broker_denied_hosts): c
            for c in cites
        }
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                cite = futs[fut]
                raw_host = urlparse(cite.get("url", "")).hostname or ""
                host = _normal_host(raw_host)
                results.append(CitationCheckResult(
                    url=cite.get("url", ""),
                    host=host,
                    reachable_on_host=False,
                    http_status=None,
                    worker_policy_permitted=True,
                    broker_attempt_verified=False,
                    classification=CLASSIFICATION_DEAD,
                    error=str(e)[:100],
                    line=cite.get("line", ""),
                    literal=cite.get("literal"),
                    literal_found=None,
                    final_url=cite.get("url", ""),
                    redirects_followed=0,
                    snapshot_source=(policy_snapshot.get("snapshot_source") or "frozen") if policy_snapshot else "none",
                ))
    return results


def is_dead(e: dict | CitationCheckResult) -> bool:
    """RC-1 (2026-09-04) + G5 (2026-09-07): True only when a citation is fabricated
    or the resource is genuinely gone -- NOT when a live page was blocked by policy.
    """
    if isinstance(e, CitationCheckResult):
        if e.classification == CLASSIFICATION_DEAD:
            return True
        if e.classification in (CLASSIFICATION_OK, CLASSIFICATION_POLICY_DENIED):
            return False
        if e.reachable_on_host:
            return False
        if e.http_status is not None:
            return e.http_status in DEAD_HTTP_STATUSES
        err = e.error or ""
        if err.startswith("blocked") or err == "unsupported scheme" or "redirect" in err:
            return False
        return True

    if e.get("classification") == CLASSIFICATION_DEAD:
        return True
    if e.get("classification") in (CLASSIFICATION_OK, CLASSIFICATION_POLICY_DENIED):
        return False
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


def summarize(evidence: list[dict | CitationCheckResult]) -> dict:
    checked = len(evidence)
    dead = sum(1 for e in evidence if is_dead(e))
    ok = sum(1 for e in evidence if e.get("classification") == CLASSIFICATION_OK)
    policy_denied = sum(1 for e in evidence if e.get("classification") == CLASSIFICATION_POLICY_DENIED)
    unreachable = sum(1 for e in evidence if e.get("classification") == CLASSIFICATION_UNREACHABLE)
    lit_checked = [e for e in evidence if e.get("literal") and e.get("reachable")]
    lit_missing = sum(1 for e in lit_checked if e.get("literal_found") is False)
    return {
        "checked": checked,
        "ok": ok,
        "dead": dead,
        "policy_denied": policy_denied,
        "unreachable": unreachable,
        "dead_frac": round(dead / checked, 2) if checked else 0.0,
        "policy_denied_frac": round(policy_denied / checked, 2) if checked else 0.0,
        "literal_checked": len(lit_checked),
        "literal_missing": lit_missing,
    }


def is_hard_fail(summary: dict) -> bool:
    return (summary["checked"] >= MIN_CHECKED_FOR_HARD_FAIL
            and summary["dead_frac"] > DEAD_FRAC_HARD_FAIL)


def evidence_block(evidence: list[dict | CitationCheckResult]) -> str:
    """Compact text for the critic prompt — structured facts only, never raw
    fetched page content (see module docstring / F10)."""
    if not evidence:
        return "(no citations found to verify)"
    lines = []
    for e in evidence[:MAX_CITATIONS]:
        classification = e.get("classification")
        if classification == CLASSIFICATION_POLICY_DENIED:
            status = "POLICY_DENIED (verified live on host; blocked by worker egress policy)"
        elif classification == CLASSIFICATION_UNREACHABLE:
            status = "UNVERIFIABLE (reachable on host, but worker never attempted via broker; no policy-denial relief)"
        elif classification == CLASSIFICATION_OK:
            status = "OK"
        elif classification == CLASSIFICATION_DEAD or is_dead(e):
            status = f"DEAD ({e.get('http_status') or e.get('error')})"
        elif e.get("reachable"):
            status = "OK"
        else:
            status = f"BLOCKED ({e.get('http_status') or e.get('error')})"

        lit = f", claimed value '{e.get('literal')}' found on page: {e.get('literal_found')}" \
            if e.get("literal") and (classification == CLASSIFICATION_OK or (classification is None and e.get("reachable"))) else ""
        lines.append(f"- {e.get('url')}: {status}{lit}")
    return "\n".join(lines)


def check_abuse_bounds(summary: dict) -> tuple[bool, str | None]:
    """Check Gap 3 abuse bounds for POLICY_DENIED and UNREACHABLE citations (F134, F135).

    Bounds:
    1. Minimum 2 OK citations required when any non-OK citations (policy_denied or unreachable) are present.
    2. Maximum 2 absolute POLICY_DENIED citations.
    3. Maximum 25% POLICY_DENIED fraction of total citations.
    """
    checked = summary.get("checked", 0)
    policy_denied = summary.get("policy_denied", 0)
    unreachable = summary.get("unreachable", 0)
    ok = summary.get("ok", 0)

    non_ok = policy_denied + unreachable
    if non_ok > 0 and ok < MIN_OK_CITATIONS:
        return False, f"insufficient_verified_sources: found {ok} OK citations, minimum {MIN_OK_CITATIONS} required"

    if policy_denied > 0:
        if policy_denied > MAX_POLICY_DENIED_COUNT:
            return False, f"high_policy_denial_count: {policy_denied} policy-denied citations exceeds maximum allowed ({MAX_POLICY_DENIED_COUNT})"
        if checked > 0 and (policy_denied / checked > MAX_POLICY_DENIED_FRAC):
            frac = policy_denied / checked
            return False, f"high_policy_denial_fraction: {policy_denied}/{checked} ({frac:.0%}) exceeds {MAX_POLICY_DENIED_FRAC:.0%} ceiling"

    return True, None


_CONF_3_RE = re.compile(r'\b(?:confidence|conf)\s*[:=]?\s*3\b', re.IGNORECASE)
_QUOTE_RE = re.compile(r'["“][^"”\n]{3,}["”]')


def _standalone_url_pat(url: str) -> re.Pattern:
    """Compile pattern that matches url as a standalone token, not embedded in another URL (e.g. web.archive.org)."""
    return re.compile(r'(?<![a-zA-Z0-9/_.-])' + re.escape(url) + r'(?![a-zA-Z0-9/_.-])')


def _find_url_contexts(text: str, url: str) -> list[str]:
    """Find text contexts around standalone occurrences of url within its list item, table row, or sentence."""
    if not text or not url:
        return []
    pat = _standalone_url_pat(url)
    lines = text.splitlines()
    contexts = []
    for i, line in enumerate(lines):
        if not pat.search(line):
            continue
        # Table row: if multiple distinct URLs exist in row, scope strictly to sentence/clause
        if line.strip().startswith("|") and line.strip().endswith("|"):
            all_urls = re.findall(r'https?://[^\s)\]\"\'|,]+', line)
            if len(set(all_urls)) > 1:
                cells = [c.strip() for c in line.strip().split("|")[1:-1]]
                matching_cells = [c for c in cells if pat.search(c)]
                for cell in matching_cells:
                    sentences = re.split(r'(?<=[.!?])\s+', cell)
                    matching_sentences = [s for s in sentences if pat.search(s)]
                    if matching_sentences:
                        contexts.extend(matching_sentences)
                    else:
                        contexts.append(cell)
            else:
                contexts.append(line)
            continue
        # Markdown list item: gather only this item and any indented continuation lines
        if re.match(r'^\s*[-*+\d.]', line):
            item_lines = [line]
            for next_line in lines[i + 1:]:
                if next_line.strip() and not re.match(r'^\s*[-*+\d.]', next_line) and next_line.startswith(('  ', '\t')):
                    item_lines.append(next_line)
                else:
                    break
            target = " ".join(item_lines)
        else:
            # Paragraph bounded by empty lines, list items, or tables
            start = i
            while start > 0 and lines[start - 1].strip() and not re.match(r'^\s*[-*+\d.|]', lines[start - 1]):
                start -= 1
            end = i + 1
            while end < len(lines) and lines[end].strip() and not re.match(r'^\s*[-*+\d.|]', lines[end]):
                end += 1
            target = " ".join(lines[start:end])

        # If multiple sentences in target, isolate sentences referencing this url
        sentences = re.split(r'(?<=[.!?])\s+', target)
        matching_sentences = [s for s in sentences if pat.search(s)]
        if matching_sentences:
            contexts.append(" ".join(matching_sentences))
        else:
            contexts.append(target)
    return contexts


def _find_url_context(text: str, url: str) -> str:
    """Backward-compatible helper returning the primary context for url."""
    contexts = _find_url_contexts(text, url)
    return contexts[0] if contexts else ""


def detect_fabrication(
    text: str,
    evidence: list[CitationCheckResult | dict],
) -> list[dict[str, Any]]:
    """Detect worker fabrication on non-allowlisted / un-attempted citations (F134, F135).

    A worker cannot assert high confidence (confidence 3) or attribute verbatim
    quotations ("...") to a source that was blocked by egress policy (POLICY_DENIED)
    or never attempted via the broker (UNREACHABLE).
    Such assertions are mechanically provable fabrications since the worker
    did not load the resource at the network layer.
    """
    fabrications: list[dict[str, Any]] = []
    for e in evidence:
        cls_name = getattr(e, "classification", None) or (e.get("classification") if isinstance(e, dict) else None)
        if cls_name not in (CLASSIFICATION_POLICY_DENIED, CLASSIFICATION_UNREACHABLE):
            continue

        url = getattr(e, "url", None) or (e.get("url") if isinstance(e, dict) else "")
        host = getattr(e, "host", None) or (e.get("host") if isinstance(e, dict) else "")

        contexts = _find_url_contexts(text, url)
        if not contexts:
            raw_line = getattr(e, "line", None) or (e.get("line") if isinstance(e, dict) else "")
            if raw_line:
                contexts = [raw_line]

        prefix = "policy_denied" if cls_name == CLASSIFICATION_POLICY_DENIED else "unattempted"
        reasons = []
        offending_quotes = []

        for ctx in contexts:
            # Strip markdown link syntax to avoid treating markdown link titles as a quote
            clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', ctx)

            # 1. Check confidence 3
            has_conf3 = False
            if _CONF_3_RE.search(clean):
                has_conf3 = True
            elif clean.strip().startswith("|") and clean.strip().endswith("|"):
                cells = [c.strip() for c in clean.strip().split("|")[1:-1]]
                for c in cells:
                    if c in ("3", "3.0", "Conf 3", "conf 3", "Confidence 3"):
                        has_conf3 = True
                        break
            if has_conf3 and f"{prefix}_conf3" not in reasons:
                reasons.append(f"{prefix}_conf3")

            # 2. Check verbatim quotes
            quotes = _QUOTE_RE.findall(clean)
            if re.search(r'^\s*>[ \t]+["“]?[^"\n]{3,}["”]?', clean, re.MULTILINE):
                quotes.append("> blockquote")
            if quotes:
                if f"{prefix}_quote" not in reasons:
                    reasons.append(f"{prefix}_quote")
                offending_quotes.extend(quotes)

        if reasons:
            source_desc = "policy-denied" if cls_name == CLASSIFICATION_POLICY_DENIED else "unattempted"
            detail = f"Fabrication: worker asserted {' and '.join(reasons)} for {source_desc} source ({url})"
            if offending_quotes:
                detail += f" [quotes: {', '.join(offending_quotes[:3])}]"
            fabrications.append({
                "url": url,
                "host": host,
                "classification": cls_name,
                "reasons": reasons,
                "detail": detail,
                "offending_quotes": offending_quotes,
            })

    return fabrications


def record_policy_expansion_candidates(
    evidence: list[CitationCheckResult | dict],
    task_id: int | None = None,
    attempt: int | None = 1,
    runs_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Append policy-denied domains to runs/policy_expansion_candidates.jsonl for operator review (F134).

    Append-only log for candidates to consider allowlisting in egress_policy.yaml.
    """
    runs = Path(runs_dir) if runs_dir is not None else (ROOT / "runs")
    # A1 (Claude follow-up / Hermes Flag 1): fixture-segregation guard.
    # If running under model-free test tier (AGI_TEST_TIER set), tests MUST inject a temp runs_dir.
    # Writing test fixtures to the production runs/ directory is strictly prohibited.
    if os.environ.get("AGI_TEST_TIER"):
        prod_runs = (ROOT / "runs").resolve()
        if runs.resolve() == prod_runs:
            raise AssertionError(
                "Fixture-segregation guard: test suite attempted to write policy_expansion_candidates "
                "to production runs/ directory without injecting a temporary runs_dir!"
            )
    log_file = runs / "policy_expansion_candidates.jsonl"
    runs.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, Any]] = []
    seen = set()
    now_iso = datetime.now(timezone.utc).isoformat()

    for e in evidence:
        cls_name = getattr(e, "classification", None) or (e.get("classification") if isinstance(e, dict) else None)
        if cls_name != CLASSIFICATION_POLICY_DENIED:
            continue

        url = getattr(e, "url", None) or (e.get("url") if isinstance(e, dict) else "")
        host = getattr(e, "host", None) or (e.get("host") if isinstance(e, dict) else "")
        key = (host, url)
        if key in seen:
            continue
        seen.add(key)

        entry = {
            "timestamp": now_iso,
            "task_id": task_id,
            "attempt": attempt,
            "host": host,
            "url": url,
            "classification": CLASSIFICATION_POLICY_DENIED,
        }
        candidates.append(entry)

    if candidates:
        try:
            with log_file.open("a", encoding="utf-8") as f:
                for c in candidates:
                    f.write(json.dumps(c) + "\n")
        except Exception:
            pass

    return candidates

