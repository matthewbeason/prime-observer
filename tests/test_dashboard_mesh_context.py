from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "viz" / "index.html"


def extract_function(source: str, signature: str) -> str:
    start = source.find(signature)
    if start == -1:
        raise AssertionError(f"Could not find {signature}")
    paren_start = source.find("(", start)
    depth = 0
    brace_start = None
    for index in range(paren_start, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                brace_start = source.find("{", index)
                break
    if brace_start is None:
        raise AssertionError(f"Could not find opening brace for {signature}")
    depth = 0
    for index in range(brace_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Could not find closing brace for {signature}")


class DashboardMeshContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_current_inventory_is_removed_from_historical_lan_panel(self):
        lan_panel = self.html[self.html.index('id="lanPanelKicker"'):self.html.index('id="lan"')]
        self.assertNotIn('id="meshContext"', lan_panel)
        self.assertNotIn("Probe attachment", lan_panel)
        self.assertNotIn("Associations", lan_panel)
        self.assertNotIn("renderMeshContext", self.html)
        self.assertNotIn("meshLanTooltipHtml", self.html)
        self.assertNotIn("meshContextPayload?.lan_evidence", self.html)

    def test_dashboard_reads_only_prime_mesh_history_projection(self):
        self.assertIn('const MESH_CONTEXT_URL = "./mesh_context.json";', self.html)
        self.assertIn("loadOptionalJson(MESH_CONTEXT_URL)", self.html)
        self.assertIn("meshContextPayload?.history_evidence", self.html)
        self.assertNotIn("mesh_signal.json", self.html)
        self.assertNotIn("MESH_SIGNAL_ARTIFACT_PATH", self.html)
        self.assertNotIn("SOAP", self.html)

    def test_vertical_mesh_event_lines_are_removed(self):
        self.assertNotIn("mesh-change-line", self.html)
        self.assertNotIn('stroke-dasharray", d => d.lineage_boundary', self.html)
        self.assertIn('.attr("class", "mesh-change-hit")', self.html)
        self.assertIn('.attr("cy", d => y(d.p95))', self.html)

    def test_mesh_marker_style_is_provider_identity_only(self):
        render_line = extract_function(self.html, "function renderLine")
        marker_start = render_line.index('markerLayer.selectAll(".mesh-change-hit")')
        marker_block = render_line[marker_start:]
        self.assertIn('.attr("fill", "rgba(111, 78, 155, 0.92)")', marker_block)
        self.assertNotIn("category_counts", marker_block)
        self.assertNotIn("severity", marker_block)
        self.assertNotIn("isBad", marker_block)

    def test_tooltip_is_clamped_inside_mobile_viewport(self):
        tip_show = extract_function(self.html, "function tipShow")
        self.assertIn("Math.max(pad, Math.min(left, window.innerWidth - w - pad))", tip_show)
        self.assertIn("Math.max(pad, Math.min(top, window.innerHeight - h - pad))", tip_show)

    def test_lan_scale_and_ordinary_tooltip_semantics_remain_intact(self):
        self.assertIn("const lanYDomain = latencyDomainForSeries(currentVizState.lanSeries);", self.html)
        self.assertNotIn("meshSeries", self.html)
        self.assertNotIn("meshYDomain", self.html)
        render_line = extract_function(self.html, "function renderLine")
        self.assertIn("selectedBucketEvidenceHtml(opts.selectedEvidence)", render_line)
        self.assertIn("Isolated operator-bad sample.", render_line)
        self.assertIn("jitter ${fmt1(d.jitter)} ms • loss ${fmt1(d.loss)}%", render_line)

    def test_selected_interval_uses_historical_change_wording_and_stays_quiet(self):
        update_panel = extract_function(self.html, "function updateSelectedBucketPanel")
        period_text = extract_function(self.html, "function meshPeriodText")
        self.assertIn('id="selectedBucketMeshChip" hidden', self.html)
        self.assertIn("meshChip.hidden = !meshContext || !hasMeaningfulMeshContext", update_panel)
        self.assertIn("meshContext.before", update_panel)
        self.assertIn("meshContext.during", update_panel)
        self.assertIn("meshContext.after", update_panel)
        self.assertIn('period.changes.join(" · ")', period_text)
        self.assertIn("Time adjacency only; no causal claim.", update_panel)

    def test_client_and_node_identifiers_do_not_enter_dashboard(self):
        for forbidden in ("client_id", "node_id", "friendly_name", "local_ip_addresses", "mac_address"):
            self.assertNotIn(forbidden, self.html)

    def test_no_topology_or_new_semantic_consumers_are_added(self):
        self.assertNotIn('id="meshTopology"', self.html)
        self.assertNotIn("renderMeshTopology", self.html)
        self.assertNotIn("mesh_context.json", (ROOT / "viz" / "investigate.html").read_text(encoding="utf-8"))
        self.assertNotIn("mesh_context", (ROOT / "bin" / "build_investigation.py").read_text(encoding="utf-8"))
        self.assertNotIn("mesh_context", (ROOT / "bin" / "build_operator_assistant_input.py").read_text(encoding="utf-8"))
        self.assertNotIn("mesh_context", (ROOT / "bin" / "health_dimensions.py").read_text(encoding="utf-8"))


class DashboardMeshMarkerMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.marker_function = extract_function(cls.html, "function meshMarkersForLanSeries")
        cls.tooltip_function = extract_function(cls.html, "function meshChangeTooltipHtml")

    def run_markers(self, history, observations, tolerance_ms=120_000):
        script = textwrap.dedent(
            f"""
            const MESH_LAN_MATCH_TOLERANCE_MS = 2 * 60 * 1000;
            function parseTs(value) {{
              if (!value) return null;
              const parsed = new Date(value);
              return Number.isFinite(parsed.getTime()) ? parsed : null;
            }}
            {self.marker_function}
            const history = {json.dumps(history)};
            const observations = {json.dumps(observations)}.map(item => ({{...item, t: new Date(item.t)}}));
            const result = meshMarkersForLanSeries(history, observations, {tolerance_ms});
            console.log(JSON.stringify(result.map(item => ({{
              t: item.t.toISOString(), p95: item.p95, jitter: item.jitter,
              event_times: item.event_times.map(value => value.toISOString()),
              event_count: item.event_count, changes: item.changes,
              category_counts: item.category_counts
            }}))));
            """
        )
        completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, text=True, capture_output=True)
        return json.loads(completed.stdout)

    @staticmethod
    def point(observed_at, count=1, change="Client attachment changed", category="client"):
        return {
            "observed_at": observed_at,
            "event_count": count,
            "changes": [change],
            "category_counts": {category: count},
            "lineage_boundary": False,
        }

    def test_marker_uses_nearest_real_lan_observation_without_interpolation(self):
        history = {"state": "available", "change_points": [self.point("2026-08-25T16:00:50Z")]}
        observations = [
            {"t": "2026-08-25T16:00:00Z", "p95": 17.0, "jitter": 2.0},
            {"t": "2026-08-25T16:01:00Z", "p95": 137.0, "jitter": 9.0},
        ]
        result = self.run_markers(history, observations)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["t"], "2026-08-25T16:01:00.000Z")
        self.assertEqual(result[0]["p95"], 137.0)
        self.assertEqual(result[0]["jitter"], 9.0)
        self.assertEqual(result[0]["event_times"], ["2026-08-25T16:00:50.000Z"])

    def test_event_without_nearby_lan_observation_creates_no_marker(self):
        history = {"state": "available", "change_points": [self.point("2026-08-25T16:05:00Z")]}
        observations = [{"t": "2026-08-25T16:00:00Z", "p95": 17.0, "jitter": 2.0}]
        self.assertEqual(self.run_markers(history, observations), [])

    def test_multiple_events_on_one_observation_are_grouped(self):
        history = {
            "state": "available",
            "change_points": [
                self.point("2026-08-25T16:00:40Z", 2, "Client presence changed", "client"),
                self.point("2026-08-25T16:01:10Z", 1, "Satellite state changed", "satellite"),
            ],
        }
        observations = [{"t": "2026-08-25T16:01:00Z", "p95": 55.0, "jitter": 4.0}]
        result = self.run_markers(history, observations)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["event_count"], 3)
        self.assertEqual(result[0]["category_counts"], {"client": 2, "satellite": 1})
        self.assertEqual(result[0]["changes"], ["Client presence changed", "Satellite state changed"])

    def test_unavailable_or_missing_history_fails_safely(self):
        observations = [{"t": "2026-08-25T16:01:00Z", "p95": 55.0}]
        self.assertEqual(self.run_markers({"state": "missing"}, observations), [])
        self.assertEqual(self.run_markers(None, observations), [])

    def test_marker_tooltip_combines_lan_measurement_and_mesh_context(self):
        script = textwrap.dedent(
            f"""
            function escapeTooltipText(value) {{ return String(value ?? ""); }}
            function fmt1(value) {{ return Number(value).toFixed(1); }}
            {self.tooltip_function}
            const html = meshChangeTooltipHtml({{
              t: new Date("2026-08-25T16:01:00Z"), p95: 137,
              event_times: [new Date("2026-08-25T16:00:50Z")], event_count: 2,
              changes: ["Client presence changed", "Association changed"], lineage_boundary: false
            }});
            console.log(html);
            """
        )
        completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, text=True, capture_output=True)
        tooltip = completed.stdout
        self.assertIn("Gateway p95: 137.0 ms", tooltip)
        self.assertIn("Mesh changes:</span> 2", tooltip)
        self.assertIn("Client presence changed · Association changed", tooltip)
        self.assertIn("LAN observation", tooltip)
        self.assertIn("Temporal alignment does not establish cause.", tooltip)


if __name__ == "__main__":
    unittest.main()
