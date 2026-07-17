# OpenClaw → Hermes migration — record & decision

**Status:** CLOSED (data import skipped by decision; OpenClaw retired dormant) · **Date:** 2026-07-17

## Assessment (from `hermes claw migrate --dry-run`)
The OpenClaw install on this machine was a barely-used husk (last touched 2026-05-24, one
WhatsApp-bound agent, all optional skills disabled). The dry-run reported only 3 low/no-value
items would import, and 2 conflicts where OpenClaw would have *degraded* Hermes:

| Dry-run item | Verdict | Why |
|---|---|---|
| `user-profile → USER.md` | **Reject** | OpenClaw's `USER.md` is a BLANK template; Hermes's `USER.md` holds the real profile. Importing would clobber good data with empty fields. |
| `whatsapp-settings → .env` | **Reject** | Escalation channel is Telegram (already configured in Hermes). WhatsApp not wanted. |
| `full-providers → config.yaml` | **Redundant** | Hermes already has the Ollama provider configured. |
| `soul` | Conflict (kept Hermes) | Hermes SOUL.md is the one we want. |
| `model-config` | Conflict (kept Hermes) | Hermes model routing is authoritative. |

**Decision:** skip the `hermes claw migrate` data import entirely — nothing of value to move, and
the one non-conflicting import (empty USER.md) would have caused harm. Recorded as an immutable
decision in the design rationale.

## What actually carried over (the one real migration)
A single ElevenLabs API key was the only shared asset worth relocating — and it was found leaked
in cleartext across **five** locations spanning both agents (Hermes `MEMORY.md`, `.hermes_history`,
`interrupt_debug.log`, a `SKILL.md`, plus an inert `.env.example` placeholder). This was
consolidated into Hermes `.env` as `ELEVENLABS_API_KEY` (verified `hermes status` → ElevenLabs ✓)
and scrubbed from every prompt-facing/log surface. See the security section of the project memory
and [[HARNESS_DESIGN]] §2.6. (A second, benign copy remains in `skills/video-use/.env` — proper
secret storage, not an injection surface; left for optional consolidation.)

## OpenClaw retirement
- **State:** dormant — no running processes, no scheduled tasks, no services (verified 2026-07-17).
- **Package:** left installed (`openclaw@2026.5.22`, npm global) as an inert fallback/reference,
  per operator choice. It auto-starts nothing.
- **Attack surface note:** OpenClaw's 2026 CVE record (one-click RCE CVE-2026-25253 CVSS 8.8,
  command injection, prompt-injection RCE) is why the gateway stays loopback-only and the package
  is a candidate for later removal.

## How to reverse / complete later
- **Fully remove OpenClaw:** `npm uninstall -g openclaw` (then optionally delete `~/.openclaw`).
- **Actually run the import anyway** (not recommended): `hermes claw migrate --preset user-data`
  — would import the empty USER.md + WhatsApp setting; add `--overwrite` to force the conflicts.
- **Undo the secret consolidation:** the pre-edit skill is backed up at
  `~/.hermes/skills/creative/spiderman-bnd-production/SKILL.md.pre-secret-fix.bak` (key redacted).
