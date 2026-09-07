# G5 F134 Follow-up Review — The Un-Attempted (UNREACHABLE) Branch

**Reviewer:** Claude Code (independent final review, 2026-09-07)
**Target:** F134 (commit `1909502`, `orchestrator/citecheck.py`, `deliverable_preflight.py`, `evaluation.py`)
**Git HEAD at review:** `1909502` (**== `origin/master`, pushed**; working tree clean)
**Task ID:** G5-F135-UNREACHABLE-REVIEW-2026-09-07
**Task Status:** REVIEW COMPLETE — F134 landing **ACCEPTED**; **Path 2 greenlight CONDITIONAL on F135**

> F134 (abuse bounds + fabrication guard + candidate logging) is landed, committed,
> pushed, and 76/76 green. The core POLICY_DENIED protection is sound and tested. This
> review found **one adjacent gap** in the Un-Attempted (`UNREACHABLE`) branch that
> partially recreates the Option A blind-critic fabrication hole for the un-attempted-
> citation case. It must be closed (F135) before Path 2 deploys. F135 is small and
> localized to `citecheck.py`.

---

## 0. Verified this session (measured against disk, not trusted from any doc)

| Item | State | How verified |
| :--- | :--- | :--- |
| F134 committed & pushed | **Yes** — `1909502` == `origin/master`, tree clean | `git log`, `git status`, `git rev-parse` |
| Gate | **76/76 green, exit 0** | `python -B tests/run_all.py` (run this session) |
| Abuse bounds (≤25% / ≤2 abs / min 2 OK) | **PASS** — boundaries use `>`/`<` so at-threshold passes | `citecheck.py:692-713`; 4 regressions in `test_deliverable_preflight.py` |
| Fabrication guard on POLICY_DENIED (conf-3 / quotes) | **PASS** — fires only on POLICY_DENIED; conf-3 regex + table cell; double/curly quotes + blockquote | `citecheck.py:742-801`; 3 regressions |
| Gap 1 — attestation snapshot, not live file | **HELD** — `snapshot_egress_policy()` written into `worker.usage.json` at dispatch + re-injected on all paths | `task_runner.py:400-476` |
| Gap 2 — broker `host=` deny logging | **HELD** — deny/bytes/method paths all pass `host=extracted_host`; per-attempt `broker.audit.jsonl`; un-attempted → `UNREACHABLE` (tested) | `egress_broker.py:205-283`; `test_citecheck.py:273-280` |

---

## 1. The finding — the Un-Attempted branch is mislabeled and un-guarded

When a cited URL is **reachable on host** (critic gets 200) but the worker was **not
policy-permitted** AND **never attempted it via the broker** (no deny record),
`classify_citation` correctly returns `UNREACHABLE` (`citecheck.py:439`). That part is
right. Two things then go wrong:

### Finding 1 (clear bug): `evidence_block()` mislabels `UNREACHABLE` as `"OK"`

`citecheck.py:670-689` drives the critic-facing label off `reachable`, not `classification`:

```python
classification = e.get("classification")
if classification == CLASSIFICATION_POLICY_DENIED:
    status = "POLICY_DENIED (verified live on host; blocked by worker egress policy)"
elif e.get("reachable"):          # <-- line 680: UNREACHABLE has reachable_on_host=True
    status = "OK"                  # <-- an UNREACHABLE citation is shown to the critic as "OK"
elif is_dead(e):
    ...
```

Because `UNREACHABLE` has `reachable_on_host=True`, it falls into the `elif e.get("reachable")`
branch and is presented to the critic LLM as **`"OK"`** — a verified source. The `literal_found`
line is also appended (`e.get("reachable")` is True), further reinforcing the false-OK signal.

**No test catches this.** `test_citecheck.py:309-311` builds `evidence_block` from a result set
that *includes* an `UNREACHABLE` citation (`res_b[0]`) but only asserts the POLICY_DENIED label
is present — it never asserts what the UNREACHABLE citation is labeled as. The mislabel passes
silently.

### Finding 2 (fabrication hole): `detect_fabrication` ignores `UNREACHABLE`

`citecheck.py:756` filters to `POLICY_DENIED` only:

```python
if cls_name != CLASSIFICATION_POLICY_DENIED:
    continue
```

So a worker that asserts `confidence: 3` or a verbatim quote (`"..."`) on a URL it **never
fetched** (no broker deny record → `UNREACHABLE`) is **not** mechanically caught. The POLICY_DENIED
guard's premise — "the worker claimed to read a page it was blocked from receiving" — applies
equally, and more sharply, to the un-attempted case: the worker never loaded the page at all, so
any conf-3 or verbatim quote is unsupported by fetched content.

### Finding 3 (abuse-bound gap): bounds don't see `UNREACHABLE`

`check_abuse_bounds` (`citecheck.py:704`) gates everything behind `if policy_denied > 0`. A
deliverable whose non-OK citations are all `UNREACHABLE` (worker cited a batch of search-snippet
URLs without fetching any) passes the abuse bounds trivially — `policy_denied == 0` → return
`(True, None)`. The `insufficient_verified_sources` (min-2-OK) invariant never fires.

> **Pre-existing spec deviation (note, not mandated by F135):** the Rev 2.0 proposal §3.2 states
> the min-2-OK invariant as **unconditional** ("Every research deliverable MUST contain at least
> 2 OK citations"). The implementation gates it behind `policy_denied > 0`, which is narrower
> than spec'd. F135 should at minimum extend the gate to cover `UNREACHABLE`; whether to make it
> fully unconditional is the implementer's call (false-fail risk on short deliverables not yet
> cohort-validated).

### Failure scenario (why this matters)

A lazy or corner-cutting worker — **exactly the threat model Gap 3 targets** — cites non-allowlisted
URLs found in search snippets without fetching them (no broker deny records). It asserts
`confidence: 3` or wraps a fabricated quote in `"..."`. Today: critic fetches directly → 200 →
`UNREACHABLE` → `evidence_block` shows **"OK"** → fabrication guard does not fire → abuse bounds
skipped → critic LLM sees a "verified OK" citation (with at most advisory `literal_found=False`)
→ deliverable can pass with no hard fail. This **partially recreates the Option A blind-critic
fabrication hole** that Option B+ exists to close, and it matters precisely because the weak-AI
strategy trusts mechanical gates *over* the LLM critic.

**Severity: medium.** The core POLICY_DENIED protection (worker-attempted-and-lied, the harder and
more common case) is solid and tested. The gap is the adjacent un-attempted branch — which is
itself the adversarial/lazy-worker surface.

---

## 2. F135 fix specification

Owner: **Gemini** (primary implementer). Claude stays read-only; re-verifies when F135 lands.
Scope: `orchestrator/citecheck.py` + `tests/test_citecheck.py` (+ `test_deliverable_preflight.py`
if Fix 3's regressions land there). **No Path 2 / deployment changes.**

### Fix 1 (REQUIRED — clear bug, zero false-fail risk): `evidence_block` labels driven by classification

Drive the critic-facing label off `classification` exclusively, not off `reachable`. Add an
explicit `UNREACHABLE` branch **before** any `reachable`/`OK` fallthrough:

```python
classification = e.get("classification")
if classification == CLASSIFICATION_POLICY_DENIED:
    status = "POLICY_DENIED (verified live on host; blocked by worker egress policy)"
elif classification == CLASSIFICATION_UNREACHABLE:
    status = "UNVERIFIABLE (reachable on host, but worker never attempted via broker; no policy-denial relief)"
elif classification == CLASSIFICATION_OK:
    status = "OK"
elif is_dead(e):
    status = f"DEAD ({e.get('http_status') or e.get('error')})"
else:
    status = f"BLOCKED ({e.get('http_status') or e.get('error')})"
```

Also gate the `literal_found` append on `classification == CLASSIFICATION_OK` (a literal "found
on page" line is misleading for an UNREACHABLE citation the worker never read). Legacy dict
evidence without a `classification` field (`e.get("classification")` → None) falls through to
`is_dead` / `BLOCKED` unchanged — backward compatible.

### Fix 2 (REQUIRED — closes the fabrication hole): extend `detect_fabrication` to `UNREACHABLE`

In `citecheck.py:756`, inspect `UNREACHABLE` citations in addition to `POLICY_DENIED`:

```python
if cls_name not in (CLASSIFICATION_POLICY_DENIED, CLASSIFICATION_UNREACHABLE):
    continue
```

Use a distinct reason tag so the operator/critic can tell the two apart. Suggested:
- `POLICY_DENIED` + conf-3/quote → reasons include `"policy_denied_conf3"` / `"policy_denied_quote"`
- `UNREACHABLE` + conf-3/quote → reasons include `"unattempted_conf3"` / `"unattempted_quote"`

Semantics (state in the docstring): a verbatim quote on an `UNREACHABLE` URL is a **provable
fabrication** (the worker cannot have a quote from a page it never fetched). A `confidence: 3`
on an `UNREACHABLE` URL is an **overclaim** that violates the worker's own instruction ("If a
source URL returned 403 or was unreachable ... mark facts as confidence 1"). Both are mechanically
detectable; both should hard-fail.

Preserve the existing behavior: `UNREACHABLE` at `confidence: 1` with no verbatim quote **passes**
the guard (consistent with the POLICY_DENIED conf-1/unquoted carve-out — the worker honestly
reported a non-fetched source at low confidence).

### Fix 3 (RECOMMENDED — implementer's call on the threshold): abuse-bound coverage of `UNREACHABLE`

Extend the min-2-OK grounding invariant to fire whenever **any** non-OK citation is present, not
only when `policy_denied > 0`:

```python
non_ok = policy_denied + unreachable
if non_ok > 0 and ok < MIN_OK_CITATIONS:
    return False, f"insufficient_verified_sources: found {ok} OK citations, minimum {MIN_OK_CITATIONS} required"
```

**Do NOT** fold `UNREACHABLE` into the 25% / ≤2-absolute POLICY_DENIED ceiling — that ceiling is
scoped to policy-relief claims; `UNREACHABLE` is a different category (un-attempted, not
policy-blocked) and bounding it by the same fraction risks false-fails on legitimate hard-research
targets without cohort validation. If a separate `UNREACHABLE` ceiling is wanted later, propose it
with cohort data; do not add it in F135.

If the implementer judges that even Fix-3's min-2-OK extension false-fails on real cohort
deliverables, **surface that with evidence** rather than silently dropping it — the spec deviation
(§1 note) should be resolved deliberately, not by omission.

### Tests (REQUIRED — these are the regressions that would have caught Finding 1 + 2)

Add to `tests/test_citecheck.py` (or `test_deliverable_preflight.py` where the preflight-level
assertions already live):

1. `evidence_block` labels an `UNREACHABLE` citation as `"UNVERIFIABLE"`, **not** `"OK"` (the
   exact regression for Finding 1 — the existing `:309-311` test silently passed the mislabel).
2. `detect_fabrication` fires on `confidence: 3` on an `UNREACHABLE` citation → mechanical FAIL
   (deliverable_preflight + evaluation paths).
3. `detect_fabrication` fires on a verbatim quote on an `UNREACHABLE` citation → mechanical FAIL.
4. `detect_fabrication` does **not** fire on an `UNREACHABLE` citation at `confidence: 1` with no
   quote (carve-out parity with POLICY_DENIED).
5. (If Fix 3 adopted) `check_abuse_bounds`: 1 OK + 1 UNREACHABLE →
   `insufficient_verified_sources`; 2 OK + 1 UNREACHABLE → passes.

Gate must reach its next count green (read the count from `tests/run_all.py` output — never match
a hardcoded number).

---

## 3. Path 2 greenlight condition

**Path 2 (three-identity deployment) remains BLOCKED until F135 lands + Claude re-verifies.**

When F135 commits, bring the commit SHA + test output to Claude. Claude re-verifies against the
**committed** tree (not a dirty tree):
1. Does `evidence_block` label `UNREACHABLE` as `UNVERIFIABLE` (not `OK`)?
2. Does `detect_fabrication` fire on conf-3 / verbatim quotes for `UNREACHABLE` citations?
3. Does the min-2-OK invariant cover `UNREACHABLE` (if Fix 3 adopted)?
4. Gate green at the new count, zero `[FAIL]`.

If yes → **greenlight Path 2.** Critic still runs as `AGI_Controller` (unrestricted) — preserve this.

---

## 4. Hard invariants (unchanged, still in force)

- **ESTOP engaged** between controlled windows. F135 is model-free; safe under ESTOP.
- **Weak-AI strategy LOCKED** — preflight is mechanical, never a second LLM judge.
- **citecheck is the authoritative floor** — preflight rescues, never relaxes citecheck.
- **Critic stays unrestricted** — containing the critic recreates Option A's blind-critic hole.
- **Single write scope** — Gemini claims F135 in `ACTIVE_WORK.json`; Claude read-only.
- **Gate green with zero `[FAIL]` before handoff** — count is dynamic, read from `tests/run_all.py`.
- **Phase order is FIXED** — F132 → F133 → F134 (done) → F135 → Path 2. Do not reorder.

## 5. Do-not-do directives

- Do NOT start Path 2 deployment until F135 lands + Claude greenlights.
- Do NOT fold `UNREACHABLE` into the POLICY_DENIED 25%/≤2 ceiling (different category; false-fail risk).
- Do NOT make `evidence_block` label `UNREACHABLE` as `"OK"` (that is the bug).
- Do NOT contain the critic — unrestricted critic is the ground-truth vantage.
- Do NOT re-open Option A.
