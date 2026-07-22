import datetime as dt
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
MODULE_PATH = BIN / "health_dimensions.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "health_dimensions_calibration.json"


def load_module():
    if str(BIN) not in sys.path:
        sys.path.insert(0, str(BIN))
    spec = importlib.util.spec_from_file_location("health_dimensions", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def telemetry_rows(summary):
    telemetry = summary.get("telemetry") or {}
    if not telemetry:
        return []
    base = dt.datetime(2026, 7, 21, 12, 0, tzinfo=dt.timezone.utc)
    rows = []
    series = [
        ("192.168.1.1", telemetry.get("gateway_p95_ms") or []),
        ("1.1.1.1", telemetry.get("internet_probe_p95_ms") or []),
        ("45.90.28.134", telemetry.get("resolver_primary_p95_ms") or []),
        ("45.90.30.134", telemetry.get("resolver_secondary_p95_ms") or []),
    ]
    offset = 0
    for host, values in series:
        for value in values:
            rows.append({
                "ts": (base + dt.timedelta(minutes=offset)).isoformat(),
                "phase_label": "fiber",
                "host": host,
                "p95_ms": str(value),
                "jitter_ms": "5",
                "loss_pct": str(telemetry.get("loss_pct", 0)),
            })
            offset += 1
    return rows


def diagnostic_payload(summary):
    return {"status": "ok", "items": summary.get("diagnostics") or []}


class HealthDimensionsEvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.fixtures = json.loads(FIXTURE_PATH.read_text())["fixtures"]

    def evaluate_fixture(self, fixture_id):
        fixture = next(item for item in self.fixtures if item["id"] == fixture_id)
        return self.module.evaluate_health_dimensions(
            telemetry_rows(fixture["evidence_summary"]),
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence=diagnostic_payload(fixture["evidence_summary"]),
        )

    def assert_expected(self, fixture_id):
        fixture = next(item for item in self.fixtures if item["id"] == fixture_id)
        result = self.evaluate_fixture(fixture_id)
        expected = fixture["expected"]
        dependency = result["dependency_groups"][0] if result["dependency_groups"] else {"state": "insufficient_evidence", "redundancy_status": "unknown"}
        self.assertEqual(result["technical_condition"]["state"], expected["technical_condition"])
        self.assertEqual(result["user_impact"]["state"], expected["user_impact"])
        self.assertEqual(result["operational_risk"]["state"], expected["operational_risk"])
        self.assertEqual(result["detection_confidence"], expected["detection_confidence"])
        self.assertEqual(result["attribution"]["domain"], expected["attribution_domain"])
        self.assertEqual(result["attribution_confidence"], expected["attribution_confidence"])
        self.assertEqual(dependency["state"], expected["dependency_group_state"])
        self.assertEqual(dependency["redundancy_status"], expected["redundancy_state"])

    def test_calibration_incident_matches_expected_multidimensional_result(self):
        result = self.evaluate_fixture("nextdns_anycast_primary_sydney_active_secondary")
        dependency = result["dependency_groups"][0]

        self.assertEqual(result["technical_condition"]["state"], "severe")
        self.assertEqual(result["user_impact"]["state"], "not_observed")
        self.assertEqual(result["operational_risk"]["state"], "elevated")
        self.assertEqual(result["detection_confidence"], "high")
        self.assertEqual(result["attribution"]["domain"], "resolver_provider_path")
        self.assertEqual(result["attribution_confidence"], "high")
        self.assertEqual(dependency["state"], "active_healthy_peer_degraded")
        self.assertEqual(dependency["redundancy_status"], "reduced")
        self.assertEqual(dependency["active_member"], "nextdns_secondary")

    def test_fixture_matrix_expected_outputs(self):
        for fixture_id in (
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
        ):
            with self.subTest(fixture_id=fixture_id):
                self.assert_expected(fixture_id)

    def test_absent_diagnostics_degrade_safely(self):
        result = self.module.evaluate_health_dimensions(
            telemetry_rows(self.fixtures[1]["evidence_summary"]),
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "missing", "items": []},
        )

        self.assertEqual(result["diagnostic_evidence"]["status"], "missing")
        self.assertIn("active_dependency_path", result["unresolved_evidence"])

    def test_malformed_diagnostics_reader_degrades_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diagnostic_evidence.json"
            path.write_text("{not-json")

            payload = self.module.load_diagnostic_evidence(path)

        self.assertEqual(payload["status"], "malformed")
        self.assertEqual(payload["items"], [])
        self.assertTrue(payload["limitations"])

    def test_legacy_inputs_without_dependency_metadata_are_unknown(self):
        result = self.module.evaluate_health_dimensions(
            [],
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "missing", "items": []},
        )

        self.assertEqual(result["technical_condition"]["state"], "unknown")
        self.assertEqual(result["attribution"]["domain"], "unknown")
        self.assertEqual(result["dependency_groups"], [])


if __name__ == "__main__":
    unittest.main()
