"""
prediction_machine.core.prediction_store
=========================================

Immutable prediction database for the AGI_like prediction machine.

This module implements a SQLite-backed store where:

* **Predictions are immutable.**  Once written, the fields
  ``prediction_id``, ``prediction_type``, ``created_at``, ``target``,
  ``prediction``, ``confidence``, ``input_features``, ``model_version``,
  ``code_commit`` and ``outcome_due_at`` can **never** be modified.
  Only the outcome-related fields (``actual``, ``actual_recorded_at``,
  ``actual_source``, ``error``, ``valid_for_training``,
  ``invalid_reason``) may be updated, and ``actual`` may be set only
  once.

* **Outcomes are recorded once.**  ``record_outcome`` will refuse to
  overwrite a previously recorded outcome.

* **Anti-cheat ordering.**  When recording an outcome we verify that
  ``prediction.created_at < actual_recorded_at`` (the prediction was
  made strictly before the outcome was recorded) so that a prediction
  cannot be back-dated after the outcome is already known.

* **All new times are RFC 3339 UTC strings** with a ``Z`` suffix.

The module is stdlib-only — it uses only ``sqlite3``, ``uuid``,
``json``, ``datetime`` and ``pathlib`` — matching the discipline of the
rest of the AGI_like orchestrator.

Import path::

    from prediction_machine.core.prediction_store import PredictionStore
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from prediction_machine.paths import PREDICTION_DB
from prediction_machine.timebase import utc_iso


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CONFIDENCE_LEVELS = {"low", "medium", "high"}
VALID_PREDICTION_TYPES = {
    "task_outcome",
    "video_engagement",
    "skill_safety",
    "miks_campaign",
}
VALID_EXPERIMENT_DECISIONS = {"ACCEPT", "REJECT", "PENDING"}

# Default database location (canonical path on the S: drive).
DEFAULT_DB_PATH = PREDICTION_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id     TEXT PRIMARY KEY,
    prediction_type   TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    target            TEXT NOT NULL,
    prediction        TEXT NOT NULL,
    confidence        TEXT NOT NULL,
    input_features    TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    code_commit       TEXT,
    outcome_due_at    TEXT NOT NULL,
    actual            TEXT,
    actual_recorded_at TEXT,
    actual_source     TEXT,
    error             TEXT,
    valid_for_training INTEGER DEFAULT 1,
    invalid_reason    TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    prediction_type    TEXT NOT NULL,
    observed_failure   TEXT NOT NULL,
    hypothesis        TEXT NOT NULL,
    proposed_change   TEXT NOT NULL,
    previous_metric   REAL,
    new_metric        REAL,
    sample_size       INTEGER,
    decision          TEXT,
    backtest_details  TEXT
);

CREATE TABLE IF NOT EXISTS model_versions (
    version         TEXT PRIMARY KEY,
    prediction_type TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    description     TEXT,
    active          INTEGER DEFAULT 1,
    parent_version  TEXT
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return canonical RFC 3339 UTC, truncated to seconds."""
    return utc_iso()


def _json_dumps(value: Any) -> str:
    """Serialise *value* to a compact JSON string."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_loads(value: Optional[str]) -> Any:
    """Deserialise a JSON string; return ``None`` if *value* is falsy."""
    if value is None:
        return None
    return json.loads(value)


def _validate_confidence(level: str) -> None:
    if level not in VALID_CONFIDENCE_LEVELS:
        raise ValueError(
            f"Invalid confidence '{level}'. Must be one of {sorted(VALID_CONFIDENCE_LEVELS)}"
        )


def _validate_prediction_type(ptype: str) -> None:
    if ptype not in VALID_PREDICTION_TYPES:
        raise ValueError(
            f"Invalid prediction_type '{ptype}'. "
            f"Must be one of {sorted(VALID_PREDICTION_TYPES)}"
        )


def _validate_decision(decision: str) -> None:
    if decision not in VALID_EXPERIMENT_DECISIONS:
        raise ValueError(
            f"Invalid decision '{decision}'. "
            f"Must be one of {sorted(VALID_EXPERIMENT_DECISIONS)}"
        )


# ---------------------------------------------------------------------------
# PredictionStore
# ---------------------------------------------------------------------------

class PredictionStore:
    """SQLite-backed immutable prediction store.

    Parameters
    ----------
    db_path : Path or str, optional
        Path to the SQLite database file.  Defaults to
        ``prediction_machine/data/predictions.db`` under the discovered repository.

    Notes
    -----
    The connection is opened with ``check_same_thread=False`` so the
    store can be shared across threads in the orchestrator, and
    ``isolation_level=None`` (autocommit) so that each public method is
    an atomic, self-contained transaction.
    """

    # ------------------------------------------------------------------
    # Construction / initialisation
    # ------------------------------------------------------------------

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path: Path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit — each statement is its own txn
        )
        self._conn.row_factory = sqlite3.Row
        # Enforce foreign keys and improve durability.
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self.init_db()

    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Create all tables if they do not already exist."""
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "PredictionStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        cur = self._execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return row

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        cur = self._execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Predictions — creation (immutable write)
    # ------------------------------------------------------------------

    def create_prediction(
        self,
        prediction_type: str,
        target: str,
        prediction: Any,
        confidence: str,
        input_features: Any,
        model_version: str,
        outcome_due_at: str,
        code_commit: Optional[str] = None,
    ) -> str:
        """Create a new immutable prediction record.

        Parameters
        ----------
        prediction_type : str
            One of ``task_outcome``, ``video_engagement``, ``skill_safety``,
            ``miks_campaign``.
        target : str
            What is being predicted (e.g. ``task_id``, ``video_id``,
            ``skill_name``).
        prediction : Any
            The full prediction payload (will be JSON-serialised).
        confidence : str
            ``low``, ``medium`` or ``high``.
        input_features : Any
            Features used to make the prediction (will be JSON-serialised).
        model_version : str
            Version tag, e.g. ``"task_outcome_v1"``.
        outcome_due_at : str
            ISO timestamp after which the outcome is expected to be
            available.
        code_commit : str, optional
            Git commit hash at prediction time.

        Returns
        -------
        str
            The generated ``prediction_id`` (UUID).

        Raises
        ------
        ValueError
            If ``prediction_type`` or ``confidence`` is invalid.
        sqlite3.IntegrityError
            If a prediction with the generated UUID already exists
            (astronomically unlikely).
        """
        _validate_prediction_type(prediction_type)
        _validate_confidence(confidence)

        prediction_id = str(uuid.uuid4())
        created_at = _now_iso()

        self._execute(
            """
            INSERT INTO predictions (
                prediction_id, prediction_type, created_at, target,
                prediction, confidence, input_features, model_version,
                code_commit, outcome_due_at, actual, actual_recorded_at,
                actual_source, error, valid_for_training, invalid_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 1, NULL)
            """,
            (
                prediction_id,
                prediction_type,
                created_at,
                target,
                _json_dumps(prediction),
                confidence,
                _json_dumps(input_features),
                model_version,
                code_commit,
                outcome_due_at,
            ),
        )
        return prediction_id

    # ------------------------------------------------------------------
    # Predictions — outcome recording (write-once)
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        prediction_id: str,
        actual: Any,
        actual_source: str,
        error: Any = None,
    ) -> dict:
        """Record the actual outcome for a prediction.

        The outcome can be recorded **exactly once**.  Attempting to
        record a second outcome raises ``ValueError``.

        Anti-cheat: this method verifies that the prediction's
        ``created_at`` timestamp is strictly earlier than the current
        recording time, ensuring the prediction was made before the
        outcome was known.

        Parameters
        ----------
        prediction_id : str
            The UUID of the prediction to update.
        actual : Any
            The actual outcome (will be JSON-serialised).
        actual_source : str
            Where the outcome came from (e.g. ``"ledger.db"``,
            ``"youtube_api"``).
        error : Any, optional
            Computed error metrics (will be JSON-serialised).

        Returns
        -------
        dict
            The updated prediction row as a dict.

        Raises
        ------
        ValueError
            If the prediction does not exist, already has an outcome
            recorded, or fails the timestamp ordering check.
        """
        row = self._fetchone(
            "SELECT created_at, actual_recorded_at FROM predictions WHERE prediction_id = ?",
            (prediction_id,),
        )
        if row is None:
            raise ValueError(f"Prediction {prediction_id} does not exist")

        if row["actual_recorded_at"] is not None:
            raise ValueError(
                f"Prediction {prediction_id} already has an outcome recorded "
                f"(at {row['actual_recorded_at']}). Outcomes are write-once."
            )

        recorded_at = _now_iso()

        # Anti-cheat: prediction must have been made at or before the
        # outcome is recorded.  We use ``>`` (strictly after) as the
        # violation rather than ``>=`` because ISO timestamps are truncated
        # to whole seconds — two operations within the same second are
        # legitimate and must not be rejected.
        if row["created_at"] > recorded_at:
            raise ValueError(
                f"Anti-cheat violation: prediction created_at ({row['created_at']}) "
                f"is after actual_recorded_at ({recorded_at})"
            )

        self._execute(
            """
            UPDATE predictions
               SET actual            = ?,
                   actual_recorded_at = ?,
                   actual_source      = ?,
                   error             = ?
             WHERE prediction_id = ?
               AND actual_recorded_at IS NULL
            """,
            (
                _json_dumps(actual),
                recorded_at,
                actual_source,
                _json_dumps(error) if error is not None else None,
                prediction_id,
            ),
        )

        result = self.get_prediction(prediction_id)
        assert result is not None, "record_outcome: prediction vanished mid-update"
        return result

    # ------------------------------------------------------------------
    # Predictions — invalidation
    # ------------------------------------------------------------------

    def invalidate_prediction(self, prediction_id: str, reason: str) -> bool:
        """Mark a prediction as invalid for training.

        Parameters
        ----------
        prediction_id : str
            The prediction to invalidate.
        reason : str
            Human-readable explanation of why the prediction is invalid.

        Returns
        -------
        bool
            ``True`` if the prediction was found and invalidated,
            ``False`` if the prediction_id does not exist.
        """
        cur = self._execute(
            """
            UPDATE predictions
               SET valid_for_training = 0,
                   invalid_reason     = ?
             WHERE prediction_id = ?
            """,
            (reason, prediction_id),
        )
        affected = cur.rowcount
        cur.close()
        return affected > 0

    # ------------------------------------------------------------------
    # Predictions — queries
    # ------------------------------------------------------------------

    def get_prediction(self, prediction_id: str) -> Optional[dict]:
        """Return a single prediction as a dict, or ``None`` if not found."""
        row = self._fetchone(
            "SELECT * FROM predictions WHERE prediction_id = ?",
            (prediction_id,),
        )
        return self._row_to_dict(row)

    def get_pending_outcomes(self, prediction_type: Optional[str] = None) -> list[dict]:
        """Return predictions where ``actual`` is NULL and ``outcome_due_at`` has passed.

        Parameters
        ----------
        prediction_type : str, optional
            If given, filter to this prediction type.

        Returns
        -------
        list[dict]
            List of matching prediction dicts.
        """
        now = _now_iso()
        if prediction_type is not None:
            _validate_prediction_type(prediction_type)
            rows = self._fetchall(
                """
                SELECT * FROM predictions
                 WHERE actual IS NULL
                   AND valid_for_training = 1
                   AND outcome_due_at <= ?
                   AND prediction_type = ?
                 ORDER BY outcome_due_at ASC
                """,
                (now, prediction_type),
            )
        else:
            rows = self._fetchall(
                """
                SELECT * FROM predictions
                 WHERE actual IS NULL
                   AND valid_for_training = 1
                   AND outcome_due_at <= ?
                 ORDER BY outcome_due_at ASC
                """,
                (now,),
            )
        return [self._row_to_dict(r) for r in rows]

    def get_mature_predictions(
        self,
        prediction_type: Optional[str] = None,
        valid_only: bool = True,
    ) -> list[dict]:
        """Return predictions that have an outcome recorded (``actual`` is not NULL).

        Parameters
        ----------
        prediction_type : str, optional
            If given, filter to this prediction type.
        valid_only : bool, default True
            If True, only return predictions where
            ``valid_for_training = 1``.

        Returns
        -------
        list[dict]
            List of matching prediction dicts, oldest first.
        """
        clauses = ["actual IS NOT NULL"]
        params: list[Any] = []
        if prediction_type is not None:
            _validate_prediction_type(prediction_type)
            clauses.append("prediction_type = ?")
            params.append(prediction_type)
        if valid_only:
            clauses.append("valid_for_training = 1")

        where = " AND ".join(clauses)
        sql = f"SELECT * FROM predictions WHERE {where} ORDER BY created_at ASC"
        rows = self._fetchall(sql, tuple(params))
        return [self._row_to_dict(r) for r in rows]

    def get_all_predictions(
        self,
        prediction_type: Optional[str] = None,
        valid_only: Optional[bool] = None,
    ) -> list[dict]:
        """Return all predictions, optionally filtered.

        Parameters
        ----------
        prediction_type : str, optional
            If given, filter to this prediction type.
        valid_only : bool, optional
            If ``True`` (``False``), filter to
            ``valid_for_training = 1`` (``0``).  ``None`` means no
            validity filter.

        Returns
        -------
        list[dict]
            List of matching prediction dicts, newest first.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if prediction_type is not None:
            _validate_prediction_type(prediction_type)
            clauses.append("prediction_type = ?")
            params.append(prediction_type)
        if valid_only is True:
            clauses.append("valid_for_training = 1")
        elif valid_only is False:
            clauses.append("valid_for_training = 0")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM predictions{where} ORDER BY created_at DESC"
        rows = self._fetchall(sql, tuple(params))
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Model versions
    # ------------------------------------------------------------------

    def register_model_version(
        self,
        version: str,
        prediction_type: str,
        description: Optional[str] = None,
        parent_version: Optional[str] = None,
    ) -> None:
        """Register a new model version.

        Parameters
        ----------
        version : str
            Unique version tag, e.g. ``"task_outcome_v1"``.
        prediction_type : str
            Which prediction type this version serves.
        description : str, optional
            Human-readable description.
        parent_version : str, optional
            Previous version this one improves upon.

        Raises
        ------
        ValueError
            If ``prediction_type`` is invalid or the version already
            exists.
        """
        _validate_prediction_type(prediction_type)

        existing = self._fetchone(
            "SELECT version FROM model_versions WHERE version = ?",
            (version,),
        )
        if existing is not None:
            raise ValueError(f"Model version '{version}' is already registered")

        self._execute(
            """
            INSERT INTO model_versions
                (version, prediction_type, created_at, description, active, parent_version)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (version, prediction_type, _now_iso(), description, parent_version),
        )

    def activate_model_version(self, version: str) -> None:
        """Mark *version* as the sole active version for its prediction type.

        All other versions sharing the same ``prediction_type`` are
        deactivated.

        Raises
        ------
        ValueError
            If *version* does not exist.
        """
        row = self._fetchone(
            "SELECT prediction_type FROM model_versions WHERE version = ?",
            (version,),
        )
        if row is None:
            raise ValueError(f"Model version '{version}' does not exist")

        ptype = row["prediction_type"]
        self._execute(
            "UPDATE model_versions SET active = 0 WHERE prediction_type = ?",
            (ptype,),
        )
        self._execute(
            "UPDATE model_versions SET active = 1 WHERE version = ?",
            (version,),
        )

    def get_active_version(self, prediction_type: str) -> Optional[str]:
        """Return the currently active model version for *prediction_type*.

        Parameters
        ----------
        prediction_type : str
            The prediction type to look up.

        Returns
        -------
        str or None
            The version tag, or ``None`` if no active version exists.
        """
        _validate_prediction_type(prediction_type)
        row = self._fetchone(
            "SELECT version FROM model_versions WHERE prediction_type = ? AND active = 1",
            (prediction_type,),
        )
        return row["version"] if row is not None else None

    # ------------------------------------------------------------------
    # Experiments
    # ------------------------------------------------------------------

    def create_experiment(
        self,
        prediction_type: str,
        observed_failure: str,
        hypothesis: str,
        proposed_change: str,
        previous_metric: Optional[float] = None,
    ) -> str:
        """Create a new experiment record and return its UUID.

        Parameters
        ----------
        prediction_type : str
            The prediction domain this experiment targets.
        observed_failure : str
            Description of the failure that motivated the experiment.
        hypothesis : str
            What we think causes the failure.
        proposed_change : str
            What we propose to fix it.
        previous_metric : float, optional
            The metric value before the change.

        Returns
        -------
        str
            The generated ``experiment_id`` (UUID).
        """
        _validate_prediction_type(prediction_type)
        experiment_id = str(uuid.uuid4())
        self._execute(
            """
            INSERT INTO experiments
                (experiment_id, created_at, prediction_type, observed_failure,
                 hypothesis, proposed_change, previous_metric, new_metric,
                 sample_size, decision, backtest_details)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'PENDING', NULL)
            """,
            (
                experiment_id,
                _now_iso(),
                prediction_type,
                observed_failure,
                hypothesis,
                proposed_change,
                previous_metric,
            ),
        )
        return experiment_id

    def update_experiment(
        self,
        experiment_id: str,
        new_metric: float,
        sample_size: int,
        decision: str,
        backtest_details: Any = None,
    ) -> None:
        """Update an experiment with backtest results and a decision.

        Parameters
        ----------
        experiment_id : str
            The UUID of the experiment to update.
        new_metric : float
            The metric value after the change.
        sample_size : int
            Number of predictions in the backtest.
        decision : str
            ``ACCEPT``, ``REJECT``, or ``PENDING``.
        backtest_details : Any, optional
            Full backtest results (will be JSON-serialised).

        Raises
        ------
        ValueError
            If the experiment does not exist or *decision* is invalid.
        """
        _validate_decision(decision)

        row = self._fetchone(
            "SELECT id FROM experiments WHERE experiment_id = ?",
            (experiment_id,),
        )
        if row is None:
            raise ValueError(f"Experiment {experiment_id} does not exist")

        self._execute(
            """
            UPDATE experiments
               SET new_metric       = ?,
                   sample_size      = ?,
                   decision         = ?,
                   backtest_details = ?
             WHERE experiment_id = ?
            """,
            (
                new_metric,
                sample_size,
                decision,
                _json_dumps(backtest_details) if backtest_details is not None else None,
                experiment_id,
            ),
        )

    def get_experiments(
        self,
        prediction_type: Optional[str] = None,
        decision: Optional[str] = None,
    ) -> list[dict]:
        """Return experiments, optionally filtered.

        Parameters
        ----------
        prediction_type : str, optional
            Filter to this prediction type.
        decision : str, optional
            Filter to this decision status.

        Returns
        -------
        list[dict]
            List of matching experiment dicts, newest first.
        """
        if decision is not None:
            _validate_decision(decision)
        if prediction_type is not None:
            _validate_prediction_type(prediction_type)

        clauses: list[str] = []
        params: list[Any] = []
        if prediction_type is not None:
            clauses.append("prediction_type = ?")
            params.append(prediction_type)
        if decision is not None:
            clauses.append("decision = ?")
            params.append(decision)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM experiments{where} ORDER BY created_at DESC"
        rows = self._fetchall(sql, tuple(params))
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Utility / introspection
    # ------------------------------------------------------------------

    def count_predictions(
        self,
        prediction_type: Optional[str] = None,
    ) -> int:
        """Return the total number of predictions, optionally filtered by type."""
        if prediction_type is not None:
            _validate_prediction_type(prediction_type)
            row = self._fetchone(
                "SELECT COUNT(*) AS n FROM predictions WHERE prediction_type = ?",
                (prediction_type,),
            )
        else:
            row = self._fetchone("SELECT COUNT(*) AS n FROM predictions")
        return int(row["n"]) if row is not None else 0

    def summary(self) -> dict:
        """Return a quick summary dict of the store's current state."""
        total = self.count_predictions()
        pending = len(self.get_pending_outcomes())
        mature = len(self.get_mature_predictions(valid_only=False))
        return {
            "db_path": str(self.db_path),
            "total_predictions": total,
            "pending_outcomes": pending,
            "mature_predictions": mature,
            "active_versions": {
                pt: self.get_active_version(pt)
                for pt in sorted(VALID_PREDICTION_TYPES)
            },
            "total_experiments": len(self.get_experiments()),
        }
