import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "viz" / "investigate.html"


def page_script():
    html = HTML_PATH.read_text()
    start = html.index("<script>") + len("<script>")
    end = html.index("</script>", start)
    return html[start:end].replace("main().catch(showInvestigationLoadError);", "")


class InvestigationOperatorAssistantContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = page_script()

    def run_node(self, body):
        script = f"""
globalThis.window = {{
  location: {{protocol: "http:", search: "", pathname: "/investigate.html"}},
  addEventListener() {{}},
}};
globalThis.history = {{pushState() {{}}}};
const elements = new Map();
function makeElement() {{
  return {{
    innerHTML: "",
    textContent: "",
    dataset: {{}},
    style: {{}},
    attributes: {{}},
    focus() {{}},
    setAttribute(name, value) {{ this.attributes[name] = value; }},
    classList: {{
      values: new Set(),
      add(value) {{ this.values.add(value); }},
      remove(value) {{ this.values.delete(value); }},
      contains(value) {{ return this.values.has(value); }},
    }},
    addEventListener() {{}},
  }};
}}
globalThis.document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  }},
  querySelectorAll() {{ return []; }},
}};
globalThis.fetch = async () => ({{ok: false, status: 404, json: async () => ({{}})}});
{self.script}
{body}
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            self.fail(f"Node script failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return result.stdout.strip()

    def investigation_payload(self):
        return {
            "schema_version": 2,
            "generated_at": "2026-07-20T03:24:41Z",
            "id": "investigation-1",
            "title": "Automatic current-event investigation",
            "status": "available",
            "artifact_state": {"label": "Recovery in progress", "is_current": True, "is_stale": False},
            "freshness": {
                "telemetry_latest_at": "2026-07-20T03:23:58Z",
                "evidence_latest_at": "2026-07-20T03:23:16Z",
            },
            "selected_event": {
                "id": "event-1",
                "target_class": "resolver_probe",
                "lifecycle_state": "recovering",
                "affected_targets": ["45.90.30.134"],
            },
            "operator_brief": {
                "headline": "Resolver probe degradation is recovering.",
                "summary": "Resolver probes degraded while comparison groups stayed healthier.",
                "likely_fault_domain": "Likely upstream (ISP / path)",
                "affected_scope": "Resolver probes degraded.",
                "unaffected_scope": "Internet probes and gateway were comparison groups.",
                "confidence": "high",
                "operational_state": {"state": "recovering", "label": "Signals are healthy again.", "recommendation": "Continue observation."},
                "recommended_actions": [
                    {
                        "action": "Continue observation through the recovery window.",
                        "reason": "Recovery is not complete.",
                        "expected_observation": "Healthy samples continue.",
                        "assessment_change": "A renewed anomaly reopens active degradation.",
                    }
                ],
                "supporting_evidence": ["Resolver probes showed sustained degradation."],
                "limiting_evidence": ["Cause is inferred, not proven."],
                "conditions_that_change_assessment": ["Gateway degradation appears."],
                "monitoring_guidance": "Watch resolver and comparison probes.",
            },
            "scope_impact": {
                "scope_conclusion": "Resolver probes degraded while internet probes stayed below sustained thresholds.",
                "affected_probe_label": "Resolver probes",
                "affected_endpoints": ["45.90.30.134"],
                "anomalous_samples": 8,
                "sustained_bad_samples": 2,
                "affected_evidence_buckets": 1,
                "representative_latency_ms": 176,
                "maximum_excursion_ms": 320,
                "packet_loss_pct": 0,
                "current_recovery_state": "recovering",
                "unaffected_comparison_groups": [{"target_class": "internet_probe", "sample_count": 30, "raw_bad_count": 0, "sustained_bad_count": 0}],
            },
            "recovery_progress": {"available": True, "healthy_observation_seconds": 480, "required_stable_seconds": 900, "remaining_stable_seconds": 420, "healthy_samples_since_last_anomaly": 5},
            "episode_summary": {"total_observations_consolidated": 2, "sustained_episodes": 1, "isolated_excursions": 1, "summary": "Episodes consolidated."},
            "evidence_argument": {
                "supporting_evidence": ["Resolver probes showed sustained degradation."],
                "limiting_evidence": ["Cause is inferred, not proven."],
                "evidence_against_broader_impact": ["Internet probes remained below sustained thresholds."],
                "verification_steps": ["Compare resolver and internet probes."],
            },
            "evidence_buckets": {"total_buckets": 4, "stable_buckets": 2, "sustained_degradation_buckets": 1, "isolated_excursion_buckets": 1, "recovery_buckets": 1, "affected_time_range": {}},
            "timeline": [],
            "periods": {},
            "events": [],
            "event_neighborhoods": [],
            "timeline_samples": [],
            "sources": {"telemetry_files": []},
            "thresholds": {},
            "observation_references": [],
            "dns_context": {"available": False, "status": "unavailable"},
        }

    def test_deterministic_fallback_is_visible_without_failure_message(self):
        body = f"""
renderAssistantReview(null, null, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{
  visible: document.getElementById("assistantReviewSection").classList.contains("visible"),
  headline: document.getElementById("assistantReviewHeadline").textContent,
  assessment: document.getElementById("assistantReviewAssessment").textContent,
  pills: document.getElementById("assistantReviewPills").innerHTML,
}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertTrue(rendered["visible"])
        self.assertIn("Resolver probe", rendered["headline"])
        self.assertNotIn("unavailable", rendered["assessment"].lower())
        self.assertNotIn("failed", rendered["assessment"].lower())
        self.assertIn("Deterministic fallback", rendered["pills"])

    def test_matching_llm_assessment_is_primary(self):
        review = {
            "status": "ok",
            "input_hash": "a" * 64,
            "headline": "LLM headline",
            "assessment": "LLM operator assessment",
            "likely_fault_domain": "Most consistent with resolver path.",
            "affected_scope": "Resolver probes",
            "healthy_scope": "Gateway",
            "confidence": "medium",
            "uncertainty": "Cause not proven.",
            "next_steps": [],
            "limitations": [],
            "requested_model": "google/gemini-3.5-flash",
        }
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{
  headline: document.getElementById("assistantReviewHeadline").textContent,
  assessment: document.getElementById("assistantReviewAssessment").textContent,
  pills: document.getElementById("assistantReviewPills").innerHTML,
}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["headline"], "LLM headline")
        self.assertEqual(rendered["assessment"], "LLM operator assessment")
        self.assertIn("LLM interpretation", rendered["pills"])

    def test_stale_llm_output_falls_back_without_exposing_stale_error(self):
        review = {"status": "ok", "input_hash": "a" * 64, "headline": "Old", "assessment": "Old analysis"}
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'b' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{assessment: document.getElementById("assistantReviewAssessment").textContent, pills: document.getElementById("assistantReviewPills").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertNotIn("Old analysis", rendered["assessment"])
        self.assertNotIn("does not match", rendered["assessment"])
        self.assertIn("Deterministic fallback", rendered["pills"])

    def test_malformed_matching_llm_output_falls_back_to_deterministic(self):
        review = {"status": "ok", "input_hash": "a" * 64, "headline": "", "assessment": ""}
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{headline: document.getElementById("assistantReviewHeadline").textContent, pills: document.getElementById("assistantReviewPills").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("Resolver probe", rendered["headline"])
        self.assertIn("Deterministic fallback", rendered["pills"])

    def test_pending_generation_keeps_safe_deterministic_content_visible(self):
        body = f"""
renderAssistantReview(null, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{assessment: document.getElementById("assistantReviewAssessment").textContent, nextSteps: document.getElementById("assistantReviewNextSteps").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("Resolver probes", rendered["assessment"])
        self.assertIn("Continue observation", rendered["nextSteps"])
        self.assertNotIn("generation", rendered["assessment"].lower())
        self.assertNotIn("pending", rendered["assessment"].lower())

    def test_next_step_ids_are_not_rendered_to_operator(self):
        review = {
            "status": "ok",
            "input_hash": "a" * 64,
            "headline": "LLM headline",
            "assessment": "LLM operator assessment",
            "confidence": "medium",
            "next_steps": [{"id": "COMPARE_RESOLVER_AND_INTERNET", "label": "Compare resolver and internet", "reason": "Confirm scope.", "expected_observation": "Resolver improves.", "assessment_change": "Broaden if internet degrades."}],
        }
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{nextSteps: document.getElementById("assistantReviewNextSteps").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("Compare resolver and internet", rendered["nextSteps"])
        self.assertNotIn("COMPARE_RESOLVER_AND_INTERNET", rendered["nextSteps"])

    def test_material_limitations_are_secondary_disclosures(self):
        review = {
            "status": "ok",
            "input_hash": "a" * 64,
            "headline": "LLM headline",
            "assessment": "LLM operator assessment",
            "confidence": "medium",
            "limitations": ["No after-window telemetry samples were available."],
            "next_steps": [],
        }
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{main: document.getElementById("assistantReviewAssessment").textContent, limitations: document.getElementById("assistantReviewLimitations").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertNotIn("No after-window", rendered["main"])
        self.assertIn("No after-window", rendered["limitations"])

    def test_current_and_historical_loading_statuses_are_operational(self):
        current = self.investigation_payload()
        historical = json.loads(json.dumps(current))
        historical["artifact_state"] = {"label": "Historical investigation", "is_historical": True}
        body = f"""
globalThis.fetch = async (url) => {{
  if (url === INVESTIGATION_URL) return {{ok: true, json: async () => ({json.dumps(current)})}};
  if (url === "./investigations/event-history.json") return {{ok: true, json: async () => ({json.dumps(historical)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await loadInvestigation(INVESTIGATION_URL, "investigation.json", true);
  const currentStatus = document.getElementById("status").textContent;
  await loadInvestigation("./investigations/event-history.json", "investigations/event-history.json", false);
  console.log(JSON.stringify({{currentStatus, historicalStatus: document.getElementById("status").textContent, mode: document.getElementById("modePill").textContent}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("current investigation", rendered["currentStatus"])
        self.assertIn("Historical Investigation", rendered["historicalStatus"])
        self.assertEqual(rendered["mode"], "Historical Investigation")

    def test_missing_or_malformed_catalog_keeps_current_investigation_usable(self):
        current = self.investigation_payload()
        body = f"""
globalThis.fetch = async (url) => {{
  if (url === INVESTIGATION_CATALOG_URL) return {{ok: false, status: 404, json: async () => ({{}})}};
  if (url === INVESTIGATION_URL) return {{ok: true, json: async () => ({json.dumps(current)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await main();
  console.log(JSON.stringify({{status: document.getElementById("status").textContent, history: document.getElementById("historyList").innerHTML, assessment: document.getElementById("assistantReviewAssessment").textContent}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("current investigation", rendered["status"])
        self.assertIn("catalog is not available", rendered["history"])
        self.assertIn("Resolver probes", rendered["assessment"])

    def test_empty_catalog_and_invalid_snapshot_metadata_render_calm_history_status(self):
        body = """
renderHistory({artifact_type: "investigation_catalog", events: [], invalid_snapshots: []});
const empty = document.getElementById("historyList").innerHTML;
renderHistory({artifact_type: "investigation_catalog", events: [{event_id: "event-ok", snapshot_path: "investigations/event-ok.json", target_class: "resolver_probe", severity: "low", first_anomalous_at: "2026-07-20T00:00:00Z", recovered_at: "2026-07-20T00:15:00Z", duration: 15, lifecycle: "complete", affected_targets: ["45.90.30.134"]}], invalid_snapshots: [{snapshot_path: "investigations/bad.json", error_type: "malformed_json"}]});
console.log(JSON.stringify({empty, mixed: document.getElementById("historyList").innerHTML}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("No completed event snapshots", rendered["empty"])
        self.assertIn("Resolver probes", rendered["mixed"])
        self.assertIn("invalid snapshot", rendered["mixed"])

    def test_failed_historical_fetch_preserves_current_view(self):
        current = self.investigation_payload()
        body = f"""
globalThis.fetch = async (url) => {{
  if (url === INVESTIGATION_URL) return {{ok: true, json: async () => ({json.dumps(current)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await loadInvestigation(INVESTIGATION_URL, "investigation.json", true);
  const before = document.getElementById("assistantReviewAssessment").textContent;
  try {{ await loadInvestigation("./investigations/missing.json", "investigations/missing.json", false); }} catch (err) {{ showInvestigationLoadError(err); }}
  console.log(JSON.stringify({{preserved: before === document.getElementById("assistantReviewAssessment").textContent, error: document.getElementById("status").classList.contains("error")}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertTrue(rendered["preserved"])
        self.assertTrue(rendered["error"])

    def test_no_browser_crypto_dependency_remains(self):
        self.assertNotIn("crypto.subtle", self.script)
        self.assertNotIn("subtle.digest", self.script)
        self.assertNotIn("stableStringify", self.script)

    def test_browser_fetches_local_artifacts_only(self):
        self.assertIn('const OPERATOR_ASSISTANT_INPUT_URL = "./operator_assistant_input.json"', self.script)
        self.assertIn('const OPERATOR_ASSISTANT_OUTPUT_URL = "./operator_assistant_output.json"', self.script)
        self.assertIn('const INVESTIGATION_CATALOG_URL = "./investigation_catalog.json"', self.script)
        self.assertNotIn("openrouter.ai", self.script)
        self.assertNotIn("crypto.subtle", self.script)


if __name__ == "__main__":
    unittest.main()
