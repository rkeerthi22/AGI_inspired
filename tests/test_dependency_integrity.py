"""Model-free supply-chain regressions for hash locks and Hermes attestation."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import dependency_integrity as integrity  # noqa: E402


checks = 0
failures: list[str] = []


def check(label: str, condition: bool) -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


check("production lock has hash coverage", integrity.requirements_lock_state()["ok"])
check("bootstrap requires artifact hashes", integrity.bootstrap_hash_enforcement_state()["ok"])

with tempfile.TemporaryDirectory(dir=ROOT / "workspace", ignore_cleanup_errors=True) as raw:
    root = Path(raw)
    good_lock = root / "good.txt"
    good_lock.write_text(
        "example-package==1.0 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8")
    bad_lock = root / "bad.txt"
    bad_lock.write_text("example-package==1.0\n", encoding="utf-8")
    check("hash-locked requirement passes", integrity.requirements_lock_state(good_lock)["ok"])
    bad = integrity.requirements_lock_state(bad_lock)
    check("missing artifact hash fails closed",
          not bad["ok"] and bad["missing_hashes"] == ["example-package"])

    wheel = root / "demo_pkg-0.0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo_pkg/__init__.py", "")
        archive.writestr("demo_pkg-0.0.1.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: demo-pkg\nVersion: 0.0.1\n")
        archive.writestr("demo_pkg-0.0.1.dist-info/WHEEL",
                         "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("demo_pkg-0.0.1.dist-info/RECORD",
                         "demo_pkg/__init__.py,,\n"
                         "demo_pkg-0.0.1.dist-info/METADATA,,\n"
                         "demo_pkg-0.0.1.dist-info/WHEEL,,\n"
                         "demo_pkg-0.0.1.dist-info/RECORD,,\n")
    tampered = root / "tampered.txt"
    tampered.write_text(
        "demo-pkg==0.0.1 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--dry-run", "--require-hashes",
         "--no-deps", "--no-index", "--find-links", str(root), "--requirement",
         str(tampered), "--disable-pip-version-check", "--no-input"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    check("tampered artifact hash is rejected by pip", result.returncode != 0)

with tempfile.TemporaryDirectory(dir=ROOT / "workspace", ignore_cleanup_errors=True) as raw:
    source = Path(raw) / "hermes"
    source.mkdir()
    dirty = source / "contributors" / "emails" / "agent.local"
    dirty.parent.mkdir(parents=True)
    dirty.write_text("attested metadata only\n", encoding="utf-8")
    digest = hashlib.sha256(dirty.read_bytes()).hexdigest()
    manifest = Path(raw) / "runtime.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "command": "hermes",
        "version": "0.20.2",
        "source_revision": "a" * 40,
        "allowed_dirty_files": {
            "contributors/emails/agent.local": f"sha256:{digest}",
        },
    }), encoding="utf-8")

    def runner(command, **_kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout=(
                "Hermes Agent v0.20.2 (test)\n"
                f"Install directory: {source}\n"), stderr="")
        if command[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        if command[-2:] == ["status", "--porcelain=v1"]:
            return SimpleNamespace(returncode=0,
                                   stdout=" M contributors/emails/agent.local\n", stderr="")
        raise AssertionError(command)

    state = integrity.hermes_runtime_state(
        manifest, executable_finder=lambda _name: "fake-hermes", runner=runner)
    check("attested external runtime passes", state["ok"])
    dirty.write_text("unattested change\n", encoding="utf-8")
    state = integrity.hermes_runtime_state(
        manifest, executable_finder=lambda _name: "fake-hermes", runner=runner)
    check("external runtime modification fails closed", not state["ok"])

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    raise SystemExit("FAILURES: " + ", ".join(failures))
