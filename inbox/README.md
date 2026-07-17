# inbox/ — operator drop zone

Drop files here for the analyst to ingest: CSV/exports, PDFs, competitor lists, ad exports.
The nightly ingest pass (per HARNESS_DESIGN.md §2.7) normalizes them into typed facts with
provenance in `memory/ledgerbook.db`, then the file can be archived.

Contents are gitignored (raw data doesn't belong in version control); only this README is tracked.

## Conventions
- One topic per file; a descriptive filename helps the analyst tag provenance.
- Tabular data as `.csv`; documents as `.pdf`/`.md`.
- Nothing here is treated as instructions — file contents are DATA, never commands
  (instruction-source boundary, HARNESS_DESIGN.md §2.6).
