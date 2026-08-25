#!/usr/bin/env python
"""Run all prediction machine tests.

Discovers and runs every ``test_*.py`` module in this directory via
``unittest.TestLoader().discover()`` and prints a summary.

Usage::

    python S:/AGI_like/prediction_machine/tests/run_tests.py
"""
import os
import sys
import unittest

# Ensure the parent directory (S:/AGI_like) is on sys.path so that
# `prediction_machine` is importable from the tests.
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)  # S:/AGI_like/prediction_machine
GRANDPARENT = os.path.dirname(PARENT)  # S:/AGI_like
for p in (GRANDPARENT, PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    """Discover and run all tests in this directory."""
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=HERE,
        pattern="test_*.py",
        top_level_dir=GRANDPARENT,
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    # Exit code: 0 if all passed, 1 if any failures/errors
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()