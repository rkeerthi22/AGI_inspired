#!/usr/bin/env python
"""Run all prediction machine tests.

Discovers and runs every ``test_*.py`` module in this directory via
``unittest.TestLoader().discover()`` and prints a summary.

Usage::

    python prediction_machine/tests/run_tests.py
"""
import os
import sys
import argparse
import unittest

# Ensure the repository root is on sys.path so that
# `prediction_machine` is importable from the tests.
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)  # prediction_machine
GRANDPARENT = os.path.dirname(PARENT)  # repository root
for p in (GRANDPARENT, PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)


def main(argv=None):
    """Discover and run all tests in this directory."""
    parser = argparse.ArgumentParser(description="Run prediction-machine unit tests")
    parser.parse_args(argv)
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
