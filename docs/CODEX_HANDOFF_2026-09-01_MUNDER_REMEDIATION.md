# Codex Handoff — Munder Boundary Remediation

**Task:** `MUNDER-BOUNDARY-REMEDIATION`  
**Completed:** 2026-09-01T01:05:33Z  
**AGI checkpoint:** `e7d0692b1c644648c514b42d4e73ca7d19d222bc`  
**Scope:** Model-free test isolation plus external `S:\MunderState` security cleanup. No AGI runtime behavior changed.

## Completed

- Fixed `tests/test_operator_cli.py` so both injected process-inventory probes remain inside the temporary environment patch. The test no longer inspects whichever development CLI happens to be running.
- Created sanitized rollback material at `S:\MunderState-RemediationBackups\AGI_like-20260901T002313Z`. Archives exclude `.codex`, credentials, sessions, SQLite runtime state, Git object stores, and junction traversal.
- Replaced the three verified Codex session junctions with empty project-local directories; global targets were not touched.
- Rewrote both local Hive Git histories with `git-filter-repo`, removing every `agents/*/.codex/**` path from all refs. Both repos have no remote and validate with zero sensitive-history paths.
- Removed `bypassPermissions` from the live roster and all 107 roster backups (258 historical flag occurrences). The canonical Munder config and Hive registry had no such flag.
- Removed the duplicated GitHub token field from the historical migration config without reading or logging its value.
- Reduced `Authenticated Users` access on `S:\MunderState` from Modify to ReadAndExecute, retained explicit operator FullControl, and protected the active `.codex` and migration-backup directories for operator/SYSTEM/Administrators only.

## Verification

- Operator CLI targeted suite: **109/109 PASS**.
- Full model-free gate after external remediation: **46/46 PASS**.
- Munder boundary suite: **27/27 PASS**.
- Hive quiescence suite: **63/63 PASS**.
- Active Hive: clean at `a60419006abc358285c61903a274870fcd99230a`; zero `.codex` history paths; no remote.
- Quarantined Hive: clean at `2be475a09fb40cdf5bc5b16ca886e8d8ae16ea2d`; zero `.codex` history paths; no remote.
- `enforce.js` unchanged: SHA-256 `60BA31AE16BCACE287874A7E70A11277D4F73C21C2DD5A75B94EDCD2F6DDBAF4`.
- ESTOP remained engaged. No provider, canary, M1-M7, isolation transition, or batch execution occurred.

## Human-only follow-up

1. Treat the still-present active Jim `auth.json` credential as compromised. From a separate human terminal, revoke/rotate it through the issuing account, configure Codex credential storage to `keyring`, log in again, verify `codex login status`, and remove the plaintext file only after successful keyring authentication.
2. Rotate the GitHub credential in the current Munder application config. Its historical duplicate was removed, but server-side revocation cannot be performed safely by this agent.
3. Start and stop Munder through its supported controls to regenerate a truthful fleet snapshot. The existing fleet remains stale; it was not given a fake timestamp or hand-edited into a false healthy state.
4. Keep `_global` / `projects/<id>` namespace migration and enforcement-revision pinning deferred until after M1-M7.

## Preserved unrelated state

`docs/reviews/CLAUDE_OPERATOR_CLI_REVIEW_2026-09-01.md` was created by another actor before this task claimed ownership. It remains untracked and untouched.
