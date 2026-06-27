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


class DashboardEpisodeObservationStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def test_dashboard_declares_episode_projection_adapter(self):
        self.assertIn("function selectEpisodeObservations", self.html)
        self.assertIn("function applyEpisodeObservationsToBuckets", self.html)
        self.assertIn("function resolveEpisodeStateForBucket", self.html)
        self.assertIn("semanticSource = bucket.episodeObservations.length ? \"observations\" : \"classification\"", self.html)
        self.assertIn("buildCompositeWanBuckets(internetSeriesMarked, resolverSeriesMarked, episodeObservations)", self.html)


@unittest.skipUnless(shutil.which("osascript"), "osascript is required for dashboard JS adapter tests")
class DashboardEpisodeObservationBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def run_js(self, body: str):
        functions = [
            "function parseObservationTime",
            "function compareEpisodeObservations",
            "function adaptEpisodeObservation",
            "function selectEpisodeObservations",
            "function intervalOverlapsBucket",
            "function resolveEpisodeStateForBucket",
            "function applyEpisodeObservationsToBuckets",
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

    def test_sustained_episode_marks_overlapping_bucket(self):
        result = self.run_js(
            """
            const baseBucket = {
              phase: "FIBER",
              targetClass: "internet_probe",
              t: new Date("2026-06-27T07:15:00+00:00"),
              t2: new Date("2026-06-27T07:30:00+00:00"),
              total: 3,
              bad: 0,
              rawBad: 1,
              p95Bad: 1,
              jitterBad: 0,
              lossBad: 0,
              maxRawRun: 1,
              isBadBucket: false,
              isTurbulence: false
            };
            const adapted = applyEpisodeObservationsToBuckets([baseBucket], selectEpisodeObservations({
              observations: [{
                id: "observation-episode-a",
                type: "episode",
                scope: { system: "prime_observer", subject: "network", view: "episode", phase: "FIBER", target_class: "internet_probe" },
                interval: { start: "2026-06-27T07:20:00+00:00", end: "2026-06-27T07:26:00+00:00" },
                state: { status: "sustained_degradation", label: "Sustained degradation" },
                explanation: "Internet probe sustained degradation observed in this interval.",
                supporting_facts: ["1 sustained bad WAN sample(s)."]
              }]
            }));
            return adapted[0];
            """
        )
        self.assertTrue(result["isBadBucket"])
        self.assertFalse(result["isTurbulence"])
        self.assertEqual(result["semanticSource"], "observations")
        self.assertEqual(result["episodeObservation"]["status"], "sustained_degradation")
        self.assertEqual(result["evidenceLabel"], "Internet probe sustained degradation observed in this interval.")

    def test_turbulence_episode_marks_overlapping_bucket(self):
        result = self.run_js(
            """
            const baseBucket = {
              phase: "FIBER",
              targetClass: "resolver_probe",
              t: new Date("2026-06-27T07:30:00+00:00"),
              t2: new Date("2026-06-27T07:45:00+00:00"),
              total: 4,
              bad: 0,
              rawBad: 4,
              p95Bad: 4,
              jitterBad: 0,
              lossBad: 0,
              maxRawRun: 1,
              isBadBucket: false,
              isTurbulence: false
            };
            const adapted = applyEpisodeObservationsToBuckets([baseBucket], selectEpisodeObservations({
              observations: [{
                id: "observation-episode-b",
                type: "episode",
                scope: { system: "prime_observer", subject: "network", view: "episode", phase: "FIBER", target_class: "resolver_probe" },
                interval: { start: "2026-06-27T07:30:00+00:00", end: "2026-06-27T07:45:00+00:00" },
                state: { status: "turbulence", label: "Turbulence" },
                explanation: "Resolver turbulence was observed without a sustained run.",
                supporting_facts: ["4 raw bad WAN sample(s)."]
              }]
            }));
            return adapted[0];
            """
        )
        self.assertFalse(result["isBadBucket"])
        self.assertTrue(result["isTurbulence"])
        self.assertEqual(result["semanticSource"], "observations")
        self.assertEqual(result["episodeObservation"]["status"], "turbulence")

    def test_falls_back_to_browser_classification_when_projection_missing(self):
        result = self.run_js(
            """
            const baseBucket = {
              phase: "FIBER",
              targetClass: "internet_probe",
              t: new Date("2026-06-27T07:15:00+00:00"),
              t2: new Date("2026-06-27T07:30:00+00:00"),
              total: 3,
              bad: 2,
              rawBad: 3,
              p95Bad: 3,
              jitterBad: 0,
              lossBad: 0,
              maxRawRun: 2,
              isBadBucket: true,
              isTurbulence: false
            };
            const adapted = applyEpisodeObservationsToBuckets([baseBucket], []);
            return adapted[0];
            """
        )
        self.assertTrue(result["isBadBucket"])
        self.assertFalse(result["isTurbulence"])
        self.assertEqual(result["semanticSource"], "classification")
        self.assertIsNone(result["episodeObservation"])

    def test_malformed_episode_projection_is_ignored(self):
        result = self.run_js(
            """
            const baseBucket = {
              phase: "FIBER",
              targetClass: "resolver_probe",
              t: new Date("2026-06-27T07:30:00+00:00"),
              t2: new Date("2026-06-27T07:45:00+00:00"),
              total: 4,
              bad: 0,
              rawBad: 4,
              p95Bad: 4,
              jitterBad: 0,
              lossBad: 0,
              maxRawRun: 1,
              isBadBucket: false,
              isTurbulence: true
            };
            const adapted = applyEpisodeObservationsToBuckets([baseBucket], selectEpisodeObservations({
              observations: [{
                id: "observation-episode-c",
                type: "episode",
                scope: { system: "prime_observer", subject: "network", view: "episode", phase: "FIBER", target_class: "resolver_probe" },
                interval: { start: "not-a-date", end: "2026-06-27T07:45:00+00:00" },
                state: { status: "turbulence", label: "Turbulence" }
              }]
            }));
            return adapted[0];
            """
        )
        self.assertFalse(result["isBadBucket"])
        self.assertTrue(result["isTurbulence"])
        self.assertEqual(result["semanticSource"], "classification")
        self.assertIsNone(result["episodeObservation"])

    def test_interval_mapping_is_deterministic_for_overlapping_observations(self):
        result = self.run_js(
            """
            const baseBucket = {
              phase: "FIBER",
              targetClass: "internet_probe",
              t: new Date("2026-06-27T07:15:00+00:00"),
              t2: new Date("2026-06-27T07:30:00+00:00"),
              total: 3,
              bad: 0,
              rawBad: 2,
              p95Bad: 2,
              jitterBad: 0,
              lossBad: 0,
              maxRawRun: 1,
              isBadBucket: false,
              isTurbulence: false
            };
            const payload = {
              observations: [
                {
                  id: "observation-episode-z",
                  type: "episode",
                  scope: { system: "prime_observer", subject: "network", view: "episode", phase: "FIBER", target_class: "internet_probe" },
                  interval: { start: "2026-06-27T07:18:00+00:00", end: "2026-06-27T07:20:00+00:00" },
                  state: { status: "turbulence", label: "Turbulence" },
                  explanation: "Later turbulence."
                },
                {
                  id: "observation-episode-a",
                  type: "episode",
                  scope: { system: "prime_observer", subject: "network", view: "episode", phase: "FIBER", target_class: "internet_probe" },
                  interval: { start: "2026-06-27T07:17:00+00:00", end: "2026-06-27T07:19:00+00:00" },
                  state: { status: "sustained_degradation", label: "Sustained degradation" },
                  explanation: "Earlier sustained interval."
                }
              ]
            };
            const first = applyEpisodeObservationsToBuckets([baseBucket], selectEpisodeObservations(payload))[0];
            const second = applyEpisodeObservationsToBuckets([baseBucket], selectEpisodeObservations(payload))[0];
            return {
              firstStatus: first.episodeObservation.status,
              secondStatus: second.episodeObservation.status,
              firstExplanation: first.episodeObservation.explanation,
              secondExplanation: second.episodeObservation.explanation
            };
            """
        )
        self.assertEqual(result["firstStatus"], "sustained_degradation")
        self.assertEqual(result["secondStatus"], "sustained_degradation")
        self.assertEqual(result["firstExplanation"], "Earlier sustained interval.")
        self.assertEqual(result["secondExplanation"], "Earlier sustained interval.")

    def test_visual_parity_keeps_bucket_highlight_shape_when_observations_drive_state(self):
        result = self.run_js(
            """
            const baseBuckets = [
              {
                phase: "FIBER",
                targetClass: "internet_probe",
                t: new Date("2026-06-27T07:15:00+00:00"),
                t2: new Date("2026-06-27T07:30:00+00:00"),
                total: 3,
                bad: 0,
                rawBad: 1,
                p95Bad: 1,
                jitterBad: 0,
                lossBad: 0,
                maxRawRun: 1,
                isBadBucket: false,
                isTurbulence: false
              },
              {
                phase: "FIBER",
                targetClass: "internet_probe",
                t: new Date("2026-06-27T07:30:00+00:00"),
                t2: new Date("2026-06-27T07:45:00+00:00"),
                total: 3,
                bad: 0,
                rawBad: 1,
                p95Bad: 1,
                jitterBad: 0,
                lossBad: 0,
                maxRawRun: 1,
                isBadBucket: false,
                isTurbulence: false
              }
            ];
            const adapted = applyEpisodeObservationsToBuckets(baseBuckets, selectEpisodeObservations({
              observations: [{
                id: "observation-episode-parity",
                type: "episode",
                scope: { system: "prime_observer", subject: "network", view: "episode", phase: "FIBER", target_class: "internet_probe" },
                interval: { start: "2026-06-27T07:20:00+00:00", end: "2026-06-27T07:26:00+00:00" },
                state: { status: "sustained_degradation", label: "Sustained degradation" },
                explanation: "One bucket should highlight."
              }]
            }));
            return adapted.map(bucket => ({
              start: bucket.t.toISOString(),
              end: bucket.t2.toISOString(),
              isBadBucket: bucket.isBadBucket,
              isTurbulence: bucket.isTurbulence
            }));
            """
        )
        self.assertEqual(
            result,
            [
                {
                    "start": "2026-06-27T07:15:00.000Z",
                    "end": "2026-06-27T07:30:00.000Z",
                    "isBadBucket": True,
                    "isTurbulence": False,
                },
                {
                    "start": "2026-06-27T07:30:00.000Z",
                    "end": "2026-06-27T07:45:00.000Z",
                    "isBadBucket": False,
                    "isTurbulence": False,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
