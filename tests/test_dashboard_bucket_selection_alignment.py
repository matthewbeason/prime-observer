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


class DashboardBucketSelectionAlignmentStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def test_dashboard_declares_bucket_interval_domain_helpers(self):
        self.assertIn("function normalizeIntervalBoundary", self.html)
        self.assertIn("function normalizeBucketInterval", self.html)
        self.assertIn("function resolveSharedChartDomain", self.html)
        self.assertIn("const xDomain = resolveSharedChartDomain(", self.html)


@unittest.skipUnless(shutil.which("osascript"), "osascript is required for dashboard JS alignment tests")
class DashboardBucketSelectionAlignmentBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def run_js(self, body: str):
        functions = [
            "function parseObservationTime",
            "function normalizeIntervalBoundary",
            "function normalizeBucketInterval",
            "function resolveSharedChartDomain",
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
            const d3 = {{
              extent(values){{
                if (!values.length) return [undefined, undefined];
                let min = values[0];
                let max = values[0];
                for (const value of values){{
                  if (value < min) min = value;
                  if (value > max) max = value;
                }}
                return [min, max];
              }}
            }};

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

    def test_first_bucket_domain_starts_on_bucket_edge(self):
        result = self.run_js(
            """
            const domain = resolveSharedChartDomain(
              [
                [{ t: new Date("2026-06-27T00:00:41-07:00") }],
                [{ t: new Date("2026-06-27T00:02:52-07:00") }],
                []
              ],
              [{
                t: new Date("2026-06-27T00:00:00-07:00"),
                t2: new Date("2026-06-27T00:15:00-07:00")
              }]
            );
            return domain.map(item => item.toISOString());
            """
        )
        self.assertEqual(
            result,
            ["2026-06-27T07:00:00.000Z", "2026-06-27T07:15:00.000Z"],
        )

    def test_last_bucket_domain_ends_on_bucket_edge(self):
        result = self.run_js(
            """
            const domain = resolveSharedChartDomain(
              [
                [{ t: new Date("2026-06-27T08:35:12-07:00") }],
                [{ t: new Date("2026-06-27T08:37:54-07:00") }],
                [{ t: new Date("2026-06-27T08:36:01-07:00") }]
              ],
              [{
                t: new Date("2026-06-27T08:30:00-07:00"),
                t2: new Date("2026-06-27T08:45:00-07:00")
              }]
            );
            return domain.map(item => item.toISOString());
            """
        )
        self.assertEqual(
            result,
            ["2026-06-27T15:30:00.000Z", "2026-06-27T15:45:00.000Z"],
        )

    def test_middle_bucket_fraction_stays_aligned_to_bucket_grid(self):
        result = self.run_js(
            """
            const buckets = [
              { t: new Date("2026-06-27T00:00:00Z"), t2: new Date("2026-06-27T00:15:00Z") },
              { t: new Date("2026-06-27T00:15:00Z"), t2: new Date("2026-06-27T00:30:00Z") },
              { t: new Date("2026-06-27T00:30:00Z"), t2: new Date("2026-06-27T00:45:00Z") }
            ];
            const domain = resolveSharedChartDomain(
              [[
                { t: new Date("2026-06-27T00:02:00Z") },
                { t: new Date("2026-06-27T00:20:00Z") },
                { t: new Date("2026-06-27T00:41:00Z") }
              ]],
              buckets
            );
            const interval = normalizeBucketInterval(buckets[1]);
            const start = domain[0].getTime();
            const end = domain[1].getTime();
            return {
              startFraction: (interval.start.getTime() - start) / (end - start),
              endFraction: (interval.end.getTime() - start) / (end - start)
            };
            """
        )
        self.assertAlmostEqual(result["startFraction"], 1 / 3, places=6)
        self.assertAlmostEqual(result["endFraction"], 2 / 3, places=6)

    def test_daylight_saving_style_offsets_preserve_absolute_interval_length(self):
        result = self.run_js(
            """
            const interval = normalizeBucketInterval({
              t: "2026-11-01T01:45:00-07:00",
              t2: "2026-11-01T01:00:00-08:00"
            });
            return {
              start: interval.start.toISOString(),
              end: interval.end.toISOString(),
              minutes: (interval.end.getTime() - interval.start.getTime()) / 60000
            };
            """
        )
        self.assertEqual(result["start"], "2026-11-01T08:45:00.000Z")
        self.assertEqual(result["end"], "2026-11-01T09:00:00.000Z")
        self.assertEqual(result["minutes"], 15)

    def test_observation_backed_bucket_keeps_same_interval_domain(self):
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
            const observationBucket = applyEpisodeObservationsToBuckets([baseBucket], selectEpisodeObservations({
              observations: [{
                id: "observation-episode-a",
                type: "episode",
                scope: { system: "prime_observer", subject: "network", view: "episode", phase: "FIBER", target_class: "internet_probe" },
                interval: { start: "2026-06-27T07:20:00+00:00", end: "2026-06-27T07:26:00+00:00" },
                state: { status: "sustained_degradation", label: "Sustained degradation" },
                explanation: "Observation-backed interval."
              }]
            }))[0];
            const observedDomain = resolveSharedChartDomain(
              [[{ t: new Date("2026-06-27T07:22:00+00:00") }]],
              [observationBucket]
            );
            const fallbackDomain = resolveSharedChartDomain(
              [[{ t: new Date("2026-06-27T07:22:00+00:00") }]],
              [baseBucket]
            );
            return {
              observed: observedDomain.map(item => item.toISOString()),
              fallback: fallbackDomain.map(item => item.toISOString())
            };
            """
        )
        self.assertEqual(result["observed"], result["fallback"])
        self.assertEqual(
            result["observed"],
            ["2026-06-27T07:15:00.000Z", "2026-06-27T07:30:00.000Z"],
        )

    def test_legacy_fallback_bucket_interval_normalization_is_stable(self):
        result = self.run_js(
            """
            const interval = normalizeBucketInterval({
              phase: "FIBER",
              targetClass: "composite_wan",
              t: new Date("2026-06-27T07:30:00+00:00"),
              t2: new Date("2026-06-27T07:45:00+00:00"),
              semanticSource: "classification"
            });
            return {
              start: interval.start.toISOString(),
              end: interval.end.toISOString(),
              minutes: (interval.end.getTime() - interval.start.getTime()) / 60000
            };
            """
        )
        self.assertEqual(result["start"], "2026-06-27T07:30:00.000Z")
        self.assertEqual(result["end"], "2026-06-27T07:45:00.000Z")
        self.assertEqual(result["minutes"], 15)


if __name__ == "__main__":
    unittest.main()
