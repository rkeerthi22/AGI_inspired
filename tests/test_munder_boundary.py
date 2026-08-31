"""Model-free regression: Munder hive boundary enforcement (2026-08-31).

Drives enforce.js (the hive PreToolUse deny/ownership choke point) as a
subprocess with synthetic hook payloads.  No real hive agents, no Claude
Code, no live tools; the audit log is redirected into a temp home.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

ENFORCE = Path(r"S:\MunderState\AGI_like\hive\bin\enforce.js")
# The shipped enforce.js defaults to the real paths; tests point it at a
# sandbox via environment overrides it supports (HIVE_HOME-like).
checks = {"enforce.js present": ENFORCE.is_file()}


def run_hook(payload: dict, env_extra: dict | None = None) -> tuple[int, str]:
    """Run enforce.js with the payload; sandbox paths via env overrides."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["node", str(ENFORCE)],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, env=env)
    return proc.returncode, proc.stdout.strip()


with tempfile.TemporaryDirectory() as td:
    hive_home = Path(td) / "hive-home"
    hive_home.mkdir()
    hermes_home = Path(td) / "hermes-home"
    hermes_home.mkdir()
    hermes_home.joinpath("ESTOP").write_text('{"reason":"test"}\n',
                                             encoding="utf-8")

    # Sandbox the ownership registry: a temp AGI-like tree with an
    # ACTIVE_WORK.json the tests control.
    agi_sandbox = Path(td) / "AGI_like"
    (agi_sandbox / "docs").mkdir(parents=True)
    active_work = {
        "active_agents": [
            {"agent": "dwight-mtgg4xv5", "read_only": False,
             "owned_paths": [str(agi_sandbox / "orchestrator")]},
            {"agent": "pam-mtgg4sp1", "read_only": True, "owned_paths": []},
            {"agent": "codex", "read_only": True, "owned_paths": []},
        ]
    }
    (agi_sandbox / "docs" / "ACTIVE_WORK.json").write_text(
        json.dumps(active_work), encoding="utf-8")

    env_extra = {
        "MUNDER_HARNESS_HOME": str(hive_home),
        "HERMES_HOME": str(hermes_home),
        "MUNDER_AGI_REPO": str(agi_sandbox),
    }

    def hook(agent, tool, tool_input):
        return run_hook({"agent_id": agent, "tool_name": tool,
                         "tool_input": tool_input}, env_extra)

    # --- live-command deny -------------------------------------------
    code, out = hook("god", "Bash",
                     {"command": "python workspace/validation/run_cohort.py --controlled-window --only M1"})
    checks["run_cohort invocation denied"] = (code == 2 and
        json.loads(out)["permissionDecision"] == "deny")

    code, out = hook("god", "Bash",
                     {"command": "python orchestrator/run_task.py --mission 001"})
    checks["run_task invocation denied"] = (code == 2)

    code, out = hook("god", "Bash",
                     {"command": "python orchestrator/batch_runner.py"})
    checks["batch_runner invocation denied"] = (code == 2)

    code, out = hook("god", "Bash",
                     {"command": "python workspace/validation/byteplus_connectivity_canary.py --authorize-single-estop-bypass"})
    checks["canary with bypass flag denied"] = (code == 2)

    code, out = hook("god", "Bash",
                     {"command": "rm C:/Users/moham/AppData/Local/hermes/ESTOP"})
    checks["ESTOP sentinel deletion denied"] = (code == 2)

    code, out = hook("god", "Bash",
                     {"command": 'echo "" > C:\\Users\\moham\\AppData\\Local\\hermes\\ESTOP'})
    checks["ESTOP overwrite via echo denied"] = (code == 2)

    code, out = hook("god", "Bash",
                     {"command": "python orchestrator/execution_pause.py --authorize-canary"})
    checks["execution_pause authorize-canary denied"] = (code == 2)

    code, out = hook("god", "Bash",
                     {"command": "rm C:/Users/moham/AppData/Local/hermes/.canary-operator-auth.json"})
    checks["canary marker deletion denied"] = (code == 2)

    # --- text about live commands remains ALLOWED --------------------
    # The deny boundary matches EXECUTION, not prose.  A write whose BODY
    # merely mentions run_cohort.py is allowed for an owner of that path.
    code, out = hook("dwight-mtgg4xv5", "Write",
                     {"file_path": str(agi_sandbox / "orchestrator" / "proposal.md"),
                      "content": "Operator, please consider running run_cohort.py for M1."})
    checks["text proposals about live commands allowed (owned path)"] = (code == 0)
    # And a shell command that merely MENTIONS the tool without invoking it
    # is denied only when it matches an execution pattern; `grep run_cohort`
    # reads source (allowed) while `python ... run_cohort.py` executes (denied).
    code, out = hook("dwight-mtgg4xv5", "Bash",
                     {"command": "grep -n run_cohort workspace/validation/run_cohort.py"})
    checks["source grep mentioning run_cohort allowed"] = (code == 0)
    # Command-substitution smuggling is still denied even inside a read verb.
    code, out = hook("dwight-mtgg4xv5", "Bash",
                     {"command": "grep $(python workspace/validation/run_cohort.py) file.txt"})
    checks["substitution smuggling in read verb denied"] = (code == 2)
    # Redirect into a live-control file is denied even from a read verb.
    code, out = hook("dwight-mtgg4xv5", "Bash",
                     {"command": "grep pattern source.py > workspace/validation/run_cohort.py"})
    checks["redirect into live file denied"] = (code == 2)
    # Compound command with a benign prefix cannot smuggle an invocation.
    code, out = hook("dwight-mtgg4xv5", "Bash",
                     {"command": "echo hi && python orchestrator/run_task.py --mission 001"})
    checks["compound smuggle after benign prefix denied"] = (code == 2)

    # --- ordinary development commands remain ALLOWED ------------------
    code, out = hook("dwight-mtgg4xv5", "Bash",
                     {"command": "python -B tests/run_all.py"})
    checks["ordinary test command allowed"] = (code == 0)

    code, out = hook("dwight-mtgg4xv5", "Bash",
                     {"command": "git status --porcelain"})
    checks["git status allowed"] = (code == 0)

    # --- write ownership enforcement -----------------------------------
    # dwight owns orchestrator/ in the sandbox registry.
    code, out = hook("dwight-mtgg4xv5", "Edit",
                     {"file_path": str(agi_sandbox / "orchestrator" / "mailbus.py"),
                      "old_string": "a", "new_string": "b"})
    checks["owned-path write allowed"] = (code == 0)

    # jim is not registered → deny.
    code, out = hook("jim-mtgg46e6", "Edit",
                     {"file_path": str(agi_sandbox / "orchestrator" / "mailbus.py"),
                      "old_string": "a", "new_string": "b"})
    checks["unregistered agent write denied"] = (code == 2)

    # pam is read-only → deny even with owned_paths.
    code, out = hook("pam-mtgg4sp1", "Write",
                     {"file_path": str(agi_sandbox / "docs" / "review.md"),
                      "content": "review text"})
    checks["read-only reviewer write denied"] = (code == 2)

    # god writing outside its owned scope → deny.
    code, out = hook("god", "Write",
                     {"file_path": str(agi_sandbox / "docs" / "ACTIVE_WORK.json"),
                      "content": "{}"})
    checks["out-of-scope write denied"] = (code == 2)

    # Malformed registry → fail closed for AGI writes.
    (agi_sandbox / "docs" / "ACTIVE_WORK.json").write_text("{broken",
                                                           encoding="utf-8")
    code, out = hook("dwight-mtgg4xv5", "Edit",
                     {"file_path": str(agi_sandbox / "orchestrator" / "mailbus.py"),
                      "old_string": "a", "new_string": "b"})
    checks["malformed registry fails closed"] = (code == 2)
    (agi_sandbox / "docs" / "ACTIVE_WORK.json").write_text(
        json.dumps(active_work), encoding="utf-8")

    # Writes outside the AGI repo (hive-internal) are not ownership-gated.
    code, out = hook("god", "Write",
                     {"file_path": r"S:\MunderState\AGI_like\hive\board.md",
                      "content": "note"})
    checks["hive-internal write not ownership-gated"] = (code == 0)

    # Audit log received entries (deny + allow) and contains no secret blobs.
    audit_path = hive_home / "boundary_audit.jsonl"
    checks["audit log exists"] = audit_path.is_file()
    if audit_path.is_file():
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in lines]
        checks["audit has deny entries"] = any(
            e["event"] == "deny" for e in parsed)
        checks["audit has allow entries"] = any(
            e["event"] == "allow" for e in parsed)
        checks["audit never logs message bodies"] = all(
            "content" not in e and "command" not in e for e in parsed)
        checks["audit entries are timestamped"] = all(
            "ts" in e for e in parsed)

failed = []
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failed.append(name)
if failed:
    raise SystemExit("munder boundary failures: " + ", ".join(failed))