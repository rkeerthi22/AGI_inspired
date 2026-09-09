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
    mapping = {
        "byteplus-coding": "byteplus_coding",
        "custom:byteplus-coding": "byteplus_coding",
        "openai-api": "openai",
    }
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
    profile = os.environ.get("HARNESS_RETRIEVAL_PROFILE")
    if profile:
        # Resolve explicit profiles lazily so the long-standing generic launch
        # path retains the original one-argument adapter contract. Unknown
        # profiles still fail closed before run_oneshot can contact a provider.
        from retrieval_progress import retrieval_policy_for_profile
        retrieval_policy = retrieval_policy_for_profile(profile)
        install_hermes_adapter(Path(audit) if audit else None, retrieval_policy)
    else:
        try:
            from retrieval_progress import RetrievalPolicy
            if args.toolsets and "browser" not in args.toolsets:
                install_hermes_adapter(Path(audit) if audit else None, RetrievalPolicy(max_calls=(3, 5, 0), low_novelty_limit=4))
            else:
                install_hermes_adapter(Path(audit) if audit else None)
        except (ImportError, AttributeError):
            install_hermes_adapter(Path(audit) if audit else None)
    original_args = ["-z", args.oneshot]
    for flag, value in (("--provider", args.provider), ("-m", args.model),
                        ("-t", args.toolsets), ("--usage-file", args.usage_file)):
        if value is not None:
            original_args.extend((flag, value))
    sys.argv = ["hermes", *original_args]

    # Windows Restricted Token ACL Patch:
    # Under restricted tokens (S-1-5-12 / BUILTIN\Users), Python's os.mkdir, os.makedirs,
    # and tempfile.mkdtemp with mode=0o700 create directories with inheritance blocked
    # and access granted only to OWNER RIGHTS/SYSTEM/Administrators, excluding BUILTIN\Users.
    # When browser tools or child processes attempt to open files in those directories,
    # Windows raises [Errno 13] Permission denied.
    # Normalizing 0o700 to 0o777 on Windows preserves full inheritance of BUILTIN\Users modify
    # permissions from HARNESS_WORKER_HOME.
    if sys.platform == "win32":
        _orig_mkdir = os.mkdir
        _orig_makedirs = os.makedirs
        _orig_chmod = os.chmod

        def _safe_mkdir(path, mode=0o777, *a, **kw):
            if mode == 0o700 or (isinstance(mode, int) and (mode & 0o777) == 0o700):
                mode = 0o777
            return _orig_mkdir(path, mode, *a, **kw)

        def _safe_makedirs(name, mode=0o777, exist_ok=False):
            if mode == 0o700 or (isinstance(mode, int) and (mode & 0o777) == 0o700):
                mode = 0o777
            return _orig_makedirs(name, mode=mode, exist_ok=exist_ok)

        def _safe_chmod(path, mode, *a, **kw):
            if mode == 0o700 or (isinstance(mode, int) and (mode & 0o777) == 0o700):
                mode = 0o777
            return _orig_chmod(path, mode, *a, **kw)

        os.mkdir = _safe_mkdir
        os.makedirs = _safe_makedirs
        os.chmod = _safe_chmod
        os.environ.setdefault(
            "AGENT_BROWSER_ARGS",
            "--no-sandbox,--disable-dev-shm-usage,--disable-crash-reporter,--disable-breakpad,--no-crash-upload,--disable-gpu",
        )
        try:
            import tempfile
            if hasattr(tempfile, "_os"):
                tempfile._os.mkdir = _safe_mkdir
        except Exception:
            pass

    # Pre-emptively patch async_delegation before any tools or agent modules are imported.
    # When run_agent is imported, it transitively loads tools.process_registry which
    # initializes a module-level ProcessRegistry and attempts to restore undelivered
    # completions from SQLite. Under restricted worker tokens or read-only environments,
    # accessing state.db fails with "unable to open database file".
    try:
        import tools.async_delegation as _ad
        _ad.restore_undelivered_completions = lambda *a, **kw: 0
    except Exception:
        pass

    # Research workers run under restricted OS tokens (BUILTIN\Users) without write
    # access to the host's ~/.hermes/state.db. Furthermore, one-shot research
    # turns must never leak ephemeral scratch turns into the host's interactive
    # session history. Disable session DB creation and incremental persistence
    # so the agent runs purely in memory during research.
    import hermes_cli.oneshot as _oneshot_mod
    if hasattr(_oneshot_mod, "_create_session_db_for_oneshot"):
        _oneshot_mod._create_session_db_for_oneshot = lambda: None
    try:
        import run_agent
        _orig_agent_init = run_agent.AIAgent.__init__
        def _safe_agent_init(self, *a, **kw):
            _orig_agent_init(self, *a, **kw)
            self._persist_disabled = True
        run_agent.AIAgent.__init__ = _safe_agent_init
    except Exception:
        pass

    from hermes_cli.oneshot import run_oneshot

    # Patch Hermes DDGS search provider to execute directly in-process via egress broker proxy
    try:
        import urllib.request
        import urllib.parse
        import lxml.html
        import plugins.web.ddgs.provider as _ddgs_provider

        def _direct_ddgs_search(query: str, safe_limit: int = 5) -> list[dict]:
            proxy_url = (
                os.environ.get("HTTPS_PROXY")
                or os.environ.get("HTTP_PROXY")
                or os.environ.get("DDGS_PROXY")
                or "http://127.0.0.1:8787"
            )
            # Try ddgs with brave/yahoo engines via egress proxy first
            try:
                from ddgs import DDGS
                with DDGS(proxy=proxy_url, timeout=12) as client:
                    for backend in ["yahoo", "brave", "auto"]:
                        try:
                            hits = list(client.text(query, backend=backend, max_results=safe_limit))
                            if hits:
                                results = []
                                for i, hit in enumerate(hits[:safe_limit]):
                                    url = str(hit.get("href") or hit.get("url") or "")
                                    results.append({
                                        "title": str(hit.get("title", "")),
                                        "url": url,
                                        "description": str(hit.get("body", "")),
                                        "position": i + 1,
                                    })
                                return results
                        except Exception:
                            continue
            except Exception as ddgs_err:
                sys.stderr.write(f"ddgs package search error: {ddgs_err}\n")

            # Fallback to direct HTML scraper
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
            )
            data = urllib.parse.urlencode({"q": query}).encode("utf-8")
            req = urllib.request.Request(
                "https://html.duckduckgo.com/html/",
                data=data,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            try:
                with opener.open(req, timeout=15) as res:
                    html = res.read().decode("utf-8", errors="replace")
            except Exception as req_err:
                sys.stderr.write(f"Direct DDG search request error: {req_err}\n")
                return []

            try:
                tree = lxml.html.fromstring(html)
            except Exception as parse_err:
                sys.stderr.write(f"Direct DDG search parse error: {parse_err}\n")
                return []

            results = []
            body_nodes = tree.xpath("//div[contains(@class, 'result__body')]")
            for i, node in enumerate(body_nodes):
                if i >= safe_limit:
                    break
                title_nodes = node.xpath(".//h2//text()")
                href_nodes = node.xpath(".//a[contains(@class, 'result__a')]/@href")
                snippet_nodes = node.xpath(".//a[contains(@class, 'result__snippet')]//text()")
                title = "".join(title_nodes).strip()
                url = href_nodes[0].strip() if href_nodes else ""
                snippet = "".join(snippet_nodes).strip()
                if url:
                    if "/l/?uddg=" in url:
                        parsed = urllib.parse.urlparse(url)
                        params = urllib.parse.parse_qs(parsed.query)
                        if "uddg" in params:
                            url = params["uddg"][0]
                    results.append({
                        "title": title,
                        "url": url,
                        "description": snippet,
                        "position": len(results) + 1,
                    })
            return results

        _ddgs_provider._run_ddgs_search_bounded = _direct_ddgs_search
        _ddgs_provider._run_ddgs_search = _direct_ddgs_search
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to patch Hermes DDGS provider: {e}\n")

    # Patch Hermes web_extract tool to fetch directly via egress broker proxy
    try:
        import tools.web_tools as _web_tools

        async def _direct_web_extract(
            urls: list,
            format: str = "markdown",
            char_limit: int | None = None,
        ) -> str:
            proxy_url = (
                os.environ.get("HTTPS_PROXY")
                or os.environ.get("HTTP_PROXY")
                or "http://127.0.0.1:8787"
            )
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
            )
            results = []
            target_urls = urls[:5] if isinstance(urls, list) else []
            for item in target_urls:
                u = item if isinstance(item, str) else (item.get("url") or item.get("href") if isinstance(item, dict) else "")
                if not u:
                    continue
                req = urllib.request.Request(
                    u,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    },
                )
                try:
                    with opener.open(req, timeout=15) as res:
                        raw_bytes = res.read()
                        doc = lxml.html.fromstring(raw_bytes)
                        title = doc.findtext(".//title") or ""
                        for el in doc.xpath("//script|//style|//nav|//header|//footer|//svg|//noscript"):
                            el.drop_tree()
                        text = " ".join(doc.text_content().split())
                        limit = char_limit or 15000
                        results.append({
                            "url": u,
                            "title": title.strip(),
                            "content": text[:limit],
                            "error": None,
                        })
                except urllib.error.HTTPError as http_err:
                    results.append({
                        "url": u,
                        "title": "",
                        "content": "",
                        "error": f"HTTP {http_err.code} ({http_err.reason})",
                    })
                except Exception as fetch_err:
                    results.append({
                        "url": u,
                        "title": "",
                        "content": "",
                        "error": f"Fetch error: {fetch_err}",
                    })
            return json.dumps({"results": results}, ensure_ascii=False)

        _web_tools.web_extract_tool = _direct_web_extract
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to patch Hermes web_extract tool: {e}\n")

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
        try:
            from egress_policy import snapshot_egress_policy
            snap = snapshot_egress_policy()
            research_usage.setdefault("policy_digest", snap.get("policy_digest"))
            research_usage.setdefault("allowlisted_hosts", snap.get("allowlisted_hosts", []))
        except Exception:
            pass
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
        try:
            from egress_policy import snapshot_egress_policy
            snap = snapshot_egress_policy()
            usage.setdefault("policy_digest", snap.get("policy_digest"))
            usage.setdefault("allowlisted_hosts", snap.get("allowlisted_hosts", []))
        except Exception:
            pass
        usage_path.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    print(final)
    # A bounded failure is useful evidence, but it is not a successful worker
    # deliverable and must reach the orchestrator as infrastructure failure.
    return 0 if success else 70


if __name__ == "__main__":
    raise SystemExit(main())
