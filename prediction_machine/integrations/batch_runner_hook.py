"""Batch-runner hook — auto-predict before running tasks.

A minimal, fault-tolerant module that ``batch_runner.py`` can import to
automatically create predictions before tasks run and record outcomes
after they complete.

Usage in batch_runner.py::

    from prediction_machine.integrations.batch_runner_hook import (
        before_task_runs,
        after_task_completes,
    )

    # Before running a task:
    pred_id = before_task_runs(task_id, spec, mission_id)

    # ... run the task ...

    # After the task completes:
    error = after_task_completes(task_id)

If the prediction machine fails for any reason, these functions return
None and log the error — the batch_runner continues normally.

Stdlib-only.  Python 3.11.
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PM_DIR = os.path.dirname(_THIS_DIR)          # prediction_machine/
_REPO_DIR = os.path.dirname(_PM_DIR)          # S:/AGI_like/

if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

# ---------------------------------------------------------------------------
# Lazy singleton state
# ---------------------------------------------------------------------------

_store = None      # cached PredictionStore
_integration = None  # cached TaskOutcomeIntegration


def _get_store():
    """Lazily import and create a PredictionStore singleton."""
    global _store
    if _store is None:
        from prediction_machine.core.prediction_store import PredictionStore
        _store = PredictionStore()
    return _store


def _get_integration():
    """Lazily create a TaskOutcomeIntegration singleton."""
    global _integration
    if _integration is None:
        from prediction_machine.integrations.task_outcome_integration import (
            TaskOutcomeIntegration,
        )
        _integration = TaskOutcomeIntegration()
    return _integration


# ---------------------------------------------------------------------------
# Public hook functions
# ---------------------------------------------------------------------------

def before_task_runs(
    task_id: int,
    spec: str,
    mission_id: str,
) -> Optional[str]:
    """Create a prediction for a task before it runs.

    Parameters
    ----------
    task_id : int
        The ledger task_id.
    spec : str
        Task spec text.
    mission_id : str
        Mission identifier.

    Returns
    -------
    str or None
        The prediction_id on success, or None if the prediction machine
        failed or is unavailable.
    """
    try:
        store = _get_store()
        integration = _get_integration()

        # Avoid duplicate predictions
        from prediction_machine.integrations.task_outcome_integration import (
            get_predicted_task_ids,
        )
        already_predicted = get_predicted_task_ids(store)
        if str(task_id) in already_predicted:
            return None

        return integration.predict_for_task(task_id, spec, mission_id, store)

    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(
            f"batch_runner_hook.before_task_runs: "
            f"prediction failed for task {task_id} (continuing):\n{tb}\n"
        )
        return None


def after_task_completes(task_id: int) -> Optional[dict]:
    """Record the outcome after a task completes.

    Parameters
    ----------
    task_id : int
        The ledger task_id.

    Returns
    -------
    dict or None
        The error dict on success, or None if no prediction was found
        or the prediction machine failed.
    """
    try:
        store = _get_store()
        integration = _get_integration()
        return integration.record_task_outcome(task_id, store)

    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(
            f"batch_runner_hook.after_task_completes: "
            f"outcome recording failed for task {task_id} (continuing):\n{tb}\n"
        )
        return None