import datetime as dt
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
