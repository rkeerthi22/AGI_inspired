"""F64: a live recovery critic exposed a missing datetime import.

The critic call succeeded, but execution.ollama_chat silently dropped its reasoning
trace because the trace formatter referenced datetime without importing it.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import execution

checks = 0
fails = 0


def check(name, got, want=True):
    global checks, fails
    checks += 1
    if got != want:
        fails += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")
    else:
        print(f"PASS {name}")


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({
            "message": {"content": "VERDICT: PASS", "thinking": "checked criteria"},
            "prompt_eval_count": 12,
            "eval_count": 3,
        }).encode()


with tempfile.TemporaryDirectory() as td:
    trace = Path(td) / "critic_reasoning.txt"
    usage = {}
    with patch("urllib.request.urlopen", return_value=Response()):
        out = execution.ollama_chat("critic", "judge", trace_path=trace, usage_out=usage)
    check("critic response survives trace persistence", out, "VERDICT: PASS")
    check("reasoning trace is created", trace.is_file())
    text = trace.read_text(encoding="utf-8")
    check("trace contains reasoning", "checked criteria" in text)
    check("trace contains timestamp", "reasoning trace" in text and "T" in text)
    check("usage remains accounted", usage, {"input_tokens": 12, "output_tokens": 3})

print(f"\n{checks - fails}/{checks} assertions passed")
raise SystemExit(1 if fails else 0)
