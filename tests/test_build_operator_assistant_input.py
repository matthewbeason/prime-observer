import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "build_operator_assistant_input.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_operator_assistant_input", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildOperatorAssistantInputTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz_dir = self.base / "viz"
        self.viz_dir.mkdir()
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz_dir
        self.module.INVESTIGATION = self.viz_dir / "investigation.json"
        self.module.OUT = self.viz_dir / "operator_assistant_input.json"
        self.module.STATE_OUT = self.viz_dir / "operator_assistant_generation_state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_investigation(self, payload):
        self.module.INVESTIGATION.write_text(json.dumps(payload))

    def investigation_payload(self):
        return {
            "schema_version": 1,
            "generated_at": "2026-07-06T23:16:14+00:00",
            "id": "investigation-1",
            "status": "available",
            "requested_window": {
                "start": "2026-07-06T22:59:19+00:00",
                "end": "2026-07-06T23:14:19+00:00",
                "duration_minutes": 15.0,
            },
            "periods": {
                "during": {
                    "total_samples": 97,
                    "wan": {
                        "target_groups": {
                            "internet_probe": {"sample_count": 39, "raw_bad_count": 0, "sustained_bad_count": 0},
                            "resolver_probe": {"sample_count": 38, "raw_bad_count": 8, "sustained_bad_count": 2},
                        },
                    },
                    "lan": {
                        "sample_count": 20,
                        "elevated_p95_count": 0,
                        "max_p95_ms": 109.4,
                        "max_loss_pct": 0.0,
                        "target_groups": {
                            "gateway_probe": {"sample_count": 20},
                        },
                    },
                    "wan_buckets": [
                        {
                            "target_class": "internet_probe",
                            "max_p95_ms": 114.1,
                            "max_loss_pct": 0.0,
                        },
                        {
                            "target_class": "resolver_probe",
                            "max_p95_ms": 173.6,
                            "max_loss_pct": 0.0,
                        },
                    ],
                },
                "after": {"total_samples": 12},
            },
            "observation_references": [
                {
                    "id": "obs-window",
                    "type": "attribution",
                    "scope": {"view": "window_attribution"},
                    "interval": {
                        "start": "2026-07-06T07:00:01+00:00",
                        "end": "2026-07-06T23:14:19+00:00",
                    },
                    "state": {"status": "inconclusive", "label": "Inconclusive"},
                },
                {
                    "id": "obs-current",
                    "type": "attribution",
                    "scope": {"view": "current_attribution"},
                    "interval": {
                        "start": "2026-07-06T23:01:03+00:00",
                        "end": "2026-07-06T23:16:03+00:00",
                    },
                    "state": {
                        "status": "likely_upstream",
                        "label": "Likely upstream (ISP / path)",
                    },
                },
                {
                    "id": "obs-episode",
                    "type": "episode",
                    "scope": {"view": "episode", "target_class": "resolver_probe"},
                    "interval": {
                        "start": "2026-07-06T23:07:18+00:00",
                        "end": "2026-07-06T23:08:42+00:00",
                    },
                    "state": {
                        "status": "sustained_degradation",
                        "label": "Sustained degradation",
                    },
                },
            ],
            "dns_context": {
                "available": True,
                "status": "ok",
                "window": "-24h",
                "generated_at": "2026-07-06T22:47:09Z",
                "minutes_from_event_midpoint": 19.7,
                "source_file": "viz/nextdns_summary.json",
                "summary": {
                    "total_queries": 189448,
                    "blocked_queries": 7217,
                    "block_rate_pct": 3.8,
                },
            },
            "internet_conditions_context": {
                "available": True,
                "status": "normal",
                "summary": "No United States Internet outages or traffic anomalies detected.",
                "provider": "cloudflare_radar",
                "provider_display_name": "US Radar",
                "fallback_used": False,
                "generated_at": "2026-07-06T22:47:11Z",
                "minutes_from_event_midpoint": 19.6,
                "source_file": "viz/internet_conditions.json",
            },
            "power_infrastructure_context": {
                "available": True,
                "status": "normal",
                "summary": "No APS outages or PSPS events reported.",
                "generated_at": "2026-07-06T22:47:12Z",
                "minutes_from_event_midpoint": 19.5,
                "source_file": "viz/aps_power_context.json",
            },
            "provenance": {"producer": "bin/build_investigation.py"},
            "notes": [
                "Prime Observer investigation output is factual telemetry evidence, not interpretation.",
                "LAN, internet probe, and resolver probe evidence are reported separately where available.",
            ],
            "timeline_samples": [{"ts": "2026-07-06T23:00:00+00:00"}],
            "events": [{"id": "requested-1"}],
            "sources": {"telemetry_files": [{"path": "data/bakeoff_20260706.csv", "rows": 97}]},
            "thresholds": {"wan_bad_p95_ms": 140.0},
        }

    def test_successful_package_generation(self):
        self.write_investigation(self.investigation_payload())

        payload = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["semantic_schema_version"], "operator_assistant_input.v2")
        self.assertEqual(payload["investigation"]["id"], "investigation-1")
        self.assertEqual(payload["attribution"]["current"]["status"], "likely_upstream")
        self.assertEqual(payload["attribution"]["window"]["status"], "inconclusive")
        self.assertEqual(payload["episode"]["target_class"], "resolver_probe")
        self.assertEqual(payload["evidence"]["resolver"]["raw_bad_count"], 8)
        self.assertEqual(payload["environmental_context"]["dns"]["status"], "ok")
        self.assertRegex(payload["input_hash"], r"^[0-9a-f]{64}$")
        self.assertTrue(payload["observations"])
        self.assertIn("claim_boundaries", payload)
        self.assertIn("prohibited_claims", payload)
        self.assertEqual(payload["provenance"]["source_producer"], "bin/build_investigation.py")

    def test_missing_optional_context_degrades_safely(self):
        payload = self.investigation_payload()
        payload.pop("internet_conditions_context")
        payload.pop("power_infrastructure_context")
        self.write_investigation(payload)

        output = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertFalse(output["environmental_context"]["internet_conditions"]["available"])
        self.assertFalse(output["environmental_context"]["power"]["available"])
        self.assertIn("Internet Conditions context was unavailable or missing for this package.", output["limitations"])
        self.assertIn("Power context was unavailable or missing for this package.", output["limitations"])

    def test_unavailable_optional_context_is_preserved(self):
        payload = self.investigation_payload()
        payload["internet_conditions_context"]["available"] = False
        payload["internet_conditions_context"]["status"] = "unavailable"
        payload["power_infrastructure_context"]["available"] = False
        payload["power_infrastructure_context"]["status"] = "unavailable"
        self.write_investigation(payload)

        output = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertEqual(output["environmental_context"]["internet_conditions"]["status"], "unavailable")
        self.assertEqual(output["environmental_context"]["power"]["status"], "unavailable")

    def test_conflicting_current_and_window_attribution_is_preserved(self):
        self.write_investigation(self.investigation_payload())

        output = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertNotEqual(output["attribution"]["current"]["status"], output["attribution"]["window"]["status"])
        self.assertIn(
            "Current attribution and window attribution disagree and should be preserved as separate scopes.",
            output["limitations"],
        )

    def test_no_after_window_samples_is_preserved(self):
        payload = self.investigation_payload()
        payload["periods"]["after"] = {"total_samples": 0}
        self.write_investigation(payload)

        output = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertIn("No after-window telemetry samples were available in this investigation package.", output["limitations"])

    def test_malformed_investigation_input_writes_unavailable_shape(self):
        self.module.INVESTIGATION.write_text("{not-json")

        output = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertEqual(output["investigation"]["source_status"], "unavailable")
        self.assertEqual(output["limitations"], ["Investigation artifact was unreadable."])
        self.assertFalse(output["environmental_context"]["dns"]["available"])

    def test_bounded_output_and_excluded_raw_sections(self):
        self.write_investigation(self.investigation_payload())

        output = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertLessEqual(len(output["observations"]), 8)
        self.assertLessEqual(len(output["limitations"]), 10)
        self.assertNotIn("timeline_samples", output)
        self.assertNotIn("events", output)
        self.assertNotIn("sources", output)
        self.assertNotIn("thresholds", output)

    def test_generated_artifact_is_additive_and_independent(self):
        payload = self.investigation_payload()
        self.write_investigation(payload)
        original = self.module.INVESTIGATION.read_text()

        built = self.module.build_from_path(self.module.INVESTIGATION)
        self.module.write_json(self.module.OUT, built)

        self.assertEqual(self.module.INVESTIGATION.read_text(), original)
        written = json.loads(self.module.OUT.read_text())
        self.assertEqual(written["investigation"]["id"], "investigation-1")
        self.assertNotEqual(self.module.OUT, self.module.INVESTIGATION)

    def test_rebuild_timestamp_does_not_change_input_hash(self):
        investigation = self.investigation_payload()
        older = self.module.build_package(investigation, "viz/investigation.json")
        investigation["generated_at"] = "2026-07-06T23:30:00+00:00"
        investigation["dns_context"]["generated_at"] = "2026-07-06T23:29:00Z"
        investigation["dns_context"]["minutes_from_event_midpoint"] = 42.0
        newer = self.module.build_package(investigation, "viz/investigation.json")

        self.assertEqual(older["input_hash"], newer["input_hash"])

    def test_schema_2_prefers_event_windows_and_freshness(self):
        payload = self.investigation_payload()
        payload.update({
            "schema_version": 2,
            "mode": "automatic",
            "artifact_state": {
                "is_current": True,
                "is_stale": False,
                "is_historical": False,
                "label": "Active investigation",
                "stale_reason": None,
            },
            "freshness": {
                "telemetry_latest_at": "2026-07-17T23:04:07+00:00",
                "evidence_latest_at": "2026-07-17T22:56:53+00:00",
                "generated_at": "2026-07-17T23:04:46+00:00",
                "telemetry_age_seconds": 39,
                "evidence_lag_seconds": 434,
            },
            "selected_event": {
                "id": "event-resolver_probe-2026-07-17t22-53-23z",
                "target_class": "resolver_probe",
                "lifecycle_state": "active",
                "first_anomalous_at": "2026-07-17T22:53:23+00:00",
                "last_anomalous_at": "2026-07-17T22:56:53+00:00",
                "severity": "medium",
                "confidence": "high",
            },
            "windows": {
                "baseline": {
                    "assessment_code": "stable_baseline",
                    "assessment_label": "Stable baseline",
                    "summary": "Resolver latency was stable before the event.",
                    "confidence": "high",
                    "supporting_metrics": {"wan_samples": 10, "raw_bad_count": 0, "sustained_bad_count": 0},
                },
                "degradation": {
                    "assessment_code": "sustained_degradation",
                    "assessment_label": "Sustained degradation",
                    "summary": "Resolver degradation persisted across multiple samples.",
                    "confidence": "high",
                    "supporting_metrics": {"wan_samples": 12, "raw_bad_count": 5, "sustained_bad_count": 2, "max_p95_ms": 170.0},
                },
                "recovery": {
                    "assessment_code": "awaiting_recovery_evidence",
                    "assessment_label": "Insufficient evidence",
                    "summary": "Recovery has not yet been confirmed.",
                    "confidence": "low",
                    "supporting_metrics": {"wan_samples": 0, "raw_bad_count": 0, "sustained_bad_count": 0},
                },
            },
            "timeline": [
                {"phase": "Degradation", "assessment_code": "sustained_degradation", "assessment_label": "Sustained degradation"}
            ],
        })
        self.write_investigation(payload)

        output = self.module.build_from_path(self.module.INVESTIGATION)

        self.assertEqual(output["investigation"]["mode"], "automatic")
        self.assertEqual(output["artifact_state"]["label"], "Active investigation")
        self.assertEqual(output["selected_event"]["target_class"], "resolver_probe")
        self.assertEqual(output["freshness"]["telemetry_latest_at"], "2026-07-17T23:04:07+00:00")
        self.assertEqual(output["evidence"]["resolver"]["assessment_code"], "sustained_degradation")
        self.assertTrue(output["timeline"])

    def test_schema_2_volatile_generated_at_does_not_change_input_hash(self):
        investigation = self.investigation_payload()
        investigation.update({
            "schema_version": 2,
            "mode": "automatic",
            "artifact_state": {"label": "Completed investigation", "is_current": True, "is_stale": False},
            "freshness": {"telemetry_latest_at": "2026-07-17T23:04:07+00:00", "evidence_latest_at": "2026-07-17T22:56:53+00:00", "generated_at": "2026-07-17T23:04:46+00:00"},
            "selected_event": {"id": "event-1", "target_class": "resolver_probe", "lifecycle_state": "complete"},
            "windows": {},
            "timeline": [],
        })
        older = self.module.build_package(investigation, "viz/investigation.json")
        investigation["generated_at"] = "2026-07-17T23:30:00+00:00"
        investigation["freshness"]["generated_at"] = "2026-07-17T23:30:00+00:00"
        newer = self.module.build_package(investigation, "viz/investigation.json")

        self.assertEqual(older["input_hash"], newer["input_hash"])

    def test_changed_semantic_input_writes_pending_generation_state(self):
        payload = self.module.build_package(self.investigation_payload(), "viz/investigation.json")

        changed = self.module.write_input_and_pending_state(
            self.module.OUT,
            self.module.STATE_OUT,
            payload,
            "bin/build_operator_assistant_input.py",
        )

        self.assertTrue(changed)
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["input_hash"], payload["input_hash"])
        self.assertEqual(state["attempt_count"], 0)

    def test_freshness_only_rebuild_does_not_replace_generation_state(self):
        investigation = self.investigation_payload()
        first = self.module.build_package(investigation, "viz/investigation.json")
        self.module.write_input_and_pending_state(self.module.OUT, self.module.STATE_OUT, first, "test")
        completed = {"schema_version": 2, "status": "complete", "input_hash": first["input_hash"], "attempt_count": 1}
        self.module.STATE_OUT.write_text(json.dumps(completed))
        investigation["generated_at"] = "2026-07-20T05:30:00Z"
        investigation["dns_context"]["generated_at"] = "2026-07-20T05:29:00Z"
        second = self.module.build_package(investigation, "viz/investigation.json")

        changed = self.module.write_input_and_pending_state(self.module.OUT, self.module.STATE_OUT, second, "test")

        self.assertFalse(changed)
        self.assertEqual(json.loads(self.module.STATE_OUT.read_text()), completed)

    def test_semantic_context_change_resets_pending_work(self):
        investigation = self.investigation_payload()
        first = self.module.build_package(investigation, "viz/investigation.json")
        self.module.write_input_and_pending_state(self.module.OUT, self.module.STATE_OUT, first, "test")
        investigation["internet_conditions_context"]["summary"] = "A material routing disruption is now reported."
        second = self.module.build_package(investigation, "viz/investigation.json")

        changed = self.module.write_input_and_pending_state(self.module.OUT, self.module.STATE_OUT, second, "test")

        self.assertTrue(changed)
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["input_hash"], second["input_hash"])
        self.assertNotEqual(first["input_hash"], second["input_hash"])


if __name__ == "__main__":
    unittest.main()
