from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
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

    def test_mesh_context_is_compact_and_located_inside_lan_panel(self):
        lan_panel = self.html.index('id="lanPanelKicker"')
        context = self.html.index('id="meshContext"')
        lan_chart = self.html.index('id="lan"')
        self.assertLess(lan_panel, context)
        self.assertLess(context, lan_chart)
        for element_id in (
            "meshContextState",
            "meshContextSummary",
            "meshContextProbeHost",
            "meshContextNodes",
            "meshContextClients",
            "meshContextMedium",
            "meshContextAssociations",
            "meshContextSatellites",
            "meshContextBands",
            "meshContextLineage",
        ):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_dashboard_reads_only_prime_mesh_projection(self):
        self.assertIn('const MESH_CONTEXT_URL = "./mesh_context.json";', self.html)
        self.assertIn("loadOptionalJson(MESH_CONTEXT_URL)", self.html)
        self.assertIn("meshContextPayload?.lan_evidence", self.html)
        self.assertNotIn("mesh_signal.json", self.html)
        self.assertNotIn("MESH_SIGNAL_ARTIFACT_PATH", self.html)
        self.assertNotIn("SOAP", self.html)

    def test_mesh_statuses_and_last_good_lineage_remain_explicit(self):
        self.assertIn('state === "current" ? "ok"', self.html)
        self.assertIn('["partial", "stale"].includes(state)', self.html)
        self.assertIn('evidence.lineage === "last_good"', self.html)
        self.assertIn("last known good", self.html)
        self.assertIn("LAN telemetry remains available", self.html)
        self.assertIn("reported offline", self.html)

    def test_lan_tooltip_adds_snapshot_context_without_changing_chart_scale(self):
        self.assertIn('if (svgSel === "#lan") extra += meshLanTooltipHtml(opts.meshEvidence);', self.html)
        self.assertIn("Snapshot context only; relative signal is vendor-defined", self.html)
        self.assertIn("const lanYDomain = latencyDomainForSeries(currentVizState.lanSeries);", self.html)
        self.assertNotIn("meshSeries", self.html)
        self.assertNotIn("meshYDomain", self.html)

    def test_client_and_node_identifiers_do_not_enter_dashboard(self):
        self.assertNotIn("client_id", self.html)
        self.assertNotIn("node_id", self.html)
        self.assertNotIn("friendly_name", self.html)
        self.assertIn("Probe signal is a raw relative vendor metric", self.html)
        self.assertNotIn("local_ip_addresses", self.html)
        self.assertNotIn("mac_address", self.html)

    def test_no_topology_or_new_semantic_consumers_are_added(self):
        self.assertNotIn('id="meshTopology"', self.html)
        self.assertNotIn("renderMeshTopology", self.html)
        self.assertNotIn("mesh_context.json", (ROOT / "viz" / "investigate.html").read_text(encoding="utf-8"))
        self.assertNotIn("mesh_context", (ROOT / "bin" / "build_investigation.py").read_text(encoding="utf-8"))
        self.assertNotIn("mesh_context", (ROOT / "bin" / "build_operator_assistant_input.py").read_text(encoding="utf-8"))
        self.assertNotIn("mesh_context", (ROOT / "bin" / "health_dimensions.py").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("osascript"), "osascript is required for mesh renderer tests")
class DashboardMeshContextRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.functions = "\n\n".join(
            extract_function(cls.html, signature)
            for signature in (
                "function meshLineageLabel",
                "function meshCount",
                "function meshBandLabel",
                "function meshProbeHostText",
                "function renderMeshContext",
            )
        )

    def render(self, evidence):
        script = textwrap.dedent(
            f"""
            {self.functions}
            const ids = [
              "meshContextState", "meshContextSummary", "meshContextProbeHost", "meshContextNodes",
              "meshContextClients", "meshContextMedium", "meshContextAssociations",
              "meshContextSatellites", "meshContextBands", "meshContextLineage",
              "meshContextQualityNote"
            ];
            const nodes = Object.fromEntries(ids.map(id => [id, {{ textContent: "", className: "" }}]));
            const document = {{ getElementById: id => nodes[id] }};
            function parseTs(value) {{ return value ? new Date(value) : null; }}
            function toCompactRelativeAge(_value) {{ return "age labeled"; }}
            function applyPillState(element, label, tone) {{ element.textContent = label; element.className = tone; }}
            renderMeshContext({json.dumps({'lan_evidence': evidence})});
            console.log(JSON.stringify(Object.fromEntries(ids.map(id => [id, {{ text: nodes[id].textContent, className: nodes[id].className }}]))));
            """
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
            path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["osascript", "-l", "JavaScript", str(path)],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )
        finally:
            path.unlink(missing_ok=True)
        output = (completed.stdout or completed.stderr).strip()
        return json.loads(output.splitlines()[-1])

    @staticmethod
    def evidence(state, label, *, client_lineage="latest_attempt", clients=2):
        freshness = {"state": "fresh", "age_seconds": 60}
        return {
            "state": state,
            "label": label,
            "summary": f"{label} summary",
            "nodes": {"total_reported": 2 if clients is not None else None},
            "router": {"lineage": "latest_attempt", "observed_at": "2026-08-23T20:00:00Z", "freshness": freshness},
            "satellites": {
                "lineage": "latest_attempt",
                "collection_status": "complete",
                "observed_at": "2026-08-23T20:00:00Z",
                "freshness": freshness,
                "total": 1 if clients is not None else None,
                "by_state": {"online": 1, "offline": 0, "unknown": 0} if clients is not None else {},
            },
            "clients": {
                "lineage": client_lineage,
                "collection_status": "complete",
                "observed_at": "2026-08-23T20:00:00Z" if clients is not None else None,
                "freshness": freshness,
                "total": clients,
                "by_medium": {"wired": 1, "wireless": 1} if clients is not None else {},
                "by_band": {"5_ghz": 1, "unknown": 1} if clients is not None else {},
                "resolved_associations": clients,
                "not_resolved_associations": 0 if clients is not None else None,
            },
            "probe_host": {
                "mapping_state": "matched" if clients is not None else "client_family_unavailable",
                "lineage": client_lineage,
                "collection_status": "complete",
                "observed_at": "2026-08-23T20:00:00Z" if clients is not None else None,
                "freshness": freshness,
                "attachment": {
                    "node_role": "satellite",
                    "node_name_local": "Office Satellite",
                    "medium": "wireless",
                    "band": "5_ghz",
                    "association_resolution": "resolved",
                    "signal_quality_raw_relative": 48,
                    "link_rate_mbps_apparent": 433,
                } if clients is not None else None,
            },
        }

    def test_fresh_complete_mesh_is_current_and_compact(self):
        rendered = self.render(self.evidence("current", "Mesh evidence current"))
        self.assertEqual(rendered["meshContextState"], {"text": "Mesh evidence current", "className": "ok"})
        self.assertEqual(rendered["meshContextClients"]["text"], "2 attached")
        self.assertEqual(rendered["meshContextAssociations"]["text"], "2/2 resolved")
        self.assertEqual(
            rendered["meshContextProbeHost"]["text"],
            "Office Satellite · wireless · 5 GHz · relative signal 48 · apparent link 433 Mbps",
        )

    def test_partial_last_good_lineage_is_explicit(self):
        rendered = self.render(
            self.evidence("partial", "Mesh evidence partial", client_lineage="last_good")
        )
        self.assertEqual(rendered["meshContextState"]["className"], "watch")
        self.assertIn("clients last known good", rendered["meshContextLineage"]["text"])
        self.assertTrue(rendered["meshContextProbeHost"]["text"].startswith("Last known:"))

    def test_stale_mesh_is_age_labeled(self):
        evidence = self.evidence("stale", "Mesh evidence stale")
        evidence["probe_host"]["freshness"] = {"state": "stale", "age_seconds": 780}
        rendered = self.render(evidence)
        self.assertEqual(rendered["meshContextState"], {"text": "Mesh evidence stale", "className": "watch"})
        self.assertIn("age labeled", rendered["meshContextLineage"]["text"])
        self.assertTrue(rendered["meshContextProbeHost"]["text"].startswith("Stale:"))

    def test_unavailable_mesh_does_not_look_like_network_failure(self):
        rendered = self.render(self.evidence("unavailable", "Mesh evidence unavailable", clients=None))
        self.assertEqual(rendered["meshContextState"], {"text": "Mesh evidence unavailable", "className": "neutral"})
        self.assertEqual(rendered["meshContextClients"]["text"], "—")
        self.assertIn("Unavailable", rendered["meshContextProbeHost"]["text"])


if __name__ == "__main__":
    unittest.main()
