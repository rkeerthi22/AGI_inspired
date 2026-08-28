"""Harness-scoped truthful direct fetch and unattended browser selection.

Installed only by controlled_hermes.py. It does not alter the user's global
Hermes configuration or weaken URL/access-control policy.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit


MAX_URLS = 5
MAX_BYTES = 400_000
MAX_REDIRECTS = 5
DEFAULT_CHAR_LIMIT = 15_000
USER_AGENT = "Mozilla/5.0 (compatible; AGI-harness-direct-fetch/1.0)"


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    return text[:head] + "\n\n[TRUNCATED BY DIRECT FETCH]\n\n" + text[-tail:], True


def _fetch_static(url: str, char_limit: int) -> dict:
    import requests
    from tools.url_safety import is_safe_url, normalize_url_for_request

    current = normalize_url_for_request(url)
    if not is_safe_url(current):
        return {"url": url, "title": "", "content": "", "error": "Blocked by URL safety policy"}
    session = requests.Session()
    response = None
    for _ in range(MAX_REDIRECTS + 1):
        response = session.get(
            current, headers={"User-Agent": USER_AGENT}, timeout=20,
            allow_redirects=False, stream=True,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            if not location:
                break
            target = normalize_url_for_request(urljoin(current, location))
            if not is_safe_url(target):
                return {"url": url, "title": "", "content": "",
                        "error": "Redirect blocked by URL safety policy"}
            current = target
            continue
        break
    else:
        return {"url": url, "title": "", "content": "", "error": "Too many redirects"}

    if response is None:
        return {"url": url, "title": "", "content": "", "error": "No response"}
    body = bytearray()
    for chunk in response.iter_content(64 * 1024):
        body.extend(chunk)
        if len(body) >= MAX_BYTES:
            del body[MAX_BYTES:]
            break
    content_type = (response.headers.get("content-type") or "").lower()
    if response.status_code >= 400:
        return {"url": current, "title": "", "content": "",
                "status": response.status_code,
                "error": f"HTTP {response.status_code}"}
    if "pdf" in content_type or urlsplit(current).path.lower().endswith(".pdf"):
        return {"url": current, "title": "", "content": "",
                "status": response.status_code,
                "error": "PDF extraction is not supported by this direct static fetcher; use an approved document/browser capability"}
    encoding = response.encoding or "utf-8"
    raw = bytes(body).decode(encoding, errors="replace")
    title = ""
    if "html" in content_type or "<html" in raw[:1000].lower():
        match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        title = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
        parser = _VisibleText()
        parser.feed(raw)
        text = parser.text()
    else:
        text = raw
    text, truncated = _truncate(text, char_limit)
    return {"url": current, "title": title, "content": text,
            "status": response.status_code, "truncated": truncated,
            "bytes_read": len(body), "error": ""}


async def direct_extract_handler(args, **_kwargs) -> str:
    urls = args.get("urls") if isinstance(args, dict) else []
    urls = urls[:MAX_URLS] if isinstance(urls, list) else []
    try:
        char_limit = max(2000, min(int(args.get("char_limit") or DEFAULT_CHAR_LIMIT), 50_000))
    except (TypeError, ValueError):
        char_limit = DEFAULT_CHAR_LIMIT
    results = []
    for value in urls:
        if not isinstance(value, str) or not value.strip():
            results.append({"url": "", "title": "", "content": "", "error": "Invalid URL"})
            continue
        results.append(await asyncio.to_thread(_fetch_static, value.strip(), char_limit))
    return json.dumps({"success": any(not r.get("error") for r in results),
                       "results": results}, ensure_ascii=False)


def _installed_chrome() -> str | None:
    configured = os.environ.get("HARNESS_CHROME_PATH", "").strip()
    candidates = [configured] if configured else []
    candidates.extend([
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ])
    return next((str(Path(p)) for p in candidates if p and Path(p).is_file()), None)


def install_harness_capabilities(*, unattended_browser: bool) -> dict:
    """Patch the already loaded Hermes registry for this worker process only."""
    from tools.registry import registry
    import model_tools  # noqa: F401 - triggers built-in tool registration

    extract = registry.get_entry("web_extract")
    if extract is None:
        raise RuntimeError("Hermes web_extract tool is not registered")
    schema = dict(extract.schema)
    schema["description"] = (
        "Directly fetch static public HTTP/HTTPS page content. This is a real bounded "
        "HTTP reader, not search. It does not execute JavaScript, traverse Shadow DOM, "
        "solve CAPTCHA/WAF challenges, authenticate, or extract PDFs. Use the approved "
        "browser capability for legitimate dynamic rendering when direct content is incomplete."
    )
    extract.schema = schema
    extract.handler = direct_extract_handler
    extract.check_fn = lambda: True
    extract.is_async = True

    browser = {"authorized": False, "mode": "unchanged", "executable": None}
    if unattended_browser:
        chrome = _installed_chrome()
        if not chrome:
            raise RuntimeError("approved unattended browser requested but no installed Chrome/Edge was found")
        os.environ["AGENT_BROWSER_EXECUTABLE_PATH"] = chrome
        import tools.browser_use_cli as browser_use_cli
        browser_use_cli.get_browser_backend = lambda: browser_use_cli.BACKEND_DISABLED
        browser = {"authorized": True, "mode": "builtin-local-headless", "executable": chrome}
    return {"web_extract": "bounded-static-http", "browser": browser}
