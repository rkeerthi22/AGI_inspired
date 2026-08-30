# BytePlus connectivity checkpoint

Date: 2026-08-29  
Status: **PAUSED — WAITING FOR SESSION RESET**

## Implemented architecture

- Tool-free calls use the typed provider-neutral boundary in
  `orchestrator/provider_chat.py`.
- The common dispatcher enforces the canonical ESTOP check before adapter
  invocation.
- Locks identify owners using OS process creation identity plus a unique lock
  token; PID reuse alone cannot establish ownership.
- BytePlus Coding Plan uses the OpenAI-compatible base endpoint
  `https://ark.ap-southeast.bytepluses.com/api/coding/v3`.
- `ark-code-latest` is the routing model so the actual DeepSeek-family choice
  remains controlled by BytePlus console activation.
- Repository configuration contains only `env:ARK_API_KEY`; it contains no API
  key value.
- BytePlus is available but is not active in any normal role or fallback chain.

## Canary evidence

One explicitly authorized connectivity canary used the fixed prompt `ping`. The
operator reported that it reached BytePlus and authenticated. The sanitized
result was:

```json
{"ok": false, "provider": "byteplus_coding", "error_category": "rate_limit", "retryable": true, "error": "BytePlus HTTP 429"}
```

This verifies provider routing and normalized 429 handling. It does **not** yet
verify a successful completion body, finish reason, request ID, or token usage.
The one-use permit was consumed before dispatch and no retry was attempted.

## Quota and retry gate

A read-only Coding Plan quota query showed:

- session: 100% used; reset at 2026-08-29 09:18:13 Europe/Warsaw;
- weekly: 16.9132365% used;
- monthly: 8.45661825% used.

Do not retry before the session reset. After reset, the only authorized next
runtime action is another explicit one-shot connectivity canary. Do not run a
mission, retrieval canary, synthesis canary, or cohort from this checkpoint.

## Model-free readiness

- Default non-live gate: 36/36 suites green.
- Live tier remains opt-in and excluded from the default gate.
- BytePlus provider configuration is declarative and inactive.
- ESTOP must remain engaged; the one-shot probe does not remove or modify it.
- No further runtime call is planned before the session reset.
