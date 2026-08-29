"""Typed, staged, crash-recoverable autonomy onboarding workflow.

All model calls use the canonical provider boundary. Model output is validated
before it can name a path, enter domain memory, or become a published artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402
import provider_chat  # noqa: E402
import runlock  # noqa: E402
from execution_pause import pause_engaged  # noqa: E402
from outcomes import OnboardingPhase  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs" / "onboarding_autonomy"
WS = ROOT / "workspace" / "onboarding"
BOOK = ROOT / "memory" / "ledgerbook.db"
TODAY = datetime.now().strftime("%Y-%m-%d")
LOCK_PATH = ROOT / "runs" / ".batch.lock"

PASS_CRITERIA = """1. Exactly 3 validated e-commerce/content niches.
2. Exactly 5 distinct personas and all 15 persona/niche critiques.
3. One candidate winner and one finite 0..1 estimate per niche.
4. One atomic, idempotent ledgerbook commit after critic PASS.
5. Two staged deliverables atomically published after review.
6. Critic verdict, provider provenance, and per-run token usage recorded."""


class QuotaError(RuntimeError):
    pass


class InfraError(RuntimeError):
    pass


@dataclass(frozen=True)
class Niche:
    name: str
    slug: str
    product_angle: str
    content_angle: str
    rationale: str


@dataclass(frozen=True)
class Persona:
    name: str
    age: int
    psych_trigger: str
    attention_span: str
    buying_friction: str
    description: str


@dataclass(frozen=True)
class Estimate:
    slug: str
    conversion_probability: float
    reason: str


@dataclass(frozen=True)
class Critique:
    persona: str
    niche_slug: str
    purchase_intent: int
    would_follow_content: bool
    top_objection: str
    gut_reaction: str


@dataclass(frozen=True)
class OnboardingPayload:
    niches: tuple[Niche, ...]
    personas: tuple[Persona, ...]
    critiques: tuple[Critique, ...] = ()
    estimates: tuple[Estimate, ...] = ()
    winner_slug: str | None = None
    selection_rationale: str = ""

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class OnboardingUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, result: provider_chat.ChatResult) -> None:
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens


SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def validate_slug(value: Any) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= 64):
        raise ValueError("slug must be a 1..64 character string")
    if not SLUG_RE.fullmatch(value):
        raise ValueError("slug must be lowercase kebab-case")
    return value


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    return value


def _keys(value: Mapping[str, Any], required: set[str], label: str) -> None:
    if set(value) != required:
        raise ValueError(f"{label} keys must be exactly {sorted(required)}")


def _text(value: Any, label: str, *, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-empty text up to {maximum} characters")
    return value.strip()


def _list(value: Any, label: str, count: int) -> list:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{label} must contain exactly {count} items")
    return value


def validate_onboarding_payload(payload: Any) -> OnboardingPayload:
    """Validate the complete typed domain, with selection fields optional by phase."""
    root = _object(payload, "payload")
    allowed = {"niches", "personas", "critiques", "estimates",
               "winner_slug", "selection_rationale", "rationale"}
    if not {"niches", "personas"}.issubset(root) or not set(root).issubset(allowed):
        raise ValueError("payload has missing or unknown keys")

    niches = []
    for index, raw in enumerate(_list(root["niches"], "niches", 3)):
        item = _object(raw, f"niche {index}")
        fields = {"name", "slug", "product_angle", "content_angle", "rationale"}
        _keys(item, fields, f"niche {index}")
        niches.append(Niche(_text(item["name"], "niche name"),
                            validate_slug(item["slug"]),
                            _text(item["product_angle"], "product angle"),
                            _text(item["content_angle"], "content angle"),
                            _text(item["rationale"], "niche rationale")))
    niche_slugs = {item.slug for item in niches}
    if len(niche_slugs) != 3 or len({item.name.casefold() for item in niches}) != 3:
        raise ValueError("niche names and slugs must be unique")

    personas = []
    for index, raw in enumerate(_list(root["personas"], "personas", 5)):
        item = _object(raw, f"persona {index}")
        fields = {"name", "age", "psych_trigger", "attention_span",
                  "buying_friction", "description"}
        _keys(item, fields, f"persona {index}")
        age = item["age"]
        if isinstance(age, bool) or not isinstance(age, int) or not 13 <= age <= 120:
            raise ValueError("persona age must be an integer from 13 to 120")
        personas.append(Persona(_text(item["name"], "persona name"), age,
                                _text(item["psych_trigger"], "psych trigger"),
                                _text(item["attention_span"], "attention span"),
                                _text(item["buying_friction"], "buying friction"),
                                _text(item["description"], "persona description")))
    persona_names = {item.name for item in personas}
    if len({name.casefold() for name in persona_names}) != 5:
        raise ValueError("persona names must be unique")

    estimates = []
    if "estimates" in root:
        for index, raw in enumerate(_list(root["estimates"], "estimates", 3)):
            item = _object(raw, f"estimate {index}")
            _keys(item, {"slug", "conversion_probability", "reason"}, f"estimate {index}")
            slug = validate_slug(item["slug"])
            probability = item["conversion_probability"]
            if (isinstance(probability, bool) or not isinstance(probability, (int, float))
                    or not math.isfinite(float(probability)) or not 0 <= probability <= 1):
                raise ValueError("conversion probability must be finite and within 0..1")
            estimates.append(Estimate(slug, float(probability),
                                      _text(item["reason"], "estimate reason")))
        if {item.slug for item in estimates} != niche_slugs:
            raise ValueError("estimates must cover each candidate exactly once")

    winner_slug = root.get("winner_slug")
    if winner_slug is not None:
        winner_slug = validate_slug(winner_slug)
        if winner_slug not in niche_slugs:
            raise ValueError("winner must be one of the candidates")
        if not estimates:
            raise ValueError("winner requires complete estimates")
    rationale_value = root.get("selection_rationale", root.get("rationale", ""))
    selection_rationale = (_text(rationale_value, "selection rationale")
                           if rationale_value else "")
    if winner_slug and not selection_rationale:
        raise ValueError("winner requires a selection rationale")

    critiques = []
    if "critiques" in root:
        for index, raw in enumerate(_list(root["critiques"], "critiques", 15)):
            item = _object(raw, f"critique {index}")
            fields = {"persona", "niche_slug", "purchase_intent",
                      "would_follow_content", "top_objection", "gut_reaction"}
            _keys(item, fields, f"critique {index}")
            intent = item["purchase_intent"]
            if isinstance(intent, bool) or not isinstance(intent, int) or not 0 <= intent <= 100:
                raise ValueError("purchase intent must be an integer within 0..100")
            follows = item["would_follow_content"]
            if not isinstance(follows, bool):
                raise TypeError("would_follow_content must be boolean")
            persona = _text(item["persona"], "critique persona")
            slug = validate_slug(item["niche_slug"])
            if persona not in persona_names or slug not in niche_slugs:
                raise ValueError("critique references an unknown persona or niche")
            critiques.append(Critique(persona, slug, intent, follows,
                                      _text(item["top_objection"], "top objection"),
                                      _text(item["gut_reaction"], "gut reaction")))
        pairs = {(item.persona, item.niche_slug) for item in critiques}
        expected = {(persona, slug) for persona in persona_names for slug in niche_slugs}
        if pairs != expected or len(pairs) != 15:
            raise ValueError("critiques must cover every persona/niche pair exactly once")

    return OnboardingPayload(tuple(niches), tuple(personas), tuple(critiques),
                             tuple(estimates), winner_slug, selection_rationale)


def status_for_critic_verdict(verdict: str) -> str:
    return ("done" if verdict == "pass" else
            "infra_failed" if verdict == "infra_failed" else "failed")


def _write_json_durable(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass
class OnboardingRunJournal:
    path: Path
    run_id: str
    phase: OnboardingPhase
    owner_pid: int
    owner_process_start_id: str
    task_id: int
    staging_dir: str
    data: dict = field(default_factory=dict)

    @classmethod
    def create(cls, run_id: str, task_id: int, staging_dir: Path) -> "OnboardingRunJournal":
        identity = runlock._process_start_identity(os.getpid())
        if not identity:
            raise InfraError("cannot establish onboarding process identity")
        journal = cls(RUNS / run_id / "journal.json", run_id,
                      OnboardingPhase.ADMITTED, os.getpid(), identity,
                      task_id, str(staging_dir.resolve()))
        journal.save()
        return journal

    @classmethod
    def load(cls, path: Path) -> "OnboardingRunJournal":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, raw["run_id"], OnboardingPhase(raw["phase"]),
                   raw["owner_pid"], raw["owner_process_start_id"],
                   raw["task_id"], raw["staging_dir"], raw.get("data") or {})

    def save(self) -> None:
        _write_json_durable(self.path, {
            "schema_version": 1, "run_id": self.run_id, "phase": self.phase.value,
            "owner_pid": self.owner_pid,
            "owner_process_start_id": self.owner_process_start_id,
            "task_id": self.task_id, "staging_dir": self.staging_dir,
            "data": self.data,
        })

    def advance(self, phase: OnboardingPhase, **data: Any) -> None:
        order = list(OnboardingPhase)
        if phase != OnboardingPhase.TASK_FINALIZED and order.index(phase) != order.index(self.phase) + 1:
            raise InfraError(f"invalid onboarding phase transition {self.phase.value}->{phase.value}")
        if phase == OnboardingPhase.TASK_FINALIZED and self.phase == phase:
            raise InfraError("onboarding journal is already finalized")
        self.data.update(data)
        self.phase = phase
        self.save()


def _load_roles() -> dict:
    config = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    providers = config.get("providers") or {}
    return {name: {**providers.get(role.get("provider"), {}), **role}
            for name, role in config["roles"].items()}


def _chat(model_cfg: Mapping[str, Any], messages: list[dict[str, str]], tag: str,
          journal: OnboardingRunJournal, usage: OnboardingUsage) -> str:
    request = provider_chat.ChatRequest(
        model=model_cfg["model"], messages=tuple(messages), prompt="",
        timeout_seconds=300, **provider_chat.options_from_config(model_cfg, "onboarding"))
    try:
        result = provider_chat.chat(request)
    except provider_chat.ProviderChatError as exc:
        if exc.category in {provider_chat.ErrorCategory.QUOTA,
                            provider_chat.ErrorCategory.RATE_LIMIT}:
            raise QuotaError(str(exc)) from exc
        raise InfraError(f"{exc.category.value}: {exc}") from exc
    usage.add(result)
    audit = journal.path.parent / f"{journal.run_id}_{tag}.json"
    _write_json_durable(audit, {
        "run_id": journal.run_id, "tag": tag, "provider": result.provider,
        "model": result.model, "messages": messages, "response": result.content,
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "request_id": result.request_id,
    })
    return result.content


def _parse_json(text: str, model_cfg: Mapping[str, Any], tag: str,
                journal: OnboardingRunJournal, usage: OnboardingUsage) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    cleaned = re.sub(r"```(?:json)?|```", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    repaired = _chat(model_cfg, [{"role": "user", "content":
                      "Convert to strict JSON only:\n" + text[:6000]}],
                     tag + "_repair", journal, usage)
    match = re.search(r"\{.*\}", re.sub(r"```(?:json)?|```", "", repaired), flags=re.S)
    if not match:
        raise InfraError(f"unparseable JSON at {tag}")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise InfraError(f"invalid repaired JSON at {tag}") from exc
    if not isinstance(value, dict):
        raise InfraError(f"non-object JSON at {tag}")
    return value


def _stage_artifacts(payload: OnboardingPayload, blueprint: str,
                     staging: Path) -> dict[str, dict[str, str]]:
    if not payload.winner_slug or not payload.critiques or not payload.estimates:
        raise ValueError("complete reviewed payload required for staging")
    staging.mkdir(parents=True, exist_ok=False)
    selection = staging / "niche_selection.md"
    blueprint_path = staging / f"blueprint_{validate_slug(payload.winner_slug)}.md"
    selection.write_text(
        f"# Autonomous niche selection — {TODAY}\n\n"
        f"**WINNER:** `{payload.winner_slug}`\n\n"
        "> Probabilities are model-generated hypotheses, not measurements.\n\n"
        f"## Candidates\n```json\n{json.dumps([asdict(v) for v in payload.niches], indent=2)}\n```\n\n"
        f"## Personas\n```json\n{json.dumps([asdict(v) for v in payload.personas], indent=2)}\n```\n\n"
        f"## Critiques\n```json\n{json.dumps([asdict(v) for v in payload.critiques], indent=2)}\n```\n\n"
        f"## Estimates\n```json\n{json.dumps([asdict(v) for v in payload.estimates], indent=2)}\n```\n\n"
        f"## Rationale\n{payload.selection_rationale}\n", encoding="utf-8")
    blueprint_path.write_text(blueprint.strip() + f"\n\n---\n_Generated {TODAY}._\n",
                              encoding="utf-8")
    manifest = {}
    for path in (selection, blueprint_path):
        manifest[path.name] = {
            "staged": str(path.resolve()), "destination": str((WS / path.name).resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return manifest


def _publish_artifacts(manifest: Mapping[str, Mapping[str, str]],
                       expected_staging: Path) -> None:
    WS.mkdir(parents=True, exist_ok=True)
    workspace = WS.resolve()
    staging_root = expected_staging.resolve()
    for name, item in manifest.items():
        if Path(name).name != name:
            raise InfraError("artifact manifest contains an unsafe name")
        staged, destination = Path(item["staged"]), Path(item["destination"])
        if staged.resolve().parent != staging_root:
            raise InfraError("artifact staging path escaped the run directory")
        if destination.parent != workspace:
            raise InfraError("artifact destination escaped onboarding workspace")
        if staged.is_file():
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise InfraError("staged artifact digest mismatch")
            os.replace(staged, destination)
        if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != item["sha256"]:
            raise InfraError("artifact publication cannot be verified")


def _model_collection(manager: Mapping[str, Any], worker: Mapping[str, Any],
                      journal: OnboardingRunJournal, usage: OnboardingUsage) -> tuple[OnboardingPayload, str]:
    journal.advance(OnboardingPhase.COLLECTING)
    raw_niches = _parse_json(_chat(manager, [{"role": "user", "content":
        "Return JSON with exactly 3 store+content niches: niches[{name,slug,product_angle,content_angle,rationale}]."}],
        "brainstorm", journal, usage), manager, "brainstorm", journal, usage)
    raw_personas = _parse_json(_chat(manager, [{"role": "user", "content":
        "Return JSON with exactly 5 distinct personas: personas[{name,age,psych_trigger,attention_span,buying_friction,description}]."}],
        "personas", journal, usage), manager, "personas", journal, usage)
    base = validate_onboarding_payload({**raw_niches, **raw_personas})

    critiques = []
    for persona in base.personas:
        response = _parse_json(_chat(manager, [{"role": "user", "content":
            f"Persona={json.dumps(asdict(persona))}. Niches={json.dumps([asdict(n) for n in base.niches])}. "
            "Return critiques with exactly one item per niche: niche_slug,purchase_intent integer 0..100,would_follow_content boolean,top_objection,gut_reaction."}],
            f"critique_{validate_slug(re.sub('[^a-z0-9]+', '-', persona.name.lower()).strip('-'))}",
            journal, usage), manager, "critiques", journal, usage)
        for item in response.get("critiques", []):
            critiques.append({"persona": persona.name, **item})

    selection = _parse_json(_chat(manager, [{"role": "user", "content":
        f"Niches={json.dumps([asdict(n) for n in base.niches])}; critiques={json.dumps(critiques)}. "
        "Return estimates[{slug,conversion_probability,reason}], winner_slug, and rationale."}],
        "selection", journal, usage), manager, "selection", journal, usage)
    complete = validate_onboarding_payload({
        "niches": [asdict(v) for v in base.niches],
        "personas": [asdict(v) for v in base.personas], "critiques": critiques,
        "estimates": selection.get("estimates"), "winner_slug": selection.get("winner_slug"),
        "selection_rationale": selection.get("rationale"),
    })
    winner = next(item for item in complete.niches if item.slug == complete.winner_slug)
    blueprint = _chat(worker, [{"role": "user", "content":
        f"Create a detailed markdown blueprint for {json.dumps(asdict(winner))}. Include store structure, "
        "10 products, content engine, funnel, and 10 week-one tasks with done criteria."}],
        "blueprint", journal, usage)
    if len(blueprint.strip()) < 200:
        raise ValueError("blueprint is too short")
    return complete, re.sub(r"<think>.*?</think>", "", blueprint, flags=re.S).strip()


def _run_workflow(manager: Mapping[str, Any], worker: Mapping[str, Any],
                  journal: OnboardingRunJournal) -> int:
    usage = OnboardingUsage()
    payload, blueprint = _model_collection(manager, worker, journal, usage)
    manifest = _stage_artifacts(payload, blueprint, Path(journal.staging_dir))
    journal.advance(OnboardingPhase.PREPARED, payload=payload.to_json(),
                    artifacts=manifest, usage=asdict(usage))

    step("7/7 critic: judging staged evidence against pre-written pass criteria")
    verdict_text = _chat(manager, [{"role": "system", "content":
        "Reply PASS or FAIL first, then concise criterion findings."},
        {"role": "user", "content": f"Criteria:\n{PASS_CRITERIA}\nPayload:\n"
         f"{json.dumps(payload.to_json())}\nBlueprint:\n{blueprint[:4000]}"}],
        "critic", journal, usage)
    verdict_text = re.sub(r"<think>.*?</think>", "", verdict_text, flags=re.S).strip()
    verdict = "pass" if verdict_text.upper().startswith("PASS") else "fail"
    status = status_for_critic_verdict(verdict)
    journal.advance(OnboardingPhase.REVIEWED, verdict=verdict,
                    verdict_text=verdict_text[:2000], usage=asdict(usage))
    if verdict != "pass":
        ledger.finish_task(journal.task_id, artifacts=[], tokens_in=usage.input_tokens,
                           tokens_out=usage.output_tokens, critic_verdict=verdict,
                           critic_notes=verdict_text[:500], status=status)
        journal.advance(OnboardingPhase.TASK_FINALIZED, terminal_status=status)
        return 4

    _commit_domain_memory(payload, journal.run_id, journal.task_id)
    journal.advance(OnboardingPhase.DOMAIN_COMMITTED)
    _publish_artifacts(manifest, Path(journal.staging_dir))
    journal.advance(OnboardingPhase.ARTIFACTS_PUBLISHED)
    artifact_paths = [str(Path(item["destination"]).relative_to(ROOT)) for item in manifest.values()]
    ledger.finish_task(journal.task_id, artifacts=artifact_paths,
                       tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
                       critic_verdict="pass", critic_notes=verdict_text[:500], status="done")
    ledger.add_lesson(journal.task_id, "Typed staged onboarding saga completed", "worked")
    journal.advance(OnboardingPhase.TASK_FINALIZED, terminal_status="done")
    return 0


def step(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "recover"))
    args = parser.parse_args(argv)
    if pause_engaged():
        print("onboarding execution refused: global ESTOP is engaged", file=sys.stderr)
        return 75
    ROOT.joinpath("runs").mkdir(exist_ok=True)
    try:
        with runlock.acquire(LOCK_PATH):
            _recover_onboarding_sagas()
            if args.command == "recover":
                return 0
            roles = _load_roles()
            tid = ledger.queue_task("000-onboarding", "AUTONOMY RUN: typed staged niche selection",
                                    PASS_CRITERIA)
            ledger.start_task(tid, f"manager={roles['manager']['provider']}/{roles['manager']['model']}, "
                                   f"worker={roles['worker']['provider']}/{roles['worker']['model']}")
            run_id = uuid.uuid4().hex
            journal = OnboardingRunJournal.create(run_id, tid, WS / ".staging" / run_id)
            try:
                return _run_workflow(roles["manager"], roles["worker"], journal)
            except QuotaError as exc:
                ledger.finish_task(tid, artifacts=[], status="quota_wait",
                                   critic_notes=f"quota mid-run: {exc}", append_note=True)
                journal.advance(OnboardingPhase.TASK_FINALIZED, terminal_status="quota_wait")
                return 2
            except (InfraError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
                ledger.finish_task(tid, artifacts=[], status="infra_failed",
                                   critic_notes=f"onboarding infrastructure/validation failure: {exc}",
                                   append_note=True)
                # Before review there is nothing safe to roll forward. After a
                # PASS, retain the journal so the next process can idempotently
                # complete DB commit/publication/finalization without model calls.
                if list(OnboardingPhase).index(journal.phase) < list(OnboardingPhase).index(
                        OnboardingPhase.REVIEWED):
                    journal.advance(OnboardingPhase.TASK_FINALIZED,
                                    terminal_status="infra_failed")
                return 3
    except runlock.AlreadyRunning as exc:
        print(f"onboarding refused: harness lock held ({exc})", file=sys.stderr)
        return 75


def _commit_domain_memory(payload: OnboardingPayload, run_id: str, task_id: int) -> None:
    """Commit the reviewed domain mutation exactly once in one SQLite transaction."""
    if not payload.winner_slug or len(payload.estimates) != 3:
        raise ValueError("reviewed complete payload required")
    if not BOOK.is_file():
        raise FileNotFoundError(f"ledgerbook missing: {BOOK}")
    statement = f"Onboarding saga {run_id}: selected {payload.winner_slug}"
    with closing(sqlite3.connect(f"{BOOK.resolve().as_uri()}?mode=rw", uri=True, timeout=30,
                                 isolation_level=None)) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT 1 FROM decisions WHERE statement=?", (statement,)).fetchone()
            if not existing:
                winner = next(item for item in payload.niches if item.slug == payload.winner_slug)
                con.execute("INSERT INTO decisions (statement, rationale) VALUES (?,?)",
                            (statement, payload.selection_rationale))
                for niche in payload.niches:
                    con.execute("INSERT OR IGNORE INTO entities (type,name) VALUES ('niche',?)",
                                (niche.slug,))
                for estimate in payload.estimates:
                    fact_statement = (
                        f"Estimated conversion probability {estimate.conversion_probability}: "
                        f"{estimate.reason}"
                    )
                    existing_fact = con.execute(
                        "SELECT 1 FROM facts WHERE statement=? AND run_id=?",
                        (fact_statement, run_id),
                    ).fetchone()
                    if not existing_fact:
                        con.execute(
                            "INSERT OR IGNORE INTO facts (entity,statement,provenance_url,provenance_date,"
                            "confidence,status,source_task_id,run_id) VALUES (?,?,?,?,1,'candidate',?,?)",
                            (estimate.slug, fact_statement,
                             f"internal://onboarding/{run_id}", TODAY, task_id, run_id))
                if winner.slug != payload.winner_slug:
                    raise ValueError("winner lookup mismatch")
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise


def _recover_onboarding_sagas() -> None:
    """Roll forward reviewed orphan sagas; abandon pre-review work without model calls."""
    if not RUNS.is_dir():
        return
    current_pid = os.getpid()
    current_identity = runlock._process_start_identity(current_pid)
    if not current_identity:
        raise InfraError("cannot establish recovery process identity")
    for path in sorted(RUNS.glob("*/journal.json")):
        journal = OnboardingRunJournal.load(path)
        if journal.phase == OnboardingPhase.TASK_FINALIZED:
            continue
        owner_identity = runlock._process_start_identity(journal.owner_pid)
        if owner_identity == journal.owner_process_start_id:
            raise InfraError(f"onboarding saga still owned by a live process: {journal.run_id}")
        journal.owner_pid = current_pid
        journal.owner_process_start_id = current_identity
        journal.save()
        if journal.phase in {OnboardingPhase.ADMITTED, OnboardingPhase.COLLECTING,
                            OnboardingPhase.PREPARED}:
            ledger.finish_task(journal.task_id, artifacts=[], status="infra_failed",
                               critic_notes="onboarding process terminated before review",
                               append_note=True)
            journal.advance(OnboardingPhase.TASK_FINALIZED, terminal_status="infra_failed")
            continue
        payload = validate_onboarding_payload(journal.data["payload"])
        if journal.data.get("verdict") != "pass":
            status = status_for_critic_verdict(journal.data.get("verdict", "infra_failed"))
            ledger.finish_task(journal.task_id, artifacts=[], status=status,
                               critic_verdict=journal.data.get("verdict"),
                               critic_notes=journal.data.get("verdict_text", "")[:500])
            journal.advance(OnboardingPhase.TASK_FINALIZED, terminal_status=status)
            continue
        if journal.phase == OnboardingPhase.REVIEWED:
            _commit_domain_memory(payload, journal.run_id, journal.task_id)
            journal.advance(OnboardingPhase.DOMAIN_COMMITTED)
        if journal.phase == OnboardingPhase.DOMAIN_COMMITTED:
            _publish_artifacts(journal.data["artifacts"], Path(journal.staging_dir))
            journal.advance(OnboardingPhase.ARTIFACTS_PUBLISHED)
        if journal.phase == OnboardingPhase.ARTIFACTS_PUBLISHED:
            usage = journal.data.get("usage") or {}
            artifacts = [str(Path(item["destination"]).relative_to(ROOT))
                         for item in journal.data["artifacts"].values()]
            ledger.finish_task(journal.task_id, artifacts=artifacts, status="done",
                               tokens_in=int(usage.get("input_tokens") or 0),
                               tokens_out=int(usage.get("output_tokens") or 0),
                               critic_verdict="pass",
                               critic_notes=journal.data.get("verdict_text", "")[:500])
            journal.advance(OnboardingPhase.TASK_FINALIZED, terminal_status="done")


if __name__ == "__main__":
    raise SystemExit(main())
