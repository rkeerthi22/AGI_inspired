"""F62: live recovery exposed task_scope_note's missing evaluation dependency."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

import prompts  # noqa: E402

mission = {"seeds": ["research", "Synthesis: combine"]}
research = prompts.task_scope_note("[2026-W35][seed 1] Research PromptBase", mission)
synthesis = prompts.task_scope_note("[2026-W35][seed 2] Synthesis: combine", mission)

assert "covers only the single subject" in research, research
assert "SYNTHESIS" in synthesis, synthesis
print("F62 PASS — both task-scope routes resolve through evaluation.seed_is_synthesis")
