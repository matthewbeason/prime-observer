import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "viz" / "index.html"


def extract_function(source: str, signature: str) -> str:
    start = source.find(signature)
    if start == -1:
        raise AssertionError(f"Could not find {signature}")
    paren_start = source.find("(", start)
    if paren_start == -1:
        raise AssertionError(f"Could not find parameter list for {signature}")
    depth = 0
    brace_start = None
    for idx in range(paren_start, len(source)):
        char = source[idx]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                brace_start = source.find("{", idx)
                break
    if brace_start is None:
        raise AssertionError(f"Could not find opening brace for {signature}")
    depth = 0
    for idx in range(brace_start, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:idx + 1]
    raise AssertionError(f"Could not find closing brace for {signature}")


class DashboardAttributionObservationStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def test_dashboard_declares_observation_and_legacy_attribution_sources(self):
        self.assertIn('const OBSERVATIONS_URL = "./observations.json";', self.html)
        self.assertIn('const NETWORK_ATTRIBUTION_URL = "./network_attribution.json";', self.html)
        self.assertIn("loadObservationsPayload()", self.html)
        self.assertIn("loadNetworkAttributionPayload()", self.html)
        self.assertIn("resolveCurrentAttribution({", self.html)
        self.assertIn("computeFallbackAttribution: () => computeAttribution(internetSeriesMarked, lanSeries, resolverSeriesMarked)", self.html)
        self.assertIn('return await res.json();', self.html)
        self.assertIn('return null;', self.html)

    def test_investigation_html_remains_unmigrated(self):
        investigation_html = (ROOT / "viz" / "investigate.html").read_text()
        self.assertNotIn("./observations.json", investigation_html)
        self.assertNotIn("current_attribution", investigation_html)


@unittest.skipUnless(shutil.which("osascript"), "osascript is required for dashboard JS adapter tests")
class DashboardAttributionObservationBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def run_js(self, body: str):
        functions = [
            "function normalizeAttributionConfidence",
            "function selectCurrentAttributionObservation",
            "function adaptObservationAttribution",
            "function adaptLegacyCurrentAttribution",
            "function resolveCurrentAttribution",
        ]
        snippets = "\n\n".join(extract_function(self.html, signature) for signature in functions)
        script = textwrap.dedent(
            f"""
            {snippets}
            function main(){{
            {textwrap.indent(body, "  ")}
            }}
            console.log(JSON.stringify(main()));
            """
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            script_path = handle.name
        try:
            completed = subprocess.run(
                ["osascript", "-l", "JavaScript", script_path],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )
        finally:
            Path(script_path).unlink(missing_ok=True)
        output = (completed.stdout or completed.stderr).strip()
        return json.loads(output.splitlines()[-1]) if output else None

    def test_current_attribution_uses_observation_when_present(self):
        result = self.run_js(
            """
            const payload = {
              observations: [
                {
                  type: "attribution",
                  scope: { system: "prime_observer", subject: "network", view: "current_attribution" },
                  state: { status: "likely_upstream", label: "Likely upstream (ISP / path)" },
                  confidence: "high",
                  explanation: "WAN shows sustained degradation while LAN stays below local threshold (0/17 elevated).",
                  supporting_facts: [
                    "Resolver probes degraded while internet probes remained healthy.",
                    "LAN/gateway elevated samples: 0/17."
                  ],
                  provenance: { source_export: "current_attribution" },
                  evidence_references: [{ kind: "artifact", path: "viz/network_attribution.json" }]
                }
              ]
            };
            const resolved = resolveCurrentAttribution({
              observationsPayload: payload,
              networkAttributionPayload: {
                current_attribution: {
                  status: "inconclusive",
                  label: "Inconclusive",
                  confidence: "low",
                  evidence: ["Legacy fallback should not win when observation is present."],
                  metrics: { target_group_facts: ["Legacy fact"] }
                }
              },
              computedAttribution: { label: "Computed", confidence: "Low", why: "Computed fallback", facts: ["Computed fact"] }
            });
            return resolved;
            """
        )
        self.assertEqual(result["source"], "observations")
        self.assertEqual(result["status"], "likely_upstream")
        self.assertEqual(result["label"], "Likely upstream (ISP / path)")
        self.assertEqual(result["confidence"], "High")
        self.assertEqual(
            result["why"],
            "WAN shows sustained degradation while LAN stays below local threshold (0/17 elevated).",
        )
        self.assertEqual(
            result["facts"],
            [
                "Resolver probes degraded while internet probes remained healthy.",
                "LAN/gateway elevated samples: 0/17.",
            ],
        )

    def test_falls_back_to_network_attribution_when_observation_missing(self):
        result = self.run_js(
            """
            const resolved = resolveCurrentAttribution({
              observationsPayload: { observations: [] },
              networkAttributionPayload: {
                current_attribution: {
                  status: "mixed_evidence",
                  label: "Mixed evidence",
                  confidence: "medium",
                  evidence: ["WAN evidence and LAN elevation are both present."],
                  metrics: {
                    target_group_facts: [
                      "Both internet and resolver probes degraded.",
                      "LAN/gateway also degraded."
                    ]
                  }
                }
              },
              computedAttribution: { label: "Computed", confidence: "Low", why: "Computed fallback", facts: [] }
            });
            return resolved;
            """
        )
        self.assertEqual(result["source"], "network_attribution")
        self.assertEqual(result["label"], "Mixed evidence")
        self.assertEqual(result["confidence"], "Medium")
        self.assertEqual(result["why"], "WAN evidence and LAN elevation are both present.")
        self.assertEqual(
            result["facts"],
            [
                "Both internet and resolver probes degraded.",
                "LAN/gateway also degraded.",
            ],
        )

    def test_falls_back_to_browser_computation_when_json_sources_unavailable(self):
        result = self.run_js(
            """
            const computed = {
              label: "Likely local (LAN / Wi-Fi)",
              confidence: "High",
              why: "LAN is elevated while WAN target groups remain stable.",
              facts: ["LAN/gateway also degraded."]
            };
            const resolved = resolveCurrentAttribution({
              observationsPayload: null,
              networkAttributionPayload: null,
              computedAttribution: computed
            });
            return resolved;
            """
        )
        self.assertEqual(result["label"], "Likely local (LAN / Wi-Fi)")
        self.assertEqual(result["confidence"], "High")
        self.assertEqual(result["why"], "LAN is elevated while WAN target groups remain stable.")
        self.assertEqual(result["facts"], ["LAN/gateway also degraded."])

    def test_does_not_invoke_browser_fallback_when_observation_exists(self):
        result = self.run_js(
            """
            let fallbackCalls = 0;
            const resolved = resolveCurrentAttribution({
              observationsPayload: {
                observations: [
                  {
                    type: "attribution",
                    scope: { system: "prime_observer", subject: "network", view: "current_attribution" },
                    state: { status: "likely_upstream", label: "Likely upstream (ISP / path)" },
                    confidence: "high",
                    explanation: "Observation-backed attribution.",
                    supporting_facts: ["Observation fact"]
                  }
                ]
              },
              networkAttributionPayload: null,
              computeFallbackAttribution: () => {
                fallbackCalls += 1;
                return { label: "Computed", confidence: "Low", why: "Computed fallback", facts: [] };
              }
            });
            return { resolved, fallbackCalls };
            """
        )
        self.assertEqual(result["resolved"]["source"], "observations")
        self.assertEqual(result["fallbackCalls"], 0)

    def test_invokes_browser_fallback_only_when_generated_sources_are_unavailable(self):
        result = self.run_js(
            """
            let fallbackCalls = 0;
            const resolved = resolveCurrentAttribution({
              observationsPayload: null,
              networkAttributionPayload: null,
              computeFallbackAttribution: () => {
                fallbackCalls += 1;
                return {
                  label: "Likely local (LAN / Wi-Fi)",
                  confidence: "High",
                  why: "Computed fallback",
                  facts: ["Computed fact"],
                  source: "computed"
                };
              }
            });
            return { resolved, fallbackCalls };
            """
        )
        self.assertEqual(result["resolved"]["source"], "computed")
        self.assertEqual(result["fallbackCalls"], 1)


if __name__ == "__main__":
    unittest.main()
