-- Task Ledger + Ledgerbook memory. Design: HARNESS_DESIGN.md §2.3, §2.4, §3.1.
-- Append-only in spirit: rows are inserted and updated with verdicts, never deleted.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── TASK LEDGER — single source of truth ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    task_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id     TEXT    NOT NULL,
    spec           TEXT    NOT NULL,
    pass_criteria  TEXT    NOT NULL,          -- WRITTEN BEFORE THE RUN (§3.1)
    status         TEXT    NOT NULL DEFAULT 'queued',
                   -- queued | running | quota_wait | blocked | done | failed
    started_at     TEXT,
    finished_at    TEXT,
    model_used     TEXT,
    tokens_in      INTEGER DEFAULT 0,
    tokens_out     INTEGER DEFAULT 0,
    cost_usd       REAL    DEFAULT 0.0,
    artifacts      TEXT,                       -- JSON array of workspace paths
    critic_verdict TEXT,                       -- pass | fail | NULL(not yet judged)
    critic_notes   TEXT,
    human_verdict  TEXT,                       -- pass | fail | NULL(not spot-checked)
    interventions  INTEGER DEFAULT 0,
    intervention_types TEXT,                   -- JSON array
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    run_id         TEXT                        -- orchestrator process that inserted this row
                                                 -- (H2, docs/HARDENING.md — a NULL run_id on a
                                                 -- new row is the rogue-write signature: the
                                                 -- worker is never told this schema exists)
);
CREATE INDEX IF NOT EXISTS idx_tasks_mission ON tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);

-- Lesson candidates harvested from tasks (feed the gated skill-promotion loop §2.4)
CREATE TABLE IF NOT EXISTS lesson_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(task_id),
    lesson      TEXT NOT NULL,
    kind        TEXT,                           -- worked | failed | shortcut
    times_seen  INTEGER DEFAULT 1,
    promoted_to TEXT,                           -- skill name if promoted, else NULL
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── LEDGERBOOK — typed domain memory (world model, §2.3) ──────────────────────
-- Facts carry provenance, confidence, and Zep-style validity windows.
CREATE TABLE IF NOT EXISTS facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity         TEXT,                        -- FK-ish to entities.name (soft)
    statement      TEXT NOT NULL,
    provenance_url TEXT,
    provenance_date TEXT,                        -- retrieval date (YYYY-MM-DD)
    confidence     INTEGER DEFAULT 1,           -- 1 low | 2 medium | 3 high (source count)
    valid_from     TEXT NOT NULL DEFAULT (datetime('now')),
    valid_until    TEXT,                         -- NULL = currently true
    superseded_by  INTEGER REFERENCES facts(id), -- supersede, don't overwrite
    status         TEXT NOT NULL DEFAULT 'candidate', -- candidate | permanent | expired
    ttl_expires    TEXT,                         -- candidates auto-expire (14d default)
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    source_task_id INTEGER,                      -- task that produced this fact (for retraction)
    run_id         TEXT                          -- orchestrator process that inserted this row (H2)
);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);

-- Cause/effect from own history (cheap, honest causal knowledge §2.3)
CREATE TABLE IF NOT EXISTS experiences (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER REFERENCES tasks(task_id),
    context    TEXT NOT NULL,
    action     TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    worked     INTEGER,                          -- 1 yes | 0 no
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Decisions are immutable rationale records (§1.2)
CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    statement  TEXT NOT NULL,
    rationale  TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Failures enter WITH a hypothesis + test (§2.4) — negative knowledge is kept
CREATE TABLE IF NOT EXISTS failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(task_id),
    description TEXT NOT NULL,
    hypothesis  TEXT,
    test        TEXT,
    resolved    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Entities + typed relations (world model graph, kept minimal §2.3)
CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL,                    -- competitor|product|channel|supplier|keyword|trend
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS relations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity   TEXT NOT NULL,
    to_entity     TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── WEEKLY SCORECARD — fitness function history (§3.2) ─────────────────────────
CREATE TABLE IF NOT EXISTS scorecards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start        TEXT NOT NULL,
    tasks_attempted   INTEGER,
    completion_rate   REAL,
    accuracy          REAL,
    intervention_rate REAL,
    avg_cost_usd      REAL,
    fitness           REAL,                       -- F = weighted sum (§3.2)
    canaries_green    INTEGER,                    -- of 5
    notes             TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
