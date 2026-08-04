import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "incident_similarity.py"


def load_module():
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location("incident_similarity", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class IncidentSimilarityTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.generated_at = dt.datetime(2026, 8, 3, 12, 0, tzinfo=dt.timezone.utc)

    def incident(self, incident_id="event-current", target_class="resolver_probe", affected=None, likely="Resolver provider path", impact="none_observed", app="working", adaptive="anomalous", technical="degraded", recovery="recovered", duration=90, members=None):
        affected = affected or ["Resolver probes"]
        members = members if members is not None else [{"member_id": "nextdns_secondary", "role": "secondary", "baseline_state": adaptive, "baseline_source": "durable", "incident_eligible": True}]
        return {
            "id": f"investigation-{incident_id}",
            "selected_event": {"id": incident_id, "target_class": target_class, "lifecycle_state": recovery, "duration_minutes": duration, "affected_targets": ["45.90.30.134"]},
            "incident_record": {"incident_id": incident_id, "likely_issue": likely, "affected_services": affected, "user_facing_impact": impact, "duration_minutes": duration},
            "incident_phases": {"during": {"affected_services": affected, "likely_issue": likely}, "after": {"status": recovery}},
            "health_dimensions": {
                "technical_condition": {"state": technical},
                "estimated_user_impact": {"state": impact},
                "observed_user_impact": {"state": impact, "source": "operator_feedback"},
                "application_experience": {"state": app},
                "adaptive_baseline": {"resolver_members": members},
                "dependency_groups": [{"state": "resolver_degraded" if target_class == "resolver_probe" else "gateway_degraded"}],
            },
            "internet_conditions_context": {"available": False},
        }

    def build(self, current, snapshots):
        return self.module.build_incident_similarity(
            current_investigation=current,
            completed_snapshots=[(f"investigations/{item['selected_event']['id']}.json", item) for item in snapshots],
            generated_at=self.generated_at,
        )

    def test_identical_incidents_score_as_strong_match(self):
        result = self.build(self.incident(), [self.incident("event-previous")])

        self.assertEqual(result["current_incident"]["pattern"], "adaptive_baseline_event")
        self.assertGreaterEqual(result["matches"][0]["score"], 90)
        self.assertIn("Affected Services", result["matches"][0]["matching_dimensions"])
        self.assertTrue(any(item["dimension"] == "likely_issue" and item["weight"] == 10 for item in result["matches"][0]["similarity_breakdown"]))

    def test_same_pattern_different_impact_remains_match_with_difference(self):
        current = self.incident(impact="none_observed")
        previous = self.incident("event-previous", impact="noticeable")
        result = self.build(current, [previous])

        self.assertTrue(result["matches"])
        self.assertIn("User Impact", result["matches"][0]["different_dimensions"])

    def test_same_impact_different_cause_does_not_force_match(self):
        current = self.incident(impact="possible")
        previous = self.incident("gateway-old", target_class="gateway_probe", affected=["Gateway"], likely="Local gateway path", adaptive=None, impact="possible")
        result = self.build(current, [previous])

        self.assertEqual(result["matches"], [])

    def test_adaptive_baseline_event_pattern(self):
        result = self.build(self.incident(adaptive="elevated_but_stable", likely="Established degraded resolver baseline"), [self.incident("old", adaptive="elevated_but_stable", likely="Established degraded resolver baseline")])

        self.assertEqual(result["current_incident"]["pattern"], "adaptive_baseline_event")

    def test_gateway_and_resolver_patterns(self):
        gateway = self.incident(target_class="gateway_probe", affected=["Gateway"], likely="Local gateway path", adaptive=None)
        resolver = self.incident(target_class="resolver_probe", affected=["Resolver probes"], likely="Resolver provider path", adaptive=None)

        self.assertEqual(self.module.pattern_label(self.module.incident_features(gateway)), "gateway_instability")
        self.assertEqual(self.module.pattern_label(self.module.incident_features(resolver)), "resolver_path_degradation")

    def test_multiple_matches_are_stably_ordered(self):
        current = self.incident()
        better = self.incident("b-better")
        weaker = self.incident("a-weaker", impact="noticeable")
        result = self.build(current, [weaker, better])

        self.assertEqual([item["incident_id"] for item in result["matches"][:2]], ["b-better", "a-weaker"])

    def test_no_matches_and_missing_fields_are_safe(self):
        current = self.incident()
        result = self.build(current, [{"selected_event": {"id": "legacy"}}])

        self.assertEqual(result["matches"], [])
        self.assertIn("No historically similar", result["current_incident"]["summary"])

    def test_legacy_snapshot_with_available_dimensions_can_match(self):
        legacy = {
            "selected_event": {"id": "legacy", "target_class": "resolver_probe", "lifecycle_state": "complete"},
            "incident_record": {"affected_services": ["Resolver probes"], "likely_issue": "Resolver provider path", "user_facing_impact": "none_observed"},
        }
        result = self.build(self.incident(adaptive=None), [legacy])

        self.assertTrue(result["matches"])
        self.assertEqual(result["matches"][0]["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
