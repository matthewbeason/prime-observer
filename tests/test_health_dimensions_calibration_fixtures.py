import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "health_dimensions_calibration.json"


EXPECTED_FIELDS = {
    "technical_condition",
    "user_impact",
    "operational_risk",
    "detection_confidence",
    "attribution_domain",
    "attribution_confidence",
    "dependency_group_state",
    "redundancy_state",
    "unresolved_evidence",
    "operator_input_still_required",
}


class HealthDimensionsCalibrationFixtureTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE_PATH.read_text())

    def test_fixture_file_has_expected_top_level_contract(self):
        self.assertEqual(self.payload["schema_version"], 1)
        self.assertEqual(
            self.payload["model_version"],
            "prime_observer.health_dimensions_calibration.v1.proposal",
        )
        self.assertIsInstance(self.payload.get("fixtures"), list)
        self.assertGreaterEqual(len(self.payload["fixtures"]), 12)

    def test_fixture_ids_are_unique_and_expected_results_are_complete(self):
        seen = set()
        for fixture in self.payload["fixtures"]:
            with self.subTest(fixture=fixture.get("id")):
                fixture_id = fixture.get("id")
                self.assertIsInstance(fixture_id, str)
                self.assertNotIn(fixture_id, seen)
                seen.add(fixture_id)

                self.assertIsInstance(fixture.get("title"), str)
                self.assertIsInstance(fixture.get("evidence_summary"), dict)
                self.assertEqual(set(fixture.get("expected", {})), EXPECTED_FIELDS)
                self.assertIsInstance(fixture["expected"]["unresolved_evidence"], list)
                self.assertIsInstance(fixture["expected"]["operator_input_still_required"], list)

    def test_expected_values_use_declared_enums(self):
        enums = self.payload["allowed_expected_states"]
        enum_fields = EXPECTED_FIELDS - {"unresolved_evidence", "operator_input_still_required"}
        for fixture in self.payload["fixtures"]:
            with self.subTest(fixture=fixture.get("id")):
                expected = fixture["expected"]
                for field in enum_fields:
                    self.assertIn(expected[field], enums[field], field)

    def test_required_calibration_cases_are_present(self):
        fixture_ids = {fixture["id"] for fixture in self.payload["fixtures"]}
        required = {
            "nextdns_anycast_primary_sydney_active_secondary",
            "primary_degraded_secondary_healthy_active_unknown",
            "primary_degraded_secondary_healthy_active_primary",
            "secondary_degraded_primary_healthy",
            "both_resolvers_degraded",
            "resolver_icmp_high_dns_normal",
            "internet_and_resolver_groups_degraded",
            "gateway_and_wan_degraded",
            "isolated_resolver_outlier_without_persistence",
            "symptoms_confirmed_despite_healthy_fallback",
            "diagnostic_evidence_unavailable",
            "stale_diagnostic_evidence",
            "legacy_artifact_without_new_fields",
        }
        self.assertTrue(required.issubset(fixture_ids))


if __name__ == "__main__":
    unittest.main()
