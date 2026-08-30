# DeepSeek Handoff & Continuity Dossier — 2026-08-30

**Author:** Gemini CLI (Independent Principal Architect & Reviewer)  
**Recipient:** DeepSeek-V4-pro / Claude / Cade  
**Universal Entry Point:** `AGENTS.md` $\to$ `.harness/continuity/current.json` $\to$ `python orchestrator/continuity.py recover`  
**Current Date/Time:** 2026-08-30T05:00:00+02:00  
**Overall Status:** Operational & Prepared for P0 Trajectory Stream Implementation

---

## 1. Executive Continuity Snapshot

1. **Architecture Hardening (A1, A2, A3 Completed & Verified):**
   - **F-01 / A1 (Failover Stderr 429 Detection):** In `orchestrator/execution.py`, `worker_with_failover` checks `combined_error` with `is_quota_error()`. Proven live in Task 105 when BytePlus returned 429.
   - **F-02 / A2 (Lease Harmonization):** In `orchestrator/ledger.py`, `LEASE_SECONDS = 4200` (70m), safely exceeding fallback timeouts.
   - **F-03 / A3 (Fact Deduplication):** In `orchestrator/evaluation.py`, `extract_facts()` enforces idempotency via `SELECT 1 FROM facts WHERE statement=? AND source_task_id=?`.

2. **Dispatcher & Isolation Hardening:**
   - **Hermes Cron Regex:** In `workspace/validation/cohort_isolation.py:76`, candidate ID parsing uses lookarounds `(?<![-0-9a-f])[0-9a-f]{12}(?![-0-9a-f])` to prevent UUID error string suffix collisions.
   - **Gateway Status Regex:** In `workspace/validation/cohort_isolation.py:96`, pattern `"no gateway process detected"` accurately captures inactive gateway states.

3. **M1 Adjudication & Forensic Analysis (Task 104 vs Task 105):**
   - **Task 104 (Execution Success / Criteria Double-Bind):** Executed end-to-end (243.2s, 49.6k tokens in / 11.2k tokens out, 0% dead URLs on citecheck). Failed critic solely because PromptHero MAU and free/paid split are private corporate data that cannot be verified without hallucinating.
   - **M1 Criteria Harmonization:** DeepSeek's added line (`"- [ ] If MAU, model categories, or free/paid split data is not publicly available, explicitly declare it as unavailable with the specific source attempted and the reason"`) is **APPROVED** and harmonizes M1 with M3, M4, M5, and M7.
   - **Task 105 (Upstream Quota Failover):** Task 105 tested the failover chain when BytePlus and Ollama Cloud returned 429. Controlled window cleanly restored (`phase: "restored"`, `ESTOP engaged: True`).

---

## 2. P0 Trajectory Event Stream Specification

DeepSeek is requested to implement the **P0 Unified Trajectory Event Stream** (`runs/task{id}.trajectory.jsonl`):

### Module: `orchestrator/trajectory.py`
```python
"""Structured append-only trajectory writer for task execution."""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

class TrajectoryWriter:
    def __init__(self, trajectory_path: Path, task_id: int, mission_id: str):
        self.path = trajectory_path
        self.task_id = task_id
        self.mission_id = mission_id
        self.sequence = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        stage: str,
        event_type: str,
        actor: str = "orchestrator",
        payload: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "schema_version": 1,
            "event_id": f"evt-{self.task_id}-{self.sequence:04d}",
            "sequence": self.sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "stage": stage,
            "event_type": event_type,
            "actor": actor,
            "payload": payload or {},
        }
        if metrics:
            event["metrics"] = metrics
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return event
```

### Integration Points:
1. `orchestrator/task_runner.py`: Init `TrajectoryWriter` and emit `task_started`, `task_completed`/`task_failed`.
2. `orchestrator/execution.py`: Emit `provider_selected` and `failover_attempted`.
3. `orchestrator/retrieval_progress.py`: Emit `tool_call_finished` and `strategy_transition`.
4. `orchestrator/evaluation.py`: Emit `citecheck_completed` and `critic_evaluated`.

---

## 3. Prioritized Engineering Sequence for DeepSeek

1. **Commit Foundational Refactors:** Commit `outcomes.py`, `run_task.py`, and `integrity.py`.
2. **Implement P0 Trajectory Stream:** Create `orchestrator/trajectory.py` and unit test `tests/test_trajectory_event_stream.py`.
3. **Execute Controlled M1 Rerun:** When BytePlus quota restores, run:
   ```powershell
   python workspace/validation/run_cohort.py --controlled-window --only M1
   ```
4. **Advance to M2–M7:** Complete the full validation cohort.
