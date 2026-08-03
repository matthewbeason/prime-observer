import datetime as dt
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "investigation_model.py"


def load_module():
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location("investigation_model", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InvestigationModelTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.base = dt.datetime(2026, 7, 17, 22, 0, tzinfo=dt.timezone.utc)

    def row(self, minute, host="45.90.30.134", target_class="resolver_probe", p95=30, raw=False, sustained=False):
        ts = self.base + dt.timedelta(minutes=minute)
        return {
            "ts": ts.isoformat(),
            "phase_label": "fiber",
            "host": host,
            "target_label": host,
            "target_class": target_class,
            "p95_ms": str(p95),
            "jitter_ms": "5",
            "loss_pct": "0",
            "raw_bad": raw,
            "is_bad": sustained,
        }

    def build(self, rows, generated_minute=60):
        return self.module.build_automatic_investigation(
            rows_out=rows,
            generated_at=self.base + dt.timedelta(minutes=generated_minute),
            wan_series_marked=[
                {
                    "t": self.module.parse_ts(row["ts"]),
                    "host": row["host"],
                    "target_class": row["target_class"],
                    "raw_bad": row.get("raw_bad", False),
                    "is_bad": row.get("is_bad", False),
                }
                for row in rows
                if row["target_class"] != "gateway_probe"
            ],
            observations_projection={"observations": []},
        )

    def history_args(self, rows, generated_minute=60):
        return {
            "rows_out": rows,
            "generated_at": self.base + dt.timedelta(minutes=generated_minute),
            "wan_series_marked": [
                {
                    "t": self.module.parse_ts(row["ts"]),
                    "host": row["host"],
                    "target_class": row["target_class"],
                    "raw_bad": row.get("raw_bad", False),
                    "is_bad": row.get("is_bad", False),
                }
                for row in rows
                if row["target_class"] != "gateway_probe"
            ],
            "observations_projection": {"observations": []},
        }

    def test_isolated_excursion_does_not_create_confirmed_incident(self):
        payload = self.build([self.row(0), self.row(1, p95=180, raw=True), self.row(2)])

        self.assertIsNone(payload["selected_event"])
        self.assertEqual(payload["artifact_state"]["label"], "No sustained incident")
        self.assertEqual(payload["message"], "No sustained network incident is present in the available evidence.")
        self.assertEqual(payload["secondary_context"][0]["assessment_code"], "isolated_excursion")

    def test_sustained_samples_create_active_event(self):
        payload = self.build([
            self.row(0),
            self.row(1, p95=180, raw=True),
            self.row(2, p95=181, raw=True, sustained=True),
        ])

        event = payload["selected_event"]
        self.assertEqual(event["lifecycle_state"], "active")
        self.assertEqual(event["first_anomalous_at"], (self.base + dt.timedelta(minutes=1)).isoformat())
        self.assertEqual(event["confirmed_at"], (self.base + dt.timedelta(minutes=2)).isoformat())
        self.assertIsNone(event["recovered_at"])

    def test_adaptive_metadata_does_not_change_incident_lifecycle(self):
        rows = [
            self.row(0),
            self.row(1, p95=180, raw=True),
            self.row(2, p95=181, raw=True, sustained=True),
        ]
        without_adaptive = self.build(rows)
        with_adaptive = self.module.build_automatic_investigation(
            rows_out=rows,
            generated_at=self.base + dt.timedelta(minutes=60),
            wan_series_marked=[
                {
                    "t": self.module.parse_ts(row["ts"]),
                    "host": row["host"],
                    "target_class": row["target_class"],
                    "raw_bad": row.get("raw_bad", False),
                    "is_bad": row.get("is_bad", False),
                }
                for row in rows
                if row["target_class"] != "gateway_probe"
            ],
            health_dimensions={"adaptive_baseline": {"resolver_members": [{"member_id": "nextdns_secondary", "incident_eligible": False}]}},
            observations_projection={"observations": []},
        )

        self.assertEqual(with_adaptive["selected_event"], without_adaptive["selected_event"])
        self.assertEqual(with_adaptive["recovery_progress"], without_adaptive["recovery_progress"])

    def test_incident_record_title_and_narrative_are_deterministic(self):
        rows = [
            self.row(0, host="192.168.1.1", target_class="gateway_probe"),
            self.row(0, host="1.1.1.1", target_class="internet_probe"),
            self.row(1, p95=180, raw=True),
            self.row(2, p95=181, raw=True, sustained=True),
        ]
        health_dimensions = {
            "application_experience": {"is_current": True, "failure_counts": {"total": 0}},
            "estimated_user_impact": {"state": "low"},
            "observed_user_impact": {"state": "none_reported"},
            "attribution": {
                "domain": "resolver_provider_path",
                "confidence": "medium",
                "evidence_for": ["resolver probes degraded while internet probes and gateway were healthy"],
            },
        }

        payload = self.module.build_automatic_investigation(
            rows_out=rows,
            generated_at=self.base + dt.timedelta(minutes=10),
            wan_series_marked=[
                {
                    "t": self.module.parse_ts(row["ts"]),
                    "host": row["host"],
                    "target_class": row["target_class"],
                    "raw_bad": row.get("raw_bad", False),
                    "is_bad": row.get("is_bad", False),
                }
                for row in rows
                if row["target_class"] != "gateway_probe"
            ],
            health_dimensions=health_dimensions,
            observations_projection={"observations": []},
        )

        record = payload["incident_record"]
        self.assertEqual(record["title"], "Resolver path degradation")
        self.assertEqual(record["incident_type"], "resolver_path_degradation")
        self.assertEqual(record["incident_id"], payload["selected_event"]["id"])
        self.assertIn("Prime Observer detected sustained degradation on resolver probes", record["narrative"])
        self.assertIn("DNS and HTTPS checks continued to succeed", record["narrative"])
        self.assertIn("dns provider path", record["narrative"].lower())
        self.assertEqual(record["likely_issue"], "DNS provider path")
        self.assertTrue(record["evidence_refs"])
        self.assertIn("incident_phases", payload)
        self.assertIn(payload["incident_phases"]["before"]["headline"], record["narrative"])
        self.assertIn(payload["incident_phases"]["during"]["headline"], record["narrative"])

    def test_incident_record_uses_unknown_for_unsupported_likely_issue(self):
        payload = self.module.build_automatic_investigation(
            rows_out=[self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)],
            generated_at=self.base + dt.timedelta(minutes=10),
            wan_series_marked=[
                {"t": self.base, "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": True, "is_bad": False},
                {"t": self.base + dt.timedelta(minutes=1), "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": True, "is_bad": True},
            ],
            health_dimensions={"attribution": {"domain": "unknown", "confidence": "low", "evidence_for": []}},
            observations_projection={"observations": []},
        )

        record = payload["incident_record"]
        self.assertEqual(record["likely_issue"], "Unknown")
        self.assertIn("not yet localized", record["narrative"])

    def test_incident_phases_show_healthy_before_degraded_during_recovered_after(self):
        rows = [self.row(0), self.row(1), self.row(2, p95=180, raw=True), self.row(3, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (4, 5, 12, 19))

        payload = self.build(rows)
        phases = payload["incident_phases"]

        self.assertEqual(phases["before"]["status"], "healthy")
        self.assertEqual(phases["during"]["status"], "degraded")
        self.assertEqual(phases["after"]["status"], "recovery_confirmed")
        self.assertIn("returned to healthy samples", phases["after"]["summary"])

    def test_incident_phases_do_not_call_elevated_before_healthy(self):
        payload = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1),
            self.row(2, p95=180, raw=True),
            self.row(3, p95=181, raw=True, sustained=True),
        ])

        before = payload["incident_phases"]["before"]
        self.assertEqual(before["status"], "elevated")
        self.assertIn("not treated as a healthy baseline", before["summary"])

    def test_incident_phases_explain_limited_pre_incident_samples(self):
        payload = self.build([self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)])

        before = payload["incident_phases"]["before"]
        self.assertEqual(before["status"], "limited")
        self.assertIn("Pre-incident comparison is limited.", before["limitations"])
        self.assertIn("Only 1 pre-incident", before["summary"])

    def test_active_incident_has_no_after_phase_without_recovery(self):
        payload = self.build([self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)])

        self.assertEqual(payload["selected_event"]["lifecycle_state"], "active")
        self.assertNotIn("after", payload["incident_phases"])
        self.assertIn("Recovery has not started.", payload["incident_record"]["narrative"])

    def test_recovery_candidate_phase_is_not_confirmed(self):
        payload = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
        ])

        after = payload["incident_phases"]["after"]
        self.assertEqual(after["status"], "recovery_candidate")
        self.assertIn("persistence has not been satisfied", after["summary"])
        self.assertIn("Recovery has not been confirmed.", after["limitations"])

    def test_recovery_started_phase_reports_remaining_confirmation_time(self):
        payload = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
            self.row(3),
        ])

        after = payload["incident_phases"]["after"]
        self.assertEqual(after["status"], "recovery_started")
        self.assertGreater(after["remaining_stable_seconds"], 0)
        self.assertIn("remain before confirmation", after["summary"])

    def test_representative_metrics_differ_from_maximum_excursions(self):
        payload = self.build([
            self.row(0),
            self.row(1),
            self.row(2, p95=180, raw=True),
            self.row(3, p95=260, raw=True, sustained=True),
            self.row(4, p95=181, raw=True, sustained=True),
        ])

        during = payload["incident_phases"]["during"]
        self.assertNotEqual(during["representative_metrics"]["typical_p95_ms"], during["maximum_excursions"]["max_p95_ms"])

    def test_incident_phases_preserve_healthy_comparisons(self):
        payload = self.build([
            self.row(0, host="1.1.1.1", target_class="internet_probe"),
            self.row(1, host="1.1.1.1", target_class="internet_probe"),
            self.row(0),
            self.row(1),
            self.row(2, p95=180, raw=True),
            self.row(2, host="1.1.1.1", target_class="internet_probe"),
            self.row(3, p95=181, raw=True, sustained=True),
            self.row(3, host="1.1.1.1", target_class="internet_probe"),
        ])

        self.assertIn("Internet probes", payload["incident_phases"]["during"]["healthy_comparisons"])

    def test_incident_replay_orders_supported_milestones(self):
        rows = [self.row(0), self.row(1), self.row(2, p95=180, raw=True), self.row(3, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (4, 5, 12, 19))

        payload = self.build(rows)
        milestones = payload["incident_replay"]["milestones"]
        states = [item["state"] for item in milestones]
        timestamps = [item["timestamp"] for item in milestones]

        self.assertEqual(timestamps, sorted(timestamps))
        self.assertIn("first_anomaly", states)
        self.assertIn("persistence_confirmed", states)
        self.assertIn("recovery_candidate", states)
        self.assertIn("recovery_started", states)
        self.assertEqual(states[-1], "recovery_confirmed")

    def test_incident_replay_omits_unsupported_recovery_milestones(self):
        payload = self.build([self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)])

        states = [item["state"] for item in payload["incident_replay"]["milestones"]]
        self.assertIn("first_anomaly", states)
        self.assertIn("persistence_confirmed", states)
        self.assertNotIn("recovery_candidate", states)
        self.assertNotIn("recovery_started", states)
        self.assertNotIn("recovery_confirmed", states)

    def test_incident_replay_unresolved_incident_ends_with_current_known_state(self):
        payload = self.build([self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)])

        last = payload["incident_replay"]["milestones"][-1]
        self.assertIn(last["state"], {"persistence_confirmed", "affected_scope_changed"})
        self.assertNotEqual(last["state"], "recovery_confirmed")

    def test_incident_replay_application_status_milestone(self):
        payload = self.module.build_automatic_investigation(
            rows_out=[self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)],
            generated_at=self.base + dt.timedelta(minutes=10),
            wan_series_marked=[
                {"t": self.base, "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": False, "is_bad": False},
                {"t": self.base + dt.timedelta(minutes=1), "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": True, "is_bad": False},
                {"t": self.base + dt.timedelta(minutes=2), "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": True, "is_bad": True},
            ],
            health_dimensions={"application_experience": {"is_current": True, "generated_at": (self.base + dt.timedelta(minutes=4)).isoformat(), "failure_counts": {"total": 0}}},
            observations_projection={"observations": []},
        )

        states = [item["state"] for item in payload["incident_replay"]["milestones"]]
        self.assertIn("application_status_changed", states)

    def test_incident_replay_operator_feedback_milestone(self):
        feedback_time = (self.base + dt.timedelta(minutes=5)).isoformat()
        payload = self.module.build_automatic_investigation(
            rows_out=[self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)],
            generated_at=self.base + dt.timedelta(minutes=10),
            wan_series_marked=[
                {"t": self.base, "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": False, "is_bad": False},
                {"t": self.base + dt.timedelta(minutes=1), "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": True, "is_bad": False},
                {"t": self.base + dt.timedelta(minutes=2), "host": "45.90.30.134", "target_class": "resolver_probe", "raw_bad": True, "is_bad": True},
            ],
            health_dimensions={"operator_impact_feedback": {"is_current": True, "impact_state": "minor_slowness", "note": "Video calls were choppy.", "observed_at": feedback_time}},
            observations_projection={"observations": []},
        )

        feedback = [item for item in payload["incident_replay"]["milestones"] if item["state"] == "operator_feedback_added"]
        self.assertEqual(len(feedback), 1)
        self.assertIn("video calls", feedback[0]["summary"].lower())

    def test_incident_replay_external_context_milestone(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_viz_dir = self.module.VIZ_DIR
            self.module.VIZ_DIR = Path(tmp) / "viz"
            self.module.VIZ_DIR.mkdir()
            try:
                (self.module.VIZ_DIR / "internet_conditions.json").write_text(json.dumps({
                    "available": True,
                    "status": "disruption",
                    "generated_at": (self.base + dt.timedelta(minutes=4)).isoformat(),
                    "summary": "Ongoing Cox routing disruption reported.",
                    "items": [],
                }))
                payload = self.build([self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)])
            finally:
                self.module.VIZ_DIR = old_viz_dir

        external = [item for item in payload["incident_replay"]["milestones"] if item["state"] == "external_context_changed"]
        self.assertEqual(len(external), 1)
        self.assertIn("routing disruption", external[0]["summary"])

    def test_incident_replay_milestones_include_evidence_refs_and_metrics(self):
        payload = self.build([self.row(0), self.row(1, p95=180, raw=True), self.row(2, p95=181, raw=True, sustained=True)])

        for milestone in payload["incident_replay"]["milestones"]:
            self.assertTrue(milestone["evidence_refs"])
            self.assertIn("metrics_snapshot", milestone)
            self.assertIn("representative_metrics", milestone["metrics_snapshot"])

    def test_baseline_ends_before_first_anomaly(self):
        payload = self.build([
            self.row(0),
            self.row(1),
            self.row(2, p95=180, raw=True),
            self.row(3, p95=181, raw=True, sustained=True),
        ])

        baseline = payload["windows"]["baseline"]
        first = self.module.parse_ts(payload["selected_event"]["first_anomalous_at"])
        self.assertLess(self.module.parse_ts(baseline["end"]), first)
        self.assertEqual(payload["periods"]["before"]["wan"]["sustained_bad_count"], 0)

    def test_active_event_has_no_completed_recovery_after_one_healthy_sample(self):
        payload = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
        ])

        event = payload["selected_event"]
        self.assertEqual(event["lifecycle_state"], "active")
        self.assertIsNotNone(event["recovery_candidate_at"])
        self.assertIsNone(event["recovery_started_at"])
        self.assertIsNone(event["recovered_at"])

    def test_stable_persistence_enters_recovering_state(self):
        payload = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
            self.row(3),
        ])

        event = payload["selected_event"]
        self.assertEqual(event["lifecycle_state"], "recovering")
        self.assertIsNotNone(event["recovery_started_at"])
        self.assertIsNone(event["recovered_at"])

    def test_sufficient_stable_window_completes_event(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 10, 17))

        payload = self.build(rows)

        event = payload["selected_event"]
        self.assertEqual(event["lifecycle_state"], "complete")
        self.assertIsNotNone(event["recovered_at"])
        self.assertEqual(payload["artifact_state"]["label"], "Completed investigation")

    def test_automatic_investigation_copies_internet_conditions_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_viz_dir = self.module.VIZ_DIR
            self.module.VIZ_DIR = Path(tmp) / "viz"
            self.module.VIZ_DIR.mkdir()
            try:
                (self.module.VIZ_DIR / "internet_conditions.json").write_text(json.dumps({
                    "schema_version": 2,
                    "model_version": "internet_conditions_v2",
                    "generated_at": self.base.isoformat().replace("+00:00", "Z"),
                    "provider": "cloudflare_radar",
                    "status": "normal",
                    "summary": "No Cox traffic anomaly or Cox-involved route leak detected in the last 7d. Broad US outage context also normal.",
                    "query_mode": "asn",
                    "query_target_label": "Cox",
                    "query_target_id": "AS22773",
                    "provider_display_name": "Cox",
                    "fallback_used": False,
                    "checked_window": {"date_range": "7d", "recent_window_hours": 24},
                    "degradation": {"partial": False, "unavailable_signals": []},
                    "limitations": ["Cloudflare Radar normal results do not prove a measured local ISP path is healthy."],
                    "scope": {"country": None, "region": None, "label": "Cox network context"},
                    "signals_checked": ["AS traffic anomalies", "BGP route leaks involving configured AS", "US outages", "US traffic anomalies"],
                    "signal_results": {
                        "as_traffic_anomalies": {
                            "key": "as_traffic_anomalies",
                            "label": "Cox traffic anomalies",
                            "available": True,
                            "status": "normal",
                            "item_count": 0,
                            "summary": "No Cox traffic anomalies detected.",
                            "latest_signal_at": None,
                            "query": {"asn": 22773, "dateRange": "7d", "type": "AS"},
                            "items": [],
                        },
                    },
                    "items": [],
                }))

                payload = self.build([
                    self.row(0, p95=180, raw=True),
                    self.row(1, p95=181, raw=True, sustained=True),
                    self.row(2),
                    self.row(3),
                    self.row(10),
                    self.row(17),
                ])

                context = payload["internet_conditions_context"]
                self.assertTrue(context["available"])
                self.assertEqual(context["provider"], "cloudflare_radar")
                self.assertEqual(context["model_version"], "internet_conditions_v2")
                self.assertEqual(context["summary"], "No Cox traffic anomaly or Cox-involved route leak detected in the last 7d. Broad US outage context also normal.")
                self.assertIn("as_traffic_anomalies", context["signal_results"])
                self.assertNotIn("internet_conditions_context", self.module.event_semantic_payload(payload))
            finally:
                self.module.VIZ_DIR = old_viz_dir

    def test_renewed_anomaly_cancels_recovery_and_continues_event(self):
        payload = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
            self.row(3),
            self.row(4, p95=182, raw=True, sustained=True),
        ])

        event = payload["selected_event"]
        self.assertEqual(event["lifecycle_state"], "active")
        self.assertEqual(event["last_anomalous_at"], (self.base + dt.timedelta(minutes=4)).isoformat())
        self.assertIsNone(event["recovery_candidate_at"])

    def test_latest_active_event_selected_over_older_complete_event(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 17))
        rows.extend([self.row(30, p95=182, raw=True), self.row(31, p95=183, raw=True, sustained=True)])

        payload = self.build(rows)

        self.assertIn(payload["selected_event"]["lifecycle_state"], {"active", "recovering"})
        self.assertIsNone(payload["selected_event"]["recovered_at"])
        self.assertEqual(payload["selected_event"]["first_anomalous_at"], (self.base + dt.timedelta(minutes=30)).isoformat())

    def test_completed_event_can_be_current_without_stale(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 17))
        payload = self.build(rows)

        self.assertEqual(payload["selected_event"]["lifecycle_state"], "complete")
        self.assertFalse(payload["artifact_state"]["is_stale"])
        self.assertTrue(payload["artifact_state"]["is_current"])

    def test_july_6_regression_bucket_semantics_do_not_pollute_baseline(self):
        rows = [self.row(0), self.row(1, p95=158, raw=True), self.row(2)]
        for minute in range(10, 44):
            raw = minute in {10, 12, 14, 16, 18, 20, 22, 35, 36}
            sustained = minute in {12, 14, 16, 18, 36}
            rows.append(self.row(minute, p95=172 if raw else 35, raw=raw, sustained=sustained))

        payload = self.build(rows)

        self.assertEqual(payload["secondary_context"][0]["assessment_code"], "isolated_excursion")
        self.assertIn(payload["selected_event"]["lifecycle_state"], {"active", "recovering"})
        self.assertIsNone(payload["selected_event"]["recovered_at"])
        self.assertEqual(payload["windows"]["degradation"]["assessment_code"], "sustained_degradation")
        self.assertEqual(payload["periods"]["before"]["wan"]["sustained_bad_count"], 0)
        self.assertGreaterEqual(payload["periods"]["during"]["wan"]["sustained_bad_count"], 4)

    def test_representative_timeline_metric_does_not_use_isolated_baseline_maximum(self):
        rows = [
            self.row(0, p95=35),
            self.row(1, p95=348, raw=True),
            self.row(2, p95=36),
            self.row(10, p95=176, raw=True),
            self.row(11, p95=177, raw=True, sustained=True),
            self.row(12, p95=175, raw=True, sustained=True),
        ]

        payload = self.build(rows)
        baseline = payload["timeline"][0]["phase_summary"]
        degradation = payload["timeline"][1]["phase_summary"]

        self.assertEqual(payload["windows"]["baseline"]["assessment_code"], "stable_baseline")
        self.assertEqual(baseline["sustained_bad_count"], 0)
        self.assertGreater(baseline["max_p95_ms"], degradation["max_p95_ms"])
        self.assertLess(baseline["typical_p95_ms"], degradation["typical_p95_ms"])
        self.assertGreater(degradation["sustained_bad_count"], 0)
        self.assertIn("operator_brief", payload)
        self.assertIn("evidence_buckets", payload)

    def test_new_telemetry_same_active_event_rewrites_without_assistant_semantic_change(self):
        rows = [
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
        ]
        first = self.build(rows, generated_minute=10)
        second = self.build(rows + [self.row(5, host="192.168.1.1", target_class="gateway_probe")], generated_minute=11)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "investigation.json"
            first_result = self.module.write_if_changed(path, first)
            second_result = self.module.write_if_changed(path, second)

        self.assertTrue(first_result["artifact_written"])
        self.assertTrue(second_result["artifact_written"])
        self.assertFalse(second_result["assistant_semantic_changed"])
        self.assertNotEqual(first["freshness"]["telemetry_latest_at"], second["freshness"]["telemetry_latest_at"])
        self.assertEqual(first["provenance"]["event_semantic_hash"], second["provenance"]["event_semantic_hash"])

    def test_healthy_telemetry_during_recovery_advances_progress_without_semantic_change(self):
        rows = [
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
            self.row(3),
        ]
        first = self.build(rows, generated_minute=10)
        second = self.build(rows + [self.row(6)], generated_minute=11)

        self.assertEqual(first["selected_event"]["lifecycle_state"], "recovering")
        self.assertEqual(second["selected_event"]["lifecycle_state"], "recovering")
        self.assertGreater(second["recovery_progress"]["healthy_observation_seconds"], first["recovery_progress"]["healthy_observation_seconds"])
        self.assertEqual(first["provenance"]["event_semantic_hash"], second["provenance"]["event_semantic_hash"])

    def test_crossing_stable_window_changes_lifecycle_and_assistant_semantics(self):
        recovering = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
            self.row(3),
        ], generated_minute=10)
        complete = self.build([
            self.row(0, p95=180, raw=True),
            self.row(1, p95=181, raw=True, sustained=True),
            self.row(2),
            self.row(3),
            self.row(17),
        ], generated_minute=18)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "investigation.json"
            self.module.write_if_changed(path, recovering)
            result = self.module.write_if_changed(path, complete)

        self.assertEqual(recovering["selected_event"]["lifecycle_state"], "recovering")
        self.assertEqual(complete["selected_event"]["lifecycle_state"], "complete")
        self.assertIsNotNone(complete["selected_event"]["recovered_at"])
        self.assertTrue(result["assistant_semantic_changed"])

    def test_no_newer_telemetry_does_not_rewrite_or_churn_generated_at(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        first = self.build(rows, generated_minute=10)
        second = self.build(rows, generated_minute=11)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "investigation.json"
            self.module.write_if_changed(path, first)
            result = self.module.write_if_changed(path, second)
            written = self.module.load_json(path)

        self.assertFalse(result["artifact_written"])
        self.assertEqual(written["generated_at"], first["generated_at"])

    def test_stale_state_detected_against_newer_telemetry(self):
        payload = self.build([self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)])
        stale = self.module.mark_stale_against_telemetry(payload, self.base + dt.timedelta(minutes=30))

        self.assertTrue(stale["artifact_state"]["is_stale"])
        self.assertEqual(stale["artifact_state"]["label"], "Stale investigation")

    def test_completed_snapshot_is_written_once_and_never_mutated(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 17))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            investigations = root / "investigations"
            catalog = root / "investigation_catalog.json"
            first = self.module.write_completed_investigation_history(
                **self.history_args(rows), investigations_dir=investigations, catalog_path=catalog
            )
            snapshot_path = investigations / f"{first['snapshots_written'][0]}.json"
            original = snapshot_path.read_bytes()

            changed_rows = list(rows) + [self.row(18, p95=400, raw=True, sustained=True)]
            second = self.module.write_completed_investigation_history(
                **self.history_args(changed_rows, generated_minute=90),
                investigations_dir=investigations,
                catalog_path=catalog,
            )

            self.assertEqual(snapshot_path.read_bytes(), original)
            self.assertEqual(second["snapshots_written"], [])
            snapshot = self.module.load_json(snapshot_path)
            self.assertEqual(snapshot["artifact_type"], "completed_investigation_snapshot")
            self.assertTrue(snapshot["immutable"])
            self.assertIn("snapshot_written_at", snapshot)
            self.assertEqual(snapshot["generator"], self.module.INVESTIGATION_GENERATOR)
            self.assertTrue(snapshot["artifact_state"]["is_historical"])
            self.assertEqual(snapshot["selected_event"]["lifecycle_state"], "complete")

    def test_snapshot_publication_leaves_complete_json_and_no_temp_files(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 17))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.module.write_completed_investigation_history(
                **self.history_args(rows),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )
            snapshot_path = root / "investigations" / f"{result['snapshots_written'][0]}.json"
            snapshot = json.loads(snapshot_path.read_text())
            leftovers = list((root / "investigations").glob("*.tmp"))

        self.assertEqual(snapshot["artifact_type"], "completed_investigation_snapshot")
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(leftovers, [])

    def test_valid_existing_snapshot_is_never_replaced(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 17))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.module.write_completed_investigation_history(
                **self.history_args(rows),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )
            snapshot_path = root / "investigations" / f"{first['snapshots_written'][0]}.json"
            before_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
            second = self.module.write_completed_investigation_history(
                **self.history_args(rows, generated_minute=120),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )
            after_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()

        self.assertEqual(second["snapshots_written"], [])
        self.assertEqual(after_hash, before_hash)

    def test_malformed_existing_snapshot_is_reported_and_not_overwritten(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 17))
        event_id = "event-resolver-probe-2026-07-17t22-00-00z"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            investigations = root / "investigations"
            investigations.mkdir()
            snapshot_path = investigations / f"{event_id}.json"
            snapshot_path.write_text("{not valid json")
            result = self.module.write_completed_investigation_history(
                **self.history_args(rows),
                investigations_dir=investigations,
                catalog_path=root / "investigation_catalog.json",
            )

            self.assertEqual(snapshot_path.read_text(), "{not valid json")

        self.assertEqual(result["snapshots_written"], [])
        self.assertEqual(result["snapshot_count"], 0)
        self.assertEqual(result["invalid_snapshots"][0]["event_id"], event_id)
        self.assertEqual(result["invalid_snapshots"][0]["error_type"], "malformed_json")
        self.assertEqual(result["catalog"]["events"], [])
        self.assertEqual(result["catalog"]["invalid_snapshots"][0]["event_id"], event_id)

    def test_valid_snapshots_still_catalog_when_malformed_snapshots_coexist(self):
        first_rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        first_rows.extend(self.row(minute) for minute in (2, 3, 17))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            investigations = root / "investigations"
            first = self.module.write_completed_investigation_history(
                **self.history_args(first_rows),
                investigations_dir=investigations,
                catalog_path=root / "investigation_catalog.json",
            )
            (investigations / "event-bad.json").write_text("[]")
            catalog = self.module.build_investigation_catalog(investigations, self.base)

        self.assertEqual(len(catalog["events"]), 1)
        self.assertEqual(catalog["events"][0]["event_id"], first["snapshots_written"][0])
        self.assertEqual(catalog["invalid_snapshots"][0]["event_id"], "event-bad")
        self.assertEqual(catalog["invalid_snapshots"][0]["error_type"], "structurally_invalid")

    def test_catalog_orders_completed_events_newest_first(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        rows.extend(self.row(minute) for minute in (2, 3, 17))
        rows.extend([self.row(30, p95=182, raw=True), self.row(31, p95=183, raw=True, sustained=True)])
        rows.extend(self.row(minute) for minute in (32, 33, 47))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.module.write_completed_investigation_history(
                **self.history_args(rows),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )

        events = result["catalog"]["events"]
        self.assertEqual(result["catalog"]["artifact_type"], "investigation_catalog")
        self.assertEqual(result["catalog"]["generator"], self.module.INVESTIGATION_GENERATOR)
        self.assertEqual(result["catalog"]["invalid_snapshots"], [])
        self.assertEqual(len(events), 2)
        self.assertGreater(events[0]["recovered_at"], events[1]["recovered_at"])
        self.assertEqual(
            set(events[0]),
            {
                "event_id", "lifecycle", "first_anomalous_at", "recovered_at", "severity",
                "confidence", "target_class", "affected_targets", "duration", "snapshot_path",
            },
        )

    def test_catalog_handles_missing_snapshot_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = self.module.build_investigation_catalog(Path(tmp) / "missing", self.base)

        self.assertEqual(catalog["artifact_type"], "investigation_catalog")
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["events"], [])
        self.assertEqual(catalog["invalid_snapshots"], [])

    def test_snapshot_metadata_does_not_change_event_semantic_hash(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        payload = self.build(rows)
        with_metadata = json.loads(json.dumps(payload))
        with_metadata["artifact_type"] = "current_investigation"
        with_metadata["immutable"] = False
        with_metadata["generator"] = {"name": "changed", "format_version": "changed"}
        with_metadata["snapshot_written_at"] = "2026-07-17T23:59:00+00:00"

        self.assertEqual(
            self.module.event_semantic_hash(payload),
            self.module.event_semantic_hash(with_metadata),
        )

    def test_snapshot_metadata_update_rewrites_without_assistant_regeneration(self):
        rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        payload = self.build(rows)
        existing = json.loads(json.dumps(payload))
        existing.pop("artifact_type", None)
        existing.pop("immutable", None)
        existing.pop("generator", None)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "investigation.json"
            path.write_text(json.dumps(existing, sort_keys=True))
            result = self.module.write_if_changed(path, payload)

        self.assertTrue(result["artifact_written"])
        self.assertFalse(result["assistant_semantic_changed"])

    def test_active_event_does_not_replace_completed_snapshot_or_current_selection(self):
        completed_rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        completed_rows.extend(self.row(minute) for minute in (2, 3, 17))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.module.write_completed_investigation_history(
                **self.history_args(completed_rows),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )
            snapshot_path = root / "investigations" / f"{first['snapshots_written'][0]}.json"
            original = snapshot_path.read_bytes()
            active_rows = completed_rows + [
                self.row(30, p95=190, raw=True),
                self.row(31, p95=191, raw=True, sustained=True),
            ]
            current_before = self.build(active_rows)
            result = self.module.write_completed_investigation_history(
                **self.history_args(active_rows, generated_minute=90),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )
            current_after = self.build(active_rows)

            self.assertEqual(snapshot_path.read_bytes(), original)
            self.assertEqual(len(result["catalog"]["events"]), 1)
            self.assertEqual(current_before, current_after)
            self.assertEqual(current_after["selected_event"]["lifecycle_state"], "active")

    def test_catalog_preserves_snapshots_when_completed_event_ages_out(self):
        completed_rows = [self.row(0, p95=180, raw=True), self.row(1, p95=181, raw=True, sustained=True)]
        completed_rows.extend(self.row(minute) for minute in (2, 3, 17))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.module.write_completed_investigation_history(
                **self.history_args(completed_rows),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )
            active_only = [self.row(60, p95=190, raw=True), self.row(61, p95=191, raw=True, sustained=True)]
            second = self.module.write_completed_investigation_history(
                **self.history_args(active_only, generated_minute=90),
                investigations_dir=root / "investigations",
                catalog_path=root / "investigation_catalog.json",
            )

        self.assertEqual(second["catalog"]["events"], first["catalog"]["events"])


if __name__ == "__main__":
    unittest.main()
