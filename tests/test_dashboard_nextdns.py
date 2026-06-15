import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardNextDnsTest(unittest.TestCase):
    def test_dashboard_consumes_generated_summary_only(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn('const NEXTDNS_URL = "./nextdns_summary.json";', html)
        self.assertIn('fetch(NEXTDNS_URL, { cache: "no-store" })', html)
        self.assertIn('id="dnsQueries"', html)
        self.assertIn('id="dnsEncrypted"', html)
        self.assertIn('id="dnsUnavailable"', html)
        self.assertNotIn("api.nextdns.io", html)
        self.assertNotIn("X-Api-Key", html)
        self.assertNotIn("NEXTDNS_API_KEY", html)

    def test_dashboard_explains_heatmap_and_chart_semantics(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn("dark gray = sustained p95/jitter/loss degradation", html)
        self.assertIn("line = p95 latency only", html)
        self.assertIn("Raw reasons: p95", html)
        self.assertNotIn("Number(k)", html)

    def test_investigation_view_renders_dns_context_without_api_access(self):
        html = (ROOT / "viz" / "investigate.html").read_text()

        self.assertIn("DNS Context", html)
        self.assertIn("data.dns_context", html)
        self.assertIn("Total queries", html)
        self.assertIn("Blocked", html)
        self.assertNotIn("api.nextdns.io", html)
        self.assertNotIn("X-Api-Key", html)


if __name__ == "__main__":
    unittest.main()
