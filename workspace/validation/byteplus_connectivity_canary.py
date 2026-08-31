"""One-call BytePlus Coding Plan connectivity probe.

This script never clears or edits ESTOP. Its explicit CLI acknowledgement creates
one in-memory permit bound to byteplus_coding/connectivity_canary. The dispatcher
consumes the permit before its sole network attempt, including when that attempt
fails. There is no retry path.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))

import execution_pause  # noqa: E402
import provider_chat  # noqa: E402

PROVIDER = "byteplus_coding"
PURPOSE = "connectivity_canary"
PROMPT = "ping"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-single-estop-bypass", action="store_true",
        help="authorize exactly one BytePlus call without modifying the ESTOP sentinel")
    args = parser.parse_args(argv)
    if not args.authorize_single_estop_bypass:
        raise SystemExit("ABORT: explicit --authorize-single-estop-bypass is required")
    if not execution_pause.pause_engaged():
        raise SystemExit("ABORT: ESTOP must remain engaged for this scoped-bypass canary")
    # Boundary hardening 2026-08-31: the CLI flag alone is no longer authority.
    # A single-use, 30-minute operator marker must be consumed on this machine.
    try:
        execution_pause.consume_canary_authorization()
    except RuntimeError as exc:
        raise SystemExit(f"ABORT: {exc}")
    if not os.environ.get("ARK_API_KEY", ""):
        raise SystemExit("ABORT: ARK_API_KEY is not present in this process environment")

    # Defense-in-depth (Codex review #2, 2026-08-31): the operator canary must
    # not execute when a mutation-capable Munder development process is live.
    # Current canary authorization is a scoped one-shot capability, not
    # cryptographic human authentication; this gate caps the blast radius of
    # that known limitation.  It runs AFTER marker consumption on purpose: a
    # refused canary still burns the one-shot marker.
    import cohort_hive_quiesce  # noqa: E402  (orchestrator/ is on sys.path)
    try:
        cohort_hive_quiesce.ensure_canary_process_quiescence()
    except cohort_hive_quiesce.HiveQuiesceError as exc:
        raise SystemExit(f"ABORT: {exc}")

    config = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    provider = (config.get("providers") or {}).get(PROVIDER)
    if not isinstance(provider, dict):
        raise SystemExit(f"ABORT: provider {PROVIDER!r} is not configured")

    request = provider_chat.ChatRequest(
        provider=PROVIDER,
        model=provider["routing_model"],
        prompt=PROMPT,
        timeout_seconds=30,
        endpoint=provider["endpoint"],
        authentication_reference=provider["authentication_reference"],
        purpose=PURPOSE,
        metadata={"probe": PURPOSE},
    )
    permit = provider_chat.authorize_single_paused_canary(PROVIDER)
    try:
        result = provider_chat.chat(request, pause_bypass=permit)
    except provider_chat.ProviderChatError as exc:
        print(json.dumps({"ok": False, "provider": PROVIDER,
                          "error_category": exc.category.value,
                          "retryable": exc.retryable, "error": str(exc)}))
        return 2

    print(json.dumps({
        "ok": True,
        "provider": result.provider,
        "model": result.model,
        "content": result.content[:200],
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "finish_reason": result.finish_reason,
        "request_id": result.request_id,
        "latency_seconds": round(result.latency_seconds, 3),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
