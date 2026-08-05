import json
import subprocess
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


class DashboardTimeContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def run_js(self, body):
        functions = [
            "function parseTs",
            "function toShortTime",
            "function fmt1",
            "function healthLabel",
            "function intervalsOverlap",
            "function itemTimeWindow",
            "function timeContextMatchesGenerated",
            "function timeContextFromSelection",
            "function externalEventsForContext",
            "function selectedTimePatternText",
            "function selectedTimeSummaryText",
        ]
        snippets = "\n\n".join(extract_function(self.html, signature) for signature in functions)
        script = textwrap.dedent(f"""
        {snippets}
        function main(){{
        {textwrap.indent(body, "  ")}
        }}
        console.log(JSON.stringify(main()));
        """)
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            self.fail(f"Node script failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return json.loads(result.stdout.strip())

    def test_selected_interval_changes_dashboard_summary(self):
        result = self.run_js("""
        const bucket = {t: new Date("2026-08-05T12:00:00Z"), t2: new Date("2026-08-05T12:15:00Z"), isBadBucket: true, isTurbulence: false};
        const evidence = {internetSustainedBadSamples: 0, internetRawBadSamples: 0, internetSamples: 2, resolverSustainedBadSamples: 2, resolverRawBadSamples: 2, resolverSamples: 2, lanElevatedSamples: 0, lanSamples: 1};
        return selectedTimeSummaryText(bucket, evidence, null);
        """)

        self.assertIn("Sustained degradation", result)
        self.assertIn("Resolver probes", result)

    def test_graphs_highlight_selected_interval(self):
        self.assertIn("selectedInterval", self.html)
        self.assertIn("Inside selected heatmap interval", self.html)
        self.assertIn("blue outline = selected interval", self.html)

    def test_external_events_appear_only_when_overlapping(self):
        result = self.run_js("""
        const context = {start: "2026-08-05T12:00:00Z", end: "2026-08-05T12:15:00Z"};
        const overlapping = {items: [{started: "2026-08-05T12:05:00Z", ended: "2026-08-05T12:10:00Z"}]};
        const outside = {items: [{started: "2026-08-05T13:00:00Z", ended: "2026-08-05T13:10:00Z"}]};
        return {
          yes: externalEventsForContext(context, overlapping, null),
          no: externalEventsForContext(context, outside, null),
        };
        """)

        self.assertEqual(result["yes"], ["Cloudflare routing event"])
        self.assertEqual(result["no"], [])

    def test_operational_learnings_respect_selected_interval_incident(self):
        result = self.run_js("""
        const context = {incident_id: "event-2"};
        const learnings = {insights: [
          {title: "Wrong incident", supporting_incidents: ["event-1"], confidence: "high", stability: "stable"},
          {title: "Matching learning", supporting_incidents: ["event-2"], confidence: "medium", stability: "emerging"}
        ]};
        return selectedTimePatternText(context, null, learnings);
        """)

        self.assertEqual(result, "Matching learning")

    def test_similarity_respects_selected_interval_incident(self):
        result = self.run_js("""
        const similarity = {current_incident: {incident_id: "event-1", pattern: "resolver_path_degradation"}};
        return {
          match: selectedTimePatternText({incident_id: "event-1"}, similarity, null),
          miss: selectedTimePatternText({incident_id: "event-2"}, similarity, null),
        };
        """)

        self.assertEqual(result["match"], "Resolver Path Degradation")
        self.assertEqual(result["miss"], "No selected-time pattern match")

    def test_no_selection_defaults_to_current_context(self):
        result = self.run_js("""
        return timeContextFromSelection(null, {mode: "current", start: "2026-08-05T12:00:00Z", end: "2026-08-05T12:15:00Z"});
        """)

        self.assertEqual(result["mode"], "current")

    def test_legacy_missing_time_context_is_safe(self):
        result = self.run_js("""
        return timeContextFromSelection(null, null);
        """)

        self.assertEqual(result["mode"], "current")
        self.assertFalse(result["overlaps_incident"])

    def test_browser_does_not_infer_time_context_semantics(self):
        self.assertIn('const TIME_CONTEXT_URL = "./time_context.json";', self.html)
        self.assertNotIn("inferTimeContext", self.html)
        self.assertNotIn("inferSelectedInterval", self.html)
        self.assertNotIn("openrouter.ai/api/v1/chat/completions", self.html)


if __name__ == "__main__":
    unittest.main()
