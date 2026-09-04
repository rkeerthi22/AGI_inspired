"""A5: early-abort worker paths must leave diagnostic raw artifacts."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.worker_diagnostics import diagnostic_output, write_worker_raw


class WorkerDiagnosticTests(unittest.TestCase):
    def test_empty_worker_output_uses_failure_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = write_worker_raw(
                Path(raw), 117, "", {"failure": "No Anthropic credentials found."}, "worker")
            self.assertTrue(path.is_file())
            content = path.read_text(encoding="utf-8")
            self.assertTrue(content.strip())
            self.assertIn("worker diagnostic", content)
            self.assertIn("failure: No Anthropic credentials found.", content)

    def test_empty_synthesis_output_uses_process_diagnostic(self) -> None:
        content = diagnostic_output(
            "   ", {"process_error": "HTTP 503: upstream unavailable"}, "synthesis")
        self.assertIn("synthesis diagnostic", content)
        self.assertIn("process_error: HTTP 503: upstream unavailable", content)

    def test_nonempty_output_is_preserved_exactly(self) -> None:
        self.assertEqual(diagnostic_output(" report ", {}, "worker"), " report ")


if __name__ == "__main__":
    unittest.main()
