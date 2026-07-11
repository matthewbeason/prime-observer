import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InvestigationViewLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "viz" / "investigate.html").read_text()

    def test_investigation_view_uses_narrative_section_order(self):
        section_ids = [
            'id="assistantReviewSection"',
            'id="summarySection"',
            'id="timelineSection"',
            'id="coreEvidenceSection"',
            'id="environmentalContextSection"',
            'id="rawDetailSection"',
        ]
        indexes = [self.html.index(section_id) for section_id in section_ids]
        self.assertEqual(indexes, sorted(indexes))
        self.assertIn("Investigation Summary", self.html)
        self.assertIn("Operator Review", self.html)
        self.assertIn("Timeline", self.html)
        self.assertIn("Core Evidence", self.html)
        self.assertIn("Environmental Context", self.html)
        self.assertIn("Raw Detail", self.html)

    def test_investigation_view_exposes_presentation_only_status_and_chip_helpers(self):
        self.assertIn('id="attributionScopeSummary"', self.html)
        self.assertIn("function statusLabel(value)", self.html)
        self.assertIn("function toneForStatus(value)", self.html)
        self.assertIn("function renderChip(label, value, tone = \"tone-muted\")", self.html)
        self.assertIn("function attributionScopeSentence(currentAttribution, windowAttribution)", self.html)

    def test_operator_first_wording_replaces_ambiguous_attribution_labels(self):
        self.assertIn("Selected interval assessment", self.html)
        self.assertIn("Broader period", self.html)
        self.assertNotIn('renderMetricCard("Investigation window"', self.html)
        self.assertNotIn('renderMetricCard("Current attribution"', self.html)
        self.assertNotIn('renderMetricCard("Window attribution"', self.html)

    def test_notes_and_limitations_are_secondary_disclosures(self):
        summary_start = self.html.index('id="summarySection"')
        summary_end = self.html.index('</section>', summary_start)
        self.assertNotIn("Investigation Notes", self.html[summary_start:summary_end])
        self.assertIn("About this investigation", self.html)
        self.assertIn("Assessment details", self.html)
        self.assertNotIn("<h3>Limitations</h3>", self.html)

    def test_assistant_evidence_is_not_rendered_and_actions_hide_ids(self):
        self.assertNotIn('id="assistantReviewEvidence"', self.html)
        self.assertNotIn("review.evidence.map", self.html)
        self.assertIn("function operatorNextStep(step)", self.html)
        self.assertNotIn("fmt(step.id)", self.html)

    def test_probe_groups_are_humanized(self):
        self.assertIn('resolver_probe: "Resolver probes"', self.html)
        self.assertIn('internet_probe: "Internet probes"', self.html)
        self.assertIn('gateway_probe: "Gateway"', self.html)

    def test_investigation_view_keeps_raw_detail_in_disclosures(self):
        self.assertIn("<details class=\"disclosure\" open>", self.html)
        self.assertIn("Investigation Events", self.html)
        self.assertIn("Nearby Events", self.html)
        self.assertIn("Timeline Samples", self.html)
        self.assertIn("Telemetry Sources", self.html)

    def test_environmental_context_is_artifact_driven_and_hidden_when_absent(self):
        self.assertIn('id="environmentalContextSection" class="section" style="display:none;"', self.html)
        self.assertIn('id="internetConditionsSection" class="card context-block" style="display:none;"', self.html)
        self.assertIn('id="powerInfrastructureSection" class="card context-block" style="display:none;"', self.html)
        self.assertIn('const context = data.internet_conditions_context || {};', self.html)
        self.assertIn('const context = data.power_infrastructure_context || {};', self.html)
        self.assertNotIn("./internet_conditions.json", self.html)
        self.assertNotIn("./aps_power_context.json", self.html)

    def test_investigation_view_stays_artifact_driven_without_direct_model_calls(self):
        self.assertIn('const INVESTIGATION_URL = "./investigation.json";', self.html)
        self.assertIn('const OPERATOR_ASSISTANT_INPUT_URL = "./operator_assistant_input.json";', self.html)
        self.assertIn('const OPERATOR_ASSISTANT_OUTPUT_URL = "./operator_assistant_output.json";', self.html)
        self.assertIn('fetch(INVESTIGATION_URL, {cache: "no-store"})', self.html)
        self.assertIn("fetchOptionalJson(OPERATOR_ASSISTANT_INPUT_URL)", self.html)
        self.assertIn("fetchOptionalJson(OPERATOR_ASSISTANT_OUTPUT_URL)", self.html)
        self.assertIn('renderChip("Requested model"', self.html)
        self.assertIn("reviewHash !== currentInputHash", self.html)
        self.assertIn("does not match the current evidence package and is hidden", self.html)
        self.assertNotIn("crypto.subtle", self.html)
        self.assertNotIn("operatorAssistantInputHashForInvestigation", self.html)
        self.assertNotIn("./observations.json", self.html)
        self.assertNotIn("./network_attribution.json", self.html)
        self.assertNotIn("openrouter.ai/api/v1/chat/completions", self.html)


if __name__ == "__main__":
    unittest.main()
