"""Check (or engage) the ESTOP sentinel. A fresh install ships PAUSED.

Invoked by scripts/install.ps1. This helper only ever ENGAGES (or audits) the
sentinel; it NEVER disengages ESTOP. Disengagement stays a separate
controlled-window CLI operation (see orchestrator/execution_pause.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
from execution_pause import estop_path, pause_engaged  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="audit only; do not engage the sentinel")
    args = ap.parse_args()

    engaged = pause_engaged()
    if not engaged and not args.check:
        p = estop_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("ESTOP engaged by installer\n", encoding="utf-8")
        engaged = pause_engaged()
    print("engaged=" + str(engaged).lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
