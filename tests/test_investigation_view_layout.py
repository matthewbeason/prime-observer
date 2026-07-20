import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InvestigationViewLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "viz" / "investigate.html").read_text()

    def test_operator_first_section_order(self):
        section_ids = [
            'id="assistantReviewSection"',
            'id="recommendedActionsSection"',
            'id="summarySection"',
            'id="timelineSection"',
            'id="recoverySection"',
            'id="coreEvidenceSection"',
            'id="bucketSummarySection"',
            'id="rawDetailSection"',
            'id="historySection"',
            'id="forensicTablesSection"',
        ]
        indexes = [self.html.index(section_id) for section_id in section_ids]
        self.assertEqual(indexes, sorted(indexes))

    def test_primary_page_uses_operator_language(self):
        for text in (
            "Operator Assessment",
            "Recommended Next Actions",
            "Scope And Impact",
            "Primary Timeline",
            "Recovery Progress",
            "Supporting And Limiting Evidence",
            "Condensed Evidence Buckets",
            "Raw Forensic Evidence",
            "Investigation History",
        ):
            self.assertIn(text, self.html)
        self.assertNotIn("Artifact state", self.html)
        self.assertNotIn("Episode status", self.html)

    def test_failure_language_is_not_primary_assessment_content(self):
        self.assertNotIn("Operator Assistant review is unavailable", self.html)
        self.assertNotIn("does not match the current evidence package and is hidden", self.html)
        self.assertNotIn("Provider error", self.html)
        self.assertIn("Deterministic fallback", self.html)

    def test_renderer_uses_python_generated_operator_fields(self):
        for field in (
            "data.operator_brief",
            "data.scope_impact",
            "data.recovery_progress",
            "data.episode_summary",
            "data.evidence_argument",
            "data.evidence_buckets",
            "row.phase_summary",
        ):
            self.assertIn(field, self.html)
        self.assertNotIn("inferLifecycle", self.html)
        self.assertNotIn("inferSeverity", self.html)
        self.assertNotIn("calculateDuration", self.html)

    def test_probe_groups_are_humanized(self):
        self.assertIn('resolver_probe: "Resolver probes"', self.html)
        self.assertIn('internet_probe: "Internet probes"', self.html)
        self.assertIn('gateway_probe: "Gateway"', self.html)

    def test_raw_detail_stays_secondary_in_disclosures(self):
        self.assertIn("Assessment provenance", self.html)
        self.assertIn("Observation references", self.html)
        self.assertIn("Health and thresholds", self.html)
        self.assertIn("Investigation Events", self.html)
        self.assertIn("Timeline Samples", self.html)
        self.assertIn("Telemetry Sources", self.html)

    def test_environmental_context_is_artifact_driven_without_direct_fetches(self):
        self.assertIn("DNS Context", self.html)
        self.assertIn("data.dns_context", self.html)
        self.assertIn("data.internet_conditions_context", self.html)
        self.assertIn("data.power_infrastructure_context", self.html)
        self.assertNotIn("./internet_conditions.json", self.html)
        self.assertNotIn("./aps_power_context.json", self.html)

    def test_assistant_internal_state_is_secondary_not_primary_copy(self):
        primary_start = self.html.index('id="assistantReviewSection"')
        primary_end = self.html.index('id="recommendedActionsSection"')
        primary = self.html[primary_start:primary_end]

        self.assertNotIn("requested_model", primary)
        self.assertNotIn("provider_model", primary)
        self.assertIn("assistantReviewProvenance", self.html)

    def test_timeline_separates_representative_and_maximum_metrics(self):
        self.assertIn("Representative p95", self.html)
        self.assertIn("Maximum", self.html)
        self.assertIn("Isolated excursions stay separate", self.html)
        self.assertIn("Persistence", self.html)

    def test_buckets_are_condensed_by_default(self):
        self.assertIn("Stable buckets are collapsed by default", self.html)
        self.assertIn("Show all evidence buckets", self.html)
        self.assertIn("summary.stable_buckets", self.html)
        self.assertIn("summary.sustained_degradation_buckets", self.html)

    def test_history_is_url_addressable_and_local(self):
        self.assertIn("data-event-id", self.html)
        self.assertIn("history.pushState", self.html)
        self.assertIn("popstate", self.html)
        self.assertIn("?event=", self.html)
        self.assertIn('loadInvestigation(`./${button.dataset.snapshotPath}`', self.html)

    def test_browser_remains_local_renderer_only(self):
        self.assertIn('const INVESTIGATION_URL = "./investigation.json";', self.html)
        self.assertIn('const OPERATOR_ASSISTANT_INPUT_URL = "./operator_assistant_input.json";', self.html)
        self.assertIn('const OPERATOR_ASSISTANT_OUTPUT_URL = "./operator_assistant_output.json";', self.html)
        self.assertNotIn("openrouter.ai/api/v1/chat/completions", self.html)
        self.assertNotIn("crypto.subtle", self.html)
        self.assertNotIn("is_wan_bad", self.html)


if __name__ == "__main__":
    unittest.main()
