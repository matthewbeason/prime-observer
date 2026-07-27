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


def custom_rows(*, gateway=None, internet=None, primary=None, secondary=None, loss=0):
    base = dt.datetime(2026, 7, 21, 12, 0, tzinfo=dt.timezone.utc)
    rows = []
    series = [
        ("192.168.1.1", gateway or []),
        ("1.1.1.1", internet or []),
        ("45.90.28.134", primary or []),
        ("45.90.30.134", secondary or []),
    ]
    offset = 0
    for host, values in series:
        for item in values:
            if isinstance(item, dict):
                p95 = item.get("p95", 20)
                item_loss = item.get("loss", loss)
                jitter = item.get("jitter", 5)
            else:
                p95 = item
                item_loss = loss
                jitter = 5
            rows.append({
                "ts": (base + dt.timedelta(minutes=offset)).isoformat(),
                "phase_label": "fiber",
                "host": host,
                "p95_ms": str(p95),
                "jitter_ms": str(jitter),
                "loss_pct": str(item_loss),
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
        self.assertEqual(result["estimated_user_impact"]["state"], "low")
        self.assertEqual(result["observed_user_impact"]["state"], "none_reported")
        self.assertEqual(result["operational_risk"]["state"], "elevated")
        self.assertEqual(result["detection_confidence"], "high")
        self.assertEqual(result["attribution"]["domain"], "resolver_provider_path")
        self.assertEqual(result["attribution_confidence"], "high")
        self.assertEqual(dependency["state"], "active_healthy_peer_degraded")
        self.assertEqual(dependency["redundancy_status"], "reduced")
        self.assertEqual(dependency["active_member"], "nextdns_secondary")

    def test_one_resolver_degraded_with_healthy_active_peer_estimates_low_impact(self):
        result = self.evaluate_fixture("nextdns_anycast_primary_sydney_active_secondary")

        self.assertEqual(result["estimated_user_impact"]["state"], "low")
        self.assertEqual(result["observed_user_impact"]["state"], "none_reported")

    def test_one_resolver_degraded_with_active_path_unknown_estimates_low_not_likely(self):
        result = self.evaluate_fixture("primary_degraded_secondary_healthy_active_unknown")

        self.assertEqual(result["estimated_user_impact"]["state"], "low")
        self.assertEqual(result["observed_user_impact"]["state"], "unknown")
        self.assertIn("active_dependency_path", result["estimated_user_impact"]["missing_evidence"])

    def test_both_resolvers_degraded_but_reachable_estimates_possible(self):
        result = self.evaluate_fixture("both_resolvers_degraded")

        self.assertEqual(result["estimated_user_impact"]["state"], "possible")

    def test_high_latency_with_normal_direct_dns_query_estimates_none_expected(self):
        result = self.evaluate_fixture("resolver_icmp_high_dns_normal")

        self.assertEqual(result["estimated_user_impact"]["state"], "none_expected")

    def test_repeated_dns_timeout_estimates_likely(self):
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[260, 280, 290], secondary=[220, 230, 240]),
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "ok", "items": [{"type": "direct_dns_query_timeout", "status": "timeout", "freshness": "fresh"}]},
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "likely")

    def test_broad_internet_and_resolver_failure_estimates_likely(self):
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 9, 8], internet=[{"p95": 190, "loss": 3}, {"p95": 210, "loss": 3}, {"p95": 220, "loss": 3}], primary=[{"p95": 240, "loss": 3}, {"p95": 260, "loss": 3}, {"p95": 270, "loss": 3}], secondary=[{"p95": 220, "loss": 3}, {"p95": 230, "loss": 3}, {"p95": 240, "loss": 3}]),
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "ok", "items": []},
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "likely")

    def test_gateway_outage_estimates_likely_or_severe(self):
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[{"p95": 180, "loss": 5}, {"p95": 190, "loss": 5}, {"p95": 200, "loss": 5}], internet=[210, 230, 240], primary=[230, 240, 250], secondary=[220, 230, 240]),
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "ok", "items": []},
        )

        self.assertIn(result["estimated_user_impact"]["state"], {"likely", "severe"})

    def test_packet_loss_raises_estimated_impact(self):
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 9], internet=[30, 31], primary=[{"p95": 170, "loss": 3}, {"p95": 175, "loss": 3}, {"p95": 180, "loss": 3}], secondary=[35, 36, 37]),
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "ok", "items": []},
        )

        self.assertIn("packet loss exceeded degradation threshold", result["estimated_user_impact"]["drivers"])

    def test_confirmed_user_symptoms_are_observed_major(self):
        result = self.evaluate_fixture("symptoms_confirmed_despite_healthy_fallback")

        self.assertEqual(result["observed_user_impact"]["state"], "reported_major")
        self.assertEqual(result["estimated_user_impact"]["state"], "likely")

    def test_no_symptom_evidence_remains_observed_unknown(self):
        result = self.evaluate_fixture("primary_degraded_secondary_healthy_active_unknown")

        self.assertEqual(result["observed_user_impact"]["state"], "unknown")

    def test_confirmed_service_failure_observed_state_is_explicit(self):
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8], internet=[25, 25], primary=[35, 35], secondary=[35, 35]),
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "ok", "items": [{"type": "application_symptom", "status": "confirmed_service_failure", "freshness": "fresh"}]},
        )

        self.assertEqual(result["observed_user_impact"]["state"], "confirmed_service_failure")
        self.assertEqual(result["estimated_user_impact"]["state"], "severe")

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

    def test_stale_diagnostics_do_not_create_observed_impact(self):
        result = self.evaluate_fixture("stale_diagnostic_evidence")

        self.assertEqual(result["observed_user_impact"]["state"], "unknown")

    def test_legacy_inputs_without_dependency_metadata_are_unknown(self):
        result = self.module.evaluate_health_dimensions(
            [],
            generated_at=dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc),
            diagnostic_evidence={"status": "missing", "items": []},
        )

        self.assertEqual(result["technical_condition"]["state"], "unknown")
        self.assertEqual(result["attribution"]["domain"], "unknown")
        self.assertEqual(result["dependency_groups"], [])

    def test_semantic_health_dimensions_includes_additive_impact_v2_states(self):
        result = self.evaluate_fixture("nextdns_anycast_primary_sydney_active_secondary")
        semantic = self.module.semantic_health_dimensions(result)

        self.assertEqual(semantic["estimated_user_impact"], "low")
        self.assertEqual(semantic["observed_user_impact"], "none_reported")


if __name__ == "__main__":
    unittest.main()
