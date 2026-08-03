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


def timed_rows(base, entries):
    rows = []
    for minute, host, p95 in entries:
        rows.append({
            "ts": (base + dt.timedelta(minutes=minute)).isoformat(),
            "phase_label": "fiber",
            "host": host,
            "p95_ms": str(p95),
            "jitter_ms": "5",
            "loss_pct": "0",
        })
    return rows


def adaptive_rows(base, *, secondary, primary=None, internet=None, gateway=None, secondary_loss=0, primary_loss=0, internet_loss=0, gateway_loss=0):
    primary = primary if primary is not None else [35 for _ in secondary]
    internet = internet if internet is not None else [30 for _ in secondary]
    gateway = gateway if gateway is not None else [8 for _ in secondary]
    rows = []
    for idx, secondary_p95 in enumerate(secondary):
        ts = base + dt.timedelta(minutes=idx * 10)
        for host, p95, loss in (
            ("192.168.1.1", gateway[min(idx, len(gateway) - 1)], gateway_loss),
            ("1.1.1.1", internet[min(idx, len(internet) - 1)], internet_loss),
            ("45.90.28.134", primary[min(idx, len(primary) - 1)], primary_loss),
            ("45.90.30.134", secondary_p95, secondary_loss),
        ):
            rows.append({
                "ts": ts.isoformat(),
                "phase_label": "fiber",
                "host": host,
                "p95_ms": str(p95),
                "jitter_ms": "5",
                "loss_pct": str(loss),
            })
    return rows


def diagnostic_payload(summary):
    return {"status": "ok", "items": summary.get("diagnostics") or []}


def app_payload(generated_at, *, primary="ok", secondary="ok", system="ok", https="ok", https_category=None, https_total=100):
    def dns(role, status):
        success = status == "ok"
        return {
            "role": role,
            "type": "system_dns" if role == "system" else "direct_dns",
            "target_hostname": "example.com",
            "resolver_endpoint": "system" if role == "system" else f"192.0.2.{10 if role == 'primary' else 11}",
            "checked_at": generated_at.isoformat().replace("+00:00", "Z"),
            "status": "ok" if success else status,
            "success": success,
            "latency_ms": 25 if success else None,
            "timeout": status == "timeout",
            "rcode": "NOERROR" if success else None,
            "failure_category": None if success else status,
        }
    https_success = https == "ok"
    evidence = []
    if system == "ok":
        evidence.append("System DNS queries are succeeding normally.")
    if secondary == "timeout":
        evidence.append("Direct secondary resolver queries are timing out.")
    if https_success:
        evidence.append("HTTPS session establishment remains normal.")
    else:
        evidence.append("HTTPS transaction failed.")
    return {
        "schema_version": 1,
        "model_version": "prime_observer.application_experience.v1",
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "status": "ok" if all(item == "ok" for item in (primary, secondary, system, https)) else "degraded",
        "freshness": "fresh",
        "is_current": True,
        "dns_transactions": [dns("primary", primary), dns("secondary", secondary), dns("system", system)],
        "https_transaction": {
            "target_url": "https://example.com/status",
            "checked_at": generated_at.isoformat().replace("+00:00", "Z"),
            "status": "ok" if https_success else "failed",
            "success": https_success,
            "http_status": 204 if https_success else None,
            "timeout": https == "timeout",
            "failure_category": None if https_success else (https_category or https),
            "total_duration_ms": https_total,
        },
        "failure_counts": {"total": len([item for item in (primary, secondary, system, https) if item != "ok"])},
        "latency_summaries": {},
        "evidence": evidence,
        "limitations": [],
    }


def feedback_payload(generated_at, incident_id="event-current", impact="none_observed", note=""):
    return {
        "schema_version": 1,
        "model_version": "prime_observer.operator_impact_feedback.v1",
        "status": "ok",
        "incident_id": incident_id,
        "current_incident_id": "event-current",
        "observed_at": generated_at.isoformat().replace("+00:00", "Z"),
        "impact_state": impact,
        "note": note,
        "source": "operator",
        "freshness": "fresh",
        "is_current": True,
        "association": "current_incident",
        "limitations": [],
    }


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

    def secondary_adaptive_baseline(self, result):
        members = result["adaptive_baseline"]["resolver_members"]
        return next(item for item in members if item["member_id"] == "nextdns_secondary")

    def evaluate_adaptive(self, *, secondary, primary=None, internet=None, gateway=None, app_secondary="ok", secondary_loss=0, primary_loss=0, internet_loss=0, gateway_loss=0, feedback="none_observed"):
        generated_at = dt.datetime(2026, 7, 21, 14, 0, tzinfo=dt.timezone.utc)
        base = generated_at - dt.timedelta(hours=3)
        return self.module.evaluate_health_dimensions(
            adaptive_rows(base, secondary=secondary, primary=primary, internet=internet, gateway=gateway, secondary_loss=secondary_loss, primary_loss=primary_loss, internet_loss=internet_loss, gateway_loss=gateway_loss),
            generated_at=generated_at,
            application_experience=app_payload(generated_at, secondary=app_secondary),
            operator_impact_feedback=feedback_payload(generated_at, impact=feedback),
            diagnostic_evidence={"status": "ok", "items": []},
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

    def test_stable_elevated_secondary_resolver_is_adaptive_degraded_baseline(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176])

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertEqual(adaptive["absolute_threshold_state"], "breached")
        self.assertEqual(adaptive["baseline_state"], "elevated_but_stable")
        self.assertFalse(adaptive["incident_eligible"])
        self.assertEqual(adaptive["incident_suppression_reason"], "established_degraded_baseline")
        self.assertEqual(adaptive["guardrail_breaches"], [])
        self.assertTrue(adaptive["evidence_window"]["direct_dns_success"])
        self.assertTrue(adaptive["evidence_window"]["system_dns_success"])
        self.assertTrue(adaptive["evidence_window"]["https_success"])

    def test_sudden_worsening_from_elevated_baseline_is_eligible(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 248, 252, 255])

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn("rapid_worsening", adaptive["guardrail_breaches"])

    def test_packet_loss_keeps_stable_elevated_resolver_eligible(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176], secondary_loss=3)

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn("packet_loss_above_threshold", adaptive["guardrail_breaches"])

    def test_timeout_keeps_stable_elevated_resolver_eligible(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176], app_secondary="timeout")

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn("timeout", adaptive["guardrail_breaches"])

    def test_direct_dns_failure_keeps_stable_elevated_resolver_eligible(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176], app_secondary="failed")

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn("dns_failure", adaptive["guardrail_breaches"])

    def test_both_resolver_members_degraded_remain_eligible(self):
        result = self.evaluate_adaptive(
            primary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176],
            secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176],
        )

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn("both_resolver_members_degraded", adaptive["guardrail_breaches"])

    def test_gateway_failure_keeps_stable_elevated_resolver_eligible(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176], gateway=[160] * 12)

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn("gateway_degradation", adaptive["guardrail_breaches"])

    def test_broad_correlated_degradation_keeps_stable_elevated_resolver_eligible(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172, 175, 178, 173, 176, 174, 177, 175, 176], internet=[190] * 12)

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn("broad_correlated_resolver_and_internet_degradation", adaptive["guardrail_breaches"])

    def test_too_little_data_blocks_adaptive_baseline_learning(self):
        result = self.evaluate_adaptive(secondary=[170, 174, 176, 172])

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertEqual(adaptive["baseline_state"], "unknown")
        self.assertEqual(adaptive["incident_suppression_reason"], "insufficient_baseline_evidence")

    def test_worsening_trend_cannot_be_normalized(self):
        result = self.evaluate_adaptive(secondary=[150, 152, 153, 151, 154, 153, 152, 154, 153, 210, 215, 220])

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertEqual(adaptive["baseline_state"], "degraded_from_baseline")
        self.assertTrue(adaptive["deviation_from_baseline"]["worsening_trend"])
        self.assertTrue(adaptive["incident_eligible"])

    def test_improving_elevated_condition_is_marked_recovering_metadata_only(self):
        result = self.evaluate_adaptive(secondary=[220, 224, 226, 222, 225, 228, 223, 226, 224, 138, 140, 139])

        adaptive = self.secondary_adaptive_baseline(result)
        self.assertEqual(adaptive["baseline_state"], "recovering")
        self.assertTrue(adaptive["deviation_from_baseline"]["improving_trend"])
        self.assertTrue(adaptive["incident_eligible"])

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

    def test_current_condition_can_be_healthy_while_rolling_condition_remains_severe(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        base = generated_at - dt.timedelta(minutes=40)
        rows = timed_rows(base, [
            (0, "45.90.30.134", 260),
            (1, "45.90.30.134", 270),
            (25, "192.168.1.1", 8),
            (25, "1.1.1.1", 25),
            (25, "45.90.28.134", 35),
            (25, "45.90.30.134", 36),
            (26, "192.168.1.1", 8),
            (26, "1.1.1.1", 25),
            (26, "45.90.28.134", 35),
            (26, "45.90.30.134", 36),
        ])

        result = self.module.evaluate_health_dimensions(
            rows,
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at),
        )

        self.assertEqual(result["technical_condition"]["state"], "severe")
        self.assertEqual(result["rolling_condition"]["state"], "severe")
        self.assertEqual(result["current_condition"]["state"], "healthy")
        self.assertEqual(result["current_condition"]["window"]["minutes"], self.module.ATTRIBUTION_CUT_MINUTES)
        self.assertTrue(result["application_experience"]["is_current"])

    def test_current_probe_failure_is_not_hidden_by_healthy_application_checks(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        base = generated_at - dt.timedelta(minutes=5)
        rows = timed_rows(base, [
            (0, "192.168.1.1", 8),
            (0, "1.1.1.1", 25),
            (0, "45.90.28.134", 35),
            (0, "45.90.30.134", 260),
            (1, "192.168.1.1", 8),
            (1, "1.1.1.1", 25),
            (1, "45.90.28.134", 35),
            (1, "45.90.30.134", 270),
        ])

        result = self.module.evaluate_health_dimensions(
            rows,
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at),
        )

        self.assertEqual(result["current_condition"]["state"], "severe")
        self.assertEqual(result["technical_condition"]["state"], "severe")
        self.assertEqual(result["application_experience"]["failure_counts"]["total"], 0)

    def test_application_success_reduces_estimated_impact_for_direct_resolver_degradation(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[260, 280, 290], secondary=[35, 35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at),
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "low")
        self.assertIn("System DNS queries are succeeding normally.", result["estimated_user_impact"]["drivers"])
        self.assertEqual(result["observed_user_impact"]["state"], "unknown")

    def test_current_healthy_application_evidence_dampens_telemetry_only_likely_impact(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        rows = custom_rows(
            gateway=[8, 8, {"p95": 20, "loss": 2}],
            internet=[170, 180, {"p95": 190, "loss": 3}],
            primary=[260, 280, 290],
            secondary=[220, 230, 240],
        )
        baseline = self.module.evaluate_health_dimensions(
            rows,
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
        )
        result = self.module.evaluate_health_dimensions(
            rows,
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at),
        )

        self.assertEqual(baseline["estimated_user_impact"]["state"], "likely")
        self.assertEqual(result["estimated_user_impact"]["state"], "possible")
        self.assertIn("Current application checks did not reproduce user-facing failure.", result["estimated_user_impact"]["drivers"])
        self.assertEqual(result["technical_condition"], baseline["technical_condition"])
        self.assertEqual(result["attribution"], baseline["attribution"])
        def legacy_dependency(group):
            return {
                "state": group.get("state"),
                "redundancy_status": group.get("redundancy_status"),
                "active_member": group.get("active_member"),
                "fallback_status": group.get("fallback_status"),
                "members": [
                    {
                        "member_id": member.get("member_id"),
                        "role": member.get("role"),
                        "endpoint": member.get("endpoint"),
                        "provider": member.get("provider"),
                        "technical_condition": member.get("technical_condition"),
                    }
                    for member in group.get("members", [])
                ],
            }
        self.assertEqual([legacy_dependency(group) for group in result["dependency_groups"]], [legacy_dependency(group) for group in baseline["dependency_groups"]])
        self.assertEqual(result["observed_user_impact"], baseline["observed_user_impact"])

    def test_stale_healthy_application_evidence_does_not_dampen_telemetry_likely_impact(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        stale = app_payload(generated_at - dt.timedelta(seconds=self.module.APPLICATION_EXPERIENCE_FRESHNESS_SECONDS + 1))
        stale["freshness"] = {"stale_after_seconds": self.module.APPLICATION_EXPERIENCE_FRESHNESS_SECONDS}

        result = self.module.evaluate_health_dimensions(
            custom_rows(
                gateway=[8, 8, {"p95": 20, "loss": 2}],
                internet=[170, 180, {"p95": 190, "loss": 3}],
                primary=[260, 280, 290],
                secondary=[220, 230, 240],
            ),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=self.module.normalize_application_experience(stale, generated_at=generated_at),
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "likely")
        self.assertNotIn("Current application checks did not reproduce user-facing failure.", result["estimated_user_impact"]["drivers"])

    def test_application_direct_timeout_raises_estimated_impact_but_system_https_dampen(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[35, 35, 35], secondary=[260, 280, 290]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at, secondary="timeout"),
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "possible")
        self.assertIn("Direct secondary resolver queries are timing out.", result["estimated_user_impact"]["drivers"])

    def test_application_system_dns_timeout_raises_estimated_impact(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[35, 35, 35], secondary=[35, 35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at, system="timeout"),
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "likely")

    def test_application_https_failure_raises_estimated_impact(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[35, 35, 35], secondary=[35, 35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at, https="failed", https_category="tls_failure"),
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "likely")
        self.assertEqual(result["observed_user_impact"]["state"], "unknown")

    def test_application_failure_is_not_dampened_by_healthy_direct_resolver_checks(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(
                gateway=[8, 8, {"p95": 20, "loss": 2}],
                internet=[170, 180, {"p95": 190, "loss": 3}],
                primary=[260, 280, 290],
                secondary=[220, 230, 240],
            ),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at, https="failed", https_category="tls_failure"),
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "likely")
        self.assertNotIn("Current application checks did not reproduce user-facing failure.", result["estimated_user_impact"]["drivers"])

    def test_reported_and_confirmed_impact_are_not_dampened_by_healthy_application_checks(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        rows = custom_rows(gateway=[8, 8], internet=[25, 25], primary=[35, 35], secondary=[35, 35])
        reported = self.module.evaluate_health_dimensions(
            rows,
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": [{"type": "user_report", "status": "symptoms_confirmed", "freshness": "fresh"}]},
            application_experience=app_payload(generated_at),
        )
        confirmed = self.module.evaluate_health_dimensions(
            rows,
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": [{"type": "application_symptom", "status": "confirmed_service_failure", "freshness": "fresh"}]},
            application_experience=app_payload(generated_at),
        )

        self.assertEqual(reported["observed_user_impact"]["state"], "reported_major")
        self.assertEqual(reported["estimated_user_impact"]["state"], "likely")
        self.assertEqual(confirmed["observed_user_impact"]["state"], "confirmed_service_failure")
        self.assertEqual(confirmed["estimated_user_impact"]["state"], "severe")

    def test_application_broad_transaction_failure_can_be_severe(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[35, 35, 35], secondary=[35, 35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=app_payload(generated_at, primary="timeout", secondary="timeout", system="timeout", https="failed", https_category="tcp_failure"),
        )

        self.assertEqual(result["estimated_user_impact"]["state"], "severe")

    def test_application_high_latency_without_failure_remains_low(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        payload = app_payload(generated_at, https_total=1400)
        payload["evidence"].append("HTTPS transaction is slow without failure.")
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[35, 35, 35], secondary=[35, 35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            application_experience=payload,
        )

        self.assertIn(result["estimated_user_impact"]["state"], {"none_expected", "low"})

    def test_stale_malformed_and_missing_application_artifacts_do_not_affect_impact(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            missing = self.module.load_application_experience(Path(tmp) / "missing.json", generated_at=generated_at)
            malformed_path = Path(tmp) / "application_experience.json"
            malformed_path.write_text("{bad-json")
            malformed = self.module.load_application_experience(malformed_path, generated_at=generated_at)
            stale_path = Path(tmp) / "stale.json"
            stale_payload = app_payload(generated_at - dt.timedelta(minutes=30), system="timeout", https="failed")
            stale_payload["freshness"] = {"stale_after_seconds": 300}
            stale_path.write_text(json.dumps(stale_payload))
            stale = self.module.load_application_experience(stale_path, generated_at=generated_at)

        for payload in (missing, malformed, stale):
            with self.subTest(status=payload["status"]):
                result = self.module.evaluate_health_dimensions(
                    custom_rows(gateway=[8, 8, 8], internet=[25, 26, 25], primary=[35, 35, 35], secondary=[35, 35, 35]),
                    generated_at=generated_at,
                    diagnostic_evidence={"status": "ok", "items": []},
                    application_experience=payload,
                )
                self.assertEqual(result["estimated_user_impact"]["state"], "none_expected")

    def test_application_default_freshness_exceeds_scheduled_refresh_cadence(self):
        self.assertGreater(self.module.APPLICATION_EXPERIENCE_FRESHNESS_SECONDS, 1800)
        self.assertEqual(self.module.APPLICATION_EXPERIENCE_FRESHNESS_SECONDS, 2100)

    def test_legacy_inputs_without_application_experience_remain_compatible(self):
        result = self.evaluate_fixture("nextdns_anycast_primary_sydney_active_secondary")

        self.assertEqual(result["user_impact"]["state"], "not_observed")
        self.assertEqual(result["application_experience"]["status"], "missing")

    def test_operator_feedback_none_observed_sets_observed_none_only(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        baseline = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8], internet=[25, 25], primary=[260, 280, 290], secondary=[35, 35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
        )
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8], internet=[25, 25], primary=[260, 280, 290], secondary=[35, 35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            operator_impact_feedback=feedback_payload(generated_at, impact="none_observed", note="Streaming remained normal."),
        )

        self.assertEqual(result["observed_user_impact"]["state"], "none_reported")
        self.assertEqual(result["technical_condition"], baseline["technical_condition"])
        self.assertEqual(result["attribution"], baseline["attribution"])
        self.assertEqual(result["estimated_user_impact"]["state"], baseline["estimated_user_impact"]["state"])

    def test_operator_feedback_maps_supported_impact_states(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        cases = {
            "minor_slowness": "reported_minor",
            "intermittent_failures": "reported_minor",
            "major_disruption": "reported_major",
            "full_outage": "confirmed_service_failure",
        }
        for feedback_state, observed_state in cases.items():
            with self.subTest(feedback_state=feedback_state):
                result = self.module.evaluate_health_dimensions(
                    custom_rows(gateway=[8, 8], internet=[25, 25], primary=[35, 35], secondary=[35, 35]),
                    generated_at=generated_at,
                    diagnostic_evidence={"status": "ok", "items": []},
                    operator_impact_feedback=feedback_payload(generated_at, impact=feedback_state),
                )
                self.assertEqual(result["observed_user_impact"]["state"], observed_state)

    def test_operator_feedback_intermittent_major_note_maps_major(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8], internet=[25, 25], primary=[35, 35], secondary=[35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            operator_impact_feedback=feedback_payload(generated_at, impact="intermittent_failures", note="major app failures"),
        )

        self.assertEqual(result["observed_user_impact"]["state"], "reported_major")

    def test_wrong_stale_malformed_and_cleared_feedback_do_not_apply(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator_impact_feedback.json"
            path.write_text(json.dumps({**feedback_payload(generated_at, incident_id="event-other", impact="full_outage"), "current_incident_id": "event-current"}))
            wrong = self.module.load_operator_impact_feedback(path, current_incident_id="event-current", generated_at=generated_at)
            path.write_text(json.dumps(feedback_payload(generated_at - dt.timedelta(days=2), impact="full_outage")))
            stale = self.module.load_operator_impact_feedback(path, current_incident_id="event-current", generated_at=generated_at)
            path.write_text("{bad-json")
            malformed = self.module.load_operator_impact_feedback(path, current_incident_id="event-current", generated_at=generated_at)
            cleared = {**feedback_payload(generated_at, impact="full_outage"), "cleared": True}
            path.write_text(json.dumps(cleared))
            cleared_loaded = self.module.load_operator_impact_feedback(path, current_incident_id="event-current", generated_at=generated_at)

        for payload in (wrong, stale, malformed, cleared_loaded):
            with self.subTest(status=payload["status"], association=payload.get("association")):
                result = self.module.evaluate_health_dimensions(
                    custom_rows(gateway=[8, 8], internet=[25, 25], primary=[35, 35], secondary=[35, 35]),
                    generated_at=generated_at,
                    diagnostic_evidence={"status": "ok", "items": []},
                    operator_impact_feedback=payload,
                )
                self.assertEqual(result["observed_user_impact"]["state"], "unknown")

    def test_semantic_health_dimensions_includes_feedback_without_freshness_only_churn(self):
        generated_at = dt.datetime(2026, 7, 21, 13, 0, tzinfo=dt.timezone.utc)
        result = self.module.evaluate_health_dimensions(
            custom_rows(gateway=[8, 8], internet=[25, 25], primary=[35, 35], secondary=[35, 35]),
            generated_at=generated_at,
            diagnostic_evidence={"status": "ok", "items": []},
            operator_impact_feedback=feedback_payload(generated_at, impact="minor_slowness", note="work laptop slow"),
        )
        semantic = self.module.semantic_health_dimensions(result)

        self.assertEqual(semantic["operator_impact_feedback"]["impact_state"], "minor_slowness")
        self.assertEqual(semantic["operator_impact_feedback"]["note"], "work laptop slow")
        self.assertNotIn("observed_at", semantic["operator_impact_feedback"])


if __name__ == "__main__":
    unittest.main()
