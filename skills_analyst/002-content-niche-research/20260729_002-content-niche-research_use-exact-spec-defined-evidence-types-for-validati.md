---
mission: 002-content-niche-research
title: Use exact spec-defined evidence types for validation
status: active
approved: 2026-07-29
canary_baseline: 3
canary_baseline_week: 2026-W29
canary_baseline_note: >-
  Re-stamped 2026-07-29 (F34). Approved with 0 because the current week had no canary
  rows, which the old _current_canary_green() could not distinguish from "ran, none
  passed" -- and a baseline of 0 disarms auto-rollback entirely, since no green count is
  below zero. 3 is the last REAL observation (2026-W29: 3 done+pass, 2 quota-parked and
  therefore not counted), i.e. an honest floor of "at least 3 were observed green".
created: 2026-07-29
evidence_lesson_ids: [2, 3, 4]
---

When a task specifies required evidence types such as YouTube Data API view/like counts or search-interest signals, do not substitute general news articles, unnamed sites, or vague "search results" citations. Always provide the exact public metrics or specific URLs demanded by the spec to validate audience demand. If you cannot obtain the required evidence type, flag it rather than replacing it with an unsupported alternative.
