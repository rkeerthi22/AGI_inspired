"""Launch Hermes with the harness retrieval-progress adapter installed."""

from __future__ import annotations

import os
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys

from execution_pause import pause_engaged


def merge_finalization_usage(usage: dict, final_usage: dict) -> dict:
    """Return usage with exactly one separately metered finalization call."""
    merged = dict(usage)
    extra_in = int(final_usage.get("input_tokens") or 0)
    extra_out = int(final_usage.get("output_tokens") or 0)
    merged["input_tokens"] = int(merged.get("input_tokens") or 0) + extra_in
    merged["output_tokens"] = int(merged.get("output_tokens") or 0) + extra_out
    merged["total_tokens"] = int(merged.get("total_tokens") or 0) + extra_in + extra_out
    merged["api_calls"] = int(merged.get("api_calls") or 0) + 1
    merged["retrieval_finalization_calls"] = 1
    return merged


def finalizer_provider(hermes_provider: str | None) -> str:
    """Translate Hermes transport selectors to harness provider identities."""
    mapping = {"custom:byteplus-coding": "byteplus_coding"}
    value = (hermes_provider or "ollama").strip().lower()
    return mapping.get(value, value)


def finalizer_call_options(argv: list[str]) -> dict[str, str]:
    """Build finalizer kwargs from the exact Hermes research argv."""
    selected = next((argv[i + 1] for i, arg in enumerate(argv[:-1])
                     if arg == "--provider"), "ollama")
    provider = finalizer_provider(selected)
    return ({"provider": provider, "purpose": "retrieval_finalization"}
            if provider != "ollama" else {})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one harness-controlled Hermes research turn")
    parser.add_argument("-z", "--oneshot", required=True, help="research objective")
    parser.add_argument("--provider")
    parser.add_argument("-m", "--model")
    parser.add_argument("-t", "--toolsets")
    parser.add_argument("--usage-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if pause_engaged():
        print("controlled Hermes execution refused: global ESTOP is engaged", file=sys.stderr)
        return 75
    # The launcher runs with Hermes' venv Python.  Its checkout is two parents
    # above venv/Scripts/python.exe (or venv/bin/python on POSIX).
    hermes_root = Path(sys.executable).resolve().parents[2]
    sys.path.insert(0, str(hermes_root))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hermes_contract import validate_installed_hermes
    from hermes_capabilities import install_harness_capabilities
    from retrieval_progress import active_controller, install_hermes_adapter

    # Contract validation is model-free and runs before any Hermes worker call.
    # An incompatible installed checkout is an explicit launch failure, never a
    # subtly degraded retrieval run.
    validate_installed_hermes(hermes_root)
    install_harness_capabilities(
        unattended_browser=os.environ.get("HARNESS_UNATTENDED_BROWSER") == "1"
    )
    audit = os.environ.get("HARNESS_RETRIEVAL_AUDIT")
    install_hermes_adapter(Path(audit) if audit else None)
    original_args = ["-z", args.oneshot]
    for flag, value in (("--provider", args.provider), ("-m", args.model),
                        ("-t", args.toolsets), ("--usage-file", args.usage_file)):
        if value is not None:
            original_args.extend((flag, value))
    sys.argv = ["hermes", *original_args]
    from hermes_cli.oneshot import run_oneshot
    # One-shot research output is deliberately withheld: the only user-visible
    # result is the dedicated evidence-only finalization below.
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        def _value(*flags: str) -> str | None:
            for flag in flags:
                if flag in original_args:
                    index = original_args.index(flag) + 1
                    return original_args[index] if index < len(original_args) else None
            return None

        rc = run_oneshot(
            args.oneshot, model=args.model, provider=args.provider,
            toolsets=args.toolsets, usage_file=args.usage_file,
        )

    controller = active_controller()
    if controller is None or "-z" not in original_args:
        print(captured.getvalue(), end="")
        if rc:
            raise SystemExit(rc)
        return int(rc or 0)

    prompt_index = original_args.index("-z") + 1
    mission = original_args[prompt_index] if prompt_index < len(original_args) else ""
    usage_path = None
    if "--usage-file" in original_args:
        index = original_args.index("--usage-file") + 1
        if index < len(original_args):
            usage_path = Path(original_args[index])

    research_usage = {}
    if usage_path and usage_path.exists():
        research_usage = json.loads(usage_path.read_text(encoding="utf-8"))
    controller.research_finished(
        api_calls=int(research_usage.get("api_calls") or 0),
        input_tokens=int(research_usage.get("input_tokens") or 0),
        output_tokens=int(research_usage.get("output_tokens") or 0),
        total_tokens=int(research_usage.get("total_tokens") or 0),
    )
    # A retrieval controller changes presentation, not process semantics.  Never
    # let its evidence-only finalizer turn a failed Hermes research process into
    # a successful deliverable.  stdout was intentionally buffered above; emit
    # it now, while stderr has remained attached to the caller throughout.
    if rc:
        print(captured.getvalue(), end="")
        research_usage["process_returncode"] = int(rc)
        research_usage["failed"] = True
        if usage_path:
            usage_path.write_text(json.dumps(research_usage, indent=2) + "\n",
                                  encoding="utf-8")
        return int(rc)
    controller.finalization_started()
    final_usage: dict[str, int] = {}
    success = False
    reason = ""
    try:
        from execution import ollama_chat
        final_options = finalizer_call_options(original_args)
        final = ollama_chat(
            next((original_args[i + 1] for i, arg in enumerate(original_args[:-1])
                  if arg in {"-m", "--model"}), ""),
            controller.finalization_prompt(mission),
            timeout=300,
            usage_out=final_usage,
            **final_options,
        ).strip()
        if not final:
            reason = "finalizer returned empty output"
            final = controller.bounded_failure(reason)
        else:
            success = True
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        final = controller.bounded_failure(reason)

    controller.finalization_finished(
        success=success,
        input_tokens=int(final_usage.get("input_tokens") or 0),
        output_tokens=int(final_usage.get("output_tokens") or 0),
        reason=reason,
    )
    if usage_path and usage_path.exists():
        usage = merge_finalization_usage(research_usage, final_usage)
        usage_path.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    print(final)
    # A bounded failure is useful evidence, but it is not a successful worker
    # deliverable and must reach the orchestrator as infrastructure failure.
    return 0 if success else 70


if __name__ == "__main__":
    raise SystemExit(main())
