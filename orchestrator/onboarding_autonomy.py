"""M1 Onboarding Run — Full Autonomy Mode (approved plan: temporal-honking-tome).

Manager (roles.manager) brainstorms 3 niches -> 5 simulated personas critique them ->
manager selects the winner -> ledgerbook records the decision -> worker (roles.worker)
builds the structural blueprint -> critic judges vs pre-written criteria -> ledger + fitness.

Direct Ollama API; models come from config/models.yaml (model-agnostic hard constraint).
Every raw exchange is saved to runs/onboarding_autonomy/ for audit.

Exit codes: 0 done · 2 quota_wait · 3 infra_failed · 4 failed (no deliverable)."""
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs" / "onboarding_autonomy"
WS = ROOT / "workspace" / "onboarding"
BOOK = ROOT / "memory" / "ledgerbook.db"
API = "http://127.0.0.1:11434/api/chat"
TODAY = datetime.now().strftime("%Y-%m-%d")

PASS_CRITERIA = """1. 3 candidate e-commerce+content niches with rationale.
2. 5 distinct personas (psych trigger, attention span, buying friction); 15 critiques with purchase-intent scores.
3. One winner selected autonomously with per-niche conversion-probability ESTIMATES + rationale.
4. Ledgerbook: decision row, 3 niche entities, estimates stored as facts confidence=1 (model-estimated).
5. Deliverables exist: workspace/onboarding/niche_selection.md + blueprint_<slug>.md (product line, content engine, funnel, week-1 tasks).
6. Critic verdict logged; ledger row has token counts; fitness reported."""


class QuotaError(Exception):
    pass


class InfraError(Exception):
    pass


TOKENS = {"in": 0, "out": 0}


def chat(model: str, messages: list, tag: str, temperature: float = 0.7) -> str:
    body = json.dumps({"model": model, "messages": messages, "stream": False,
                       "options": {"temperature": temperature}}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=300)
        d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read()[:300].decode(errors="replace")
        if e.code == 429 or "usage limit" in msg.lower():
            raise QuotaError(msg)
        raise InfraError(f"HTTP {e.code}: {msg}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise InfraError(f"{type(e).__name__}: {e}")
    content = (d.get("message") or {}).get("content", "") or ""
    TOKENS["in"] += d.get("prompt_eval_count", 0) or 0
    TOKENS["out"] += d.get("eval_count", 0) or 0
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{tag}.json").write_text(
        json.dumps({"model": model, "messages": messages, "response": content,
                    "in": d.get("prompt_eval_count"), "out": d.get("eval_count")},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    return content


def parse_json(text: str, model: str, tag: str) -> dict:
    """Lenient JSON extraction: strip think-blocks and code fences; one repair retry."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    t = re.sub(r"```(?:json)?|```", "", t)
    m = re.search(r"\{.*\}", t, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    fixed = chat(model, [{"role": "user", "content":
                          "Convert this into VALID minified JSON only, no prose:\n" + text[:6000]}],
                 tag + "_repair", temperature=0.0)
    m = re.search(r"\{.*\}", re.sub(r"```(?:json)?|```", "", fixed), flags=re.S)
    if not m:
        raise InfraError(f"unparseable JSON from {model} at {tag}")
    return json.loads(m.group(0))


def step(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    roles = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["roles"]
    mgr, wrk = roles["manager"]["model"], roles["worker"]["model"]
    WS.mkdir(parents=True, exist_ok=True)

    tid = ledger.queue_task("000-onboarding",
                            "AUTONOMY RUN: manager niche selection via 5-persona simulation "
                            "+ worker structural blueprint", PASS_CRITERIA)
    ledger.start_task(tid, f"manager={mgr}, worker={wrk}")
    step(f"ledger task {tid} started (manager={mgr}, worker={wrk})")

    try:
        # ── Step 1: brainstorm ────────────────────────────────────────────────
        step("1/7 manager: brainstorming 3 niches")
        out = chat(mgr, [
            {"role": "system", "content":
             "You are the Manager brain of an autonomous AI research/BI harness — a "
             "survival-driven architect. Your operator: a solo builder in Europe running a "
             "Shopify store + YouTube content channels from one Windows laptop. Hard "
             "constraints: official APIs only, tiny ad budget, no warehouse (dropship or "
             "print-on-demand or digital), content must be producible by one person with "
             "AI tools (image gen, TTS, video assembly). Survival = pick niches that can "
             "realistically produce first revenue in 60 days, not vanity markets."},
            {"role": "user", "content":
             "Brainstorm exactly 3 high-potential e-commerce + content crossover niches "
             "(the content channel feeds the store). Return JSON only:\n"
             '{"niches":[{"name":str,"slug":str,"product_angle":str,"content_angle":str,'
             '"rationale":str}]}'}], "1_brainstorm")
        niches = parse_json(out, mgr, "1_brainstorm")["niches"][:3]
        step("   niches: " + ", ".join(n["name"] for n in niches))

        # ── Step 2: personas ─────────────────────────────────────────────────
        step("2/7 manager: generating 5 consumer personas")
        out = chat(mgr, [
            {"role": "user", "content":
             "Create exactly 5 distinct virtual consumer personas for stress-testing "
             "e-commerce niches. Each must have a DIFFERENT dominant psychological trigger "
             "(e.g. status, FOMO, practicality/value, novelty, trust/social-proof), a "
             "different attention span (seconds-scroller to deep-reader), and a different "
             "buying friction level (impulse to heavy-researcher). Return JSON only:\n"
             '{"personas":[{"name":str,"age":int,"psych_trigger":str,"attention_span":str,'
             '"buying_friction":str,"description":str}]}'}], "2_personas")
        personas = parse_json(out, mgr, "2_personas")["personas"][:5]
        step("   personas: " + ", ".join(p["name"] for p in personas))

        # ── Step 3: simulation — each persona critiques each niche ───────────
        niche_brief = json.dumps(niches, ensure_ascii=False)
        critiques = []
        for i, p in enumerate(personas, 1):
            step(f"3/7 simulation: persona {i}/5 ({p['name']}) critiques all 3 niches")
            out = chat(mgr, [
                {"role": "system", "content":
                 f"You ARE this consumer, not an analyst. Stay fully in character.\n"
                 f"{json.dumps(p, ensure_ascii=False)}\n"
                 f"React exactly as this person would: your psychological trigger is "
                 f"{p['psych_trigger']}, your attention span is {p['attention_span']}, "
                 f"your buying friction is {p['buying_friction']}."},
                {"role": "user", "content":
                 "Three store+content concepts are pitched to you:\n" + niche_brief +
                 "\nFor EACH niche, react honestly. Return JSON only:\n"
                 '{"critiques":[{"niche_slug":str,"purchase_intent":0-100,'
                 '"would_follow_content":bool,"top_objection":str,"gut_reaction":str}]}'},
            ], f"3_sim_persona{i}", temperature=0.9)
            for c in parse_json(out, mgr, f"3_sim_persona{i}")["critiques"]:
                c["persona"] = p["name"]
                critiques.append(c)
        step(f"   collected {len(critiques)} critiques")

        # ── Step 4: selection ────────────────────────────────────────────────
        step("4/7 manager: aggregating simulation -> selecting winner")
        out = chat(mgr, [
            {"role": "system", "content":
             "You are the Manager brain. Decide like a survival-driven architect: the "
             "operator's runway is short; pick the niche most likely to convert REAL "
             "buyers, not the most interesting one. Be honest that your probabilities "
             "are estimates from a simulation, not measurements."},
            {"role": "user", "content":
             f"NICHES:\n{niche_brief}\n\nSIMULATION CRITIQUES (5 personas x 3 niches):\n"
             f"{json.dumps(critiques, ensure_ascii=False)}\n\n"
             "Aggregate the evidence. Return JSON only:\n"
             '{"estimates":[{"slug":str,"conversion_probability":0.0-1.0,"reason":str}],'
             '"winner_slug":str,"rationale":str}'}], "4_selection", temperature=0.3)
        sel = parse_json(out, mgr, "4_selection")
        winner = next(n for n in niches if n["slug"] == sel["winner_slug"])
        step(f"   WINNER: {winner['name']} "
             f"(est. conv. {[e['conversion_probability'] for e in sel['estimates'] if e['slug'] == sel['winner_slug']][0]})")

        # ── Step 5: ledgerbook writes ────────────────────────────────────────
        step("5/7 ledgerbook: decision + entities + estimate facts")
        with sqlite3.connect(BOOK, timeout=30) as c:
            c.execute("INSERT INTO decisions (statement, rationale) VALUES (?,?)",
                      (f"Onboarding niche selected autonomously: {winner['name']} "
                       f"({winner['slug']})",
                       f"Manager aggregation of 5-persona simulation. {sel['rationale']} "
                       f"[Estimates are model-generated, not measured.]"))
            for n in niches:
                c.execute("INSERT OR IGNORE INTO entities (type, name) VALUES ('niche', ?)",
                          (n["slug"],))
            for e in sel["estimates"]:
                c.execute(
                    "INSERT INTO facts (entity, statement, provenance_url, provenance_date,"
                    " confidence, status) VALUES (?,?,?,?,1,'candidate')",
                    (e["slug"],
                     f"Estimated conversion probability {e['conversion_probability']} "
                     f"(persona-simulation, MODEL-ESTIMATED not measured): {e['reason']}",
                     "internal://onboarding_autonomy/4_selection", TODAY))

        # niche_selection.md — the auditable record of steps 1-4
        est_rows = "\n".join(
            f"| {e['slug']} | {e['conversion_probability']} | {e['reason']} |"
            for e in sel["estimates"])
        crit_rows = "\n".join(
            f"| {c['persona']} | {c['niche_slug']} | {c['purchase_intent']} | "
            f"{'yes' if c.get('would_follow_content') else 'no'} | {c['top_objection']} |"
            for c in critiques)
        (WS / "niche_selection.md").write_text(f"""# Autonomous niche selection — {TODAY}

**WINNER: {winner['name']}** (`{winner['slug']}`)

> All probabilities below are MODEL-GENERATED ESTIMATES from a 5-persona simulation
> (confidence=1 in ledgerbook). They are hypotheses to validate with real data, not measurements.

## Candidates
{json.dumps(niches, indent=2, ensure_ascii=False)}

## Personas
{json.dumps(personas, indent=2, ensure_ascii=False)}

## Simulation critiques (persona x niche)
| Persona | Niche | Purchase intent | Follows content | Top objection |
|---|---|---|---|---|
{crit_rows}

## Manager estimates
| Niche | Est. conversion probability | Reason |
|---|---|---|
{est_rows}

## Selection rationale
{sel['rationale']}
""", encoding="utf-8")

        # ── Step 6: worker builds the blueprint ──────────────────────────────
        step(f"6/7 worker ({wrk}): building structural blueprint")
        blueprint = chat(wrk, [
            {"role": "system", "content":
             "You are a precise e-commerce + content operations worker. Output clean "
             "markdown only — no preamble, no JSON, no code fences around the whole doc."},
            {"role": "user", "content":
             f"Build the structural blueprint for this validated niche:\n"
             f"{json.dumps(winner, ensure_ascii=False)}\n"
             f"Persona insights (use them to shape products + content):\n"
             f"{json.dumps(sel['estimates'], ensure_ascii=False)}\n\n"
             "Operator constraints: solo, Shopify, dropship/POD/digital only, content made "
             "with AI tools (images, TTS, video assembly), official APIs only.\n\n"
             "Sections required:\n"
             "# Blueprint: <niche>\n## Store structure (collections + 10 concrete product "
             "ideas with price points)\n## Content engine (3 formats mapped to "
             "YouTube/Shorts, cadence, how each feeds the store)\n## Funnel (content -> "
             "store path, email capture, first-purchase hook)\n## Week-1 task list "
             "(10 concrete tasks, each with a done-criterion)"}], "6_blueprint",
            temperature=0.4)
        blueprint = re.sub(r"<think>.*?</think>", "", blueprint, flags=re.S).strip()
        bp_path = WS / f"blueprint_{winner['slug']}.md"
        bp_path.write_text(blueprint + f"\n\n---\n_Generated {TODAY} by {wrk} "
                           f"(autonomy onboarding run)._\n", encoding="utf-8")
        step(f"   blueprint written: {bp_path.name} ({len(blueprint)} chars)")

        # ── Step 7: critic ───────────────────────────────────────────────────
        step("7/7 critic: judging vs pre-written pass criteria")
        verdict_text = chat(mgr, [
            {"role": "system", "content":
             "You are a strict critic. Reply with PASS or FAIL as the first word, then "
             "one sentence per criterion."},
            {"role": "user", "content":
             f"PASS CRITERIA:\n{PASS_CRITERIA}\n\nEVIDENCE:\n"
             f"- niches: {[n['slug'] for n in niches]}\n"
             f"- personas: {len(personas)}, critiques: {len(critiques)}\n"
             f"- winner: {sel['winner_slug']} with estimates for "
             f"{[e['slug'] for e in sel['estimates']]}\n"
             f"- files written: niche_selection.md ({(WS / 'niche_selection.md').stat().st_size} B), "
             f"{bp_path.name} ({bp_path.stat().st_size} B)\n"
             f"- ledgerbook: 1 decision, {len(niches)} entities, {len(sel['estimates'])} facts\n"
             f"- BLUEPRINT HEAD:\n{blueprint[:1500]}"}], "7_critic", temperature=0.0)
        verdict_text = re.sub(r"<think>.*?</think>", "", verdict_text, flags=re.S).strip()
        verdict = "pass" if verdict_text.upper().lstrip().startswith("PASS") else "fail"

        ledger.finish_task(tid, artifacts=[str(p.relative_to(ROOT)) for p in
                                           (WS / "niche_selection.md", bp_path)],
                           cost_usd=0.0, tokens_in=TOKENS["in"], tokens_out=TOKENS["out"],
                           critic_verdict=verdict, critic_notes=verdict_text[:500],
                           status="done")
        ledger.add_lesson(tid, "First live end-to-end autonomy run: manager sim -> "
                          "ledgerbook -> worker -> critic completed on cloud models.",
                          "worked")
        step(f"DONE task {tid}: verdict={verdict}, tokens in={TOKENS['in']} out={TOKENS['out']}")
        print("FITNESS:", json.dumps(ledger.weekly_fitness(), indent=2))
        return 0

    except QuotaError as e:
        ledger.finish_task(tid, artifacts=[], status="quota_wait",
                           critic_notes=f"quota mid-run: {e}")
        step(f"QUOTA_WAIT: {e}")
        return 2
    except InfraError as e:
        ledger.finish_task(tid, artifacts=[], status="infra_failed",
                           critic_notes=f"infra failure mid-run: {e}")
        step(f"INFRA_FAILED: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
