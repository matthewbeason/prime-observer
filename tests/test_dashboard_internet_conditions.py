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
        self.assertIn('id="mobileInternetConditionsCard"', html)
        self.assertNotIn("api.cloudflare.com", html)
        self.assertNotIn("Authorization: Bearer", html)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", html)

    def test_investigation_view_remains_independent(self):
        html = (ROOT / "viz" / "investigate.html").read_text()

        self.assertNotIn("./internet_conditions.json", html)
        self.assertNotIn("Internet Conditions", html)


if __name__ == "__main__":
    unittest.main()
