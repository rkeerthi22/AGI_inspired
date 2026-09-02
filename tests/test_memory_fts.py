"""Model-free contract tests for the dedicated Markdown FTS5 index."""
from __future__ import annotations

import ast
import hashlib
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import memory_fts  # noqa: E402

failures: list[str] = []
checks = 0


def check(name: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(name)
    print(f"  [{'PASS' if got == want else 'FAIL'}] {name}")


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


print("=== memory FTS containment and search ===")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
    td = Path(raw)
    agents = td / "agents"
    (agents / "codex").mkdir(parents=True)
    (agents / "claude").mkdir(parents=True)
    (agents / "codex" / "memory.md").write_text(
        "Timestamp: 2026-09-02\nAuthor: Codex\nSource: test\n\n"
        "The retrieval audit uses browser navigation immediately.\n\n"
        "Task worktrees use compare and swap ownership.", encoding="utf-8")
    (agents / "claude" / "memory.md").write_text(
        "Timestamp: 2026-09-02\nAuthor: Claude\nSource: test\n\n"
        "Independent review confirms the browser retrieval plan.", encoding="utf-8")
    index = memory_fts.MemoryFtsIndex(td / "fts_index.db", agents)

    protected = [ROOT / "ledger" / "ledger.db", ROOT / "memory" / "ledgerbook.db"]
    before = {str(path): digest(path) for path in protected}
    check("initial sync indexes paragraphs", index.sync(), 5)
    after = {str(path): digest(path) for path in protected}
    check("sync never changes protected databases", before, after)
    check("index is the dedicated fts_index database", index.db_path.name, "fts_index.db")

    with sqlite3.connect(index.db_path) as conn:
        triggers = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        fts_count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    check("all external-content synchronization triggers exist",
          triggers >= {"chunks_ai", "chunks_ad", "chunks_au"})
    check("FTS trigger content matches chunks", fts_count, 5)

    results = index.search("browser")
    check("search returns highlighted excerpt", "«browser»" in results[0].excerpt.lower())
    scoped = index.search("browser", agent="claude")
    check("agent filter scopes results", [item.agent for item in scoped], ["claude"])

    # A paragraph change updates the FTS index, while a no-op sync is stable.
    codex_memory = agents / "codex" / "memory.md"
    codex_memory.write_text(codex_memory.read_text(encoding="utf-8").replace(
        "compare and swap", "atomic compare and swap"), encoding="utf-8")
    check("incremental sync preserves paragraph count", index.sync(), 5)
    check("updated paragraph is searchable", len(index.search("atomic")), 1)
    check("no-op sync is stable", index.sync(), 5)

    first = [item.as_dict() for item in index.search("browser", limit=10)]
    check("deterministic rebuild restores all rows", index.rebuild(), 5)
    second = [item.as_dict() for item in index.search("browser", limit=10)]
    check("rebuild search output is deterministic", first, second)
    try:
        index.search("")
        bad_query_rejected = False
    except memory_fts.MemoryFtsError:
        bad_query_rejected = True
    check("empty search is rejected", bad_query_rejected)

source = (ROOT / "orchestrator" / "memory_fts.py").read_text(encoding="utf-8")
imports = {node.names[0].name.split(".")[0] for node in ast.walk(ast.parse(source))
           if isinstance(node, ast.Import)}
imports |= {node.module.split(".")[0] for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module}
check("memory index imports no execution or provider modules",
      not imports.intersection({"provider_chat", "run_task", "batch_runner", "execution_pause", "requests"}))

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES: " + ", ".join(failures))
    raise SystemExit(1)
