"""Hermetic model-free unit tests for orchestrator/browser_daemon.py."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import browser_daemon


class TestBrowserDaemon(unittest.TestCase):

    def test_find_browser_executable_nt(self):
        with mock.patch("os.name", "nt"), \
             mock.patch("os.path.isfile", side_effect=lambda p: "chrome.exe" in p.lower()):
            path = browser_daemon.find_browser_executable()
            self.assertIsNotNone(path)
            self.assertIn("chrome.exe", path.lower())

    def test_find_browser_executable_shutil_fallback(self):
        with mock.patch("os.name", "posix"), \
             mock.patch("shutil.which", return_value="/usr/bin/google-chrome"), \
             mock.patch("os.path.isfile", return_value=True):
            path = browser_daemon.find_browser_executable()
            self.assertEqual(path, "/usr/bin/google-chrome")

    def test_find_browser_executable_none(self):
        with mock.patch("os.name", "posix"), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch("os.path.isfile", return_value=False):
            path = browser_daemon.find_browser_executable()
            self.assertIsNone(path)

    def test_is_cdp_ready_true(self):
        fake_response = io.BytesIO(
            json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/123"}).encode("utf-8")
        )
        fake_response.status = 200
        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            ready = browser_daemon.is_cdp_ready(port=9222)
            self.assertTrue(ready)

    def test_is_cdp_ready_false_on_connection_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            ready = browser_daemon.is_cdp_ready(port=9222)
            self.assertFalse(ready)

    def test_browser_daemon_missing_executable(self):
        daemon = browser_daemon.BrowserDaemon(executable_path="/nonexistent/browser")
        with self.assertRaises(FileNotFoundError):
            daemon.start()

    def test_browser_daemon_start_and_stop(self):
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.terminate.return_value = None
        mock_proc.wait.return_value = 0

        ready_sequence = [False, True]

        def fake_is_ready(*a, **kw):
            return ready_sequence.pop(0) if ready_sequence else True

        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
             mock.patch("browser_daemon.is_cdp_ready", side_effect=fake_is_ready), \
             mock.patch("time.sleep", return_value=None):
            daemon = browser_daemon.BrowserDaemon(
                executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                port=9222,
            )
            url = daemon.start(ready_timeout=5.0)
            self.assertEqual(url, "http://127.0.0.1:9222")
            self.assertIsNotNone(daemon.process)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            self.assertIn("--remote-debugging-port=9222", args)
            self.assertIn("--remote-allow-origins=http://127.0.0.1:9222,http://localhost:9222", args)
            self.assertNotIn("--remote-allow-origins=*", args)
            self.assertIn("--proxy-server=http://127.0.0.1:8787", args)
            self.assertIn("--proxy-bypass-list=127.0.0.1;localhost", args)
            self.assertIn("--headless=new", args)

            daemon.stop()
            self.assertIsNone(daemon.process)
            mock_proc.terminate.assert_called_once()

    def test_browser_daemon_premature_exit(self):
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = 21
        mock_proc.returncode = 21

        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("subprocess.Popen", return_value=mock_proc), \
             mock.patch("browser_daemon.is_cdp_ready", return_value=False):
            daemon = browser_daemon.BrowserDaemon(
                executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                port=9222,
            )
            with self.assertRaises(RuntimeError) as ctx:
                daemon.start(ready_timeout=2.0)
            self.assertIn("exited prematurely with code 21", str(ctx.exception))

    def test_active_browser_daemon_reuses_existing(self):
        with mock.patch("browser_daemon.is_cdp_ready", return_value=True), \
             mock.patch("browser_daemon.BrowserDaemon") as mock_cls:
            with browser_daemon.ActiveBrowserDaemon(port=9222, allow_external_reuse=True) as cdp_url:
                self.assertEqual(cdp_url, "http://127.0.0.1:9222")
            mock_cls.assert_not_called()

    def test_active_browser_daemon_rejects_unmanaged_external_squatter(self):
        with mock.patch("browser_daemon.is_cdp_ready", return_value=True), \
             mock.patch.dict(os.environ, {"AGI_LIVE_EXECUTION_ALLOWED": "1"}), \
             mock.patch("browser_daemon.BrowserDaemon") as mock_cls:
            with self.assertRaises(RuntimeError) as ctx:
                with browser_daemon.ActiveBrowserDaemon(port=9222, allow_external_reuse=False):
                    pass
            self.assertIn("already in use by an external process", str(ctx.exception))
            mock_cls.assert_not_called()

    def test_active_browser_daemon_launches_managed(self):
        mock_instance = mock.MagicMock()
        mock_instance.start.return_value = "http://127.0.0.1:9222"

        with mock.patch("browser_daemon.is_cdp_ready", return_value=False), \
             mock.patch.dict(os.environ, {"AGI_LIVE_EXECUTION_ALLOWED": "1"}), \
             mock.patch("browser_daemon.BrowserDaemon", return_value=mock_instance):
            with browser_daemon.ActiveBrowserDaemon(port=9222) as cdp_url:
                self.assertEqual(cdp_url, "http://127.0.0.1:9222")
                mock_instance.start.assert_called_once()
            mock_instance.stop.assert_called_once()

    def test_active_browser_daemon_model_free_guard(self):
        with mock.patch("browser_daemon.is_cdp_ready", return_value=False), \
             mock.patch.dict(os.environ, {"AGI_LIVE_EXECUTION_ALLOWED": "0"}), \
             mock.patch("browser_daemon.BrowserDaemon") as mock_cls:
            with browser_daemon.ActiveBrowserDaemon(port=9222) as cdp_url:
                self.assertEqual(cdp_url, "http://127.0.0.1:9222")
            mock_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
