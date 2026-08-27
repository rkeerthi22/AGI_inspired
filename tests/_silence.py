"""tests/_silence.py — truthful monkey-patching of the orchestrator logger.

Why this helper exists
----------------------
Before Move 5a, each orchestrator module defined its own `log()` function.
Patching `br.log = lambda` silenced only `batch_runner.log`; `integrity.log`
and `execution.log` kept printing to their own files. Tests that wanted to
silence all orchestrator output had to patch three places, and forgetting one
left a leak in the test output.

Move 5a centralized the logger in `runtime_context.py`, and Move 5b turned
`log()` into a thin proxy that calls `_logger(msg)` at every invocation. That
makes a single patch on `runtime_context._logger` visible to every call site
-- including calls that already captured the `log` reference at import time,
because the routing is in `_logger`, not in `log`.

This helper exposes that single patch point with a context manager and a
capture helper, so tests don't reach into the module directly.
"""
from contextlib import contextmanager

import runtime_context as _rc


@contextmanager
def silence_log():
    """Replace `runtime_context._logger` with a no-op for the duration of the
    `with` block. Restores the previous value on exit (so nested silences
    compose and a final no-silence block still logs to the real logger).

    Use this instead of `br.log = lambda *a, **k: None`: the old pattern
    silently stopped working once modules shared a single log function, and
    even after the proxy pattern was introduced it only silenced the
    caller's own reference. This helper silences every orchestrator module.
    """
    saved = _rc._logger
    _rc._logger = lambda msg: None
    try:
        yield
    finally:
        _rc._logger = saved


def capture_log():
    """Return `(sink, context_manager)` where `sink` is a list that receives
    every orchestrator log line for the duration of the context manager.

    Usage:

        sink, ctx = capture_log()
        with ctx:
            integrity.escalate("a test reason")
        assert any("a test reason" in line for line in sink)

    The captured lines are the raw `msg` strings, exactly as callers passed
    them -- the timestamp prefix the real logger adds is NOT included.
    """
    sink = []

    @contextmanager
    def ctx():
        saved = _rc._logger
        _rc._logger = sink.append
        try:
            yield
        finally:
            _rc._logger = saved

    return sink, ctx()
