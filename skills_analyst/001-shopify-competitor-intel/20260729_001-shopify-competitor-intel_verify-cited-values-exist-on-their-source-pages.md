---
mission: 001-shopify-competitor-intel
title: Verify cited values exist on their source pages
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
evidence_lesson_ids: [9, 10]
---

Before submitting, open every cited URL and confirm the exact claimed value (rating, price, count) actually appears on that page. If a page is blocked or returns a 403/404, do not cite it as a source — find an accessible alternative or omit the fact. Unsupported citations fail mechanical checks even when the rest of the deliverable is complete.
