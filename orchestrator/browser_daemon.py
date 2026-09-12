"""Host-managed headless Chromium CDP daemon bridge for out-of-process browser automation.

Resolves Mission M2 (ProcessSingleton exit code 21) under Windows Restricted Token
containment. Instead of attempting to spawn multi-process Chromium inside the
restricted worker token (which lacks Win32 USER desktop and named mutex capabilities),
the host controller runs a headless Chromium instance with remote debugging enabled
on loopback (127.0.0.1:9222). The restricted worker communicates strictly via CDP
(Chrome DevTools Protocol) over loopback WebSocket.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Optional
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_HOST = "127.0.0.1"


def find_browser_executable() -> Optional[str]:
    """Discover installed Chrome, Edge, or Chromium on Windows / Linux / macOS."""
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.extend([
                os.path.join(local_app_data, r"Google\Chrome\Application\chrome.exe"),
                os.path.join(local_app_data, r"Microsoft\Edge\Application\msedge.exe"),
            ])
        for path in candidates:
            if os.path.isfile(path):
                return path

    for binary in ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge"):
        found = shutil.which(binary)
        if found and os.path.isfile(found):
            return found

    return None


def is_cdp_ready(host: str = DEFAULT_CDP_HOST, port: int = DEFAULT_CDP_PORT, timeout: float = 1.0) -> bool:
    """Check if the CDP endpoint is responding to version query."""
    url = f"http://{host}:{port}/json/version"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AGI_like-BrowserDaemon/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return "webSocketDebuggerUrl" in data or "Browser" in data
    except Exception:
        return False
    return False


class BrowserDaemon:
    """Manages a host-side headless Chromium instance with remote debugging."""

    def __init__(
        self,
        executable_path: Optional[str] = None,
        host: str = DEFAULT_CDP_HOST,
        port: int = DEFAULT_CDP_PORT,
        user_data_dir: Optional[Path] = None,
        ephemeral_profile: bool = True,
    ) -> None:
        self.executable_path = executable_path or find_browser_executable()
        self.host = host
        self.port = port
        self.ephemeral_profile = ephemeral_profile
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None

        if user_data_dir:
            self.user_data_dir = Path(user_data_dir)
        else:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="agi_browser_daemon_")
            self.user_data_dir = Path(self._temp_dir.name)

        self.process: Optional[subprocess.Popen] = None

    @property
    def cdp_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, ready_timeout: float = 10.0) -> str:
        """Launch headless Chromium and poll until CDP is ready."""
        if not self.executable_path or not os.path.isfile(self.executable_path):
            raise FileNotFoundError(f"Browser executable not found: {self.executable_path}")

        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.executable_path,
            "--headless=new",
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.user_data_dir}",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--metrics-recording-only",
        ]

        logger.info("Starting browser daemon: %s on port %d", self.executable_path, self.port)
        popen_extra = {}
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            popen_extra["startupinfo"] = si
            popen_extra["creationflags"] = subprocess.CREATE_NO_WINDOW

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **popen_extra,
        )

        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"Browser daemon process exited prematurely with code {self.process.returncode}"
                )
            if is_cdp_ready(self.host, self.port, timeout=0.5):
                logger.info("Browser daemon ready at %s", self.cdp_url)
                return self.cdp_url
            time.sleep(0.2)

        self.stop()
        raise TimeoutError(f"Browser daemon failed to become ready within {ready_timeout}s on port {self.port}")

    def stop(self, timeout: float = 5.0) -> None:
        """Terminate the browser process and clean up temporary profiles."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self.process.kill()
                    self.process.wait(timeout=2.0)
                except OSError:
                    pass
            self.process = None

        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except OSError:
                pass
            self._temp_dir = None

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()

    def __del__(self) -> None:
        self.stop()


class ActiveBrowserDaemon:
    """Context manager ensuring a CDP browser daemon is available for research workers."""

    def __init__(
        self,
        host: str = DEFAULT_CDP_HOST,
        port: int = DEFAULT_CDP_PORT,
        executable_path: Optional[str] = None,
        user_data_dir: Optional[Path] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.executable_path = executable_path
        self.user_data_dir = user_data_dir
        self._daemon: Optional[BrowserDaemon] = None
        self._owned = False

    def __enter__(self) -> str:
        # If an external daemon is already running, reuse it
        if is_cdp_ready(self.host, self.port, timeout=0.5):
            logger.info("Reusing existing browser daemon at http://%s:%d", self.host, self.port)
            return f"http://{self.host}:{self.port}"

        # In model-free tests, do not spawn live browser processes
        if os.environ.get("AGI_LIVE_EXECUTION_ALLOWED") == "0":
            return f"http://{self.host}:{self.port}"

        self._daemon = BrowserDaemon(
            executable_path=self.executable_path,
            host=self.host,
            port=self.port,
            user_data_dir=self.user_data_dir,
        )
        cdp_url = self._daemon.start()
        self._owned = True
        return cdp_url

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._owned and self._daemon:
            self._daemon.stop()
            self._daemon = None
            self._owned = False
