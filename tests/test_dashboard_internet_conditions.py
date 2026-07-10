import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardInternetConditionsTest(unittest.TestCase):
    def test_dashboard_consumes_generated_internet_conditions_only(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn('const INTERNET_CONDITIONS_URL = "./internet_conditions.json";', html)
        self.assertIn('fetch(INTERNET_CONDITIONS_URL, { cache: "no-store" })', html)
        self.assertIn("Cloudflare Radar", html)
        self.assertIn('id="internetConditionsCard"', html)
        self.assertIn('id="internetConditionsUnavailable"', html)
        self.assertIn('id="internetConditionsScope"', html)
        self.assertIn('id="internetConditionsDisclosure"', html)
        self.assertIn('id="internetConditionsDisclosureSummary"', html)
        self.assertIn('id="internetConditionsItemsWrap"', html)
        self.assertIn('id="internetConditionsItems"', html)
        self.assertIn('id="internetConditionsTarget"', html)
        self.assertIn('id="internetConditionsFallback"', html)
        self.assertIn('id="mobileInternetConditionsCard"', html)
        self.assertIn('id="mobileInternetConditionsDisclosure"', html)
        self.assertIn('id="mobileInternetConditionsDisclosureSummary"', html)
        self.assertIn('id="mobileInternetConditionsScope"', html)
        self.assertIn('id="mobileInternetConditionsItemsWrap"', html)
        self.assertIn('id="mobileInternetConditionsItems"', html)
        self.assertIn('id="mobileInternetConditionsTarget"', html)
        self.assertIn('id="mobileInternetConditionsFallback"', html)
        self.assertNotIn("api.cloudflare.com", html)
        self.assertNotIn("Authorization: Bearer", html)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", html)
        self.assertIn('data.scope && data.scope.label', html)
        self.assertIn('Array.isArray(data.items) ? data.items.slice(0, 3) : []', html)
        self.assertIn('data.provider_display_name', html)
        self.assertIn('data.query_target_id', html)
        self.assertIn('data.fallback_used', html)

    def test_dashboard_renders_scope_and_conditional_details_from_artifact(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn('renderInternetConditionItems("internetConditionsItemsWrap", "internetConditionsItems", items);', html)
        self.assertIn('renderInternetConditionItems("mobileInternetConditionsItemsWrap", "mobileInternetConditionsItems", items);', html)
        self.assertIn("View Radar evidence >", html)
        self.assertIn("Hide Radar evidence", html)
        self.assertIn('syncInternetConditionsDisclosureLabels(items.length);', html)
        self.assertIn('wrap.style.display = "none";', html)
        self.assertIn('wrap.style.display = "block";', html)
        self.assertIn('document.getElementById("internetConditionsTarget").textContent = targetLabel;', html)
        self.assertIn('document.getElementById("mobileInternetConditionsTarget").textContent = targetLabel;', html)

    def test_dashboard_does_not_hardcode_scope_or_signal_values(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertNotIn("United States context", html)
        self.assertNotIn("Traffic anomalies", html)
        self.assertNotIn("Outages", html)

    def test_dashboard_uses_provider_label_for_primary_status_without_raw_asn(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn('return `${providerName} normal`;', html)
        self.assertIn('return `${providerName} anomaly reported`;', html)
        self.assertNotIn('document.getElementById("internetConditionsValue").textContent = targetLabel;', html)

    def test_investigation_view_remains_independent(self):
        html = (ROOT / "viz" / "investigate.html").read_text()

        self.assertNotIn("./internet_conditions.json", html)
        self.assertIn('id="internetConditionsSection"', html)
        self.assertIn("data.internet_conditions_context", html)
        self.assertIn("context.query_target_id", html)
        self.assertIn("section.style.display = \"none\";", html)
        self.assertIn("section.style.display = \"block\";", html)


if __name__ == "__main__":
    unittest.main()
