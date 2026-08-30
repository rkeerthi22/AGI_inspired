"""Model-free regressions for controlled retrieval and outcome classification."""
import contextlib
import io
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import controlled_hermes
import workflow

checks = {}


class FakeController:
    def __init__(self):
        self.research_calls = 0
        self.finalization_calls = 0

    def research_finished(self, **kwargs):
        self.research_calls += 1

    def finalization_started(self):
        self.finalization_calls += 1


controller = FakeController()
finalizer_calls = []

fake_contract = types.ModuleType("hermes_contract")
fake_contract.validate_installed_hermes = lambda root: None
fake_capabilities = types.ModuleType("hermes_capabilities")
fake_capabilities.install_harness_capabilities = lambda **kwargs: None
fake_progress = types.ModuleType("retrieval_progress")
fake_progress.active_controller = lambda: controller
fake_progress.install_hermes_adapter = lambda audit: None
fake_oneshot = types.ModuleType("hermes_cli.oneshot")


def failed_research(*args, **kwargs):
    print("research stdout evidence")
    print("research stderr evidence", file=sys.stderr)
    return 23


fake_oneshot.run_oneshot = failed_research
fake_cli = types.ModuleType("hermes_cli")
fake_cli.oneshot = fake_oneshot
fake_execution = types.ModuleType("execution")
fake_execution.ollama_chat = lambda *a, **k: finalizer_calls.append((a, k)) or "should not run"

module_names = {
    "hermes_contract": fake_contract,
    "hermes_capabilities": fake_capabilities,
    "retrieval_progress": fake_progress,
    "hermes_cli": fake_cli,
    "hermes_cli.oneshot": fake_oneshot,
    "execution": fake_execution,
}
prior_modules = {name: sys.modules.get(name) for name in module_names}
prior_pause = controlled_hermes.pause_engaged
try:
    sys.modules.update(module_names)
    controlled_hermes.pause_engaged = lambda: False
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = controlled_hermes.main([
            "-z", "test research", "--provider", "custom:byteplus-coding",
            "-m", "ark-code-latest"])
finally:
    controlled_hermes.pause_engaged = prior_pause
    for name, prior in prior_modules.items():
        if prior is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prior

checks.update({
    "controlled research returns original nonzero code": rc == 23,
    "controlled research never starts finalization after failure":
        controller.research_calls == 1 and controller.finalization_calls == 0 and not finalizer_calls,
    "controlled research preserves stdout evidence": "research stdout evidence" in stdout.getvalue(),
    "controlled research preserves stderr evidence": "research stderr evidence" in stderr.getvalue(),
    "synthesis infra verdict stays infrastructure failure":
        workflow._status_for_critic_verdict("infra_failed") == "infra_failed",
    "synthesis content rejection stays failed":
        workflow._status_for_critic_verdict("fail") == "failed",
    "synthesis pass stays done": workflow._status_for_critic_verdict("pass") == "done",
})

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("critical-path regression failures: " + ", ".join(failed))
