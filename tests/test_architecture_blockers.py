"""Model-free regressions for the confirmed adversarial-review blockers."""
import json
import gc
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))
import execution  # noqa: E402
import evaluation  # noqa: E402
import ledger  # noqa: E402
import provider_chat  # noqa: E402
import runlock  # noqa: E402

# This suite must never inherit or use a host Credential Manager secret. Tests
# inject explicit environment/.env values where credential behavior is needed.
provider_chat.credential_vault.get_api_key = lambda _provider: None

fails = []


def check(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        fails.append(name)


old_home = os.environ.get("HERMES_HOME")
old_key = os.environ.pop("ARK_API_KEY", None)
with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    secret_home = Path(td)
    (secret_home / "config.yaml").write_text("{}\n", encoding="utf-8")
    (secret_home / ".env").write_text(
        "ARK_API_KEY=test-only-placeholder\n", encoding="utf-8")
    os.environ["HERMES_HOME"] = str(secret_home)
    check("provider dispatcher reads Hermes private secret source",
          provider_chat._secure_env_value("ARK_API_KEY") == "test-only-placeholder")
if old_key is not None:
    os.environ["ARK_API_KEY"] = old_key
if old_home is None:
    os.environ.pop("HERMES_HOME", None)
else:
    os.environ["HERMES_HOME"] = old_home


provider_config = {
    "provider": "byteplus_coding", "model": "ark-code-latest",
    "endpoint": "https://ark.ap-southeast.bytepluses.com/api/coding/v3",
    "authentication_reference": "env:ARK_API_KEY", "context_tokens": 32000,
    "response_token_reserve": 1000, "ignored_option": "must-not-propagate",
}
expected_options = provider_chat.options_from_config(provider_config, "synthesis")
check("evaluation uses canonical provider option extraction",
      evaluation._provider_call_options(provider_config, "synthesis") == expected_options)
old_candidates = execution._failover_candidates
old_chat = execution.ollama_chat
captured_options = {}
try:
    execution._failover_candidates = lambda cfg: [dict(provider_config)]

    def capture_options(model, prompt, **kwargs):
        captured_options.update(kwargs)
        return "usable synthesis output"

    execution.ollama_chat = capture_options
    execution.synthesis_with_failover("prompt", provider_config, "test")
finally:
    execution._failover_candidates = old_candidates
    execution.ollama_chat = old_chat
check("synthesis and evaluation share provider option keys",
      {k: captured_options[k] for k in expected_options} == expected_options and
      set(expected_options) == set(evaluation._provider_call_options(
          provider_config, "synthesis")))


with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    lock = Path(td) / "run.lock"
    lock.write_text("{broken", encoding="utf-8")
    try:
        with runlock.acquire(lock):
            pass
        blocked = False
    except runlock.LockCorrupted:
        blocked = True
    check("corrupt lock fails closed", blocked and lock.exists())

    current_start = runlock._process_start_identity(os.getpid())
    lock.write_text(json.dumps({"pid": os.getpid(), "process_start_id": current_start,
                                "lock_id": "live-owner",
                                "started_at": time.time() - 7200}), encoding="utf-8")
    try:
        with runlock.acquire(lock):
            pass
        blocked = False
    except runlock.AlreadyRunning:
        blocked = True
    check("old lock with live owner is not reclaimed", blocked and lock.exists())

    lock.write_text(json.dumps({"pid": os.getpid(),
                                "process_start_id": f"reused:{current_start}",
                                "lock_id": "reused-pid",
                                "started_at": time.time() - 7200}), encoding="utf-8")
    with runlock.acquire(lock):
        replacement_reclaimed = runlock._read_lock(lock)["lock_id"] != "reused-pid"
    check("old lock with reused PID is reclaimed", replacement_reclaimed and not lock.exists())

    with runlock.acquire(lock):
        lock.write_text(json.dumps({"pid": os.getpid(),
                                    "process_start_id": current_start,
                                    "lock_id": "replacement",
                                    "started_at": time.time()}), encoding="utf-8")
    check("release never deletes replacement lock", runlock._read_lock(lock)["lock_id"] ==
          "replacement")


class FakeAdapter:
    calls = 0

    def chat(self, request):
        self.calls += 1
        return provider_chat.ChatResult(
            "ok", "trace", input_tokens=2, output_tokens=1,
            finish_reason="stop", request_id="req-1", latency_seconds=0.01,
            provider=request.provider, model=request.model)


fake = FakeAdapter()
provider_chat.register("test", fake)
old_pause = provider_chat.pause_engaged
try:
    provider_chat.pause_engaged = lambda: False
    request = provider_chat.ChatRequest(
        "test", "m", "p", timeout_seconds=12, endpoint="https://registered.invalid",
        messages=({"role": "user", "content": "p"},), context_tokens=8192,
        response_token_reserve=1024, authentication_reference="ENV:TEST_KEY",
        purpose="critic", metadata={"task_id": 7})
    result = provider_chat.chat(request)
    check("provider-neutral adapter dispatches", result.content == "ok")
    check("full provider request contract survives dispatch",
          request.timeout_seconds == 12 and request.context_tokens == 8192 and
          request.response_token_reserve == 1024 and request.purpose == "critic" and
          request.authentication_reference == "ENV:TEST_KEY")
    check("full provider result contract is normalized",
          result.usage == {"input_tokens": 2, "output_tokens": 1} and
          result.finish_reason == "stop" and result.request_id == "req-1" and
          result.provider == "test" and result.model == "m")
    try:
        provider_chat.chat(provider_chat.ChatRequest("missing", "m", "p"))
        rejected = False
    except provider_chat.UnsupportedProvider as exc:
        rejected = exc.category == provider_chat.ErrorCategory.UNSUPPORTED_PROVIDER
    check("unknown provider fails loudly", rejected)

    provider_chat.pause_engaged = lambda: True
    before = fake.calls
    try:
        provider_chat.chat(provider_chat.ChatRequest("test", "m", "p"))
        paused = False
    except provider_chat.ExecutionPaused as exc:
        paused = exc.category == provider_chat.ErrorCategory.PAUSED
    check("canonical pause gate blocks before adapter invocation",
          paused and fake.calls == before)

    permit = provider_chat.authorize_single_paused_canary("test")
    canary_request = provider_chat.ChatRequest(
        "test", "m", "ping", purpose="connectivity_canary")
    permitted_result = provider_chat.chat(canary_request, pause_bypass=permit)
    check("scoped canary permit bypasses ESTOP once", permitted_result.content == "ok")
    before = fake.calls
    try:
        provider_chat.chat(canary_request, pause_bypass=permit)
        reused_permit_blocked = False
    except provider_chat.ExecutionPaused:
        reused_permit_blocked = True
    check("scoped canary permit cannot be reused",
          reused_permit_blocked and fake.calls == before)

    wrong_purpose_permit = provider_chat.authorize_single_paused_canary("test")
    try:
        provider_chat.chat(provider_chat.ChatRequest("test", "m", "ping", purpose="critic"),
                           pause_bypass=wrong_purpose_permit)
        wrong_purpose_blocked = False
    except provider_chat.ExecutionPaused:
        wrong_purpose_blocked = True
    check("scoped canary permit rejects other purposes", wrong_purpose_blocked)
finally:
    provider_chat.pause_engaged = old_pause


class FakeResponse:
    headers = {"X-Request-Id": "ollama-request-1"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"message": {"content": "answer", "thinking": "trace"},
                           "prompt_eval_count": 4, "eval_count": 2,
                           "done_reason": "stop"}).encode()


old_urlopen = provider_chat.urllib.request.urlopen
old_pause = provider_chat.pause_engaged
captured_http = {}
try:
    provider_chat.pause_engaged = lambda: False

    def fake_urlopen(request, timeout):
        captured_http["body"] = json.loads(request.data)
        captured_http["timeout"] = timeout
        return FakeResponse()

    provider_chat.urllib.request.urlopen = fake_urlopen
    ollama_result = provider_chat.chat(provider_chat.ChatRequest(
        "ollama", "local-model", "hello", timeout_seconds=9, purpose="finalization"))
    check("Ollama adapter preserves request and normalized response",
          captured_http["body"]["messages"][0]["content"] == "hello" and
          captured_http["timeout"] == 9 and ollama_result.input_tokens == 4 and
          ollama_result.output_tokens == 2 and ollama_result.finish_reason == "stop" and
          ollama_result.request_id == "ollama-request-1")

    def rate_limited(*args, **kwargs):
        raise urllib.error.HTTPError("http://ollama", 429, "limited", {}, None)

    provider_chat.urllib.request.urlopen = rate_limited
    try:
        provider_chat.chat(provider_chat.ChatRequest("ollama", "m", "p"))
        normalized_rate_limit = False
    except provider_chat.ProviderChatError as exc:
        normalized_rate_limit = (exc.category == provider_chat.ErrorCategory.RATE_LIMIT and
                                 exc.retryable)
    check("provider HTTP errors are normalized", normalized_rate_limit)
finally:
    provider_chat.urllib.request.urlopen = old_urlopen
    provider_chat.pause_engaged = old_pause


class BytePlusResponse:
    headers = {"X-Request-Id": "bp-request-1"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": "byteplus answer",
                                      "reasoning_content": "reasoning"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }).encode()


old_urlopen = provider_chat.urllib.request.urlopen
old_pause = provider_chat.pause_engaged
old_key = os.environ.get("ARK_API_KEY")
old_home = os.environ.get("HERMES_HOME")
captured_byteplus = {}
try:
    provider_chat.pause_engaged = lambda: False
    os.environ.pop("ARK_API_KEY", None)
    with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
        empty_home = Path(td)
        (empty_home / "config.yaml").write_text("{}\n", encoding="utf-8")
        os.environ["HERMES_HOME"] = str(empty_home)
        try:
            provider_chat.chat(provider_chat.ChatRequest(
                "byteplus_coding", "ark-code-latest", "p",
                authentication_reference="env:ARK_API_KEY"))
            missing_key_rejected = False
        except provider_chat.ProviderChatError as exc:
            missing_key_rejected = exc.category == provider_chat.ErrorCategory.AUTHENTICATION
    check("BytePlus missing credential fails closed", missing_key_rejected)

    if old_home is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = old_home
    os.environ["ARK_API_KEY"] = "test-only-secret"

    def fake_byteplus(request, timeout):
        captured_byteplus["url"] = request.full_url
        captured_byteplus["authorization"] = request.headers.get("Authorization")
        captured_byteplus["body"] = json.loads(request.data)
        return BytePlusResponse()

    provider_chat.urllib.request.urlopen = fake_byteplus
    byteplus_result = provider_chat.chat(provider_chat.ChatRequest(
        "byteplus_coding", "ark-code-latest", "hello", timeout_seconds=11,
        endpoint="https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        authentication_reference="env:ARK_API_KEY", purpose="synthesis"))
    check("BytePlus adapter uses registered Coding Plan route",
          captured_byteplus["url"].endswith("/api/coding/v3/chat/completions") and
          captured_byteplus["body"]["model"] == "ark-code-latest")
    check("BytePlus response and accounting are normalized",
          byteplus_result.content == "byteplus answer" and
          byteplus_result.input_tokens == 7 and byteplus_result.output_tokens == 3 and
          byteplus_result.request_id == "bp-request-1")
    check("BytePlus secret is resolved only at dispatch",
          captured_byteplus["authorization"] == "Bearer test-only-secret")
finally:
    provider_chat.urllib.request.urlopen = old_urlopen
    provider_chat.pause_engaged = old_pause
    if old_key is None:
        os.environ.pop("ARK_API_KEY", None)
    else:
        os.environ["ARK_API_KEY"] = old_key
    if old_home is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = old_home


old_candidates = execution._failover_candidates
old_worker = execution.hermes_worker
old_log = execution.log
events = []
primary = {"provider": "ollama", "model": "primary:cloud", "context_window": 10000}
fallback = {"provider": "ollama", "model": "fallback", "context_window": 10000}
try:
    execution._failover_candidates = lambda cfg, allow_local=True: [primary, fallback]
    execution.hermes_worker = lambda *a, **k: ("", {})
    execution.log = events.append
    execution.worker_with_failover("p", primary, Path("unused.json"), "t")
finally:
    execution._failover_candidates = old_candidates
    execution.hermes_worker = old_worker
    execution.log = old_log
check("zero-output fallback is never labeled succeeded",
      not any("failover succeeded" in event for event in events))

old_candidates = execution._failover_candidates
old_worker = execution.hermes_worker
old_log = execution.log
events = []
primary = {"provider": "ollama", "model": "primary:cloud", "context_window": 10000}
fallback = {"provider": "ollama", "model": "fallback:cloud", "context_window": 10000}
try:
    execution._failover_candidates = lambda cfg, allow_local=True: [primary, fallback]
    def fake_quota_worker(prompt, cfg, attempt_path, timeout):
        if cfg["model"] == "primary:cloud":
            return ("", {"process_error": "HTTP 429: Rate limit exceeded on Ollama Cloud",
                         "process_returncode": 1})
        return ("valid output from fallback with sufficient length for research deliverable",
                {"total_tokens": 150})
    execution.hermes_worker = fake_quota_worker
    execution.log = events.append
    out, usage, model_used, exhausted = execution.worker_with_failover(
        "prompt", primary, Path("unused.json"), "task 99"
    )
    check("stderr 429 triggers failover to fallback model",
          model_used["model"] == "fallback:cloud" and "valid output from fallback" in out and not exhausted)
    check("stderr 429 failover logs success on fallback",
          any("failover succeeded on ollama/fallback:cloud" in e for e in events))
finally:
    execution._failover_candidates = old_candidates
    execution.hermes_worker = old_worker
    execution.log = old_log

check("LEASE_SECONDS is at least 4200s", ledger.LEASE_SECONDS >= 4200)
check("LEASE_SECONDS exceeds LOCAL_FALLBACK_TIMEOUT_S with buffer",
      ledger.LEASE_SECONDS >= execution.LOCAL_FALLBACK_TIMEOUT_S + 600)


with tempfile.TemporaryDirectory(dir=ROOT / "workspace") as td:
    db = Path(td) / "ledger.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE tasks (
            created_at TEXT, mission_id TEXT, status TEXT, human_verdict TEXT,
            critic_notes TEXT, interventions INTEGER, cost_usd REAL,
            tokens_in INTEGER, tokens_out INTEGER)""")
        conn.execute("INSERT INTO tasks VALUES "
                     "('2099-01-01','m','infra_failed',NULL,'no evidence',0,0,0,0)")
    conn.close()
    old_db = ledger.LEDGER_DB
    try:
        ledger.LEDGER_DB = db
        fitness = ledger.weekly_fitness("2000-01-01")
    finally:
        ledger.LEDGER_DB = old_db
    gc.collect()
check("zero-evidence failure earns no cost-efficiency credit",
      fitness["cost_efficiency"] == 0.0)

canary_source = (ROOT / "workspace" / "validation" /
                 "byteplus_connectivity_canary.py").read_text(encoding="utf-8")
check("connectivity probe requires explicit acknowledgement",
      "--authorize-single-estop-bypass" in canary_source)
check("connectivity probe keeps a fixed trivial prompt", 'PROMPT = "ping"' in canary_source)
check("connectivity probe has no retry loop",
      "for attempt" not in canary_source and "while " not in canary_source)

ci_source = (ROOT / "scripts" / "ci.ps1").read_text(encoding="utf-8")
check("CI gate prefers the bootstrapped virtual environment",
      '.venv\\Scripts\\python.exe' in ci_source and '& $pythonExe -B tests/run_all.py' in ci_source)
check("CI fails closed when its virtual environment is missing",
      'elseif ($env:CI)' in ci_source and 'CI virtual environment is missing' in ci_source)

workflow_source = (ROOT / ".github" / "workflows" /
                   "model_free_gate.yml").read_text(encoding="utf-8")
action_refs = [line.split("uses:", 1)[1].split("#", 1)[0].strip()
               for line in workflow_source.splitlines() if "uses:" in line]
check("CI third-party actions use immutable commit SHAs",
      bool(action_refs) and all(
          ref.count("@") == 1 and len(ref.rsplit("@", 1)[-1]) == 40 and
          all(char in "0123456789abcdef" for char in ref.rsplit("@", 1)[-1])
          for ref in action_refs))

if fails:
    raise SystemExit(f"FAIL: {', '.join(fails)}")
print("\nAll architecture-blocker regressions passed.")
