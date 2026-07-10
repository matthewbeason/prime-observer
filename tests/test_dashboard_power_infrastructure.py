import unittest
import json
import shutil
import subprocess
import tempfile
import textwrap
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


class DashboardPowerInfrastructureTest(unittest.TestCase):
    def test_dashboard_consumes_generated_power_context_only(self):
        html = INDEX_HTML.read_text()

        self.assertIn('const APS_POWER_CONTEXT_URL = "./aps_power_context.json";', html)
        self.assertIn('fetch(APS_POWER_CONTEXT_URL, { cache: "no-store" })', html)
        self.assertIn("Power Infrastructure", html)
        self.assertIn('id="powerInfrastructureCard"', html)
        self.assertIn('id="powerInfrastructureScope"', html)
        self.assertIn('id="powerInfrastructureCustomers"', html)
        self.assertIn('id="powerInfrastructureEvents"', html)
        self.assertIn('id="powerInfrastructureNearest"', html)
        self.assertIn('id="powerInfrastructureDisclosureSummary"', html)
        self.assertIn('id="powerInfrastructureItemsWrap"', html)
        self.assertIn('id="powerInfrastructureItems"', html)
        self.assertIn('id="mobilePowerInfrastructureCard"', html)
        self.assertIn('id="mobilePowerInfrastructureScope"', html)
        self.assertIn('id="mobilePowerInfrastructureCustomers"', html)
        self.assertIn('id="mobilePowerInfrastructureEvents"', html)
        self.assertIn('id="mobilePowerInfrastructureNearest"', html)
        self.assertIn('id="mobilePowerInfrastructureDisclosure"', html)
        self.assertIn('id="mobilePowerInfrastructureDisclosureSummary"', html)
        self.assertIn('id="mobilePowerInfrastructureItemsWrap"', html)
        self.assertIn('id="mobilePowerInfrastructureItems"', html)
        self.assertNotIn("aps-ags.esriemcs.com", html)
        self.assertNotIn("outagemap.aps.com/outageviewer/mockData", html)
        self.assertIn('if (!data || data.provider !== "aps")', html)

    def test_dashboard_hides_power_card_when_fetch_fails(self):
        html = INDEX_HTML.read_text()

        self.assertIn('style="min-width:240px; display:none;" id="powerInfrastructureCard"', html)
        self.assertIn('id="mobilePowerInfrastructureCard"', html)
        self.assertIn('style="display:none;"', html)
        self.assertIn('document.getElementById("powerInfrastructureCard").style.display = "none";', html)
        self.assertIn('document.getElementById("mobilePowerInfrastructureCard").style.display = "none";', html)
        self.assertIn("setPowerInfrastructureHidden();", html)

    def test_dashboard_marks_unavailable_as_compact_visible_state(self):
        html = INDEX_HTML.read_text()

        self.assertIn('else if (status === "unavailable")', html)
        self.assertIn('label = "Unavailable";', html)
        self.assertIn('customerValue = "—";', html)
        self.assertIn('eventValue = "—";', html)
        self.assertIn('nearestValue = "—";', html)
        self.assertIn('syncPowerInfrastructureDisclosureState(status);', html)

    def test_investigation_view_renders_power_context_without_direct_provider_fetch(self):
        html = (ROOT / "viz" / "investigate.html").read_text()

        self.assertNotIn("./aps_power_context.json", html)
        self.assertIn('id="powerInfrastructureSection"', html)
        self.assertIn("data.power_infrastructure_context", html)
        self.assertIn('document.getElementById("powerInfrastructureContext")', html)
        self.assertIn('section.style.display = "none";', html)
        self.assertIn('section.style.display = "block";', html)

    def test_dashboard_uses_contextual_power_disclosure_copy(self):
        html = INDEX_HTML.read_text()

        self.assertIn("View events >", html)
        self.assertIn("Hide events", html)
        self.assertIn("function powerDisclosureClosedLabel(itemsCount)", html)
        self.assertIn("function powerDisclosureOpenLabel(itemsCount)", html)
        self.assertIn("syncPowerDisclosureLabels(items.length);", html)


@unittest.skipUnless(shutil.which("osascript"), "osascript is required for dashboard Power Infrastructure disclosure tests")
class DashboardPowerInfrastructureDisclosureBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    def run_js(self, body: str):
        functions = [
            "function isActivePowerInfrastructureStatus",
            "function powerDisclosureClosedLabel",
            "function powerDisclosureOpenLabel",
            "function setDisclosureSummary",
            "function syncPowerInfrastructureDisclosureState",
            "function syncPowerDisclosureLabels",
        ]
        snippets = "\n\n".join(extract_function(self.html, signature) for signature in functions)
        script = textwrap.dedent(
            f"""
            {snippets}
            var powerInfrastructureDisclosureState = {{ lastStatus: null, itemsCount: 0 }};
            var nodes = {{
              powerInfrastructureDisclosure: {{ open: false }},
              mobilePowerInfrastructureDisclosure: {{ open: false }},
              powerInfrastructureDisclosureSummary: {{ textContent: "" }},
              mobilePowerInfrastructureDisclosureSummary: {{ textContent: "" }}
            }};
            var document = {{
              getElementById: function(id) {{
                return nodes[id] || null;
              }}
            }};
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

    def test_normal_and_unavailable_states_remain_compact(self):
        result = self.run_js(
            """
            syncPowerInfrastructureDisclosureState("normal");
            const afterNormal = {
              desktop: nodes.powerInfrastructureDisclosure.open,
              mobile: nodes.mobilePowerInfrastructureDisclosure.open
            };
            syncPowerInfrastructureDisclosureState("unavailable");
            return {
              afterNormal,
              afterUnavailable: {
                desktop: nodes.powerInfrastructureDisclosure.open,
                mobile: nodes.mobilePowerInfrastructureDisclosure.open
              }
            };
            """
        )
        self.assertFalse(result["afterNormal"]["desktop"])
        self.assertFalse(result["afterNormal"]["mobile"])
        self.assertFalse(result["afterUnavailable"]["desktop"])
        self.assertFalse(result["afterUnavailable"]["mobile"])

    def test_events_reported_auto_expands_and_manual_collapse_persists(self):
        result = self.run_js(
            """
            syncPowerInfrastructureDisclosureState("events_reported");
            syncPowerDisclosureLabels(3);
            const autoExpanded = {
              desktop: nodes.powerInfrastructureDisclosure.open,
              mobile: nodes.mobilePowerInfrastructureDisclosure.open,
              desktopLabel: nodes.powerInfrastructureDisclosureSummary.textContent,
              mobileLabel: nodes.mobilePowerInfrastructureDisclosureSummary.textContent
            };
            nodes.powerInfrastructureDisclosure.open = false;
            nodes.mobilePowerInfrastructureDisclosure.open = false;
            syncPowerDisclosureLabels();
            syncPowerInfrastructureDisclosureState("events_reported");
            const afterManualCollapse = {
              desktop: nodes.powerInfrastructureDisclosure.open,
              mobile: nodes.mobilePowerInfrastructureDisclosure.open,
              desktopLabel: nodes.powerInfrastructureDisclosureSummary.textContent,
              mobileLabel: nodes.mobilePowerInfrastructureDisclosureSummary.textContent
            };
            syncPowerInfrastructureDisclosureState("normal");
            syncPowerDisclosureLabels();
            const afterReturnToNormal = {
              desktop: nodes.powerInfrastructureDisclosure.open,
              mobile: nodes.mobilePowerInfrastructureDisclosure.open,
              desktopLabel: nodes.powerInfrastructureDisclosureSummary.textContent,
              mobileLabel: nodes.mobilePowerInfrastructureDisclosureSummary.textContent
            };
            return { autoExpanded, afterManualCollapse, afterReturnToNormal };
            """
        )
        self.assertTrue(result["autoExpanded"]["desktop"])
        self.assertTrue(result["autoExpanded"]["mobile"])
        self.assertEqual(result["autoExpanded"]["desktopLabel"], "Hide 3 events")
        self.assertEqual(result["autoExpanded"]["mobileLabel"], "Hide 3 events")
        self.assertFalse(result["afterManualCollapse"]["desktop"])
        self.assertFalse(result["afterManualCollapse"]["mobile"])
        self.assertEqual(result["afterManualCollapse"]["desktopLabel"], "View 3 events >")
        self.assertEqual(result["afterManualCollapse"]["mobileLabel"], "View 3 events >")
        self.assertFalse(result["afterReturnToNormal"]["desktop"])
        self.assertFalse(result["afterReturnToNormal"]["mobile"])
        self.assertEqual(result["afterReturnToNormal"]["desktopLabel"], "View 3 events >")
        self.assertEqual(result["afterReturnToNormal"]["mobileLabel"], "View 3 events >")


if __name__ == "__main__":
    unittest.main()
