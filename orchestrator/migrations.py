"""SQLite schema versioning via ``PRAGMA user_version``.

Each managed database has a versioned migration path.  Migrations are
applied in order; ``user_version`` records the current schema version.
Forward-only by default; rollback is supported per-migration when the
``down`` key is provided.

Usage::

    from migrations import migrate, MigrationError
    migrate("ledger", ledger.LEDGER_DB)
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


class MigrationError(RuntimeError):
    """A migration could not be applied or rolled back."""


@dataclass(frozen=True)
class Migration:
    """A versioned schema change.

    Parameters
    ----------
    version : int
        Monotonic version number (1-based).
    description : str
        Human-readable label.
    up : str
        SQL statements to apply the migration.
    down : str | None
        SQL to revert the migration (``None`` = irreversible).
    check : str | None
        Optional SQL that returns a row when the migration is already
        applied (used for idempotent re-checks).
    """

    version: int
    description: str
    up: str
    down: str | None = None
    check: str | None = None


# ── Registered migration paths ──────────────────────────────────────────────

# Each entry maps a logical database name to its migrations.
# Migrations are applied in ascending version order.
#
# To add a migration: append a new ``Migration(version=N+1, ...)`` to
# the list.  Never renumber, delete, or modify existing migrations.

LEDGER_MIGRATIONS: Sequence[Migration] = (
    Migration(
        version=1,
        description="Establish baseline schema (ledger.db — tasks, entities, experiences, etc.)",
        up="""-- Baseline: the schema is already applied by the harness.
-- This migration records version 1 as the current state.
SELECT 1;
""",
        check="SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'",
    ),
    Migration(
        version=2,
        description="Add task owner-process identity columns for immediate orphan recovery",
        up="""ALTER TABLE tasks ADD COLUMN owner_pid INTEGER;
ALTER TABLE tasks ADD COLUMN owner_process_start_id TEXT;
""",
        down="""ALTER TABLE tasks DROP COLUMN owner_process_start_id;
ALTER TABLE tasks DROP COLUMN owner_pid;
""",
        check="SELECT 1 FROM pragma_table_info('tasks') WHERE name='owner_pid'",
    ),
)

LEDGERBOOK_MIGRATIONS: Sequence[Migration] = (
    Migration(
        version=1,
        description="Establish baseline schema (ledgerbook.db — same structure as ledger)",
        up="""-- Baseline: the schema mirrors ledger.db.
-- Records version 1 as the current state.
SELECT 1;
""",
        check="SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'",
    ),
    Migration(
        version=2,
        description="Add task owner-process identity columns for immediate orphan recovery parity",
        up="""ALTER TABLE tasks ADD COLUMN owner_pid INTEGER;
ALTER TABLE tasks ADD COLUMN owner_process_start_id TEXT;
""",
        down="""ALTER TABLE tasks DROP COLUMN owner_process_start_id;
ALTER TABLE tasks DROP COLUMN owner_pid;
""",
        check="SELECT 1 FROM pragma_table_info('tasks') WHERE name='owner_pid'",
    ),
)

FTS_INDEX_MIGRATIONS: Sequence[Migration] = (
    Migration(
        version=1,
        description="Establish FTS5 schema (memory/fts_index.db — chunks + chunks_fts)",
        up="""-- The FTS index is created by memory_fts.py on first connect.
-- This migration records version 1 as the current state.
SELECT 1;
""",
        check="SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'",
    ),
)

# Registry: logical name -> (path resolver, migrations)
_MIGRATION_REGISTRY: dict[str, tuple[Callable[[], Path], Sequence[Migration]]] = {}


def register_database(
    name: str,
    path_fn: Callable[[], Path],
    migrations: Sequence[Migration],
) -> None:
    """Register a database for migration management."""
    _MIGRATION_REGISTRY[name] = (path_fn, migrations)


def registered_databases() -> dict[str, int]:
    """Return {name: current_version} for all registered databases."""
    result: dict[str, int] = {}
    for name in list(_MIGRATION_REGISTRY):
        try:
            path_fn, _ = _MIGRATION_REGISTRY[name]
            db_path = path_fn()
            if db_path.is_file():
                conn = sqlite3.connect(db_path)
                try:
                    result[name] = conn.execute("PRAGMA user_version").fetchone()[0]
                finally:
                    conn.close()
            else:
                result[name] = -1  # not yet created
        except Exception:
            result[name] = -2  # error
    return result


# ── Core migration logic ────────────────────────────────────────────────────


def _current_version(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _set_version(db_path: Path, version: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {version}")


def migrate(db_name: str, db_path: str | Path | None = None) -> tuple[int, int]:
    """Apply pending migrations to a database.

    Parameters
    ----------
    db_name : str
        Logical database name (e.g. ``"ledger"``).
    db_path : str | Path | None
        Path to the database file.  If ``None``, uses the registered
        path function.

    Returns
    -------
    (from_version, to_version)

    Raises
    ------
    MigrationError
        If any migration fails or the database is not registered.
    """
    if db_name not in _MIGRATION_REGISTRY:
        raise MigrationError(f"unknown database: {db_name}")

    path_fn, migrations = _MIGRATION_REGISTRY[db_name]
    resolved = Path(db_path) if db_path else path_fn()

    if not resolved.is_file():
        raise MigrationError(f"database file does not exist: {resolved}")

    current = _current_version(resolved)
    pending = [m for m in migrations if m.version > current]

    if not pending:
        return (current, current)

    for migration in sorted(pending, key=lambda m: m.version):
        # Optional idempotency check: if the check query returns a row,
        # assume already applied and skip to version bump.
        if migration.check:
            try:
                with sqlite3.connect(resolved) as conn:
                    row = conn.execute(migration.check).fetchone()
                    if row:
                        _set_version(resolved, migration.version)
                        continue
            except sqlite3.Error:
                pass  # check failed; apply the migration

        try:
            with sqlite3.connect(resolved) as conn:
                conn.executescript(migration.up)
                conn.execute(f"PRAGMA user_version = {migration.version}")
        except sqlite3.Error as exc:
            raise MigrationError(
                f"migration {db_name} v{migration.version} "
                f"({migration.description}) failed: {exc}"
            ) from exc

    final = _current_version(resolved)
    return (current, final)


def rollback(db_name: str, target_version: int,
             db_path: str | Path | None = None) -> tuple[int, int]:
    """Roll back migrations to a target version.

    Only migrations with a ``down`` SQL string can be rolled back.
    """
    if db_name not in _MIGRATION_REGISTRY:
        raise MigrationError(f"unknown database: {db_name}")

    path_fn, migrations = _MIGRATION_REGISTRY[db_name]
    resolved = Path(db_path) if db_path else path_fn()

    if not resolved.is_file():
        raise MigrationError(f"database file does not exist: {resolved}")

    current = _current_version(resolved)
    if target_version >= current:
        return (current, current)

    to_revert = [m for m in reversed(migrations)
                 if m.version > target_version and m.version <= current]

    for migration in to_revert:
        if migration.down is None:
            raise MigrationError(
                f"migration {db_name} v{migration.version} "
                f"({migration.description}) has no rollback path"
            )
        try:
            with sqlite3.connect(resolved) as conn:
                conn.executescript(migration.down)
                conn.execute(f"PRAGMA user_version = {migration.version - 1}")
        except sqlite3.Error as exc:
            raise MigrationError(
                f"rollback {db_name} v{migration.version} failed: {exc}"
            ) from exc

    final = _current_version(resolved)
    return (current, final)


# ── Convenience: migrate all registered databases ───────────────────────────


def migrate_all() -> dict[str, tuple[int, int]]:
    """Apply pending migrations to every registered database.

    Returns ``{name: (from_version, to_version)}``.
    """
    results: dict[str, tuple[int, int]] = {}
    for name in list(_MIGRATION_REGISTRY):
        try:
            path_fn, _ = _MIGRATION_REGISTRY[name]
            results[name] = migrate(name)
        except MigrationError as exc:
            results[name] = (-1, -1)
    return results


# ── Register built-in databases ─────────────────────────────────────────────

try:
    import ledger as _ledger

    register_database(
        "ledger",
        lambda: Path(_ledger.LEDGER_DB).resolve(),
        LEDGER_MIGRATIONS,
    )
except ImportError:
    pass

try:
    from pathlib import Path as _Path

    _ROOT = _Path(__file__).resolve().parents[1]

    register_database(
        "ledgerbook",
        lambda: _ROOT / "memory" / "ledgerbook.db",
        LEDGERBOOK_MIGRATIONS,
    )

    register_database(
        "fts_index",
        lambda: _ROOT / "memory" / "fts_index.db",
        FTS_INDEX_MIGRATIONS,
    )
except Exception:
    pass
