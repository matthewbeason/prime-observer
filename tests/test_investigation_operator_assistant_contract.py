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
            "incident_record": {
                "title": "Resolver path degradation",
                "status": "recovering",
                "confidence": "high",
                "narrative": "Resolver probes were healthy before the incident, degraded during it, and recovery has started but is not confirmed.",
                "affected_services": ["Resolver probes"],
                "healthy_comparisons": ["Internet probes"],
                "likely_issue": "DNS provider path",
            },
            "incident_phases": {
                "before": {
                    "available": True,
                    "start": "2026-07-20T03:00:00Z",
                    "end": "2026-07-20T03:10:00Z",
                    "status": "healthy",
                    "headline": "Affected service was healthy before the incident.",
                    "summary": "Before the incident, resolver probes stayed below sustained degradation thresholds.",
                    "affected_services": ["Resolver probes"],
                    "healthy_comparisons": ["Internet probes"],
                    "representative_metrics": {"sample_count": 6, "typical_p95_ms": 28, "p90_p95_ms": 34, "sustained_bad_count": 0},
                    "maximum_excursions": {"max_p95_ms": 42, "max_loss_pct": 0, "isolated_excursion_bucket_count": 0, "turbulence_bucket_count": 0},
                    "evidence_refs": [],
                    "limitations": [],
                },
                "during": {
                    "available": True,
                    "start": "2026-07-20T03:11:00Z",
                    "end": "2026-07-20T03:18:00Z",
                    "status": "degraded",
                    "headline": "Resolver probes became degraded.",
                    "summary": "Persistence was confirmed and DNS and web checks continued to work.",
                    "affected_services": ["Resolver probes"],
                    "healthy_comparisons": ["Internet probes"],
                    "representative_metrics": {"sample_count": 8, "typical_p95_ms": 176, "p90_p95_ms": 260, "sustained_bad_count": 2},
                    "maximum_excursions": {"max_p95_ms": 320, "max_loss_pct": 0, "isolated_excursion_bucket_count": 1, "turbulence_bucket_count": 0},
                    "evidence_refs": [],
                    "limitations": [],
                },
                "after": {
                    "available": True,
                    "start": "2026-07-20T03:19:00Z",
                    "end": "2026-07-20T03:23:00Z",
                    "status": "recovery_started",
                    "headline": "Recovery started but is not confirmed.",
                    "summary": "Healthy samples have persisted; about 7 minute(s) remain before confirmation.",
                    "affected_services": ["Resolver probes"],
                    "healthy_comparisons": [],
                    "representative_metrics": {"sample_count": 5, "typical_p95_ms": 30, "p90_p95_ms": 35, "sustained_bad_count": 0},
                    "maximum_excursions": {"max_p95_ms": 40, "max_loss_pct": 0, "isolated_excursion_bucket_count": 0, "turbulence_bucket_count": 0},
                    "evidence_refs": [],
                    "limitations": ["Recovery has not been confirmed."],
                    "returned_to_normal": True,
                    "remaining_stable_seconds": 420,
                },
            },
            "incident_replay": {
                "milestones": [
                    {
                        "id": "first-anomaly",
                        "timestamp": "2026-07-20T03:11:00Z",
                        "state": "first_anomaly",
                        "title": "Resolver anomaly detected",
                        "summary": "Latency increased on resolver probes while Internet probes remained healthy.",
                        "affected_services": ["Resolver probes"],
                        "healthy_services": ["Internet probes"],
                        "likely_issue": "Unknown",
                        "confidence": "medium",
                        "evidence_refs": [{"field": "selected_event.first_anomalous_at", "reason": "first anomalous sample"}],
                        "metrics_snapshot": {"representative_metrics": {"sample_count": 8, "typical_p95_ms": 176, "sustained_bad_count": 2}, "maximum_excursions": {"max_p95_ms": 320}, "raw_values": {"first_anomalous_at": "2026-07-20T03:11:00Z"}},
                    },
                    {
                        "id": "persistence-confirmed",
                        "timestamp": "2026-07-20T03:12:00Z",
                        "state": "persistence_confirmed",
                        "title": "Resolver degradation persisted",
                        "summary": "Persistence was confirmed while Internet probes stayed healthy.",
                        "affected_services": ["Resolver probes"],
                        "healthy_services": ["Internet probes"],
                        "likely_issue": "DNS provider path",
                        "confidence": "high",
                        "evidence_refs": [{"field": "selected_event.confirmed_at", "reason": "persistence confirmation"}],
                        "metrics_snapshot": {"representative_metrics": {"sample_count": 8, "typical_p95_ms": 176, "sustained_bad_count": 2}, "maximum_excursions": {"max_p95_ms": 320}, "raw_values": {"confirmed_at": "2026-07-20T03:12:00Z"}},
                    },
                    {
                        "id": "recovery-started",
                        "timestamp": "2026-07-20T03:19:00Z",
                        "state": "recovery_started",
                        "title": "Recovery started",
                        "summary": "Healthy samples have persisted; recovery is not confirmed yet.",
                        "affected_services": ["Resolver probes"],
                        "healthy_services": ["Internet probes"],
                        "likely_issue": "DNS provider path",
                        "confidence": "high",
                        "evidence_refs": [{"field": "selected_event.recovery_started_at", "reason": "healthy persistence start"}],
                        "metrics_snapshot": {"representative_metrics": {"sample_count": 5, "typical_p95_ms": 30, "sustained_bad_count": 0}, "maximum_excursions": {"max_p95_ms": 40}, "raw_values": {"remaining_stable_seconds": 420}},
                    },
                ]
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
        self.assertEqual(rendered["assessment"], "")
        self.assertNotIn("unavailable", rendered["assessment"].lower())
        self.assertNotIn("failed", rendered["assessment"].lower())
        self.assertIn("local evidence", rendered["pills"])

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
        self.assertEqual(rendered["assessment"], "")
        self.assertIn("current synthesis", rendered["pills"])

    def test_stale_llm_output_falls_back_without_exposing_stale_error(self):
        review = {"status": "ok", "input_hash": "a" * 64, "headline": "Old", "assessment": "Old analysis"}
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'b' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{assessment: document.getElementById("assistantReviewAssessment").textContent, pills: document.getElementById("assistantReviewPills").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertNotIn("Old analysis", rendered["assessment"])
        self.assertNotIn("does not match", rendered["assessment"])
        self.assertIn("local evidence", rendered["pills"])

    def test_malformed_matching_llm_output_falls_back_to_deterministic(self):
        review = {"status": "ok", "input_hash": "a" * 64, "headline": "", "assessment": ""}
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{headline: document.getElementById("assistantReviewHeadline").textContent, pills: document.getElementById("assistantReviewPills").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("Resolver probe", rendered["headline"])
        self.assertIn("local evidence", rendered["pills"])

    def test_pending_generation_keeps_safe_deterministic_content_visible(self):
        body = f"""
renderAssistantReview(null, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{assessment: document.getElementById("assistantReviewAssessment").textContent, nextSteps: document.getElementById("assistantReviewNextSteps").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["assessment"], "")
        self.assertEqual(rendered["nextSteps"], "")
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

        self.assertEqual(rendered["nextSteps"], "")
        self.assertNotIn("COMPARE_RESOLVER_AND_INTERNET", rendered["nextSteps"])

    def test_concrete_intervention_is_rendered_when_required(self):
        review = {
            "status": "ok",
            "input_hash": "a" * 64,
            "headline": "Router needs attention",
            "assessment": "Operator assessment",
            "confidence": "medium",
            "next_steps": [{"label": "Restart the router", "reason": "Router check is supported."}],
        }
        body = f"""
renderAssistantReview({json.dumps(review)}, {{input_hash: "{'a' * 64}"}}, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{display: document.getElementById("recommendedActionsSection").style.display || "visible", nextSteps: document.getElementById("assistantReviewNextSteps").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["display"], "visible")
        self.assertIn("Restart local equipment", rendered["nextSteps"])

    def test_no_intervention_section_for_prime_observer_work(self):
        body = f"""
renderAssistantReview(null, null, {json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{display: document.getElementById("recommendedActionsSection").style.display, nextSteps: document.getElementById("assistantReviewNextSteps").innerHTML}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["display"], "none")
        self.assertEqual(rendered["nextSteps"], "")

    def test_operator_impact_copy_distinguishes_missing_and_reported_feedback(self):
        base = self.investigation_payload()
        base["health_dimensions"] = {
            "estimated_user_impact": {"state": "possible"},
            "observed_user_impact": {"state": "unknown"},
            "application_experience": {"is_current": True, "failure_counts": {"total": 0}, "evidence": []},
        }
        none_reported = json.loads(json.dumps(base))
        none_reported["health_dimensions"]["observed_user_impact"] = {"state": "none_reported"}
        reported = json.loads(json.dumps(base))
        reported["health_dimensions"]["observed_user_impact"] = {"state": "reported_major"}
        body = f"""
const missing = userImpactSummary({{state: "possible"}}, {json.dumps(base["health_dimensions"]["observed_user_impact"])}, {json.dumps(base["health_dimensions"]["application_experience"])}, null);
const none = userImpactSummary({{state: "possible"}}, {json.dumps(none_reported["health_dimensions"]["observed_user_impact"])}, {json.dumps(base["health_dimensions"]["application_experience"])}, null);
const disruption = userImpactSummary({{state: "possible"}}, {json.dumps(reported["health_dimensions"]["observed_user_impact"])}, {json.dumps(base["health_dimensions"]["application_experience"])}, null);
console.log(JSON.stringify({{missing, none, disruption}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("No user-facing failure detected by current checks", rendered["missing"])
        self.assertIn("Reported symptoms are unknown", rendered["missing"])
        self.assertIn("No symptoms have been reported", rendered["none"])
        self.assertIn("Major impact reported", rendered["disruption"])

    def test_operator_display_mappings_hide_model_terms(self):
        body = """
console.log(JSON.stringify({
  broadIsp: healthLabel("broad_isp_path"),
  severe: healthLabel("severe"),
  clean: cleanOperatorText("Technical condition uses refined attribution for broad_isp_path and deterministic evidence."),
  app: applicationSummary({is_current: true, failure_counts: {total: 0}, evidence: []}),
}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["broadIsp"], "ISP or upstream path")
        self.assertEqual(rendered["severe"], "Unstable")
        self.assertIn("Network condition", rendered["clean"])
        self.assertIn("likely issue", rendered["clean"])
        self.assertIn("local evidence", rendered["clean"])
        self.assertEqual(rendered["app"], "DNS and web checks are working.")

    def test_incident_phases_render_without_browser_semantic_inference(self):
        body = f"""
renderIncidentPhases({json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{
  story: document.getElementById("incidentStoryBody").innerHTML,
  before: document.getElementById("beforePhaseBody").innerHTML,
  during: document.getElementById("duringPhaseBody").innerHTML,
  after: document.getElementById("afterPhaseBody").innerHTML,
}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("Affected service was healthy before the incident", rendered["before"])
        self.assertIn("Resolver probes became degraded", rendered["during"])
        self.assertIn("Recovery started but is not confirmed", rendered["after"])
        self.assertIn("What stayed healthy", rendered["story"])
        self.assertIn("typical p95 176 ms", rendered["during"])
        self.assertIn("max p95 320 ms", rendered["during"])

    def test_old_artifacts_without_incident_phases_do_not_fail(self):
        payload = self.investigation_payload()
        payload.pop("incident_phases")
        body = f"""
renderIncidentPhases({json.dumps(payload)});
console.log(JSON.stringify({{
  beforeDisplay: document.getElementById("beforePhaseSection").style.display,
  duringDisplay: document.getElementById("duringPhaseSection").style.display,
  story: document.getElementById("incidentStoryBody").innerHTML,
}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["beforeDisplay"], "none")
        self.assertEqual(rendered["duringDisplay"], "none")
        self.assertIn("older artifact", rendered["story"])

    def test_active_incident_without_recovery_hides_after_section(self):
        payload = self.investigation_payload()
        payload["incident_phases"].pop("after")
        body = f"""
renderIncidentPhases({json.dumps(payload)});
console.log(JSON.stringify({{afterDisplay: document.getElementById("afterPhaseSection").style.display}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["afterDisplay"], "none")

    def test_current_and_historical_views_render_phase_story(self):
        current = self.investigation_payload()
        historical = json.loads(json.dumps(current))
        body = f"""
globalThis.fetch = async (url) => {{
  if (url === INVESTIGATION_URL) return {{ok: true, json: async () => ({json.dumps(current)})}};
  if (url === "./investigations/event-history.json") return {{ok: true, json: async () => ({json.dumps(historical)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await loadInvestigation(INVESTIGATION_URL, "investigation.json", "current");
  const currentStory = document.getElementById("duringPhaseBody").innerHTML;
  await loadInvestigation("./investigations/event-history.json", "investigations/event-history.json", "incident");
  console.log(JSON.stringify({{currentStory, historicalStory: document.getElementById("duringPhaseBody").innerHTML}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("Resolver probes became degraded", rendered["currentStory"])
        self.assertIn("Resolver probes became degraded", rendered["historicalStory"])

    def test_incident_replay_renders_ordered_vertical_sequence(self):
        body = f"""
renderIncidentReplay({json.dumps(self.investigation_payload())});
console.log(JSON.stringify({{html: document.getElementById("timelineMilestones").innerHTML, cls: document.getElementById("timelineMilestones").className}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["cls"], "stack")
        self.assertLess(rendered["html"].index("Resolver anomaly detected"), rendered["html"].index("Resolver degradation persisted"))
        self.assertLess(rendered["html"].index("Resolver degradation persisted"), rendered["html"].index("Recovery started"))
        self.assertIn("Latency increased on resolver probes", rendered["html"])
        self.assertIn("Healthy comparisons", rendered["html"])
        self.assertIn("Confidence", rendered["html"])
        self.assertIn("selected_event.first_anomalous_at", rendered["html"])
        self.assertIn("Typical p95", rendered["html"])

    def test_incident_replay_legacy_fallback_uses_existing_timeline(self):
        payload = self.investigation_payload()
        payload.pop("incident_replay")
        payload["selected_event"]["confirmed_at"] = "2026-07-20T03:12:00Z"
        body = f"""
renderIncidentReplay({json.dumps(payload)});
console.log(JSON.stringify({{html: document.getElementById("timelineMilestones").innerHTML, cls: document.getElementById("timelineMilestones").className}}));
"""
        rendered = json.loads(self.run_node(body))

        self.assertEqual(rendered["cls"], "card-grid")
        self.assertIn("Sustained confirmation", rendered["html"])

    def test_current_and_historical_views_render_replay_without_openrouter(self):
        current = self.investigation_payload()
        historical = json.loads(json.dumps(current))
        body = f"""
globalThis.fetch = async (url) => {{
  if (url === INVESTIGATION_URL) return {{ok: true, json: async () => ({json.dumps(current)})}};
  if (url === "./investigations/event-history.json") return {{ok: true, json: async () => ({json.dumps(historical)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await loadInvestigation(INVESTIGATION_URL, "investigation.json", "current");
  const currentReplay = document.getElementById("timelineMilestones").innerHTML;
  await loadInvestigation("./investigations/event-history.json", "investigations/event-history.json", "incident");
  console.log(JSON.stringify({{currentReplay, historicalReplay: document.getElementById("timelineMilestones").innerHTML}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("Resolver anomaly detected", rendered["currentReplay"])
        self.assertIn("Resolver anomaly detected", rendered["historicalReplay"])

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

        self.assertIn("Current incident", rendered["currentStatus"])
        self.assertIn("Completed incident", rendered["historicalStatus"])
        self.assertEqual(rendered["mode"], "Completed incident")

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

        self.assertIn("Current incident", rendered["status"])
        self.assertIn("catalog is not available", rendered["history"])
        self.assertEqual(rendered["assessment"], "")

    def test_interval_view_does_not_load_current_investigation_as_substitute(self):
        body = """
window.location.search = "?view=interval&start=2026-08-02T10:00:00.000Z&end=2026-08-02T10:15:00.000Z";
const fetched = [];
globalThis.fetch = async (url) => {
  fetched.push(url);
  return {ok: false, status: 404, json: async () => ({})};
};
(async () => {
  await main();
  console.log(JSON.stringify({
    fetched,
    status: document.getElementById("status").textContent,
    mode: document.getElementById("modePill").textContent,
    start: document.getElementById("intervalRequestStart").textContent,
    end: document.getElementById("intervalRequestEnd").textContent,
    summary: document.getElementById("intervalRequestSummary").textContent,
  }));
})().catch(err => { console.error(err); process.exit(1); });
"""
        rendered = json.loads(self.run_node(body))

        self.assertNotIn(INVESTIGATION_URL := "./investigation.json", rendered["fetched"])
        self.assertIn("./interval_summary.json", rendered["fetched"])
        self.assertEqual(rendered["mode"], "Selected interval")
        self.assertEqual(rendered["start"], "2026-08-02T10:00:00.000Z")
        self.assertEqual(rendered["end"], "2026-08-02T10:15:00.000Z")
        self.assertIn("not being shown as a substitute", rendered["summary"])

    def test_interval_view_renders_matching_interval_summary_artifact(self):
        interval = {
            "schema_version": 1,
            "model_version": "prime_observer.interval_summary.v1",
            "generated_at": "2026-08-02T10:16:00Z",
            "start": "2026-08-02T10:00:00.000Z",
            "end": "2026-08-02T10:15:00.000Z",
            "overall_condition": "elevated_but_stable",
            "confidence": "high",
            "summary": "Between 10:00 AM and 10:15 AM, resolver latency was elevated but stable.",
            "affected_services": ["Resolver probes"],
            "healthy_services": ["Gateway", "Application checks"],
            "application_summary": {"state": "working", "dns_success": True, "https_success": True},
            "incident_overlap": {"count": 0, "items": []},
            "metrics": {"gateway": {"state": "healthy", "p95_latency_ms": 8, "loss_rate_pct": 0}, "resolver": {"state": "elevated", "p95_latency_ms": 176, "loss_rate_pct": 0}, "internet": {"state": "healthy", "p95_latency_ms": 30, "loss_rate_pct": 0}, "application": {"state": "working"}, "adaptive_baseline_state": "elevated_but_stable", "dns_success": True, "https_success": True, "timeout_count": 0},
            "baseline_comparison": {"adaptive_baseline_state": "elevated_but_stable", "resolver_members": [{"baseline_source": "durable"}]},
            "coverage": {"sample_count": 12, "source_path": "data/test.csv"},
            "evidence_refs": [{"path": "viz/latest.csv", "reason": "Telemetry rows within requested interval."}],
            "likely_issue": "Established degraded resolver baseline",
        }
        body = f"""
window.location.search = "?view=interval&start=2026-08-02T10:00:00.000Z&end=2026-08-02T10:15:00.000Z";
const fetched = [];
globalThis.fetch = async (url) => {{
  fetched.push(url);
  if (url === INTERVAL_SUMMARY_URL) return {{ok: true, json: async () => ({json.dumps(interval)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await main();
  console.log(JSON.stringify({{
    fetched,
    status: document.getElementById("status").textContent,
    mode: document.getElementById("modePill").textContent,
    condition: document.getElementById("intervalCondition").textContent,
    summary: document.getElementById("intervalRequestSummary").textContent,
    affected: document.getElementById("intervalAffectedServices").innerHTML,
    healthy: document.getElementById("intervalHealthyServices").innerHTML,
    metrics: document.getElementById("intervalMetricsTable").innerHTML,
  }}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertNotIn("./investigation.json", rendered["fetched"])
        self.assertEqual(rendered["mode"], "Selected interval")
        self.assertIn("deterministic interval summary", rendered["status"])
        self.assertEqual(rendered["condition"], "Elevated But Stable")
        self.assertIn("resolver latency was elevated", rendered["summary"])
        self.assertIn("Resolver probes", rendered["affected"])
        self.assertIn("Gateway", rendered["healthy"])
        self.assertIn("Adaptive baseline", rendered["metrics"])

    def test_interval_view_falls_back_when_summary_does_not_match_requested_interval(self):
        interval = {"start": "2026-08-02T11:00:00.000Z", "end": "2026-08-02T11:15:00.000Z"}
        body = f"""
window.location.search = "?view=interval&start=2026-08-02T10:00:00.000Z&end=2026-08-02T10:15:00.000Z";
globalThis.fetch = async (url) => {{
  if (url === INTERVAL_SUMMARY_URL) return {{ok: true, json: async () => ({json.dumps(interval)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await main();
  console.log(JSON.stringify({{status: document.getElementById("status").textContent, summary: document.getElementById("intervalRequestSummary").textContent}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("not available for this exact window", rendered["status"])
        self.assertIn("not being shown as a substitute", rendered["summary"])

    def test_explicit_current_view_loads_current_artifact(self):
        current = self.investigation_payload()
        body = f"""
window.location.search = "?view=current";
const fetched = [];
globalThis.fetch = async (url) => {{
  fetched.push(url);
  if (url === INVESTIGATION_URL) return {{ok: true, json: async () => ({json.dumps(current)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await main();
  console.log(JSON.stringify({{fetched, status: document.getElementById("status").textContent, mode: document.getElementById("modePill").textContent}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
        rendered = json.loads(self.run_node(body))

        self.assertIn("./investigation.json", rendered["fetched"])
        self.assertIn("Current incident", rendered["status"])
        self.assertEqual(rendered["mode"], "Current incident")

    def test_historical_and_legacy_event_routes_load_snapshot(self):
        current = self.investigation_payload()
        historical = json.loads(json.dumps(current))
        catalog = {"artifact_type": "investigation_catalog", "events": [{"event_id": "event-history", "snapshot_path": "investigations/event-history.json"}], "invalid_snapshots": []}
        for search in ("?view=incident&event=event-history", "?event=event-history"):
            body = f"""
window.location.search = "{search}";
const fetched = [];
globalThis.fetch = async (url) => {{
  fetched.push(url);
  if (url === INVESTIGATION_CATALOG_URL) return {{ok: true, json: async () => ({json.dumps(catalog)})}};
  if (url === "./investigations/event-history.json") return {{ok: true, json: async () => ({json.dumps(historical)})}};
  return {{ok: false, status: 404, json: async () => ({{}})}};
}};
(async () => {{
  await main();
  console.log(JSON.stringify({{fetched, status: document.getElementById("status").textContent, mode: document.getElementById("modePill").textContent}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
"""
            rendered = json.loads(self.run_node(body))
            self.assertIn("./investigations/event-history.json", rendered["fetched"])
            self.assertIn("Completed incident", rendered["status"])
            self.assertEqual(rendered["mode"], "Completed incident")

    def test_back_forward_route_handler_preserves_view_state(self):
        self.assertIn("popstate", self.script)
        self.assertIn("applyRoute(catalog)", self.script)
        self.assertIn('view === "interval"', self.script)

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
