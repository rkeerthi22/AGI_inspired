# AGENTS.md — AGI_like harness

Universal entry point for any agent resuming work on this project.

## On start / resume / model switch / compaction

1. Read the Compact Brief: `.harness/continuity/current.json`.
2. Run `python orchestrator/continuity.py recover`.
3. Verify Git / runtime / database state independently.
4. Resolve disagreements in favor of live state (live always wins).
5. Read only the durable references needed for the next action.
