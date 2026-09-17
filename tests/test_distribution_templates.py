"""Distribution Engine Phase 1 (Step 1b): 4 Research Templates & Ad Copy End-to-End Suite.

Validates:
1. competitive_serp pure template function (arms preflight & citecheck, table, no invented positions).
2. ad_copy_variants pure template function (arms character limits <=30/<=90, CTA, superlative ban, forbidden claims).
3. seo_content_brief pure template function (arms H1/H2/H3 outline, entity coverage list, volume fabrication ban).
4. landing_page_recco pure template function (arms structural sections, why-it-converts rationale, grounding).
5. Zero-spend containment (3-probe test per §2: no ads SDK, no mutate endpoints, no ad hosts).
6. End-to-end dispatch of ad_copy_variants through real dispatch -> worker -> preflight -> critic -> deliverable loop.
"""
from __future__ import annotations

import json
import re
import sqlite3
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
import task_runner
from deliverable_preflight import PreflightReport
from research_templates import (
    generate_competitive_serp_task,
    generate_ad_copy_variants_task,
    generate_seo_content_brief_task,
    generate_landing_page_recco_task,
)

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


class DistributionEngineStep1bTests(unittest.TestCase):
    def setUp(self):
        self.keys = operator_auth._generate_keypair()
        self.patcher = patch.object(operator_auth, "_load_keypair", return_value=self.keys)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_competitive_serp_template(self):
        """Template 2: Pure function generates spec and criteria arming SERP research checks."""
        spec, criteria = generate_competitive_serp_task(
            SAMPLE_PROFILE,
            seed_input={"target_keyword": "water heater leaking austin"}
        )

        # Spec assertions
        self.assertIn("Acme Plumbing", spec)
        self.assertIn("acme-plumbing", spec)
        self.assertIn("water heater leaking austin", spec)
        self.assertIn("https://competitor-drain.example", spec)

        # Criteria assertions (arms deliverable_preflight and critic)
        self.assertIn("At least 2 distinct independent sources cited.", criteria)
        self.assertIn("### Sources Attempted", criteria)
        self.assertIn("not publicly disclosed", criteria)
        self.assertIn("| Competitor / Ranker | SERP Angle | Content Gap | Opportunity for Us | Source URL |", criteria)
        self.assertIn("No fabricated or invented SERP ranking positions.", criteria)
        self.assertIn("Every competitor row must cite a verifiable source URL.", criteria)
        self.assertIn("guaranteed 100% free repair", criteria)
        self.assertIn("cheapest in the universe", criteria)

    def test_ad_copy_variants_template(self):
        """Template 3: Pure function generates spec and criteria arming character limits, CTA, and superlative ban."""
        spec, criteria = generate_ad_copy_variants_task(
            SAMPLE_PROFILE,
            seed_input={"target_keyword": "emergency drain cleaning", "intent": "transactional"}
        )

        # Spec assertions
        self.assertIn("Acme Plumbing", spec)
        self.assertIn("emergency drain cleaning", spec)
        self.assertIn("transactional", spec)
        self.assertIn("Authoritative, reassuring, prompt, honest pricing", spec)
        self.assertIn("Responsive Search Ads (RSA)", spec)
        self.assertIn("Performance Max (PMax)", spec)

        # Criteria assertions (arms preflight, character limits, CTA, superlative ban, forbidden claims)
        self.assertIn("At least 2 distinct independent sources cited.", criteria)
        self.assertIn("### Sources Attempted", criteria)
        self.assertIn("not publicly disclosed", criteria)
        self.assertIn("headlines <= 30 characters", criteria)
        self.assertIn("descriptions <= 90 characters", criteria)
        self.assertIn("long headlines <= 90 characters", criteria)
        self.assertIn("explicit Call-to-Action (CTA)", criteria)
        self.assertIn("Unsubstantiated superlatives ('best', '#1', 'guaranteed') are strictly forbidden", criteria)
        self.assertIn("Authoritative, reassuring, prompt, honest pricing", criteria)
        self.assertIn("guaranteed 100% free repair", criteria)
        self.assertIn("cheapest in the universe", criteria)

    def test_seo_content_brief_template(self):
        """Template 4: Pure function generates spec and criteria arming H1/H2/H3 outline and entity coverage."""
        spec, criteria = generate_seo_content_brief_task(
            SAMPLE_PROFILE,
            seed_input={"target_keyword": "how to clear blocked drain", "intent": "informational"}
        )

        # Spec assertions
        self.assertIn("Acme Plumbing", spec)
        self.assertIn("how to clear blocked drain", spec)
        self.assertIn("informational", spec)
        self.assertIn("H1, H2, and H3", spec)
        self.assertIn("entity coverage list", spec)

        # Criteria assertions (arms preflight, outline, entities, search-volume fabrication ban)
        self.assertIn("At least 2 distinct independent sources cited.", criteria)
        self.assertIn("### Sources Attempted", criteria)
        self.assertIn("not publicly disclosed", criteria)
        self.assertIn("H1, H2, and H3", criteria)
        self.assertIn("entity coverage list", criteria)
        self.assertIn("No fabricated search-volume numbers", criteria)
        self.assertIn("guaranteed 100% free repair", criteria)
        self.assertIn("cheapest in the universe", criteria)

    def test_landing_page_recco_template(self):
        """Template 5: Pure function generates spec and criteria arming page structure and why-it-converts."""
        spec, criteria = generate_landing_page_recco_task(
            SAMPLE_PROFILE,
            seed_input={"target_keyword": "24/7 plumber near me", "intent": "transactional"}
        )

        # Spec assertions
        self.assertIn("Acme Plumbing", spec)
        self.assertIn("24/7 plumber near me", spec)
        self.assertIn("transactional", spec)
        self.assertIn("Hero, Subhead, Proof/Trust elements, CTA", spec)
        self.assertIn("NOT a full copy rewrite", spec)

        # Criteria assertions (arms preflight, sections, why-it-converts rationale)
        self.assertIn("At least 2 distinct independent sources cited.", criteria)
        self.assertIn("### Sources Attempted", criteria)
        self.assertIn("not publicly disclosed", criteria)
        self.assertIn("Section Name (Hero, Subhead, Proof/Trust, CTA)", criteria)
        self.assertIn("Why-It-Converts Rationale", criteria)
        self.assertIn("grounded in target keyword intent and client offer", criteria)
        self.assertIn("guaranteed 100% free repair", criteria)
        self.assertIn("cheapest in the universe", criteria)

    def test_zero_spend_containment_three_probes(self):
        """Re-verify Zero-Spend Containment across all 4 new templates and orchestrator code."""
        # Probe 1: No ads SDK import in orchestrator or distribution test files
        sdk_forbidden = [
            "import " + "googleads",
            "from google" + ".ads",
            "from google" + "_ads",
            "googleads" + ".client",
            "from " + "googleads",
        ]
        sdk_pattern = re.compile(r"|".join(re.escape(p) for p in sdk_forbidden))

        # Probe 2: No write / mutate endpoint symbols in orchestrator
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
        test_files = [ROOT / "tests" / "test_distribution_keyword_research.py",
                      ROOT / "tests" / "test_distribution_templates.py"]

        for f in py_files:
            content = f.read_text(encoding="utf-8", errors="ignore")
            m_sdk = sdk_pattern.search(content)
            self.assertIsNone(m_sdk, f"Probe 1 violation in {f}: matches SDK pattern {m_sdk}")

            m_mut = mutate_pattern.search(content)
            self.assertIsNone(m_mut, f"Probe 2 violation in {f}: matches mutate pattern {m_mut}")

        for tf in test_files:
            if tf.exists():
                content = tf.read_text(encoding="utf-8", errors="ignore")
                m_sdk = sdk_pattern.search(content)
                self.assertIsNone(m_sdk, f"Probe 1 violation in {tf}: matches SDK pattern {m_sdk}")

        # Probe 3: No new ad-platform host in egress allowlist
        egress_policy_yaml = (ROOT / "config" / "egress_policy.yaml").read_text(encoding="utf-8")
        ad_host_keywords = ["googleads", "ads.google", "bingads", "ads.yahoo", "ads.tiktok", "adservice"]
        for kw in ad_host_keywords:
            self.assertNotIn(kw, egress_policy_yaml, f"Probe 3 violation: ad platform {kw} found in egress policy")

    def test_end_to_end_ad_copy_variants_dispatch_and_gate(self):
        """Step 1b End-to-End: ad_copy_variants through real dispatch -> worker -> preflight -> critic -> deliverable loop."""
        spec, criteria = generate_ad_copy_variants_task(
            SAMPLE_PROFILE,
            seed_input={"target_keyword": "emergency plumber austin", "intent": "transactional"}
        )

        # Compliant ad copy deliverable adhering strictly to character limits, CTAs, no superlatives, and 2 sources
        clean_ad_copy_deliverable = (
            "# Ad Copy Variants: Acme Plumbing (Austin, TX)\n\n"
            "## Responsive Search Ads (RSA)\n\n"
            "| Format | Component | Copy Text | Characters | CTA |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| RSA | Headline 1 | Austin Emergency Plumber | 24 / 30 | Call Now |\n"
            "| RSA | Headline 2 | Fast 24/7 Leak Dispatch | 24 / 30 | Call Now |\n"
            "| RSA | Headline 3 | Water Heater Repair Pros | 24 / 30 | Schedule Online |\n"
            "| RSA | Description 1 | Burst pipe or blocked drain? Local Austin plumbers dispatched 24/7. Call for fast repair! | 89 / 90 | Call for fast repair! |\n"
            "| RSA | Description 2 | Honest upfront pricing on drain cleaning and emergency leak repair. Call our Austin team! | 89 / 90 | Call our Austin team! |\n\n"
            "## Performance Max (PMax) Variants\n\n"
            "| Format | Component | Copy Text | Characters | CTA |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| PMax | Short Headline | 24/7 Austin Plumber | 19 / 30 | Call Now |\n"
            "| PMax | Long Headline | Rapid emergency plumbing and water heater repair across Austin. 24/7 dispatch available. | 89 / 90 | Call Now |\n"
            "| PMax | Description | Licensed technicians ready for immediate burst pipe repair and drain clearing in Austin. | 88 / 90 | Get Help Now |\n\n"
            "### Sources Attempted\n"
            "- https://alive.example/austin-plumbing (status: rating-obtained, verified service scope)\n"
            "- https://alive.example/water-heater-guide (status: rating-obtained, verified component pricing)\n"
        )

        def mock_worker(prompt, *_a, **_kw):
            return worker_result(clean_ad_copy_deliverable)

        with runner_fixture(mock_worker) as (root, runs, stack):
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
            stack.enter_context(patch.object(task_runner.evaluation, "run_critic", return_value=("pass", "Verified ad copy variants with character limits and CTAs")))
            stack.enter_context(patch.object(task_runner.evaluation, "RUNS", runs))
            stack.enter_context(patch.object(task_runner.evaluation, "extract_facts", return_value=3))

            # 3. Execute ad copy task through real task_runner pipeline
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
