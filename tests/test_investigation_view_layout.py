import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InvestigationViewLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "viz" / "investigate.html").read_text()

    def test_investigation_view_uses_narrative_section_order(self):
        section_ids = [
            'id="summarySection"',
            'id="timelineSection"',
            'id="coreEvidenceSection"',
            'id="environmentalContextSection"',
            'id="rawDetailSection"',
        ]
        indexes = [self.html.index(section_id) for section_id in section_ids]
        self.assertEqual(indexes, sorted(indexes))
        self.assertIn("Investigation Summary", self.html)
        self.assertIn("Timeline", self.html)
        self.assertIn("Core Evidence", self.html)
        self.assertIn("Environmental Context", self.html)
        self.assertIn("Raw Detail", self.html)

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

    def test_investigation_view_stays_driven_by_investigation_json_only(self):
        self.assertIn('const INVESTIGATION_URL = "./investigation.json";', self.html)
        self.assertIn('fetch(INVESTIGATION_URL, {cache: "no-store"})', self.html)
        self.assertNotIn("./observations.json", self.html)
        self.assertNotIn("./network_attribution.json", self.html)


if __name__ == "__main__":
    unittest.main()
