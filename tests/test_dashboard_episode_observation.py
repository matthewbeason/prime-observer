import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "viz" / "index.html"


class DashboardEpisodeProjectionBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def test_dashboard_uses_python_projected_episode_buckets(self):
        self.assertIn("dashboardHealth.compositeWanBuckets.map", self.html)
        self.assertIn("const compositeWanBuckets = dashboardHealth", self.html)

    def test_browser_episode_classification_adapters_are_removed(self):
        for signature in (
            "function classifyBuckets",
            "function buildCompositeWanBuckets",
            "function adaptEpisodeObservation",
            "function applyEpisodeObservationsToBuckets",
            "function resolveEpisodeStateForBucket",
        ):
            self.assertNotIn(signature, self.html)

    def test_missing_projection_is_semantically_unavailable(self):
        self.assertIn(": markSemanticUnavailable(internetSeries)", self.html)
        self.assertIn(": markSemanticUnavailable(resolverSeries)", self.html)
        self.assertIn("Semantic heatmap unavailable; raw latency charts remain visible.", self.html)


if __name__ == "__main__":
    unittest.main()
