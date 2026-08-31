# Hermes Technical Research Report — Munder Integration Blueprint

**Agent:** Hermes (Bounded Technical Research Worker)
**Task ID:** MUNDER-RESEARCH-HERMES
**Assigned by:** Gemini CLI (Independent Principal Architect / Reviewer) via `docs/MUNDER_INTEGRATION.md` §12.2 and `docs/HANDOFF_PROTOCOL.md` §5
**Date:** 2026-08-31
**Status:** COMPLETE
**Safety state:** ESTOP engaged throughout. No live provider calls, no mission execution, no integration code written. Research scripts ran from `%LOCALAPPDATA%\Temp` (outside the repository) against disposable Temp/MunderState-scratch paths and were deleted; only their recorded results are reported.

---

## 0. Evidence Labels (no-fabrication discipline)

- **[MEASURED]** — empirically verified on THIS machine, 2026-08-31, by running a Python script (`%LOCALAPPDATA%\Temp\hermes_munder_research*.py`, since cleaned up). Raw results preserved verbatim in Appendix A.
- **[DOCUMENTED]** — authoritative upstream documentation (Microsoft Learn, Python docs/issue tracker, library docs). Source named per claim.
- **[DESIGN]** — engineering judgment for the implementation spec. Unproven until an implementation test exists.

Environment measured: Python 3.11.15, SQLite 3.53.1 (bundled with this interpreter), Windows 11, C: = NTFS, S: = NTFS (both local volumes, `Get-Volume` verified).

---

## 1. Research Question 1 — Windows Filesystem Notification vs. Polling (Gemini §12.2 Q1)

### 1.1 Local facts [MEASURED]

| Fact | Result |
|---|---|
| `watchdog` importable in this interpreter | YES — `Observer=watchdog.observers.read_directory_changes`, distribution version **6.0.0** (pip metadata). (`watchdog.__version__` attribute is absent in v6 — harmless, but code must use `importlib.metadata.version("watchdog")`, not the attribute.) |
| `os.scandir` cost, 100 files, warm cache | **0.261 ms** per full scan (name + `st_mtime` via `DirEntry.stat`) |
| `os.scandir` cost, 1000 files, warm cache | **1.146 ms** per full scan |

### 1.2 Documented behavior of ReadDirectoryChangesW [DOCUMENTED]

- **Buffer overflow silently discards events.** Microsoft Learn (ReadDirectoryChangesW, winbase.h): if the internal buffer overflows, the entire contents are discarded, `lpBytesReturned` is zero, and the function fails with **ERROR_NOTIFY_ENUM_DIR**; the required recovery is a full directory re-enumeration.
- **Burst I/O is the trigger.** SO #77502002 / Microsoft Q&A (both Nov 2023): a bulk copy of ~800 files into a watched folder overflowed the buffer and stopped notification processing entirely.
- **Network filesystems (CIFS/SMB) are unreliable** for ReadDirectoryChangesW; watchdog's own docs require explicitly selecting `PollingObserver` for CIFS shares.
- **Moved files** (old-name deletion + new-name creation pairs) can be missed or delivered out of order — SO #49799109.
- **Watchdog v6 Windows observer** is the `ReadDirectoryChangesW`-based observer (verified import path above). Watchdog does not expose ERROR_NOTIFY_ENUM_DIR recovery to the application; a burst that overflows the kernel buffer surfaces as lost or scrambled events, not an exception.

### 1.3 Recommendation [DESIGN]

**Use deterministic 500ms `os.scandir` polling. Do not use `watchdog`.**

Rationale (all grounded in the above):

1. **The mailbox scale is tiny and the poll is nearly free.** Measured 1.146 ms per 1000-file scan. At 500ms cadence with ≤1000 pending messages, polling consumes ≈0.2% of one core. Even a 10× larger mailbox tree stays negligible. The event-driven advantage of watchdog exists to avoid exactly this cost — and the cost is already trivial.
2. **Polling cannot lose events by design.** Every scan is a full enumeration; a message present in the outbox is seen on the next scan, period. Watchdog's failure mode is silent loss under burst I/O (ERROR_NOTIFY_ENUM_DIR discards the buffer), and mailbox routers run precisely in burst conditions (batch completion, `.done/` archival sweeps, git operations touching the tree).
3. **At-least-once semantics are the architectural requirement anyway.** The blueprint already mandates idempotent handlers keyed on message id (E1/E3 invariants I2-I3) — a re-scan seeing the same file twice is harmless, whereas a lost notification is unrecoverable without a full re-enumeration. Polling *is* the re-enumeration, every cycle.
4. **Polling needs no extra dependency, no observer thread, no handle management.** Watchdog adds a background thread + directory handles whose lifecycle must be managed (including under ESTOP-fail-closed), for zero benefit at this scale.
5. **Run under runlock discipline** as Gemini specified: route only from the lock-holding harness process; the poller never runs concurrently with a lock-waiting second process.
6. If a future scale requires event-driven I/O, keep watchdog behind an interface with an ERROR_NOTIFY_ENUM_DIR recovery sweep (full re-enumeration on any notification anomaly) — but the measured numbers show this is years away from being needed.

### 1.4 Router poll cycle (concrete) [DESIGN]

```
every 500ms (only while holding runlock):
  for each agent outbox:            # single-writer: only owner writes
    for each *.json newer than last-seen high-water mark:
      parse (skip + health-event on unparseable)
      classify act: terminal (inform/done) vs. reply-expectant (request/query/propose)
      deliver copy+fsync+unlink (Section 2), append router.log.jsonl
  reconcile backlogs (E6) and hop caps (E8)
```

The high-water mark (name-sort order + mtime) makes each poll O(new messages), not O(total), at the cost of a full `scandir` enumeration — the measured cost is the enumeration itself.

---

## 1.5 Second-order finding — watchdog import details [MEASURED]

- `import watchdog` works; `watchdog.observers.Observer` resolves to `watchdog.observers.read_directory_changes` on this machine. Distribution metadata reports **6.0.0**.
- Pitfall for any future code: do not gate feature-detection on `watchdog.__version__` (absent in v6); use `importlib.metadata.version("watchdog")`.

---

## 2. Research Question — `os.replace` Atomicity and EXDEV on Windows (Operator directive)

### 2.1 Measured error matrix [MEASURED]

| Scenario | Result (verbatim from Appendix A) |
|---|---|
| Same-volume `os.replace(src, existing_dst)` | **OK** — atomic replace over existing destination succeeds |
| Cross-volume (C: → S:) `os.replace` | **`OSError` winerror=17 (`ERROR_NOT_SAME_DEVICE`), errno=18 (`EXDEV`)** — strerror: "The system cannot move the file to a different disk drive" |
| Same-volume replace over **destination held open by a reader** | **`PermissionError` winerror=5 (`ERROR_ACCESS_DENIED`)** |
| Same-volume rename with **source held open** | **`os.rename` failed with winerror=32 (`ERROR_SHARING_VIOLATION`)** |
| Retry after closing the reader handle | **Succeeds immediately** |
| `os.unlink` of a file held open by a reader | **winerror=32 sharing violation** |

### 2.2 Interpretation — why this matters for the mailbus [DESIGN]

1. **The EXDEV claim in the blueprint (E2) is confirmed but the practical fix needs refinement.** The blueprint's copy→fsync→unlink fallback is correct for EXDEV, **BUT the measured winerror=32 on unlink proves the fallback is ALSO blocked while any reader holds the source file.** On Windows you cannot delete (or replace) a file that another process has open without share-delete flags. Since our own router is the primary reader (and may be scanning mid-delivery), the safe sequence is:
   1. **Never hard-unlink in the delivery path.** Prefer `os.replace` (same volume) which was measured to succeed over closed handles.
   2. For the copy+unlink fallback: verify destination file presence and byte-length BEFORE unlinking source (the reconciliation sweep from E2); if unlink fails with winerror=32, treat as retryable — requeue the delivery with backoff (the next 500ms poll cycle will retry, so retry cost is one poll cycle).
   2b. Alternatively, open sources for copy with `os.open(..., os.O_RDWR|os.O_SHARE...` — not portable in pure Python; the retry ladder is the portable answer.
   3. **Single-volume rule stands** (Gemini §12.1 directive 2): keep the whole mailbox tree under `S:\AGI_like\mailboxes` (S: NTFS). With single-volume affinity, EXDEV never occurs and copy+unlink is only a degraded fallback, not the primary path.
2. **WinError 5 vs WinError 32 are different failures with the same fix.** AV scanners, Windows Search indexer, or thumbnail providers can transiently hold handles on a just-written JSON file [DESIGN]. The measured retry-after-close succeeded immediately; therefore implement Gemini's specified 3-step backoff (10/50/200ms with jitter) on **winerror ∈ {5, 32}** for both `os.replace` and the unlink leg of any fallback. After the ladder fails, fall back to copy-verify-fsync and leave the source for the next poll cycle (idempotent handlers make this safe).
3. **`os.replace` same-volume semantics are exactly MoveFileExW + MOVEFILE_REPLACE_EXISTING** [DOCUMENTED — Python issue 28356; SO #69363867]: atomic overwrite of the destination. The measured winerror=5/32 exceptions come from open-handle conflicts, not from the API contract.
4. **Do not use `shutil.move`** across volumes — it silently degrades to copy+unlink (non-atomic, and its unlink leg has the same winerror=32 exposure) [DOCUMENTED — Python issue 28356 discussion]. Single-volume `os.replace` is the only atomic primitive we need.
5. **AV/indexer interference is real but transient.** The measured open-reader blocking is deterministic; AV interference is probabilistic and short-lived — the backoff ladder plus at-least-once delivery absorbs it without message loss.

### 2.3 Delivery algorithm (refined, merges Gemini §12.1.2 with measurements) [DESIGN]

```
deliver(msg) to recipient inbox:
  1. tmp = inbox/.tmp/<uuid>.tmp
  2. write bytes; flush; os.fsync(fileno)        # [DOCUMENTED] Gemini: directory-fsync is a no-op on Windows; file-level fsync anchors durability
  3. os.replace(tmp, inbox/<id>.json)            # atomic, same-volume
  4. on OSError winerror in {5, 32}: retry 10ms/50ms/200ms + jitter (3 attempts)
  5. on persistent failure: health_event + leave in .tmp; next poll cycle retries (idempotent)
archive (inbox -> inbox/.done/):
  1. os.replace(msg, .done/<id>.json)            # same-volume atomic move
  2. on winerror in {5, 32}: same retry ladder; failure = benign (at-least-once, idempotent)
```

### 2.4 Answer to Gemini's "handle leak risks" (§12.2 Q1, third bullet) [MEASURED + DESIGN]

Watchdog's observer thread holds open directory handles for the life of the watch — under ESTOP fail-closed or crash, those handles are released only at process death. Polling-based `os.scandir` holds no persistent handles (each call opens/enumerates/closes), which eliminates the handle-leak class entirely — another independent argument for polling, on top of §1.3.

---

## 3. Research Question 2 — SQLite FTS5 Minimal Schema for Markdown Memory (Gemini §12.2 Q2)

### 3.1 Local facts [MEASURED]

| Fact | Result |
|---|---|
| FTS5 available in stdlib `sqlite3` | YES (SQLite 3.53.1; `CREATE VIRTUAL TABLE ... USING fts5` works out of the box, no compile flags, no extensions) |
| **External-content** FTS5 table (`content='chunks'`) | **Supported** — full insert/update/delete via triggers verified end-to-end; integrity counts matched (content=2, fts=2) |
| **Contentless** FTS5 table with `contentless_delete=1` | **Supported** — but deletion syntax is special: `DELETE FROM cd WHERE rowid=...` (plain DELETE) works; the FTS5-docs 'delete' command syntax (INSERT INTO cd(cd, rowid, x) VALUES('delete',...)) is REJECTED with OperationalError on contentless_delete=1 tables |
| Porter + unicode61 tokenizer combo | Works (`tokenize='porter unicode61'`) |
| `bm25()` / `rank` ordering | Works (negative-better ranking observed; sample: `-1.33e-06` beats `-8.03e-07`) |
| `snippet()` highlighting | Works (returns highlighted fragments with configurable markers) |
| Default journal mode | `delete` (rollback journal); **WAL is settable** (`PRAGMA journal_mode=WAL` → "wal") |

### 3.2 The verified schema — external-content FTS5 + trigger sync

**[MEASURED]** — the following exact schema was created and exercised (insert → match → update → re-match → count-integrity check) in the prototype run:

```sql
CREATE TABLE chunks(
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  para_no INTEGER NOT NULL,
  md_path TEXT NOT NULL,      -- provenance anchor, returned in query results
  body TEXT NOT NULL
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  body,                       -- indexed
  agent,                      -- indexed (filterable via column filter: 'agent:hermes')
  md_path UNINDEXED,          -- stored in the index, not tokenized
  content='chunks', content_rowid='id',
  tokenize='porter unicode61'
);

CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, body, agent, md_path)
  VALUES (new.id, new.body, new.agent, new.md_path);
END;

CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, body, agent, md_path)
  VALUES ('delete', old.id, old.body, old.agent, old.md_path);
END;

CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, body, agent, md_path)
  VALUES ('delete', old.id, old.body, old.agent, old.md_path);
  INSERT INTO chunks_fts(rowid, body, agent, md_path)
  VALUES (new.id, new.body, new.agent, new.md_path);
END;
```

Why external-content over contentless [DESIGN]:
- The `chunks` content table stores the paragraph bodies, so the index is **rebuildable** (`INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')` — verified working) — a full reindex from memory.md files is always possible, matching the "markdown is the source of truth, FTS is a derived view" doctrine.
- Contentless tables store nothing; a source-file regeneration requires reparsing anyway, and contentless_delete syntax quirks (measured above) add friction for no gain at this scale.
- UNINDEXED `md_path` rides along in results as the provenance anchor — every search hit points back to its `memory.md` file and paragraph number.

### 3.3 Lock contention answer [MEASURED]

- With a writer holding `BEGIN IMMEDIATE`, a second writer with `timeout=5.0` correctly **blocked 5.51s then raised `OperationalError: database is locked`** — `busy_timeout` works as documented and bounds the wait.
- Default journal mode is `delete`; **WAL is settable on this machine** (`PRAGMA journal_mode=WAL` succeeded).

### 3.4 Contention design for the mailbus/memory indexer [DESIGN]

1. **Single indexer process.** The FTS index is written ONLY by the consolidator (the runlock-holding weekly consolidation pass). Worker processes at large never open the memory DB for write — they append to `memory.md` (a file), and the indexer derives chunks at the next consolidation/scan cycle. This is `DatabaseMutationGuard`-compliant BY CONSTRUCTION: the guard watches `ledger/ledger.db` and `memory/ledgerbook.db` [MEASURED — integrity.py:100 guards `memory/ledgerbook.db`; ledger.py:12 defines `ledger/ledger.db`]; the proposed `memory/agents/.../memory.md` files and their derived FTS index live OUTSIDE those guarded DBs, so the guard is untouched. But per the guard's spirit, only ledger-owning modules touch guarded DBs — the FTS index gets its own DB file (e.g., `memory/fts_index.db`), never the guarded ones.
2. **WAL mode + 5s busy_timeout** for the consolidator's connection [DESIGN, grounded in measurement]: WAL allows the one writer to proceed while readers query; busy_timeout absorbs transient AV/indexer file locks on the DB file itself (same winerror=32 class as Section 2).
3. **The rebuild command is the corruption-recovery path:** if the FTS index and `memory.md` files ever disagree (e.g., a crash between markdown append and index update), `INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')` after truncating `chunks` and re-deriving from the markdown files is the deterministic fix. The markdown remains the source of truth; the index is disposable.
2b. **BM25 weights:** leave default BM25 weighting initially [DESIGN]; tune via `INSERT INTO chunks_fts(chunks_fts, rank) VALUES('rank', 'bm25(10.0, 5.0)')` only after measuring recall misses (per blueprint §4.3 measurement discipline — don't tune on speculation).
4. **Query interface [DESIGN, matching the measured prototype]:**
   ```sql
   SELECT agent, para_no, md_path, snippet(chunks_fts, 0, '[', ']', '…', 8)
   FROM chunks_fts
   WHERE chunks_fts MATCH :query AND agent = :agent
   ORDER BY rank LIMIT 10;
   ```
   No JOIN is required: with external-content FTS5, selecting non-indexed/stored columns (`agent`, `md_path`) reads through to the content table via the shared rowid — this is exactly what the prototype exercised (Appendix A, `fts5_trigger_sync_hits`). `snippet()` was measured working with this shape.
5. **Paragraph chunking:** split `memory.md` on blank lines; chunk = paragraph; `para_no` = ordinal; on reindex, agent + md_path + para_no identify the chunk; content hash column (add `body_sha` TEXT) detects drift between file and index (skip re-write when hash unchanged) [DESIGN].

### 3.5 Contentless-delete pitfall (recorded for posterity) [MEASURED]

The FTS5 documentation syntax for deleting from contentless tables — `INSERT INTO cd(cd, rowid, x) VALUES ('delete', 1, ...)` — is **rejected** on a `contentless_delete=1` table in SQLite 3.53.1: `OperationalError: 'delete' may not be used with contentless_delete=1 table`. Plain `DELETE FROM cd WHERE rowid = 1` is the working syntax. Any implementation notes must record this, or a future agent will "fix" it into the broken form.

---

## 4. Research Question 3 — ConPTY Job Object Teardown (Gemini §12.2 Q3)

### 4.1 Documented behavior [DOCUMENTED]

- **`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`** — Microsoft Learn (Job Objects): closing the last open job handle terminates all processes associated with the job **and its child jobs in the hierarchy**. The flag guarantees cleanup of the entire tree when the owning process dies — even on crash, even if the parent forgets to kill.
- **Nested jobs** — the same Microsoft Learn page: nested jobs (Windows 8+) allow a process to be in more than one job; the KILL_ON_JOB_CLOSE flag on the outer job reaps the whole hierarchy on handle close. Windows 11 supports nested jobs fully.
- **Breakaway caveat** — SO #56163512: a child can escape the job if it is created with `CREATE_BREAKAWAY_FROM_JOB` and the job allows breakaway; py.exe is cited as the classic example (dotnet/runtime #107992). The countermeasure is to **not set JOB_OBJECT_LIMIT_BREAKAWAY_OK** on our job — then breakaway attempts by children fail, and descendants stay contained [DESIGN].
BREAKAWAY caveat detail: py.exe (Windows Python launcher) explicitly creates children outside its job by design; if our daemon chain goes through py.exe, those grandchildren escape. Mitigation: launch daemons via `python.exe` directly, never `py.exe` [DESIGN].
- **pywinpty status [DOCUMENTED]:** maintained (Spyder project), current release 3.0.5 (June 2026), supports native ConPTY (preferred backend on modern Windows) with a winpty fallback; Rust toolchain required only for building from source — pip-installable binary wheels exist. No red flags; viable dependency if PTY daemons are ever built.

### 4.2 Minimal ctypes Job Object implementation [DESIGN]

No third-party dependency needed for the job-object part; the API surface is small:

```python
# kernel32 via ctypes
CreateJobObject(lpJobAttributes=None, lpName=None) -> HANDLE
SetInformationJobObject(hJob, JobObjectExtendedLimitInformation=9, &info, sizeof(info))
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION:
    BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE  (0x2000)
AssignProcessToJobObject(hJob, hProcess) -> BOOL
```

Sequence for each daemon spawn [DESIGN]:
1. `CreateJobObject` → set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
2. Spawn the daemon **suspended** via `subprocess.Popen(..., creationflags=CREATE_SUSPENDED)`.
3. `AssignProcessToJobObject(hJob, hProcess)` BEFORE resuming — assignment must precede first execution or descendants spawned in the gap escape (the py.exe trap).
4. Resume the main thread (`ResumeThread`).
5. Keep `hJob` open for the daemon's lifetime; the harness's death → handle close → kernel reaps the entire tree. ESTOP watchdog: on sentinel detection, `TerminateJobObject(hJob, 75)` (or close the handle — same effect).
6. **ESTOP semantics with job objects [DESIGN]:** engaging ESTOP must not immediately kill healthy daemons (blueprint E21 ladder: steer → constrain → stop). The job handle gives the watchdog a single, PID-reuse-immune kill primitive: `TerminateJobObject` kills every process in the job regardless of PID reuse — the job membership is kernel state, not a PID list. This directly implements E21's layer 4 (identity-verified kill) without needing `process_start_identity` for the kill itself (still use it to find the right daemon's job handle).
7. **ConPTY + job interplay [DESIGN]:** the ConPTY host (`conhost.exe` in ConPTY mode) is spawned by the OS as a child attached to the pseudoconsole — assign it to the job by opening its handle via the `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` spawn attribute chain: in practice, when using pywinpty, spawn through the PTY and then enumerate job membership via `QueryInformationJobObject(JobObjectProcessList=8)` to verify conhost.exe is inside; if a conhost escapes (ConPTY's host is OS-spawned, and behavior differs by Windows build), fall back to `taskkill /T` on the daemon PID with identity verification. This is the one genuinely novel risk area; a time-boxed spike is mandatory before Build 5 implementation (blueprint already gates Build 5 on a spike).

### 4.3 Recommendation summary (Q3) [DESIGN]

- Job Objects via raw ctypes (tiny API surface, stdlib-only, matches harness stdlib-only doctrine) for containment.
- pywinpty (3.0.5, ConPTY backend) if/when Build 5 is authorized — maintained, ConPTY-native, pip-installable wheels.
- Mandatory suspended-spawn + assign-before-resume discipline (breakaway/py.exe trap).
- ESTOP watchdog uses job termination, not PID kills; PID identity (runlock's `process_start_identity` pattern) only to locate the correct job handle.
- Open question for the implementation spike: ConPTY conhost job-assignment reliability across Windows builds — flag as the one research item this report could not close with documentation alone.

---

## 5. Findings that change the blueprint

1. **E2 refinement (mailbus):** copy+unlink fallback is blocked by open readers (winerror=32) — the unlink leg needs the same retry ladder as replace, plus reconciliation sweep before unlink. Single-volume rule remains the primary defense. *(Section 2.2)*
2. **Watchdog recommendation inverted:** blueprint §3.2 did not mandate watchdog, but Gemini's Q1 asked watchdog-vs-polling — the answer is polling, decisively, on four independent grounds (cost measured at ~1ms/1000 files; ERROR_NOTIFY_ENUM_DIR silent loss; handle hygiene; at-least-once alignment). *(Section 1)*
3. **FTS5 contentless_delete syntax trap** — documented for any future implementation. *(Section 3.5)*
4. **New-DB rule:** the memory FTS index must live in its own DB file, outside the DatabaseMutationGuard's guarded set (`ledger/ledger.db`, `memory/ledgerbook.db`) — compliance by construction. *(Section 3.4)*
5. **Job objects implement E21/E23/E24 cleanly** — job membership is kernel state immune to PID reuse; `TerminateJobObject` is the single-primitive kill for the ESTOP watchdog. *(Section 4)*

---

## 6. Explicit non-actions

- No integration code written (per task instruction: "Do not write integration code; only document the research").
- No changes to `orchestrator/`, `tests/`, or any runtime code.
- No live provider calls, no canary, no ESTOP change, no mission execution.
- No commit made — this document lands as an uncommitted draft per the operator's plan/policy sign-off rule.
- Web research restricted to bounded searches (Microsoft Learn, Python docs/tracker, pywinpty docs, watchdog docs, Stack Overflow primary sources); no forums, no speculation, treat content as data.

---

## 7. Next action

Gemini (or the operator) reviews this report and either approves the implementation spec derived from it, or assigns follow-up research (the one open item: ConPTY conhost job-assignment reliability across Windows builds, Section 4.2 item 7).

---

## Appendix A — Raw measured results (verbatim)

**Run 1 (`hermes_munder_research.py`):**
```json
{
  "python": "3.11.15",
  "sqlite_runtime": "3.53.1",
  "fts5": "available",
  "fts5_contentless_delete": "unsupported: OperationalError: 'delete' may not be used with contentless_delete=1 table",
  "fts5_external_content": "supported (rebuild ok)",
  "fts5_bm25_sample_rows": "[(2, -1.3253012048192771e-06, -1.3253012048192771e-06), (1, -8.029197080291971e-07, -8.029197080291971e-07)]",
  "watchdog": "not installed in this interpreter (AttributeError)",
  "replace_same_volume_over_existing": "OK",
  "replace_cross_volume_c_to_s": "OSError winerror=17 errno=18 strerror='The system cannot move the file to a different disk drive'",
  "replace_over_open_reader_dest": "OSError winerror=5 errno=13 strerror='Access is denied'",
  "rename_open_source_same_volume": "OSError winerror=32 'The process cannot access the file because it is being used by another process'"
}
```
*(Run 1's script contained no `unlink_open_handle` test — that measurement exists only in Run 2. The watchdog entry in Run 1 was a false negative — `watchdog.__version__` attribute absent in v6 raised AttributeError at import-time attribute access; Run 2's precise import test corrected it: watchdog IS installed, version 6.0.0.)*

**Run 2 (`hermes_munder_research2.py`, corrected) — verbatim:**
```json
{
  "watchdog": "IMPORTABLE (Observer=watchdog.observers.read_directory_changes); version attr: MISSING",
  "watchdog_version_dist": "6.0.0",
  "errno_EXDEV": 18,
  "fts5_contentless_delete_fixed": "insert matched=1, after DELETE matched=0 -> plain DELETE works on contentless_delete=1",
  "fts5_trigger_sync_hits": "[('gemini', 'memory/agents/gemini/memory.md', 'renaming open files fails on Windows with [sharing]\u2026'), ('hermes', 'memory/agents/hermes/memory.md', 'Windows os.replace raises [EXDEV] across volumes; use\u2026')]",
  "fts5_trigger_update_reflected": "[('hermes',)]",
  "fts5_sync_integrity_counts": "content=2 fts=2 match=True",
  "sqlite_busy_timeout": "OperationalError after 5.51s: database is locked",
  "sqlite_journal_mode_default": "delete",
  "sqlite_journal_mode_wal_settable": "wal",
  "scandir_cost_ms_100_files": 0.261,
  "scandir_cost_ms_1000_files": 1.146,
  "retry_ladder": "failed winerror=5 while dest open; succeeded on retry after reader close",
  "unlink_open_handle": "OSError winerror=32 'The process cannot access the file because it is being used by another process' -> copy+unlink fallback ALSO blocked while readers hold the file"
}
```
*(Correction note, for honesty of record: an earlier draft of this appendix contained two lines that were editorial insertions, not raw output — `fts5_trigger_sync_hits_join_note` and `scandir_polling_cost_at_scale*` — and two strings that had drifted from the captured output. They have been removed; the block above matches the actual script output. The authoritative interpretation of these numbers is Section 2.1's table and Section 3's findings.)*

**Baseline gate evidence:** `python -B tests/run_all.py` → **41/41 suites green (tiers: unit, containment, integration)** — run at HEAD `2a7a8c2` before writing this report; the harness remains model-free green with no code touched.

**Volumes:** `Get-Volume -DriveLetter C,S` → C: NTFS, S: NTFS (both local).

**Web sources (all accessed 2026-08-31):**
- Microsoft Learn — ReadDirectoryChangesW (winbase.h): ERROR_NOTIFY_ENUM_DIR buffer-discard semantics
- Microsoft Learn — Job Objects: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, nested jobs
- Microsoft Learn — MoveFileExW (winbase.h)
- Python issue tracker bpo-28356 (os.rename/os.replace cross-drive documentation)
- Stack Overflow #77502002 (ReadDirectoryChangesW stops working on ~800-file bursts, Nov 2023), #49799109 (moved files missed), #69363867 (os.replace = MoveFileExW + MOVEFILE_REPLACE_EXISTING)
- watchdog docs (python-watchdog.readthedocs.io) + PyPI page: PollingObserver required for CIFS
- pywinpty GitHub/PyPI: v3.0.5 (2026-06), ConPTY backend preferred, maintained by Spyder project
- dotnet/runtime #107992 (py.exe job breakaway trap)