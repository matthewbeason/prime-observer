import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardPowerInfrastructureTest(unittest.TestCase):
    def test_dashboard_consumes_generated_power_context_only(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn('const APS_POWER_CONTEXT_URL = "./aps_power_context.json";', html)
        self.assertIn('fetch(APS_POWER_CONTEXT_URL, { cache: "no-store" })', html)
        self.assertIn("Power Infrastructure", html)
        self.assertIn('id="powerInfrastructureCard"', html)
        self.assertIn('id="powerInfrastructureScope"', html)
        self.assertIn('id="powerInfrastructureSignalsChecked"', html)
        self.assertIn('id="powerInfrastructureItemsWrap"', html)
        self.assertIn('id="powerInfrastructureItems"', html)
        self.assertIn('id="mobilePowerInfrastructureCard"', html)
        self.assertIn('id="mobilePowerInfrastructureScope"', html)
        self.assertIn('id="mobilePowerInfrastructureSignalsChecked"', html)
        self.assertIn('id="mobilePowerInfrastructureItemsWrap"', html)
        self.assertIn('id="mobilePowerInfrastructureItems"', html)
        self.assertNotIn("aps-ags.esriemcs.com", html)
        self.assertNotIn("outagemap.aps.com/outageviewer/mockData", html)
        self.assertIn('data.provider !== "aps" || data.status === "unavailable"', html)

    def test_dashboard_hides_power_card_when_unavailable(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn('style="min-width:240px; display:none;" id="powerInfrastructureCard"', html)
        self.assertIn('id="mobilePowerInfrastructureCard"', html)
        self.assertIn('style="display:none;"', html)
        self.assertIn('document.getElementById("powerInfrastructureCard").style.display = "none";', html)
        self.assertIn('document.getElementById("mobilePowerInfrastructureCard").style.display = "none";', html)
        self.assertIn("setPowerInfrastructureHidden();", html)

    def test_investigation_view_renders_power_context_without_direct_provider_fetch(self):
        html = (ROOT / "viz" / "investigate.html").read_text()

        self.assertNotIn("./aps_power_context.json", html)
        self.assertIn('id="powerInfrastructureSection"', html)
        self.assertIn("data.power_infrastructure_context", html)
        self.assertIn('document.getElementById("powerInfrastructureContext")', html)
        self.assertIn('section.style.display = "none";', html)
        self.assertIn('section.style.display = "block";', html)


if __name__ == "__main__":
    unittest.main()
