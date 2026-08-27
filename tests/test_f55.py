"""F55: orchestrator logging regression — every module must write through the
same logger, and a single patch must silence every call site.

Three concerns, three assertions:

  1. The pre-5a regression: `integrity`, `execution`, `batch_runner`, and
     `runtime_context` all resolve `log` to the SAME function object. If a
     future refactor re-introduces a per-module `log()` (the defect Moves 1
     and 2 shipped), this catches it.

  2. The active run log: when a run calls `set_log_file()`, every orchestrator
     module's log line is appended to that file. The pre-5a code path could
     silently drop integrity / execution lines into a different file
     (`runs/schtask_last.log` from `integrity.log`, stdout-only from
     `execution.log`). The proxy in `runtime_context` resolves both.

  3. Truthful patch semantics: a single `silence_log()` (or `capture_log()`)
     invocation silences every orchestrator module, not just the one whose
     `log` reference was originally captured. The pre-F55 pattern of
     `br.log = lambda` only silenced `batch_runner`'s own reference and
     looked like it worked because of the import-time capture — but the
     regression test asserts the routing is in `_logger`, not in `log`.

Temp DB and temp log file. No model is ever called.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import batch_runner as br  # noqa: E402
import integrity  # noqa: E402
import execution  # noqa: E402
import runtime_context as rc  # noqa: E402
from _silence import silence_log, capture_log  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         got={got} want={want}")


print("=== 1. every orchestrator module resolves `log` to the same proxy ===")
check("br.log is rc.log", br.log is rc.log, True)
check("integrity.log is rc.log", integrity.log is rc.log, True)
check("execution.log is rc.log", execution.log is rc.log, True)
# The proxy must be a thin callable that delegates at call time, so a patch on
# _logger is observed by every module. We assert this by patching _logger
# and verifying the call lands there -- structural introspection of co_*
# attributes is brittle across CPython versions.
saved_logger = rc._logger
hits = []
rc._logger = hits.append
br.log("probe")
check("br.log routed through _logger (proxy is a real indirection)",
      hits == ["probe"], True)
rc._logger = saved_logger

print("\n=== 2. active run log captures every module's lines ===")
tmpdir = Path(tempfile.mkdtemp())
logpath = tmpdir / "run.log"
rc.set_log_file(logpath)
try:
    br.log("hello from batch_runner")
    integrity.log("hello from integrity")
    execution.log("hello from execution")
    contents = logpath.read_text(encoding="utf-8")
    check("batch_runner line is in the active run log",
          "hello from batch_runner" in contents, True)
    check("integrity line is in the active run log (was lost pre-5a)",
          "hello from integrity" in contents, True)
    check("execution line is in the active run log (was stdout-only pre-5a)",
          "hello from execution" in contents, True)
finally:
    rc.set_log_file(None)

print("\n=== 3. a single patch silences every orchestrator module ===")
# Use a separate log path that didn't exist before the silence window so the
# existence check below is meaningful.
quietpath = tmpdir / "quiet.log"
with silence_log():
    rc.set_log_file(quietpath)
    try:
        br.log("should be silenced")
        integrity.log("should also be silenced")
        execution.log("and this one too")
    finally:
        rc.set_log_file(None)
# quietpath is touched only by writes; silence_log() suppresses those.
check("silenced writes do not create the run log file", quietpath.exists(), False)

print("\n=== 4. capture_log() collects lines from every module ===")
sink, ctx = capture_log()
with ctx:
    br.log("capture me from br")
    integrity.log("capture me from integrity")
    execution.log("capture me from execution")
check("captured br line", any("capture me from br" in m for m in sink), True)
check("captured integrity line (pre-F55 this would have been missed)",
      any("capture me from integrity" in m for m in sink), True)
check("captured execution line (pre-F55 this would have been missed)",
      any("capture me from execution" in m for m in sink), True)

print("\n=== 5. the silence is scoped -- logging resumes after the context ===")
captured = []
saved = rc._logger
rc._logger = captured.append
try:
    with silence_log():
        # inside: nothing reaches `captured`
        br.log("inside")
    # outside: _logger is back to `captured.append`
    br.log("outside")
finally:
    rc._logger = saved
check("no leak inside the silence block", any("inside" == m for m in captured), False)
check("logging resumed after the block", any("outside" == m for m in captured), True)

print("\n=== 6. validated against the defect ===")
# Pre-F55: `br.log = lambda` only silenced batch_runner. integrity.log and
# execution.log still pointed at the original function. If a future refactor
# accidentally removes the proxy and goes back to direct imports, this is the
# failure mode: silence_log() silences nothing.
saved = rc._logger
check("the proxy indirection is intact (log() routes through _logger)",
      callable(rc.log) and "msg" in rc.log.__code__.co_varnames, True)
rc._logger = saved

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
