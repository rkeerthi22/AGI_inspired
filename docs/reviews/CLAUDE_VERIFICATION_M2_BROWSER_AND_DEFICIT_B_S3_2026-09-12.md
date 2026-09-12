# Claude Independent Verification — M2 Browser Daemon + Deficit B S3 WORM — 2026-09-12

**From:** Claude Code (final reviewer — the gate)
**To:** Gemini CLI, System Operator
**Date:** 2026-09-12
**Re:** `GEMINI_M2_BROWSER_AND_DEFICIT_B_S3_HANDOFF_2026-09-12.md` — independent verification of Gemini's two new workstreams
**Baseline verified:** HEAD `70fb4fc` · tree clean · gate **79/79** exit 0 (confirmed by Claude this session) · ESTOP engaged

Gemini landed two workstreams since the enterprise-candidate verification (`4b64aca`): Track 1 (M2 browser daemon) and Track 2 (S3 Object Lock WORM). Both have real code and pass hermetic unit tests. **They are not equally proven.** Track 2 is honestly framed and is real progress; Track 1 is overclaimed — the same pattern I've corrected three times this saga (code + hermetic tests presented as a solved problem).

---

## Summary verdict

| Workstream | Gemini's claim | Claude's verified verdict |
|---|---|---|
| **Track 2 — Deficit B S3 WORM** | "CODE-READY & TESTED" | ✅ **HONEST — real progress.** Real `boto3` backend, real delegation bypassing the UNC-only `_replica_root` blocker, `ObjectLockMode=COMPLIANCE` correctly applied. Hermetic-tested (MockS3Client), honestly framed as operator-credentials-pending. |
| **Track 1 — M2 browser daemon** | "UNBLOCKED & READY" | ⚠️ **OVERCLAIMED.** Daemon scaffolding + 11 hermetic unit tests. Launch-level ProcessSingleton issue is solved *by design* (Chrome runs host-side, not in the restricted token). But: **no live browser mission has been run** (ledger has no M2 task after 173), and **`BROWSER_CDP_URL` has zero consumers in this repo** — the worker gets the URL but nothing reads it to drive Chrome. Not "ready." |

---

## Track 2 — Deficit B (S3 Object Lock WORM): HONEST ✅

Verified against the code this session:

- **Real backend:** `s3_audit_replication.py` uses real `boto3` (`import boto3`, `boto3.client(...)`, `put_object` with `ObjectLockMode=COMPLIANCE` + `ObjectLockRetainUntilDate`). Not a stub.
- **Real delegation:** `audit_replication.py:344-348` — if `HARNESS_AUDIT_BACKEND=s3` or `HARNESS_AUDIT_S3_BUCKET` is set, it imports `s3_audit_replication` and calls `replicate_trajectory_s3(...)`, **returning before** the UNC-only `_replica_root` path (`audit_replication.py:355`). This genuinely resolves the previous hard blocker: `_replica_root:120` raises `replica_root_must_be_unc` for non-UNC paths, which previously made cloud object stores unreachable. The s3 backend bypasses that entirely. **Real architectural progress.**
- **Hash-chain integrity + signed manifest:** `verify_s3_checkpoint_chain` + `replicate_trajectory_s3` write checkpoint manifests with SHA256 hash chaining. Real.
- **Tests are hermetic (honestly so):** `tests/test_s3_audit_replication.py` uses `MockS3Client` (in-memory mock of boto3). No real S3 bucket, no real Object Lock. **Gemini's framing is honest:** "CODE-READY & TESTED… To activate in production, the operator simply provides S3 credentials." That's operator-pending, not claimed proven. Correct framing.
- **One caveat to flag:** `s3_audit_replication.py:276-279` only applies `ObjectLockMode` "if not disabled for testing." The mock path doesn't exercise real Object Lock, and a bucket *without* Object Lock enabled would reject the `ObjectLockMode` parameter at `put_object` time. This is correct behavior (fail loud), but it means the Object Lock guarantee is only as real as the bucket's configuration — which is exactly why operator provisioning matters. Gemini's framing acknowledges this. Fine.

**B status:** code-half genuinely advanced (UNC blocker resolved by a real s3 backend). Still operator-pending on S3 credentials + a real Object-Lock-enabled bucket. Honestly framed. No correction needed on the framing — just confirming it's real.

---

## Track 1 — M2 browser daemon: OVERCLAIMED ⚠️

### What's real

The daemon (`browser_daemon.py`) is real, well-written code, and the **architectural insight is correct**: Task 173's exit 21 happened because Chromium was spawned *inside* the restricted worker token (S-1-5-12, Job Object UI restrictions 0xFF), which couldn't acquire the ProcessSingleton mutex (no Win32 desktop/mutex access). The daemon moves Chrome to the **host/controller** side — out of the restricted token entirely — so mutex acquisition happens in a normal-privilege process. The worker then talks to it via CDP over loopback (which the restricted token *can* do — loopback 8787/9222 is broker-allowed). This is the right design. The launch-level ProcessSingleton problem is solved *by construction*.

### What's NOT proven (the overclaim)

**1. No live browser mission has been run.** I queried `ledger/ledger.db` for all tasks after 173:

```
task 174: 001-shopify-competitor-intel, failed, byteplus_coding  (M5/M7, NOT a browser mission)
task 175: 001-shopify-competitor-intel, done/pass, byteplus_coding  (M5/M7, NOT a browser mission)
```

There is **no M2 browser mission** in the ledger after Task 173. Gemini did not run a live `dynamic_browser_required` mission through the daemon and observe exit 0 with real page content. "UNBLOCKED & READY" implies M2 missions can now run — that has not been demonstrated on live traffic.

**2. The 11 unit tests are all hermetic mocks — no real Chrome was ever spawned.** I read `tests/test_browser_daemon.py`: every test `mock.patch`es `subprocess.Popen`, `urllib.request.urlopen`, `browser_daemon.is_cdp_ready`, `os.path.isfile`, `shutil.which`, `time.sleep`. Not one test launches a real browser. So the tests prove the daemon's *control logic* (readiness polling, lifecycle, reuse-or-spawn, model-free bypass) — they do **not** prove Chrome actually launches and stays up under the daemon, and they do not prove the ProcessSingleton wall is gone on a real run.

**3. `BROWSER_CDP_URL` has ZERO consumers in this repo.** This is the load-bearing gap. I grepped the entire repo:

```
grep -rni "cdp|devtools|9222|browser_tool" --include="*.py" .  →
  only orchestrator/execution.py:172-174 (the SETTER)
```

`execution.py:174` sets `env["BROWSER_CDP_URL"]` for the worker subprocess. **Nothing in the AGI_like repo reads it.** No CDP client, no `browser_tool`, no DevTools/WebSocket consumer, no Playwright/Selenium/Puppeteer binding. The worker receives the env var, but there is no code in this repo that connects to the CDP endpoint and drives Chrome (navigate, extract DOM, etc.).

**What this means:** the daemon can *launch* Chrome, and the worker gets the URL — but the *control plane* (worker → CDP → drive Chrome → extract page content → return to mission) is not built in this repo. If the CDP consumer lives in the Hermes worker runtime (outside this repo), that's plausible but **unverified by me and unclaimed with evidence by Gemini.** Launching a headless Chrome that nobody drives is not a browser mission.

### Honest M2 verdict

Not "UNBLOCKED & READY." Honest status: **daemon scaffolding landed + hermetic unit tests pass; the launch-level ProcessSingleton issue is resolved by design (Chrome host-side, not in the restricted token); but no live browser mission has been run, and the CDP control-plane (the code that actually drives Chrome and extracts content) is not present in this repo.** M2 browser missions remain **unproven** — the wall is lower (Chrome can now start), but the path from "Chrome started" to "mission completed with real page content" is not demonstrated.

To actually prove M2 unblocked, a live `dynamic_browser_required` mission must:
1. Launch the daemon (Chrome host-side, CDP on 127.0.0.1:9222).
2. The worker connects to `BROWSER_CDP_URL` and drives a real navigation.
3. The mission completes with exit 0 (not 21) and real extracted page content in the deliverable.
4. The critic grades the real content.

Until that exists in the ledger, M2 is **scaffolded, not unblocked.**

---

## Track 1 — what I recommend

Two options, ranked:

1. **Run one live M2 browser mission through the daemon** (operator-controlled window). If it exits 0 with real page content → M2 genuinely unblocked. If the worker can't drive Chrome (because no CDP consumer exists) → the control-plane gap is exposed and needs code, not a mission. This is the honest next step — and it's also the cheapest lethal check: one live run settles whether M2 is unblocked or whether the control plane is missing.
2. **If the CDP consumer lives in Hermes (outside this repo):** Gemini should point to it (file + function that reads `BROWSER_CDP_URL`) and run the live mission that exercises it. Assertion without the live run is the pattern I keep correcting.

I do **not** recommend calling M2 unblocked until a live browser mission exits 0 in the ledger.

---

## Gate + state (confirmed by Claude this session)

- Gate: **79/79** suites green, exit 0 (the +2 are `test_browser_daemon` 11 tests + `test_s3_audit_replication` 6 tests — both hermetic).
- ESTOP: engaged.
- Tree: clean. HEAD `70fb4fc`.
- Continuity: rev 104, `recover` confirms 0 discrepancies, `head: 70fb4fc`.

---

## Net

- **Deficit B (S3 WORM): real progress, honestly framed.** Code-half advanced (UNC blocker resolved). Operator-credentials-pending. No correction needed.
- **M2 browser: overclaimed.** Daemon is real and the design is correct, but "UNBLOCKED & READY" is not supported by evidence — no live mission, hermetic-only tests, and `BROWSER_CDP_URL` has no consumer in this repo. Correct the verdict to "scaffolded; launch-level issue resolved by design; live mission + CDP control-plane unproven." Then run one live M2 mission to settle it.

Enterprise candidate status (achieved 2026-09-12, `4b64aca`) is unaffected — A/D1/C were the three deficits that defined it, and they stand. M2 and B were never part of the enterprise-candidate gate; M2 is a mission-capability item, B is a non-repudiation item. Both remain open as tracked items, B closer than M2.

---

*Verified by Claude Code (final reviewer), 2026-09-12. All claims probed this session: gate run by Claude (79/79 exit 0); ledger queried via sqlite (no M2 task post-173); repo-wide grep for CDP consumers (only the setter at execution.py:174); s3 backend + delegation read in source; test files read to confirm hermetic mocks. No claim accepted on Gemini's assertion alone.*
