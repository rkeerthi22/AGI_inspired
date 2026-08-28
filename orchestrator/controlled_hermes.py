"""Launch Hermes with the harness retrieval-progress adapter installed."""

from __future__ import annotations

import os
import contextlib
import io
import json
from pathlib import Path
import sys


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


def main() -> None:
    # The launcher runs with Hermes' venv Python.  Its checkout is two parents
    # above venv/Scripts/python.exe (or venv/bin/python on POSIX).
    hermes_root = Path(sys.executable).resolve().parents[2]
    sys.path.insert(0, str(hermes_root))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from retrieval_progress import active_controller, install_hermes_adapter

    audit = os.environ.get("HARNESS_RETRIEVAL_AUDIT")
    install_hermes_adapter(Path(audit) if audit else None)
    original_args = list(sys.argv[1:])
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
            _value("-z", "--oneshot") or "",
            model=_value("-m", "--model"),
            provider=_value("--provider"),
            toolsets=_value("-t", "--toolsets"),
            usage_file=_value("--usage-file"),
        )

    controller = active_controller()
    if controller is None or "-z" not in original_args:
        print(captured.getvalue(), end="")
        if rc:
            raise SystemExit(rc)
        return

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
    controller.finalization_started()
    final_usage: dict[str, int] = {}
    success = False
    reason = ""
    try:
        from execution import ollama_chat
        final = ollama_chat(
            next((original_args[i + 1] for i, arg in enumerate(original_args[:-1])
                  if arg in {"-m", "--model"}), ""),
            controller.finalization_prompt(mission),
            timeout=300,
            usage_out=final_usage,
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


if __name__ == "__main__":
    main()
