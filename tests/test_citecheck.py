"""Redirect-safety regressions for citecheck.

Validates that:
1. public -> public redirects are followed and counted;
2. public -> private redirects are blocked before a second fetch;
3. redirect loops fail closed after the configured limit.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import citecheck  # noqa: E402

checks = 0
failures: list[str] = []


def check(label: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  [FAIL] {label}")
    else:
        print(f"  [PASS] {label}")


class FakeResponse:
    def __init__(self, url: str, body: str, status: int = 200):
        self._url = url
        self._body = body
        self.status = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit: int | None = None):
        data = self._body.encode("utf-8")
        return data if limit is None else data[:limit]

    def geturl(self):
        return self._url


class FakeOpener:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls: list[str] = []

    def open(self, req, timeout):
        self.calls.append(req.full_url)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


original_opener = citecheck._NO_REDIRECT_OPENER
original_resolve = citecheck._resolve_safety
try:
    print("=== public redirect stays allowed ===")
    opener = FakeOpener([
        urllib.error.HTTPError(
            "https://start.example/report", 302, "found",
            {"Location": "https://final.example/report"}, None,
        ),
        FakeResponse("https://final.example/report", "Revenue 42"),
    ])
    citecheck._NO_REDIRECT_OPENER = opener
    citecheck._resolve_safety = lambda host: None
    evidence = citecheck.verify("Revenue 42 https://start.example/report")
    check("one citation returned", len(evidence), 1)
    check("redirected citation is reachable", evidence[0]["reachable"], True)
    check("final URL recorded", evidence[0]["final_url"], "https://final.example/report")
    check("redirect count recorded", evidence[0]["redirects_followed"], 1)
    check("second fetch reached final target", opener.calls[-1], "https://final.example/report")

    print("\n=== private redirect is blocked ===")
    opener = FakeOpener([
        urllib.error.HTTPError(
            "https://start.example/report", 302, "found",
            {"Location": "http://169.254.169.254/latest/meta-data"}, None,
        ),
    ])
    citecheck._NO_REDIRECT_OPENER = opener
    citecheck._resolve_safety = (
        lambda host: "blocked: resolves to a private/loopback/link-local address"
        if host == "169.254.169.254" else None
    )
    blocked = citecheck.verify("Metadata probe https://start.example/report")
    check("blocked redirect stays unreachable", blocked[0]["reachable"], False)
    check("blocked redirect reports why",
          blocked[0]["error"].startswith("blocked redirect target:"), True)
    check("blocked redirect never makes a second request", len(opener.calls), 1)

    print("\n=== redirect loops fail closed ===")
    opener = FakeOpener([
        urllib.error.HTTPError(
            "https://loop.example/a", 302, "found",
            {"Location": "https://loop.example/b"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/b", 302, "found",
            {"Location": "https://loop.example/c"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/c", 302, "found",
            {"Location": "https://loop.example/d"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/d", 302, "found",
            {"Location": "https://loop.example/e"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/e", 302, "found",
            {"Location": "https://loop.example/f"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/f", 302, "found",
            {"Location": "https://loop.example/g"}, None,
        ),
    ])
    citecheck._NO_REDIRECT_OPENER = opener
    citecheck._resolve_safety = lambda host: None
    looped = citecheck.verify("Loop https://loop.example/a")
    check("redirect loop stays unreachable", looped[0]["reachable"], False)
    check("redirect loop reports max redirects",
          looped[0]["error"], f"too many redirects (>{citecheck.MAX_REDIRECTS})")
    check("redirect loop counter hits limit",
          looped[0]["redirects_followed"], citecheck.MAX_REDIRECTS + 1)
finally:
    citecheck._NO_REDIRECT_OPENER = original_opener
    citecheck._resolve_safety = original_resolve

# === Section 4: CitationCheckResult schema & immutability ===
print("\n=== CitationCheckResult schema & immutability (F133) ===")
import json
import os
import tempfile
from dataclasses import FrozenInstanceError

res = citecheck.CitationCheckResult(
    url="https://example.com/test",
    host="example.com",
    reachable_on_host=True,
    http_status=200,
    worker_policy_permitted=True,
    broker_attempt_verified=False,
    classification=citecheck.CLASSIFICATION_OK,
)
check("schema url matches", res.url, "https://example.com/test")
check("schema host matches", res.host, "example.com")
check("schema reachable_on_host is True", res.reachable_on_host, True)
check("schema reachable property is True", res.reachable, True)
check("schema worker_policy_permitted is True", res.worker_policy_permitted, True)
check("schema broker_attempt_verified is False", res.broker_attempt_verified, False)
check("schema classification is OK", res.classification, "OK")
check("dict-like access res['url']", res["url"], "https://example.com/test")
check("dict-like access res.get('host')", res.get("host"), "example.com")
check("to_dict() contains classification", res.to_dict()["classification"], "OK")

# Test immutability
try:
    res.classification = "DEAD"  # type: ignore
    check("result is immutable (frozen)", False, True)
except FrozenInstanceError:
    check("result is immutable (frozen)", True, True)

# === Section 5: Gap 1 Attestation snapshot invariant (time-of-check) ===
print("\n=== Gap 1: Attestation snapshot invariant (F133) ===")
import egress_policy
with tempfile.TemporaryDirectory() as td:
    t_path = Path(td)
    # Create a synthetic attestation token with an attested digest
    attested_hash = "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
    import base64
    payload_data = {
        "purpose": "egress-boundary-v1",
        "policy_sha256": attested_hash,
        "broker_endpoint": "127.0.0.1:8787",
    }
    b64_payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).decode()
    token_file = t_path / "mock.signed"
    token_file.write_text(f"{b64_payload}.signature.v1", encoding="utf-8")

    # Create a dummy policy yaml with an EXPANDED allowlist and a DIFFERENT live digest
    dummy_yaml = t_path / "egress_policy.yaml"
    dummy_yaml.write_text("""
schema_version: 1
mode: broker_required
broker:
  host: 127.0.0.1
  port: 8787
  connect_port: 443
  idle_timeout_seconds: 120
  max_connection_bytes: 10485760
  allowed_hosts:
    - allowed-host.example.com
    - newly-expanded.example.com
attestation:
  environment_variable: HARNESS_EGRESS_ATTESTATION
  purpose: egress-boundary-v1
  max_age_hours: 24
  required_evidence: [restricted_worker_identity]
  required_claims: [worker_identity]
audit_log_environment_variable: HARNESS_EGRESS_AUDIT
""", encoding="utf-8")

    # Snapshot MUST read the attestation digest, NOT the live file's digest!
    snap = egress_policy.snapshot_egress_policy(
        policy_path=dummy_yaml,
        attestation_file=token_file,
    )
    check("snapshot reads attestation digest, not live file (Gap 1)",
          snap["policy_digest"], attested_hash)
    check("snapshot digest != live policy digest",
          snap["policy_digest"] != egress_policy.load_policy(dummy_yaml).digest, True)

# === Section 6: Kill-Assumption Regression (Broker log cross-check pinning) ===
print("\n=== Kill-Assumption: Broker log cross-check pinning (F133) ===")
with tempfile.TemporaryDirectory() as td:
    runs_dir = Path(td)
    tid = 9301
    attempt = 1

    # Write worker usage with frozen snapshot: only 'allowed.example.com' was permitted at run time
    worker_usage_file = runs_dir / f"task{tid}_a{attempt}_worker.usage.json"
    worker_usage_file.write_text(json.dumps({
        "policy_digest": "frozen_attestation_digest_9301",
        "allowlisted_hosts": ["allowed.example.com"],
    }), encoding="utf-8")

    # Write broker audit log: worker attempted 'blocked.example.com' and was denied!
    broker_audit_file = runs_dir / f"task{tid}_a{attempt}_broker.audit.jsonl"
    broker_audit_file.write_text(json.dumps({
        "decision": "deny",
        "host": "blocked.example.com",
        "reason": "host_not_allowlisted",
        "task_id": tid,
        "attempt": attempt,
    }) + "\n", encoding="utf-8")

    # Mock opener where all hosts return 200 OK on direct probe
    class HostDirectProbeOpener:
        def open(self, req, timeout):
            return FakeResponse(req.full_url, "Valid content 100", status=200)

    orig_opener = citecheck._NO_REDIRECT_OPENER
    orig_safety = citecheck._resolve_safety
    citecheck._NO_REDIRECT_OPENER = HostDirectProbeOpener()
    citecheck._resolve_safety = lambda host: None

    try:
        # Case A: URL is on blocked host, BUT broker confirms attempt -> POLICY_DENIED relief
        text_a = "Fact from blocked https://blocked.example.com/data"
        res_a = citecheck.verify(text_a, task_id=tid, attempt=attempt, runs_dir=runs_dir)
        check("Case A: 1 citation checked", len(res_a), 1)
        check("Case A: reachable on host is True", res_a[0].reachable_on_host, True)
        check("Case A: worker_policy_permitted is False", res_a[0].worker_policy_permitted, False)
        check("Case A: broker_attempt_verified is True", res_a[0].broker_attempt_verified, True)
        check("Case A: classification is POLICY_DENIED", res_a[0].classification, citecheck.CLASSIFICATION_POLICY_DENIED)
        check("Case A: is_dead is False for POLICY_DENIED", citecheck.is_dead(res_a[0]), False)

        # Case B: URL is on blocked host, but NEVER attempted in broker log -> relief REFUSED!
        text_b = "Fact from unattempted https://unattempted.example.com/data"
        res_b = citecheck.verify(text_b, task_id=tid, attempt=attempt, runs_dir=runs_dir)
        check("Case B: 1 citation checked", len(res_b), 1)
        check("Case B: reachable on host is True", res_b[0].reachable_on_host, True)
        check("Case B: worker_policy_permitted is False", res_b[0].worker_policy_permitted, False)
        check("Case B: broker_attempt_verified is False", res_b[0].broker_attempt_verified, False)
        check("Case B: classification is UNREACHABLE (relief refused)", res_b[0].classification, citecheck.CLASSIFICATION_UNREACHABLE)

        # Case C: URL is on allowlisted host -> OK
        text_c = "Fact from allowed https://allowed.example.com/data"
        res_c = citecheck.verify(text_c, task_id=tid, attempt=attempt, runs_dir=runs_dir)
        check("Case C: classification is OK", res_c[0].classification, citecheck.CLASSIFICATION_OK)
        check("Case C: worker_policy_permitted is True", res_c[0].worker_policy_permitted, True)

        # Case D: Dead URL (404) on blocked host -> DEAD (dead never receives relief!)
        class Dead404Opener:
            def open(self, req, timeout):
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        citecheck._NO_REDIRECT_OPENER = Dead404Opener()
        text_d = "Fact from dead https://blocked.example.com/missing"
        res_d = citecheck.verify(text_d, task_id=tid, attempt=attempt, runs_dir=runs_dir)
        check("Case D: reachable on host is False", res_d[0].reachable_on_host, False)
        check("Case D: classification is DEAD", res_d[0].classification, citecheck.CLASSIFICATION_DEAD)
        check("Case D: is_dead is True", citecheck.is_dead(res_d[0]), True)

        # Case E: Summarize & Evidence Block formatting
        all_results = [res_a[0], res_b[0], res_c[0], res_d[0]]
        summary = citecheck.summarize(all_results)
        check("Summary total checked", summary["checked"], 4)
        check("Summary ok count", summary["ok"], 1)
        check("Summary policy_denied count", summary["policy_denied"], 1)
        check("Summary dead count", summary["dead"], 1)
        check("Summary unreachable count", summary["unreachable"], 1)

        block = citecheck.evidence_block(all_results)
        check("Evidence block mentions POLICY_DENIED",
              "POLICY_DENIED (verified live on host; blocked by worker egress policy)" in block, True)
        # F135 Finding 1 regression: UNREACHABLE is labeled UNVERIFIABLE, never OK
        check("Evidence block labels UNREACHABLE as UNVERIFIABLE (F135)",
              "UNVERIFIABLE (reachable on host, but worker never attempted via broker; no policy-denial relief)" in block, True)
        check("Evidence block does NOT label UNREACHABLE as OK (F135)",
              "https://unattempted.example.com/data: OK" in block, False)
    finally:
        citecheck._NO_REDIRECT_OPENER = orig_opener
        citecheck._resolve_safety = orig_safety

# === F135: Fabrication Guard & Abuse Bounds for UNREACHABLE ===
print("\n=== F135: UNREACHABLE Fabrication & Bounds Hardening ===")
unreach_res = citecheck.CitationCheckResult(
    url="https://unattempted.example.com/spec",
    host="unattempted.example.com",
    reachable_on_host=True,
    http_status=200,
    worker_policy_permitted=False,
    broker_attempt_verified=False,
    classification=citecheck.CLASSIFICATION_UNREACHABLE,
    line="- Claim: https://unattempted.example.com/spec (confidence 3)",
)

# Test 1: UNREACHABLE + conf-3 -> mechanically flagged with unattempted_conf3
fab_conf3 = citecheck.detect_fabrication("- Claim: https://unattempted.example.com/spec (confidence 3)", [unreach_res])
check("UNREACHABLE with conf 3 detected as fabrication", len(fab_conf3), 1)
check("UNREACHABLE conf 3 has reason unattempted_conf3", "unattempted_conf3" in fab_conf3[0]["reasons"], True)

# Test 2: UNREACHABLE + verbatim quote -> mechanically flagged with unattempted_quote
quote_res = citecheck.CitationCheckResult(
    url="https://unattempted.example.com/quote",
    host="unattempted.example.com",
    reachable_on_host=True,
    http_status=200,
    worker_policy_permitted=False,
    broker_attempt_verified=False,
    classification=citecheck.CLASSIFICATION_UNREACHABLE,
    line='- Quote: "exact verbatim quote from target" (https://unattempted.example.com/quote)',
)
fab_quote = citecheck.detect_fabrication('- Quote: "exact verbatim quote from target" (https://unattempted.example.com/quote)', [quote_res])
check("UNREACHABLE with verbatim quote detected as fabrication", len(fab_quote), 1)
check("UNREACHABLE quote has reason unattempted_quote", "unattempted_quote" in fab_quote[0]["reasons"], True)

# Test 3: UNREACHABLE with confidence 1 and no quotes -> passes carve-out
honest_res = citecheck.CitationCheckResult(
    url="https://unattempted.example.com/honest",
    host="unattempted.example.com",
    reachable_on_host=True,
    http_status=200,
    worker_policy_permitted=False,
    broker_attempt_verified=False,
    classification=citecheck.CLASSIFICATION_UNREACHABLE,
    line="- Note: could not verify due to policy (https://unattempted.example.com/honest, conf 1)",
)
fab_honest = citecheck.detect_fabrication("- Note: could not verify due to policy (https://unattempted.example.com/honest, conf 1)", [honest_res])
check("UNREACHABLE conf 1 without quotes passes fabrication guard", len(fab_honest), 0)

# Test 4: Evidence block literal suppression on UNREACHABLE
unreach_with_lit = citecheck.CitationCheckResult(
    url="https://unattempted.example.com/lit",
    host="unattempted.example.com",
    reachable_on_host=True,
    http_status=200,
    worker_policy_permitted=False,
    broker_attempt_verified=False,
    classification=citecheck.CLASSIFICATION_UNREACHABLE,
    literal="100%",
    literal_found=True,
)
lit_block = citecheck.evidence_block([unreach_with_lit])
check("Evidence block suppresses claimed value literal on UNREACHABLE", "claimed value" in lit_block, False)

# Test 5: Abuse bounds cover UNREACHABLE (Fix 3)
# 1 OK + 1 UNREACHABLE -> insufficient_verified_sources
summary_1ok_1unreach = {"checked": 2, "ok": 1, "unreachable": 1, "policy_denied": 0, "dead": 0}
passed_1, reason_1 = citecheck.check_abuse_bounds(summary_1ok_1unreach)
check("Abuse bounds fail on 1 OK + 1 UNREACHABLE", passed_1, False)
check("Abuse bounds reason names insufficient_verified_sources", "insufficient_verified_sources" in (reason_1 or ""), True)

# 2 OK + 1 UNREACHABLE -> passes
summary_2ok_1unreach = {"checked": 3, "ok": 2, "unreachable": 1, "policy_denied": 0, "dead": 0}
passed_2, reason_2 = citecheck.check_abuse_bounds(summary_2ok_1unreach)
check("Abuse bounds pass on 2 OK + 1 UNREACHABLE", passed_2, True)

# Clean deliverable with 1 OK and 0 non-ok -> passes (no false fail on clean 1-citation doc)
summary_clean_1ok = {"checked": 1, "ok": 1, "unreachable": 0, "policy_denied": 0, "dead": 0}
passed_clean, reason_clean = citecheck.check_abuse_bounds(summary_clean_1ok)
check("Abuse bounds pass on clean deliverable with 1 OK and 0 non-OK", passed_clean, True)

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)

