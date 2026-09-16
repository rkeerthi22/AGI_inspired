"""Distribution Engine Phase 1 (Step 1a): Kill-Assumption Probe Suite.

Validates:
1. client_profile loader & validator (failure on missing, never invents).
2. keyword_research pure template function (arms preflight & citecheck).
3. Zero-spend containment (3-probe test per §2: no ads SDK, no mutate endpoints, no ad hosts).
4. Per-client workspace isolation (union: tasks/{tid}/ U clients/{client_id}/).
5. Full end-to-end dispatch through real admitted dispatch -> worker -> preflight -> critic -> deliverable.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
TESTS = ROOT / "tests"
for p in (ROOT, ORCH, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from v1_test_support import runner_fixture, source, worker_result
import operator_auth
import attestation_chain as chain
import client_profile
import policy
import integrity
import task_runner
from deliverable_preflight import PreflightReport
from research_templates.keyword_research import generate_keyword_research_task


SAMPLE_PROFILE = {
    "client_id": "acme-plumbing",
    "display_name": "Acme Plumbing",
    "domain": "plumbing services",
    "geo": ["US", "Austin-TX"],
    "language": ["en"],
    "offer": "Emergency drain cleaning and water heater repair with 24/7 dispatch",
    "audience": "Homeowners and property managers experiencing plumbing emergencies",
    "competitors": ["https://competitor-drain.example", "https://city-plumbing.example"],
    "brand_voice": "Authoritative, reassuring, prompt, honest pricing",
    "landing_url": "https://acme-plumbing.example/emergency-services",
    "seed_keywords": ["emergency plumber", "drain cleaning austin", "water heater repair"],
    "forbidden_claims": ["guaranteed 100% free repair", "cheapest in the universe"],
}


class DistributionEngineStep1aTests(unittest.TestCase):
    def setUp(self):
        self.keys = operator_auth._generate_keypair()
        self.patcher = patch.object(operator_auth, "_load_keypair", return_value=self.keys)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_client_profile_loader_and_validator(self):
        """Profile loads correctly from workspace/clients/{client_id}/profile.json; fails if missing."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            # Missing profile must raise ValueError("client profile not found")
            with self.assertRaises(ValueError) as cm:
                client_profile.load_client_profile("nonexistent-client", root=temp_root)
            self.assertEqual(str(cm.exception), "client profile not found")

            # Save valid profile
            saved_path = client_profile.save_client_profile(SAMPLE_PROFILE, root=temp_root)
            self.assertTrue(saved_path.is_file())
            self.assertEqual(saved_path, temp_root / "workspace" / "clients" / "acme-plumbing" / "profile.json")

            # Load valid profile
            loaded = client_profile.load_client_profile("acme-plumbing", root=temp_root)
            self.assertEqual(loaded["client_id"], "acme-plumbing")
            self.assertEqual(loaded["display_name"], "Acme Plumbing")
            self.assertEqual(loaded["seed_keywords"], SAMPLE_PROFILE["seed_keywords"])

            # Schema validation: missing field
            bad_profile = dict(SAMPLE_PROFILE)
            del bad_profile["forbidden_claims"]
            saved_path.write_text(json.dumps(bad_profile), encoding="utf-8")
            with self.assertRaises(ValueError) as cm_schema:
                client_profile.load_client_profile("acme-plumbing", root=temp_root)
            self.assertIn("missing required fields", str(cm_schema.exception))

    def test_keyword_research_template(self):
        """Pure template function generates spec and pass_criteria arming preflight checks."""
        spec, criteria = generate_keyword_research_task(SAMPLE_PROFILE, seed_input={"seed_keywords": ["clogged sink"]})
        
        # Spec verification
        self.assertIn("Acme Plumbing", spec)
        self.assertIn("acme-plumbing", spec)
        self.assertIn("clogged sink", spec)
        self.assertIn("water heater repair", spec)
        self.assertIn("emergency drain cleaning", spec.lower())
        
        # Criteria verification (arms deliverable_preflight)
        self.assertIn("At least 2 distinct independent sources cited.", criteria)
        self.assertIn("### Sources Attempted", criteria)
        self.assertIn("not publicly disclosed", criteria)
        self.assertIn("commercial, transactional, informational, navigational", criteria)
        self.assertIn("awareness, consideration, conversion", criteria)
        self.assertIn("cheapest in the universe", criteria)

    def test_zero_spend_containment_three_probes(self):
        """Probe 1: No ads-SDK import. Probe 2: No write/mutate endpoint symbol. Probe 3: No new ad host."""
        # Probe 1: No ads SDK import in orchestrator or test files
        sdk_forbidden = [
            "import " + "googleads",
            "from google" + ".ads",
            "from google" + "_ads",
            "googleads" + ".client",
            "from " + "googleads",
        ]
        sdk_pattern = re.compile(r"|".join(re.escape(p) for p in sdk_forbidden))
        
        # Probe 2: No write / mutate endpoint symbol in orchestrator or distribution tests
        mutate_forbidden = [
            "Campaign" + "Service",
            "AdGroup" + "Service",
            "Budget" + "Service",
            "mutate_" + "campaigns",
            "mutate_" + "ad_groups",
            "create_" + "campaign",
            "create_" + "ad_group",
            r"place.{0,8}bid",
            "ads." + "googleapis.com",
        ]
        mutate_pattern = re.compile(r"|".join(mutate_forbidden))

        py_files = list((ROOT / "orchestrator").rglob("*.py"))
        py_files.extend(ROOT / "tests" / p for p in ["test_distribution_keyword_research.py"])

        for f in py_files:
            content = f.read_text(encoding="utf-8", errors="ignore")
            m_sdk = sdk_pattern.search(content)
            self.assertIsNone(m_sdk, f"Probe 1 violation in {f}: matches SDK pattern {m_sdk}")
            
            # For probe 2, exclude checking the definitions inside this very test file
            if f.name != "test_distribution_keyword_research.py":
                m_mut = mutate_pattern.search(content)
                self.assertIsNone(m_mut, f"Probe 2 violation in {f}: matches mutate pattern {m_mut}")

        # Probe 3: No new ad-platform host in egress allowlist
        egress_policy_yaml = (ROOT / "config" / "egress_policy.yaml").read_text(encoding="utf-8")
        ad_host_keywords = ["googleads", "ads.google", "bingads", "ads.yahoo", "ads.tiktok", "adservice"]
        for kw in ad_host_keywords:
            self.assertNotIn(kw, egress_policy_yaml, f"Probe 3 violation: ad platform {kw} found in egress policy")

    def test_client_id_isolation_and_confinement(self):
        """Confinement check allows union of tasks/{tid}/ and clients/{client_id}/; rejects siblings."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            ws = temp_root / "workspace"
            tasks_dir = ws / "tasks" / "42"
            client_dir = ws / "clients" / "acme"
            sibling_client = ws / "clients" / "brand-b"
            tasks_dir.mkdir(parents=True)
            client_dir.mkdir(parents=True)
            sibling_client.mkdir(parents=True)

            with patch.object(policy, "ROOT", temp_root):
                # Allowed under task 42
                self.assertTrue(policy.is_path_writable(tasks_dir / "draft.txt", task_id=42, client_id="acme"))
                # Allowed under client acme
                self.assertTrue(policy.is_path_writable(client_dir / "profile.json", task_id=42, client_id="acme"))
                # Sibling client forbidden
                self.assertFalse(policy.is_path_writable(sibling_client / "leak.txt", task_id=42, client_id="acme"))
                # Sibling task forbidden
                self.assertFalse(policy.is_path_writable(ws / "tasks" / "43" / "run.txt", task_id=42, client_id="acme"))
                # worker_home forbidden
                self.assertFalse(policy.is_path_writable(ws / "worker_home" / "profile.json", task_id=42, client_id="acme"))

            # Integrity guard auto-reverts rogue sibling writes
            with patch.object(integrity, "_workspace_dir", return_value=ws):
                guard = integrity.WorkspaceConfinementGuard(task_id=42, context="test client guard", client_id="acme")
                with self.assertRaises(integrity.WorkspaceConfinementViolation):
                    with guard:
                        # Legitimate client write: allowed
                        (client_dir / "research.md").write_text("legitimate acme research", encoding="utf-8")
                        # Rogue write to sibling client: must be detected and reverted
                        (sibling_client / "rogue.txt").write_text("unauthorized write", encoding="utf-8")

                # The rogue write must have been unlinked
                self.assertFalse((sibling_client / "rogue.txt").exists())
                # The legitimate write was preserved in the client's own directory
                self.assertTrue((client_dir / "research.md").exists())

    def test_end_to_end_keyword_research_dispatch_and_gate(self):
        """Step 1a Kill-Assumption Probe: dispatch_admitted_task -> worker -> preflight -> critic -> deliverable."""
        spec, criteria = generate_keyword_research_task(SAMPLE_PROFILE)

        clean_keyword_deliverable = (
            "# Keyword Research: Acme Plumbing (Austin, TX)\n\n"
            "## Strategy & Funnel Mapping\n\n"
            "| Keyword | Intent | Funnel Stage | Rationale | Source URL |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| emergency plumber austin | transactional | conversion | Immediate dispatch for home burst pipes | https://alive.example/austin-plumbing |\n"
            "| water heater leaking repair | commercial | consideration | High intent diagnosis before technician dispatch | https://alive.example/water-heater-guide |\n"
            "| how to turn off main water valve | informational | awareness | Top of funnel emergency self-help | https://alive.example/austin-plumbing |\n\n"
            "### Sources Attempted\n"
            "- https://alive.example/austin-plumbing (status: rating-obtained, verified active)\n"
            "- https://alive.example/water-heater-guide (status: rating-obtained, verified active)\n"
        )

        def mock_worker(prompt, *_a, **_kw):
            return worker_result(clean_keyword_deliverable)

        with runner_fixture(mock_worker) as (root, runs, stack):
            # Save client profile in the fixture workspace
            client_profile.save_client_profile(SAMPLE_PROFILE, root=root)

            # 1. Dispatch through real canonical dispatch seam with client_id
            ledger_db = runs / "ledger.db"
            conn = sqlite3.connect(ledger_db)
            try:
                conn.execute(
                    "CREATE TABLE tasks (task_id INTEGER PRIMARY KEY, mission_id TEXT, "
                    "spec TEXT, pass_criteria TEXT, status TEXT, run_id TEXT, "
                    "attempt_count INTEGER DEFAULT 0, started_at TEXT, finished_at TEXT, "
                    "model_used TEXT, lease_expires_at TEXT, owner_pid INTEGER, "
                    "owner_process_start_id TEXT, artifacts TEXT, cost_usd REAL, "
                    "tokens_in INTEGER, tokens_out INTEGER, critic_verdict TEXT, "
                    "critic_notes TEXT, interventions TEXT, intervention_types TEXT)"
                )
                conn.commit()

                tid = chain.dispatch_admitted_task(
                    conn, runs, mission_id="distribution", spec=spec, pass_criteria=criteria,
                    client_id="acme-plumbing"
                )
            finally:
                conn.close()

            self.assertEqual(tid, 1)

            # Verify DISPATCH step exists and carries client_id
            payloads = chain.read_payloads(runs, tid)
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["step"], chain.Step.DISPATCH.value)
            self.assertEqual(payloads[0]["claims"]["client_id"], "acme-plumbing")
            self.assertEqual(payloads[0]["claims"]["mission_id"], "distribution")

            # 2. Setup preflight & CiteCheck mocks
            mock_sources = [
                source("https://alive.example/austin-plumbing"),
                source("https://alive.example/water-heater-guide"),
            ]
            report = PreflightReport(True, verified_sources=mock_sources)
            stack.enter_context(patch.object(task_runner.deliverable_preflight, "run_preflight", return_value=report))
            stack.enter_context(patch.object(task_runner.citecheck, "verify", return_value=mock_sources))
            stack.enter_context(patch.object(task_runner.evaluation, "run_critic", return_value=("pass", "Verified deliverable clean keyword table")))
            stack.enter_context(patch.object(task_runner.evaluation, "RUNS", runs))
            stack.enter_context(patch.object(task_runner.evaluation, "extract_facts", return_value=3))

            # 3. Execute research task through real task_runner pipeline
            cfg = {"provider": "fixture", "model": "fixture"}
            context_obj = task_runner._TaskContext(
                tid=tid,
                mission={"id": "distribution"},
                roles={"worker": cfg, "critic": cfg, "manager": cfg},
                row={"task_id": tid, "spec": spec, "pass_criteria": criteria, "attempt_count": 0, "run_id": chain.GATEWAY_RUN_ID},
            )

            result_status = task_runner._run_research_task(context_obj)
            self.assertEqual(result_status, "done")

            # 4. Verify complete 5-step DSSE attestation chain was produced and verified
            chain_statements = chain.read_chain(runs, tid)
            self.assertEqual(len(chain_statements), 5)
            payloads_full = chain.read_payloads(runs, tid)
            steps = [p["step"] for p in payloads_full]
            self.assertEqual(steps, [
                chain.Step.DISPATCH.value,
                chain.Step.WORKER.value,
                chain.Step.PREFLIGHT.value,
                chain.Step.CRITIC.value,
                chain.Step.DELIVERABLE.value,
            ])

            valid, err = chain.verify_chain(chain_statements, require_complete=True, task_id=tid)
            self.assertTrue(valid, f"Attestation chain failed verification: {err}")


if __name__ == "__main__":
    unittest.main()
