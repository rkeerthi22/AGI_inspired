"""Redirect-safety regressions for citecheck.

Validates that:
1. public -> public redirects are followed and counted;
2. public -> private redirects are blocked before a second fetch;
3. redirect loops fail closed after the configured limit.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import citecheck  # noqa: E402

checks = 0
failures: list[str] = []


def check(label: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  [FAIL] {label}")
    else:
        print(f"  [PASS] {label}")


class FakeResponse:
    def __init__(self, url: str, body: str, status: int = 200):
        self._url = url
        self._body = body
        self.status = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit: int | None = None):
        data = self._body.encode("utf-8")
        return data if limit is None else data[:limit]

    def geturl(self):
        return self._url


class FakeOpener:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls: list[str] = []

    def open(self, req, timeout):
        self.calls.append(req.full_url)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


original_opener = citecheck._NO_REDIRECT_OPENER
original_resolve = citecheck._resolve_safety
try:
    print("=== public redirect stays allowed ===")
    opener = FakeOpener([
        urllib.error.HTTPError(
            "https://start.example/report", 302, "found",
            {"Location": "https://final.example/report"}, None,
        ),
        FakeResponse("https://final.example/report", "Revenue 42"),
    ])
    citecheck._NO_REDIRECT_OPENER = opener
    citecheck._resolve_safety = lambda host: None
    evidence = citecheck.verify("Revenue 42 https://start.example/report")
    check("one citation returned", len(evidence), 1)
    check("redirected citation is reachable", evidence[0]["reachable"], True)
    check("final URL recorded", evidence[0]["final_url"], "https://final.example/report")
    check("redirect count recorded", evidence[0]["redirects_followed"], 1)
    check("second fetch reached final target", opener.calls[-1], "https://final.example/report")

    print("\n=== private redirect is blocked ===")
    opener = FakeOpener([
        urllib.error.HTTPError(
            "https://start.example/report", 302, "found",
            {"Location": "http://169.254.169.254/latest/meta-data"}, None,
        ),
    ])
    citecheck._NO_REDIRECT_OPENER = opener
    citecheck._resolve_safety = (
        lambda host: "blocked: resolves to a private/loopback/link-local address"
        if host == "169.254.169.254" else None
    )
    blocked = citecheck.verify("Metadata probe https://start.example/report")
    check("blocked redirect stays unreachable", blocked[0]["reachable"], False)
    check("blocked redirect reports why",
          blocked[0]["error"].startswith("blocked redirect target:"), True)
    check("blocked redirect never makes a second request", len(opener.calls), 1)

    print("\n=== redirect loops fail closed ===")
    opener = FakeOpener([
        urllib.error.HTTPError(
            "https://loop.example/a", 302, "found",
            {"Location": "https://loop.example/b"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/b", 302, "found",
            {"Location": "https://loop.example/c"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/c", 302, "found",
            {"Location": "https://loop.example/d"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/d", 302, "found",
            {"Location": "https://loop.example/e"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/e", 302, "found",
            {"Location": "https://loop.example/f"}, None,
        ),
        urllib.error.HTTPError(
            "https://loop.example/f", 302, "found",
            {"Location": "https://loop.example/g"}, None,
        ),
    ])
    citecheck._NO_REDIRECT_OPENER = opener
    citecheck._resolve_safety = lambda host: None
    looped = citecheck.verify("Loop https://loop.example/a")
    check("redirect loop stays unreachable", looped[0]["reachable"], False)
    check("redirect loop reports max redirects",
          looped[0]["error"], f"too many redirects (>{citecheck.MAX_REDIRECTS})")
    check("redirect loop counter hits limit",
          looped[0]["redirects_followed"], citecheck.MAX_REDIRECTS + 1)
finally:
    citecheck._NO_REDIRECT_OPENER = original_opener
    citecheck._resolve_safety = original_resolve

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
