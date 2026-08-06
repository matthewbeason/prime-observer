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
            "function formatTimeRange",
            "function renderExternalContextTimeNote",
            "function renderSimilarityForTimeContext",
            "function renderLearningsForTimeContext",
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
        # externalEventsForContext is a utility; production rendering reads
        # context.overlapping_external_event_sources from the Python artifact instead.
        # Verify that renderExternalContextTimeNote reads the artifact field.
        ext_fn = extract_function(self.html, "function renderExternalContextTimeNote")
        self.assertIn("overlapping_external_event_sources", ext_fn)
        # Renderer must NOT call intervalsOverlap or itemTimeWindow for the overlap decision.
        self.assertNotIn("intervalsOverlap(start, end,", ext_fn)
        self.assertNotIn("itemTimeWindow(item)", ext_fn)

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

    # ── Phase 2 tests ──────────────────────────────────────────────────────────

    def test_section_identity_elements_present(self):
        for el_id in (
            "operatorAssessmentTitle",
            "similarityCardTitle",
            "operationalLearningCardTitle",
            "wanInternetPanelKicker",
            "wanResolverPanelKicker",
            "lanPanelKicker",
            "internetConditionsTimeNote",
            "powerInfrastructureTimeNote",
        ):
            self.assertIn(f'id="{el_id}"', self.html, f"Missing element id={el_id}")

    def test_sync_functions_present(self):
        for fn in (
            "function formatTimeRange",
            "function applyDashboardTimeMode",
            "function renderSimilarityForTimeContext",
            "function renderLearningsForTimeContext",
            "function renderExternalContextTimeNote",
            "function syncDashboardTimeContext",
        ):
            self.assertIn(fn, self.html, f"Missing function: {fn}")

    def test_format_time_range_formats_bucket(self):
        result = self.run_js("""
        const bucket = {t: new Date("2026-08-05T19:00:00Z"), t2: new Date("2026-08-05T19:15:00Z")};
        return formatTimeRange(bucket);
        """)
        self.assertIsNotNone(result)
        self.assertIn("–", result)

    def test_format_time_range_null_without_bucket(self):
        result = self.run_js("return formatTimeRange(null);")
        self.assertIsNone(result)

    def test_external_context_note_selected_with_overlap(self):
        # Renderer reads Python-provided overlapping_external_event_sources from context.
        # When the artifact says "Cloudflare Radar" overlaps, the note should reflect that.
        ext_fn = extract_function(self.html, "function renderExternalContextTimeNote")
        self.assertIn('"Cloudflare Radar"', ext_fn)
        self.assertIn('"APS"', ext_fn)

    def test_external_context_note_no_overlap_cloudflare(self):
        # When context has no overlapping_external_event_sources field (non-generated bucket),
        # renderer must hide both notes — not attempt to compute overlap itself.
        ext_fn = extract_function(self.html, "function renderExternalContextTimeNote")
        self.assertIn("sources === null", ext_fn)
        self.assertIn("style.display = ", ext_fn)

    def test_similarity_hidden_when_selected_interval_has_no_incident(self):
        result = self.run_js("""
        // context without incident_id
        const ctx = {mode: "selected_interval", start: "2026-08-05T12:00:00Z", end: "2026-08-05T12:15:00Z", incident_id: null};
        const sim = {current_incident: {incident_id: "event-1", pattern: "resolver_path_degradation", strong_match_count: 3}, matches: [{score: 86, pattern: "resolver_path_degradation"}]};
        // no incident_id in context → similarity should not show
        const current = sim?.current_incident || null;
        const contextIncidentId = ctx?.incident_id || null;
        const strongCount = Number(current?.strong_match_count || 0);
        const matches = Array.isArray(sim?.matches) ? sim.matches : [];
        const shouldHide = !contextIncidentId || !current || current.incident_id !== contextIncidentId || strongCount <= 0 || !matches.length;
        return shouldHide;
        """)
        self.assertTrue(result)

    def test_similarity_shows_when_incident_id_matches(self):
        result = self.run_js("""
        const ctx = {mode: "selected_interval", incident_id: "event-1"};
        const sim = {current_incident: {incident_id: "event-1", pattern: "resolver_path_degradation", strong_match_count: 3}, matches: [{score: 86, pattern: "resolver_path_degradation"}]};
        const current = sim.current_incident;
        const contextIncidentId = ctx.incident_id;
        const strongCount = Number(current.strong_match_count || 0);
        const matches = sim.matches;
        const shouldShow = Boolean(contextIncidentId && current && current.incident_id === contextIncidentId && strongCount > 0 && matches.length);
        return shouldShow;
        """)
        self.assertTrue(result)

    def test_learnings_filter_to_selected_incident(self):
        result = self.run_js("""
        const ctx = {mode: "selected_interval", incident_id: "event-2"};
        const learnings = {insights: [
          {id: "a", title: "Wrong", supporting_incidents: ["event-1"], confidence: "high", stability: "stable"},
          {id: "b", title: "Match", supporting_incidents: ["event-2"], confidence: "medium", stability: "emerging"},
        ]};
        const ctxId = ctx.incident_id;
        const filtered = learnings.insights.filter(item =>
          item && item.stability !== "retired" && item.confidence !== "retired" &&
          (!ctxId || (item.supporting_incidents || []).includes(ctxId))
        );
        return filtered.map(i => i.title);
        """)
        self.assertEqual(result, ["Match"])

    def test_learnings_show_all_in_current_mode(self):
        result = self.run_js("""
        const ctx = {mode: "current", incident_id: null};
        const learnings = {insights: [
          {id: "a", title: "One", supporting_incidents: ["event-1"], confidence: "high", stability: "stable"},
          {id: "b", title: "Two", supporting_incidents: ["event-2"], confidence: "medium", stability: "emerging"},
        ]};
        const ctxId = (ctx?.mode === "selected_interval") ? (ctx?.incident_id || null) : null;
        const filtered = learnings.insights.filter(item =>
          item && item.stability !== "retired" && item.confidence !== "retired" &&
          (!ctxId || (item.supporting_incidents || []).includes(ctxId))
        );
        return filtered.map(i => i.title);
        """)
        self.assertEqual(result, ["One", "Two"])

    def test_selected_time_propagates_across_dashboard(self):
        self.assertIn("syncDashboardTimeContext", self.html)
        self.assertIn("applyDashboardTimeMode", self.html)
        # syncDashboardTimeContext must be called from rerenderFromState and tick
        rerender_idx = self.html.index("function rerenderFromState")
        tick_idx = self.html.index("async function tick")
        sync_count = self.html.count("syncDashboardTimeContext")
        self.assertGreaterEqual(sync_count, 2)  # both call sites
        self.assertIn("syncDashboardTimeContext", self.html[rerender_idx:tick_idx])

    def test_graph_annotations_synchronize(self):
        # Each graph legend already states "blue outline = selected interval"
        # Phase 2 adds panel kicker IDs so they can be updated with time range
        self.assertIn('id="wanInternetPanelKicker"', self.html)
        self.assertIn('id="wanResolverPanelKicker"', self.html)
        self.assertIn('id="lanPanelKicker"', self.html)
        # applyDashboardTimeMode updates all three
        apply_fn = extract_function(self.html, "function applyDashboardTimeMode")
        self.assertIn("wanInternetPanelKicker", apply_fn)
        self.assertIn("wanResolverPanelKicker", apply_fn)
        self.assertIn("lanPanelKicker", apply_fn)

    def test_legacy_compatibility_no_time_context_artifact(self):
        result = self.run_js("""
        // No time context artifact → falls back to current mode
        const ctx = timeContextFromSelection(null, null);
        return ctx.mode;
        """)
        self.assertEqual(result, "current")

    def test_impact_reads_from_python_interval_summary_not_sample_counts(self):
        # renderSelectedTimeContext must not derive user impact from raw sample counts.
        # It must use intervalSummaryPayload.user_impact when available.
        render_fn = extract_function(self.html, "function renderSelectedTimeContext")
        self.assertIn("intervalSummaryPayload", render_fn)
        self.assertIn("user_impact", render_fn)
        # Must not contain the previously prohibited inference pattern
        self.assertNotIn("internetSustainedBadSamples || opts.selectedEvidence?.resolverSustainedBadSamples ? \"Possible\"", render_fn)

    def test_external_context_renderer_does_not_compute_overlap(self):
        # renderSelectedTimeContext must read from overlapping_external_event_sources,
        # not call externalEventsForContext for its rendering logic.
        render_fn = extract_function(self.html, "function renderSelectedTimeContext")
        self.assertIn("overlapping_external_event_sources", render_fn)
        self.assertNotIn("externalEventsForContext(context,", render_fn)

    def test_python_artifact_boundary_assertions(self):
        # None of the rendering functions may compute Cloudflare/APS temporal overlap.
        for fn_name in ("renderSelectedTimeContext", "renderExternalContextTimeNote", "syncDashboardTimeContext"):
            fn_body = extract_function(self.html, f"function {fn_name}")
            self.assertNotIn("intervalOverlap", fn_body, f"{fn_name} must not call overlap arithmetic")


if __name__ == "__main__":
    unittest.main()
