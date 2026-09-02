"""Derived, transparent FTS5 search for agent Markdown memory.

Canonical memory remains human-auditable Markdown under ``memory/agents``.
This module writes only its dedicated derived database, ``memory/fts_index.db``;
it never opens the ledger or ledgerbook databases.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_ROOT = ROOT / "memory" / "agents"
DEFAULT_DB_PATH = ROOT / "memory" / "fts_index.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL,
  para_no INTEGER NOT NULL,
  md_path TEXT NOT NULL,
  body_sha TEXT NOT NULL,
  body TEXT NOT NULL,
  UNIQUE(md_path, para_no)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
  body,
  agent,
  md_path UNINDEXED,
  content='chunks',
  content_rowid='id',
  tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, body, agent, md_path)
  VALUES (new.id, new.body, new.agent, new.md_path);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, body, agent, md_path)
  VALUES ('delete', old.id, old.body, old.agent, old.md_path);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, body, agent, md_path)
  VALUES ('delete', old.id, old.body, old.agent, old.md_path);
  INSERT INTO chunks_fts(rowid, body, agent, md_path)
  VALUES (new.id, new.body, new.agent, new.md_path);
END;
"""


class MemoryFtsError(RuntimeError):
    """The derived index cannot be safely built or queried."""


@dataclass(frozen=True)
class SearchResult:
    agent: str
    para_no: int
    md_path: str
    excerpt: str
    rank: float

    def as_dict(self) -> dict:
        return asdict(self)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    if db_path.name != "fts_index.db":
        # Test fixtures may use an identically named temporary database; this
        # guard prevents a caller accidentally targeting a protected store.
        raise MemoryFtsError("FTS index database must be named fts_index.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _paragraphs(path: Path, agent: str, memory_root: Path) -> list[tuple[str, int, str, str, str]]:
    """Return stable paragraph rows from one Markdown document."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MemoryFtsError(f"memory markdown is not UTF-8: {path}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    body_parts = [part.strip() for part in normalized.split("\n\n") if part.strip()]
    relative = path.relative_to(memory_root).as_posix()
    return [(agent, number, relative,
             hashlib.sha256(body.encode("utf-8")).hexdigest(), body)
            for number, body in enumerate(body_parts, start=1)]


def scan_memory(memory_root: Path = DEFAULT_MEMORY_ROOT) -> list[tuple[str, int, str, str, str]]:
    """Read only canonical ``<agent>/memory.md`` files in sorted order."""
    root = Path(memory_root)
    if not root.exists():
        return []
    if not root.is_dir():
        raise MemoryFtsError(f"memory root is not a directory: {root}")
    rows: list[tuple[str, int, str, str, str]] = []
    for agent_dir in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        if not agent_dir.is_dir():
            continue
        source = agent_dir / "memory.md"
        if source.is_file():
            rows.extend(_paragraphs(source, agent_dir.name, root))
    return rows


class MemoryFtsIndex:
    """Incrementally synchronize Markdown and search its derived index."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH,
                 memory_root: Path = DEFAULT_MEMORY_ROOT):
        self.db_path = Path(db_path)
        self.memory_root = Path(memory_root)

    def _rows_by_path(self) -> dict[str, list[tuple[str, int, str, str, str]]]:
        grouped: dict[str, list[tuple[str, int, str, str, str]]] = {}
        for row in scan_memory(self.memory_root):
            grouped.setdefault(row[2], []).append(row)
        return grouped

    def sync(self) -> int:
        """Incrementally apply changed Markdown paragraphs; return row count."""
        sources = self._rows_by_path()
        with _connect(self.db_path) as conn:
            existing_paths = {r[0] for r in conn.execute("SELECT DISTINCT md_path FROM chunks")}
            for stale_path in existing_paths - set(sources):
                conn.execute("DELETE FROM chunks WHERE md_path=?", (stale_path,))
            for md_path, rows in sources.items():
                prior = {r["para_no"]: r for r in conn.execute(
                    "SELECT id, para_no, body_sha, agent FROM chunks WHERE md_path=?", (md_path,))}
                seen: set[int] = set()
                for agent, para_no, _, body_sha, body in rows:
                    seen.add(para_no)
                    old = prior.get(para_no)
                    if old is None:
                        conn.execute("INSERT INTO chunks(agent, para_no, md_path, body_sha, body) VALUES(?,?,?,?,?)",
                                     (agent, para_no, md_path, body_sha, body))
                    elif old["body_sha"] != body_sha or old["agent"] != agent:
                        conn.execute("UPDATE chunks SET agent=?, body_sha=?, body=? WHERE id=?",
                                     (agent, body_sha, body, old["id"]))
                for para_no, old in prior.items():
                    if para_no not in seen:
                        conn.execute("DELETE FROM chunks WHERE id=?", (old["id"],))
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def rebuild(self) -> int:
        """Deterministically rebuild all derived index rows from canonical Markdown."""
        rows = scan_memory(self.memory_root)
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM chunks")
            # Reset rowids so logical output is reproducible across rebuilds.
            conn.execute("DELETE FROM sqlite_sequence WHERE name='chunks'")
            conn.executemany(
                "INSERT INTO chunks(agent, para_no, md_path, body_sha, body) VALUES(?,?,?,?,?)", rows)
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def search(self, query: str, *, agent: str | None = None,
               limit: int = 20) -> list[SearchResult]:
        """Search Markdown terms with auditable snippets and optional agent scope."""
        if not isinstance(query, str) or not query.strip():
            raise MemoryFtsError("query must be non-empty")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise MemoryFtsError("limit must be between 1 and 100")
        with _connect(self.db_path) as conn:
            records = conn.execute(
                "SELECT chunks.agent, chunks.para_no, chunks.md_path, "
                "snippet(chunks_fts, 0, '«', '»', '…', 12) AS excerpt, rank "
                "FROM chunks_fts JOIN chunks ON chunks.id = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? AND (? IS NULL OR chunks.agent = ?) "
                "ORDER BY rank LIMIT ?",
                (query, agent, agent, limit)).fetchall()
        return [SearchResult(str(row["agent"]), int(row["para_no"]),
                             str(row["md_path"]), str(row["excerpt"]),
                             float(row["rank"])) for row in records]
