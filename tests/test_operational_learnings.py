import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "operational_learnings.py"


def load_module():
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location("operational_learnings", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class OperationalLearningsTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.generated_at = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)

    def incident(self, incident_id="event-1", target_class="resolver_probe", likely="Resolver provider path", impact="none_observed", app="working", recovery="recovered", adaptive="anomalous", external=False, started_at="2026-08-01T00:00:00+00:00"):
        return {
            "selected_event": {
                "id": incident_id,
                "target_class": target_class,
                "lifecycle_state": recovery,
                "first_anomalous_at": started_at,
                "last_anomalous_at": started_at,
            },
            "incident_record": {
                "incident_id": incident_id,
                "likely_issue": likely,
                "affected_services": ["Resolver probes"] if target_class == "resolver_probe" else ["Gateway"],
                "user_facing_impact": impact,
                "started_at": started_at,
            },
            "incident_phases": {
                "during": {"affected_services": ["Resolver probes"], "likely_issue": likely},
                "after": {"status": recovery},
            },
            "health_dimensions": {
                "estimated_user_impact": {"state": impact},
                "observed_user_impact": {"state": impact, "source": "operator_feedback"},
                "application_experience": {"state": app},
                "adaptive_baseline": {"resolver_members": [{"member_id": "nextdns_secondary", "role": "secondary", "baseline_state": adaptive}]},
                "dependency_groups": [{"state": "resolver_degraded"}],
            },
            "internet_conditions_context": {"available": external, "status": "events_reported" if external else "unavailable"},
        }

    def build(self, snapshots, baseline_history=None):
        return self.module.build_operational_learnings(
            completed_snapshots=[(f"investigations/{item['selected_event']['id']}.json", item) for item in snapshots],
            baseline_history=baseline_history,
            generated_at=self.generated_at,
        )

    def find(self, payload, insight_id):
        for insight in payload["insights"]:
            if insight["id"] == insight_id:
                return insight
        return None

    def test_single_incident_does_not_create_learning(self):
        payload = self.build([self.incident()])

        self.assertIsNone(self.find(payload, "resolver-path-recovers-without-intervention"))
        self.assertIsNone(self.find(payload, "pattern-adaptive_baseline_event"))

    def test_multiple_supporting_incidents_create_learning(self):
        payload = self.build([self.incident("event-1"), self.incident("event-2")])
        insight = self.find(payload, "resolver-path-recovers-without-intervention")

        self.assertIsNotNone(insight)
        self.assertEqual(insight["confidence"], "medium")
        self.assertEqual(insight["times_observed"], 2)
        self.assertEqual(insight["supporting_incidents"], ["event-1", "event-2"])

    def test_confidence_increases_with_more_support(self):
        payload = self.build([self.incident(f"event-{idx}") for idx in range(1, 5)])
        insight = self.find(payload, "resolver-path-recovers-without-intervention")

        self.assertEqual(insight["confidence"], "high")
        self.assertEqual(insight["stability"], "stable")

    def test_conflicting_evidence_reduces_confidence(self):
        conflict = self.incident("event-conflict", impact="reported_major", app="failing", recovery="active")
        payload = self.build([self.incident("event-1"), self.incident("event-2"), conflict])
        insight = self.find(payload, "resolver-elevated-without-user-impact")

        self.assertEqual(insight["confidence"], "low")
        self.assertEqual(insight["stability"], "reduced_confidence")
        self.assertTrue(any("conflicts" in ref["reason"] for ref in insight["evidence_refs"]))

    def test_retired_insight_when_conflict_outweighs_support(self):
        baseline_history = {
            "targets": {
                "FIBER|resolver_probe|45.90.30.134": {
                    "identity": {"target_class": "resolver_probe"},
                    "accepted_state": "elevated_but_stable",
                }
            }
        }
        conflict = self.incident("event-conflict", impact="reported_major", app="failing")
        payload = self.build([conflict], baseline_history=baseline_history)
        insight = self.find(payload, "resolver-elevated-without-user-impact")

        self.assertEqual(insight["confidence"], "retired")
        self.assertEqual(insight["stability"], "retired")

    def test_baseline_support_can_contribute_to_learning(self):
        baseline_history = {
            "targets": {
                "FIBER|resolver_probe|45.90.30.134": {
                    "identity": {"target_class": "resolver_probe"},
                    "accepted_state": "elevated_but_stable",
                }
            }
        }
        payload = self.build([self.incident("event-1")], baseline_history=baseline_history)
        insight = self.find(payload, "resolver-elevated-without-user-impact")

        self.assertIsNotNone(insight)
        self.assertEqual(insight["supporting_baselines"], ["viz/baseline_history.json#FIBER|resolver_probe|45.90.30.134"])

    def test_legacy_snapshot_missing_fields_is_safe(self):
        payload = self.module.build_operational_learnings(
            completed_snapshots=[("investigations/legacy.json", {"selected_event": {"id": "legacy"}})],
            baseline_history=None,
            generated_at=self.generated_at,
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["insights"], [])


if __name__ == "__main__":
    unittest.main()
