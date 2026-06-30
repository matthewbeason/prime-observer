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

        self.assertIn("Composite WAN evidence: internet + resolver probes", html)
        self.assertIn('renderHeatmap("#heatmap", currentVizState.compositeWanBuckets)', html)
        self.assertIn("function buildCompositeWanBuckets", html)
        self.assertIn('getNoticeabilitySummary([...internetSeriesMarked, ...resolverSeriesMarked]', html)
        self.assertIn("Internet degraded", html)
        self.assertIn("Resolver degraded", html)
        self.assertIn("dark gray = sustained p95/jitter/loss degradation", html)
        self.assertIn("line = p95 latency only", html)
        self.assertIn("Raw reasons: p95", html)
        self.assertIn("Selected Bucket", html)
        self.assertIn("selectedBucket: currentVizState.selectedBucket", html)
        self.assertIn("WAN resolver, and LAN gateway charts", html)
        self.assertNotIn("Internet probes only", html)
        self.assertNotIn("Number(k)", html)

    def test_dashboard_ux_copy_uses_clearer_local_loading_and_selection_states(self):
        html = (ROOT / "viz" / "index.html").read_text()

        self.assertIn("Loading latest telemetry", html)
        self.assertIn("Waiting for recent telemetry", html)
        self.assertIn("Historical Evidence", html)
        self.assertIn("No selection", html)
        self.assertIn("Choose a heatmap bucket to pin the same interval across all three charts.", html)
        self.assertIn("Additional Detail Cards", html)
        self.assertIn("Selected Interval Evidence", html)
        self.assertIn("Click a bucket to pin a 15-minute interval.", html)
        self.assertIn("review that same 15-minute interval across the evidence chips and all three charts", html)
        self.assertIn("Why This Interval", html)
        self.assertIn('id="clearSelectionButton"', html)
        self.assertIn("Clear selection", html)
        self.assertIn("Historical Evidence (Selected)", html)
        self.assertIn("Open Historical Evidence for this interval", html)
        self.assertIn('window.addEventListener("keydown", ev => {', html)
        self.assertIn('ev.key === "Escape"', html)
        self.assertIn('attr("tabindex", 0)', html)
        self.assertIn('attr("role", "button")', html)
        self.assertIn('attr("aria-pressed"', html)
        self.assertIn('function buildInvestigationHref(bucket)', html)
        self.assertIn("<h1>Prime Observer</h1>", html)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', html)
        self.assertIn('setAttribute("aria-label", bucket ? "Open Historical Evidence for the selected interval" : "Open Historical Evidence")', html)
        self.assertIn('setAttribute("aria-label", bucket ? "Open Historical Evidence for this interval" : "Open Historical Evidence")', html)
        self.assertIn('role="img" aria-labelledby="wanInternetHeading wanInternetLegend"', html)
        self.assertIn('role="group" aria-labelledby="heatmapHeading heatmapLegend"', html)
        self.assertIn('svg [role="button"]:focus-visible', html)

    def test_investigation_view_renders_dns_context_without_api_access(self):
        html = (ROOT / "viz" / "investigate.html").read_text()

        self.assertIn("DNS Context", html)
        self.assertIn("data.dns_context", html)
        self.assertIn("Total queries", html)
        self.assertIn("Blocked", html)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', html)
        self.assertNotIn("api.nextdns.io", html)
        self.assertNotIn("X-Api-Key", html)


if __name__ == "__main__":
    unittest.main()
