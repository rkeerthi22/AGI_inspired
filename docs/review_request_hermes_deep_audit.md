# Hermes Deep-Audit Pass — Verify the Fix + Chase the Surfaces You Flagged

You are the standing independent deep-auditor for this repo (S:\AGI_like). Last pass you caught the
browser-egress regression (bug #1) that both Gemini and Claude certified wrong — by parsing the
broker audit JSONL they cited but didn't read. That is the pattern: every agent here cites files
without parsing them. Your job is to keep doing exactly that.

## Round 2 — three targets, parse-don't-trust on each

### Target 1 (PRIMARY): verify Gemini's incoming bug #1 fix — don't let the gap repeat
Gemini is landing a fix for the browser-egress regression (browser_daemon.py). When it lands,
verify the fix actually closes the gap, by parsing the artifacts (not reading the commit message):
- `orchestrator/browser_daemon.py` Chrome launch: does it now include `--proxy-server=127.0.0.1:8787`?
  Is `--remote-allow-origins=*` removed (bug #2)? Is there an ownership-token check on
  127.0.0.1:9222 (bug #7, port-squat)?
- After a live browser mission: parse `runs/taskNNN_a1_broker.audit.jsonl` — do the browser's
  navigations (app.aiprm.com etc.) now appear as broker rows? (Tasks 176/178 had ZERO browser rows —
  that was the regression. The fix must make them appear.) Parse `runs/taskNNN_a1_citation_evidence.json`
  — is `broker_attempt_verified` now `True` for the browser-fetched URL?
- If Gemini claims the fix is verified but the broker JSONL still has no browser rows, that is the SAME
  bug class (citing a file without parsing it). Say so.

**Note (Claude-verified 2026-09-12, HEAD `27d026b`):** As of this commit, NO post-fix live browser
mission exists (max task_id in ledger = 183; the broker JSONL for Tasks 176/178 is PRE-FIX and contains
zero browser rows by the original regression). Gemini verified bug #1 **hermetically** — i.e.
`test_browser_daemon.py` asserts `--proxy-server` is present in the cmd list. A unit test asserting the
flag is *present in the cmd list* does NOT prove the broker logs browser rows on live traffic: Chrome
could ignore the flag, the proxy could fail to intercept, or the broker could still not log browser rows.
So for Target 1's *live* proof you must either:
  (a) request the operator authorize a single `--controlled-window` browser mission and parse the fresh
      `runs/taskNNN_a1_broker.audit.jsonl` for browser rows + `broker_attempt_verified=True` for the
      browser-fetched URL; OR
  (b) explicitly return Target 1-live as **UNVERIFIED-PENDING-LIVE-RUN** and verify only the code-level
      portion (flag present, wildcard removed, port-squat check) now.
Do NOT certify bug #1 as CLOSED on unit-test assertions alone — that is the exact bug class you caught
last round (citing a file without parsing it). The code-level fix is real; the live-traffic proof is not
yet collected. State both honestly.

### Target 2: chase the S3 durability cluster you flagged (#3-#6) — these block B provisioning
Re-read `orchestrator/s3_audit_replication.py` and confirm/refute each, with file:line:
- #3 checkpoint_key + manifest_key put_object (lines ~307-338) carry NO ObjectLockMode — chain of
  custody is mutable. Is there an If-Match/ETag guard or a portalocker equivalent? (F120 fixed this
  class for the UNC path.)
- #4 verify_s3_checkpoint_chain (~197-207) compares ContentLength only via head_object —
  trajectory_sha256 recorded but never checked against the object body. Confirm a same-length tampered
  replica passes. Does the UNC backend hash where S3 doesn't?
- #5 s3_audit_state has no retention_floor_check analog (UNC has minimum_retention_days / chain span).
- #6 config tripwires: HARNESS_AUDIT_S3_BUCKET alone routes to S3 without BACKEND=s3 (audit_replication.py:346);
  unknown HARNESS_AUDIT_RETENTION_MODE silently coerces to COMPLIANCE (the irreversible mode, ~88-90).
For each: is it a real defect, and what's the one-line fix?

### Target 3: is the citation linter (1918133) live-traffic-ready, or will it false-positive?
`orchestrator/deliverable_preflight.py` check_citation_metadata (line ~127) + anti-speculation (lines
~83-108). The hermetic tests pass, but they're synthetic. Hunt for false positives/negatives on REAL
deliverable text:
- Read 2-3 real deliverables in workspace/shopify/ (e.g., the M3 and M6 passes). Would the linter
  flag any of them wrongly? (e.g., a legitimate "Unknown" or "N/A" in a non-funding context; a source
  whose date is in prose not the regex's expected format; a confidence stated as "high" not "3".)
- Would the linter have caught M1 (missing dates), M5 (un-fetched URL), M7 ("Bootstrapped") — or do
  the regexes miss those exact patterns? The linter is untested on live traffic; your call: is a live
  validation run needed before trusting it, or is the logic sound?

## Deliverable (short)
1. Per target: VERIFIED-CLOSED / STILL-OPEN / REGRESSED, with the one decisive parsed artifact.
2. Any NEW bug you find (same parse-don't-trust discipline).
3. One sentence: is the repo safe to point at real venture work right now, or what must land first?

Constraints (unchanged): ESTOP engaged between controlled windows; critic stays unrestricted on
byteplus_coding (not in worker chain); do NOT enable HARNESS_AUDIT_ENFORCE=1 / HARNESS_AUDIT_BACKEND=s3
without a real bucket; do NOT unlock OmniRoute; credentials in credman (AGI_like/<provider>), never
printed — read presence via orchestrator/secrets.py credential_manager_has_api_key(); gate must stay
green (currently 79/79, read count from output).

Be blunt. Parse the files. The point is to catch what Gemini and Claude keep certifying unread.
