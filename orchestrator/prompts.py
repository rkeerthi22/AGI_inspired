"""orchestrator/prompts.py -- prompt building and context management.

Extracted from batch_runner.py as Move 3 of the W9 5-file split (see
REFACTOR_PLAN.md). This module owns the text that goes into worker and critic
prompts: the mission objective line, the done-definition parser, the
deliverable-requirements filter (F20), the per-task scope note (F31), the
synthesis brief block (F49), and the recent-fact ledger view (F51).

Dependency direction (per the W9 plan, section 1):
    integrity.py -> execution.py -> prompts.py -> evaluation.py -> scheduler.py

This module depends only on:
    - stdlib (re, sqlite3)
    - ROOT (pathing) -- this module's own definition, mirroring batch_runner.py
    - The mission dict structure produced by parse_mission()

What does NOT live here:
    - extract_facts / retract_facts / seed_is_synthesis: those are part of the
      memory-update stage and stay in batch_runner.py for now (will move to
      evaluation.py as part of Move 4 if it makes sense then).
    - ENTITY_TYPES: only used by extract_facts (memory-update), so it stays
      in batch_runner.py alongside extract_facts.

No internal calls between Move 3 functions (verified by pre-audit 2026-08-26):
every Move 3 function is leaf-level, only calling stdlib or batch_runner.py
residents. The shim pattern from Move 1 and Move 2 applies cleanly.
"""


import re
import sqlite3
import sys
from pathlib import Path

# Mirrors batch_runner.ROOT: the repo root, computed from this file's location.
ROOT = Path(__file__).resolve().parent.parent

# Constants referenced by the moved functions.
FACT_LEDGER_CAP = 300           # F51 (docs/HARDENING.md), 2026-07-30: cap raised 120 -> 300
                                # to cover a fortnight at more than double the busiest
                                # observed rate (108 facts in 14d, W30 alone produced 70).
                                # Used as the cap on rows SELECTed for the synthesis fact
                                # view; the result is annotated if the actual ledger has
                                # more than this in the window so the model can tell a
                                # truncation from a data gap.
SYNTHESIS_BRIEF_CHARS = 24000   # F49 (docs/HARDENING.md): per-brief character cap fed to
                                # the synthesis prompt. Raised from the original 6000-char
                                # cap after task 30 (12,464-char brief, 6,464 dropped, Topic 3
                                # missed by 60 chars) and task 27 (~18KB silently lost across
                                # three briefs) both failed for invisible-truncation reasons.
SYNTHESIS_MAX_BRIEFS = 6        # F49: the maximum number of distinct brief files shown to
                                # the synthesis model. Briefs beyond this are listed in an
                                # "omitted" section so the model can report them as withheld
                                # rather than concluded them absent.


def pass_criteria_for(mission: dict) -> str:


    m = re.search(r"## Done-definition.*?\n(.*?)(?=\n## )", mission["body"], re.S)


    return m.group(1).strip() if m else "deliverable exists; every fact sourced+dated"


# Lines in a done-definition that describe the ORCHESTRATOR's job, not the analyst's.


# These name our own storage layout, and handing a tool-holding worker that layout is


# precisely what produced the 2026-07-18 rogue-write incident (docs/INCIDENTS.md), so


# they are stripped before any of this text reaches a worker prompt.


_INTERNAL_CRITERIA_RE = re.compile(


    r"workspace[/\\]|memory[/\\]|ledgerbook|ledger\.db|\bthe ledger\b|critic verdict",


    re.I)


def deliverable_requirements(mission: dict) -> str:


    """The mission's done-definition reduced to the CONTENT/FORMAT requirements the


    analyst is actually judged on -- every line naming an internal path or schema removed.


    F20 (docs/HARDENING.md): run_critic() feeds the critic row['pass_criteria'] -- the


    FULL done-definition -- while the worker only ever received mission_objective()'s


    single "## Objective" line. The analyst was therefore graded against requirements it


    was never shown. Proven live 2026-07-27, the first real W31 run: mission 001 tasks


    24, 25 and 26 ALL failed review, and every stated reason was a done-definition item


    absent from the worker's prompt -- the top "Changes since last week" diff section,


    NEW flags on unseen products, >=2 product URLs per price range, and one section per


    tracked competitor. Completion for the day was 0/3 on requirements the worker had no


    way to know existed.


    Whole requirements are dropped, never half of one: a matching line takes its


    continuation lines and sub-bullets (anything more indented) with it, so the worker


    never sees a dangling fragment like "price/promo facts get a valid_until" with the


    sentence that gave it meaning removed."""


    kept: list[str] = []


    drop_indent: int | None = None


    for line in pass_criteria_for(mission).splitlines():


        if not line.strip():


            drop_indent = None          # a blank line ends any requirement block


            kept.append(line)


            continue


        indent = len(line) - len(line.lstrip())


        if drop_indent is not None:


            if indent > drop_indent:


                continue                # continuation / sub-bullet of a dropped line


            drop_indent = None          # back at a sibling level -- resume keeping


        if _INTERNAL_CRITERIA_RE.search(line):


            drop_indent = indent


            continue


        kept.append(line)


    # Drop "[ ]" checkboxes -- they read as a form to tick rather than a spec to satisfy.


    return re.sub(r"^(\s*-)\s*\[[ x]\]\s*", r"\1 ", "\n".join(kept).strip(), flags=re.M)


def task_scope_note(spec: str, mission: dict) -> str:


    """Which slice of the mission's done-definition THIS task is answerable for.


    F31 (docs/HARDENING.md), 2026-07-29. F20 was right that the worker must see the


    done-definition, but it handed EVERY task the whole thing -- and a done-definition


    describes the mission's COMBINED weekly brief, not one seed's share of it. The two


    resulting impossibilities were both live:


      * mission 001 seed 4 (task 27) is the TOOL-FREE synthesis, and was graded on "a


        review-sentiment signal: current average rating + one recurring theme" for each


        tracked competitor. The critic failed it for exactly that -- "three of five


        tracked competitors are missing the required review-sentiment signal" -- on a


        task forbidden from doing the lookups that would produce one, working from three


        briefs. No possible output passes.


      * the per-competitor seeds (1-3) are each told they must deliver "one section per


        tracked competitor" and "a top 'Changes since last week' diff section", which a


        single-competitor task cannot produce either.


    Returned to the WORKER and to the CRITIC from one function on purpose: F20's root


    cause was the two being given different specs, and re-deriving this note separately


    at each site would rebuild that exact failure mode."""


    n = len(mission["seeds"])


    if seed_is_synthesis(spec):


        return (


            f"This mission's done-definition describes the COMBINED weekly brief, which "


            f"{n} separate tasks produce between them. This task is the SYNTHESIS: it "


            f"works only from briefs and ledger facts the other tasks already produced, "


            f"and is forbidden from doing its own research. Per-subject requirements are "


            f"therefore met by whatever the supplied material actually contains. Where "


            f"the material does not cover one, the correct outcome is an explicit data "


            f"gap naming the subject and the missing item -- not a fabricated value, and "


            f"not a defect in this deliverable.")


    return (


        f"This mission's done-definition describes the COMBINED weekly brief, which {n} "


        f"separate tasks produce between them. This task is ONE of them and covers only "


        f"the single subject named in its spec. Every requirement that applies to that "


        f"subject must be met here, in full. Requirements that exist only across the "


        f"whole set -- a section for every tracked subject, or the combined cross-subject "


        f"\"changes since last week\" diff -- belong to the synthesis task; their absence "


        f"from a single-subject deliverable is expected and is not a defect.")


def mission_objective(mission: dict) -> str:


    """One-line objective ONLY — never hand the worker the full mission file. The file


    describes OUR storage paths/schema (ledgerbook.db, ledger.db); a worker with real


    tools will act on those as instructions if given the chance (see docs/INCIDENTS.md)."""


    m = re.search(r"## Objective\s*\n(.*?)(?=\n## )", mission["body"], re.S)


    return m.group(1).strip() if m else mission["frontmatter"].get("mission_id", "")



def _recent_fact_lines(days: int = 14, cap: int = FACT_LEDGER_CAP, db=None) -> str:


    """Fact-ledger view fed to synthesis tasks: current + prior week.


    F51 (docs/HARDENING.md), 2026-07-30 — F49's silent-truncation family, third member


    (F49 the briefs, F50 the model context, this the facts). Three defects in one line:


    1. **Silent.** `rows[:cap]` dropped the overflow with no marker, so the synthesis could


       not tell a complete fact ledger from a clipped one — exactly what made F49 damaging


       rather than merely lossy. Now stated, in the same words, for the same reason.


    2. **About to bite, not hypothetical.** Measured 2026-07-30: **108 facts in the 14-day


       window against a cap of 120** — twelve rows of headroom, when week W30 alone produced


       **70 facts**. One ordinary week would have crossed it. Cap raised 120 → 300, which


       covers a fortnight at more than double the busiest observed rate; worst case


       ~50,700 chars (~12,675 tok) at the measured 169-char mean line.


    3. **Dropping the wrong rows.** The old ordering was `ORDER BY entity, id`, so the


       overflow was the alphabetical TAIL — deterministically the same entities every time


       (today: `ai-productivity`, `dark-academia`, `modern-stoicism`, i.e. the whole


       onboarding niche-selection set), regardless of age or relevance. Now the newest `cap`


       rows are SELECTED, then presented grouped by entity: truncation drops the oldest,


       which is defensible, while the reading order stays grouped, which is readable.


    `db` is injectable and resolved at call time so tests can point it at a copy — F12's


    lesson, which cost a junk row in the live ledger the first time it was ignored."""


    import sqlite3


    path = db if db is not None else ROOT / "memory" / "ledgerbook.db"


    since = f"-{days} days"


    with sqlite3.connect(path, timeout=30) as c:


        total = c.execute("SELECT count(*) FROM facts WHERE created_at >= datetime('now', ?)",


                          (since,)).fetchone()[0]


        rows = c.execute(


            "SELECT entity, statement, provenance_date, confidence FROM ("


            "  SELECT entity, statement, provenance_date, confidence, id FROM facts "


            "  WHERE created_at >= datetime('now', ?) ORDER BY id DESC LIMIT ?"


            ") ORDER BY entity, id",


            (since, cap)).fetchall()


    if not rows:


        return "(none yet)"


    lines = [f"- [{r[2]} conf{r[3]}] {r[0]}: {r[1]}" for r in rows]


    if total > len(rows):


        lines.append(


            f"\n[TRUNCATED BY THE HARNESS: {total - len(rows)} of {total} facts in the "


            f"{days}-day window were NOT supplied to you; the {len(rows)} most RECENT were "


            f"kept. The rest exist in the fact ledger — they are withheld by a size cap, "


            f"not absent. Anything they contain is NOT a data gap.]")


    return "\n".join(lines)


def build_brief_block(briefs: list, cap: int = SYNTHESIS_BRIEF_CHARS,


                      max_briefs: int = SYNTHESIS_MAX_BRIEFS) -> str:


    """This week's briefs, with every omission STATED instead of silently applied.


    F49 (docs/HARDENING.md), 2026-07-30. This was


    `p.read_text()[:6000] for p in briefs[:6]` -- two silent caps and no marker, so the


    synthesis model could not distinguish a complete brief from a bisected one, and the


    critic (which never sees the prompt) could not either.


    Measured on task 30: task 29's brief is 12,464 chars, 6,464 were dropped, and


    `## Topic Opportunity 3` begins at char 6,060 -- the cut missed it by SIXTY characters.


    The synthesis then reported that third topic as a data gap and told the operator to go


    source material the harness already held. Task 27 was hit harder and invisibly: all


    three of its briefs were cut and ~18KB never arrived, yet the output looked complete.


    The marker does NOT recover the omitted text -- the deliverable is still built from a


    partial brief. What it changes is that the loss is now *reportable*: the model is told


    the material exists and was withheld, so it can say so instead of concluding absence.


    Raising `cap` remains available and independent of this fix.


    Distinguishing the two cases is the whole point. A data gap means nobody researched it,


    and the operator must go get it. A truncation means it WAS researched and simply was not


    shown to the model. Conflating them is what turned F49 into wasted operator work."""


    if not briefs:


        return "(none)"


    shown, dropped = briefs[:max_briefs], briefs[max_briefs:]


    parts = []


    for p in shown:


        txt = p.read_text(encoding="utf-8")


        if len(txt) > cap:


            omitted = len(txt) - cap


            body = (f"{txt[:cap]}\n\n[TRUNCATED BY THE HARNESS: {omitted} of {len(txt)} "


                    f"characters of this brief were NOT supplied to you. That material was "


                    f"researched and exists; it is withheld by a prompt-size cap, not absent. "


                    f"Anything it may contain is NOT a data gap.]")


        else:


            body = txt


        parts.append(f"### {p.name}\n{body}")


    if dropped:


        parts.append(


            "### [BRIEFS OMITTED BY THE HARNESS]\n"


            f"{len(dropped)} further brief(s) for this week were NOT supplied to you: "


            f"{', '.join(p.name for p in dropped)}. They were researched and exist; they are "


            f"withheld by a count cap, not absent. Anything they contain is NOT a data gap.")


    return "\n\n".join(parts)


