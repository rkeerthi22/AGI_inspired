"""Ensure (or audit) the operator Ed25519 attestation keypair.

Invoked by scripts/install.ps1. The PRIVATE key is never printed; only the
public-key fingerprint (sha256) is emitted, so this script is safe to run in
any log-capture context. Not part of the harness runtime import surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import operator_auth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="audit only; do not create a keypair if absent")
    args = ap.parse_args()

    loaded = operator_auth._load_keypair()
    if loaded is None:
        if args.check:
            print(json.dumps({"present": False}))
        else:
            priv, pub = operator_auth._generate_keypair()
            operator_auth._store_keypair(priv, pub)
            print(json.dumps({"present": True, "created": True,
                              "fingerprint": hashlib.sha256(pub).hexdigest()}))
    else:
        _priv, pub = loaded
        print(json.dumps({"present": True, "created": False,
                          "fingerprint": hashlib.sha256(pub).hexdigest()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
