# Independent Review Request — AGI_like Harness: Full Arc Review + Next Steps

You are reviewing the AGI_like cognitive harness repo (S:\AGI_like, git, branch master) as an
independent principal reviewer. Claude Code has been the final-reviewer gate through this arc;
Gemini CLI has been the forward implementer. Your job: independently audit the work landed since
commit c26f0fc, verify the claims against the actual repo state (not the docs' assertions), and
recommend the next steps with ranked leverage.

## Review boundary

Review the arc from c26f0fc (exclusive) to HEAD (inclusive). Recent commits:

  1918133 feat(linter): implement pre-submit citation linter hardening for M1, M5, M7
  a25e24e docs(review): Claude verification -- full M1-M7 cohort 4/7 (57.1%) HONEST
  558e15c feat(cohort): record full M1-M7 cohort yield (4/7 passes, 57.1%) and update continuity to rev 106
  ba30475 docs(gemini): full M1-M7 cohort brief -- end-to-end yield with M2 + failover operational
  23cd801 docs(review): Claude verification -- M2 browser PROVEN UNBLOCKED (Task 176)
  2774edc feat(browser): empirically verify M2 browser automation live in Task 176 (done/pass, facts+15)
  ee1db99 docs(review): Claude verification -- M2 browser overclaimed, Deficit B S3 honest
  089b8db feat(audit): implement S3/Backblaze B2 Object Lock WORM audit replication backend (Deficit B)
  e60f73e feat(browser): implement headless Chrome CDP daemon bridge for out-of-process browser automation (unblocking M2)
  4b64aca docs(review): Claude independent verification -- enterprise candidate ACHIEVED
  b55eb01 feat(cohort): prove Deficit C live failover to openai/gpt-4o, re-verify A + D1, achieve enterprise candidate

Key code changes to audit:
  - orchestrator/browser_daemon.py (new, ~224 lines) — headless Chrome CDP daemon
  - orchestrator/execution.py (modified) — ActiveBrowserDaemon wiring + BROWSER_CDP_URL injection
  - orchestrator/s3_audit_replication.py (new, ~441 lines) — S3 Object Lock WORM backend
  - orchestrator/audit_replication.py (modified, +22) — s3 backend delegation
  - orchestrator/deliverable_preflight.py (modified) — pre-submit citation linter (check_citation_metadata, anti-speculation)
  - orchestrator/task_runner.py (modified) — pass_criteria wired into run_preflight (line ~569)
  - tests/test_browser_daemon.py (new, 11 tests), tests/test_s3_audit_replication.py (new, 6 tests),
    tests/test_deliverable_preflight.py (expanded 28->33 tests)

## What Claude claims is verified (do NOT take Claude's word — re-verify against the repo)

1. Deficit A (3-identity boundary): CLOSED live. AGI_AuditSigner service runs as .\AGI_Signer
   (not LocalSystem). Check: Get-CimInstance Win32_Service -Filter "Name='AGI_AuditSigner'".
2. Deficit D1 (probe-backed attestation): CLOSED live. .harness/egress_attestation.signed is
   base64 JSON; evidence = [deny_direct_egress, broker_only_egress, restricted_worker_identity],
   probes_skipped = [], no unrun labels (raw_socket_bypass_test/private_address_test absent).
3. Deficit C (cross-provider failover): CLOSED. config/models.yaml fallback_chain is ollama-glm
   -> ollama-kimi -> anthropic -> openai/gpt-4o -> local qwen. credman AGI_like/openai present;
   AGI_like/anthropic absent (chain skips it on auth-fail, execution.py:497-504, continues to
   openai). Proven via induced-429 canary in workspace/validation/failover_live_canary.py.
4. M2 (Chromium browser): UNBLOCKED, stable. browser_daemon.py runs Chrome host-side (not the
   restricted S-1-5-12 worker token), so ProcessSingleton mutex acquires. The CDP consumer is in
   Hermes (browser_tool_cdp.py), NOT in this repo — only the BROWSER_CDP_URL setter exists here
   (execution.py:174). Verify: ledger tasks 176 + 178 both done/pass with JS-rendered content
   (live countdown timers differ between the two runs — proves fresh renders, not copies).
5. Deficit B (off-machine WORM): DEFERRED by operator decision. S3 backend is real boto3
   (s3_audit_replication.py imports boto3, put_object with ObjectLockMode=COMPLIANCE); delegation
   at audit_replication.py:344-348 bypasses the UNC-only _replica_root blocker. Tests are
   hermetic (MockS3Client). Do NOT enable HARNESS_AUDIT_BACKEND=s3 / HARNESS_AUDIT_ENFORCE=1
   without a real Object-Lock bucket.
6. Full M1-M7 cohort yield: 4/7 (57.1%), Tasks 177-183, one controlled window, 843s/310k tokens.
   3 fails (M1 missing citation dates, M5 fabricated un-fetched flowgpt.com claim, M7 ungrounded
   "Bootstrapped" claim) — all worker content-discipline, zero infra fails. Verify against
   ledger/ledger.db tasks table.
7. Pre-submit citation linter landed (1918133): check_citation_metadata in deliverable_preflight.py
   enforces (URL + retrieval date + confidence) triples; anti-speculation catches "Bootstrapped"
   /"Unknown"/empty cells and mandates "not publicly disclosed"; pass_criteria wired into
   run_preflight at task_runner.py:569. Gate 79/79 green. UNTESTED on live traffic.

## What to audit independently — find what Claude/Gemini missed

1. CODE QUALITY of the new modules (browser_daemon.py, s3_audit_replication.py, deliverable_preflight.py
   linter additions). Are there correctness bugs, resource leaks (daemon process not cleaned up on
   exception paths?), race conditions, or error categories mis-mapped? Is the daemon's __exit__/__del__
   cleanup robust if start() raises mid-launch? Does is_cdp_ready's bare except swallow real errors?
   Does the linter's regex (confidence/date detection) have false positives or negatives on real
   deliverable text? Does the anti-speculation check false-positive on legitimate "Unknown" mentions?
2. SECURITY: s3_audit_replication.py — does it ever log credentials? Is the ObjectLockRetainUntilDate
   computed safely (clock skew, past dates)? Does the broker integration actually block direct
   egress now that BROWSER_CDP_URL injects a 127.0.0.1:9222 loopback URL into the worker env — is
   there a bypass path via the CDP port? Does the linter create any injection risk via regex on
   untrusted deliverable text?
3. HONESTY of the yield. Re-query ledger/ledger.db for tasks 177-183. Are the 3 "fail" rows really
   content fails, or were any quota/infra fails relabeled? Are the 4 "pass" rows really passes, or
   did the critic grade loosely? Check critic_notes for each.
4. The failover canary (failover_live_canary.py) — is the mock faithful? It monkeypatches
   execution.provider_transport.chat to inject a 429 only for ollama rungs; anthropic+openai call
   through real_chat. Is this a fair proof, or does it mask a real bug (e.g., what if openai
   returned 429 too — does the chain handle a secondary that 429s, or infinite-loop)?
5. Anything overclaimed. Claude caught Gemini overclaiming 3x (wrong pass counts, "quota cascade",
   "deferred to next 429") then 2x honest. Is there a residual overclaim in the current docs
   (CURRENT_STATE.md, the handoff, the cohort report)? Specifically: is "enterprise candidate"
   honestly scoped, or does it imply more than A+D1+C proven?
6. The kernel-ceiling claim. The repo repeatedly says "single-host/single-NT-kernel/Windows-native
   (Level 2), not Level 3." Is that framing accurate, and is M2/A/D1 correctly described as progress
   WITHIN that ceiling, not a jump past it?
7. The linter (1918133) is UNTESTED on live traffic — only hermetic tests. Does the linter's logic
   actually catch the 3 fail modes it targets (M1/M5/M7) on real deliverable text, or only on the
   synthetic test fixtures? Flag whether a live validation run is needed before trusting it.

## Deliverable I want from you

1. Independent verification table: for each of A/D1/C/M2/B/cohort-yield/linter, your verdict
   (CONFIRMED / PARTIAL / OVERCLAIMED / REFUTED) with the single most decisive piece of evidence
   you observed (command output, file:line, ledger row).
2. Bug/risk list from the code audit (browser_daemon.py + s3_audit_replication.py + deliverable_preflight.py
   linter), ranked by severity, each with a concrete failure scenario.
3. One-paragraph verdict on overall honesty: did Claude's "gate" hold, or did something slip
   through that neither Claude nor Gemini caught?
4. Next steps, ranked by leverage, with a one-line rationale each. Consider: (a) live validation of
   the citation linter (run a cohort to prove it raises yield), (b) point the harness at real
   venture work, (c) close B with a real bucket, (d) natural-429 corroboration of C, (e) anything
   your audit found that's more urgent. Give a single recommendation for what to do FIRST and why.

Constraints to respect (do not propose violating):
- ESTOP stays engaged between controlled windows; --controlled-window is operator-only.
- Critic stays unrestricted on byteplus_coding (containing it recreates a blind-critic hole).
- BytePlus is the critic's provider and is deliberately NOT in the worker fallback chain
  (models.yaml:17-19); do not add it.
- Do NOT enable HARNESS_AUDIT_ENFORCE=1 / HARNESS_AUDIT_BACKEND=s3 without a real off-host target.
- Do NOT unlock OmniRoute (4 locked conditions).
- Credentials are in Windows Credential Manager (AGI_like/<provider>), never committed, never
  printed. Read presence via orchestrator/secrets.py credential_manager_has_api_key().
- Test gate: python -B tests/run_all.py must stay green (currently 79/79). Count is dynamic —
  read from output, never hardcode.

Be blunt. If something is overclaimed or broken, say so with evidence. The point of this review
is to catch what two agents (Gemini + Claude) both missed.
