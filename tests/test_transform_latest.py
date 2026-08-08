import csv
import datetime as dt
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "transform_latest.py"
INDEX_HTML_PATH = ROOT / "viz" / "index.html"
INVESTIGATE_HTML_PATH = ROOT / "viz" / "investigate.html"


def load_module():
    spec = importlib.util.spec_from_file_location("transform_latest", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransformLatestTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data_dir = self.base / "data"
        self.viz_dir = self.base / "viz"
        self.data_dir.mkdir()
        self.viz_dir.mkdir()
        self.module.BASE = self.base
        self.module.DATA_DIR = self.data_dir
        self.module.VIZ_DIR = self.viz_dir
        self.module.OUT = self.viz_dir / "latest.csv"
        self.module.ATTRIBUTION_OUT = self.viz_dir / "network_attribution.json"
        self.module.OBSERVATIONS_OUT = self.viz_dir / "observations.json"
        self.module.DASHBOARD_HEALTH_OUT = self.viz_dir / "dashboard_health.json"
        self.module.BASELINE_HISTORY_OUT = self.viz_dir / "baseline_history.json"
        self.module.INTERVAL_SUMMARY_OUT = self.viz_dir / "interval_summary.json"
        self.module.INCIDENT_SIMILARITY_OUT = self.viz_dir / "incident_similarity.json"
        self.module.OPERATIONAL_LEARNINGS_OUT = self.viz_dir / "operational_learnings.json"
        self.module.TIME_CONTEXT_OUT = self.viz_dir / "time_context.json"
        self.module.INVESTIGATION_OUT = self.viz_dir / "investigation.json"
        self.module.OPERATOR_ASSISTANT_INPUT_OUT = self.viz_dir / "operator_assistant_input.json"
        self.module.DIAGNOSTIC_EVIDENCE_IN = self.viz_dir / "diagnostic_evidence.json"
        self.module.APPLICATION_EXPERIENCE_IN = self.viz_dir / "application_experience.json"
        self.module.OPERATOR_IMPACT_FEEDBACK_IN = self.viz_dir / "operator_impact_feedback.json"
        self.module.INTERNET_CONDITIONS_IN = self.viz_dir / "internet_conditions.json"
        self.module.APS_POWER_CONTEXT_IN = self.viz_dir / "aps_power_context.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main_capturing_output(self):
        stream = io.StringIO()
        with mock.patch("sys.stdout", new=stream):
            self.module.main()
        output = stream.getvalue()
        self.assertIn("Wrote", output)
        return output

    def write_rows(self, rows):
        path = self.data_dir / "bakeoff_20260615.csv"
        fields = [
            "ts",
            "phase_label",
            "host",
            "sent",
            "received",
            "loss_pct",
            "avg_ms",
            "p50_ms",
            "p95_ms",
            "max_ms",
            "jitter_ms",
        ]
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def write_rows_file(self, name, rows):
        path = self.data_dir / name
        fields = [
            "ts",
            "phase_label",
            "host",
            "sent",
            "received",
            "loss_pct",
            "avg_ms",
            "p50_ms",
            "p95_ms",
            "max_ms",
            "jitter_ms",
        ]
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def baseline_history_rows(self, base, *, secondary=176, primary=35, loss=0, received="10"):
        rows = []
        for idx in range(12):
            ts = (base + dt.timedelta(minutes=idx * 10)).isoformat()
            rows.extend([
                self.telemetry_row(ts, "192.168.1.1", 8),
                self.telemetry_row(ts, "1.1.1.1", 30),
                self.telemetry_row(ts, "45.90.28.134", primary),
                {**self.telemetry_row(ts, "45.90.30.134", secondary, loss=loss), "received": received},
            ])
        return rows

    def telemetry_row(self, ts, host, p95, jitter=5, loss=0):
        return {
            "ts": ts,
            "phase_label": "fiber",
            "host": host,
            "sent": "10",
            "received": "10",
            "loss_pct": str(loss),
            "avg_ms": "20",
            "p50_ms": "20",
            "p95_ms": str(p95),
            "max_ms": str(p95 + 10),
            "jitter_ms": str(jitter),
        }

    def dashboard_sample(self, timestamp, host, p95, jitter=5, loss=0):
        return self.module.normalize_dashboard_sample(
            self.telemetry_row(timestamp.isoformat(), host, p95, jitter=jitter, loss=loss)
        )

    def write_application_experience(self, generated_at):
        checked_at = generated_at.isoformat().replace("+00:00", "Z")
        payload = {
            "schema_version": 1,
            "model_version": "prime_observer.application_experience.v1",
            "generated_at": checked_at,
            "status": "ok",
            "freshness": "fresh",
            "is_current": True,
            "dns_transactions": [
                {"role": "primary", "type": "direct_dns", "resolver_endpoint": "45.90.28.134", "checked_at": checked_at, "status": "ok", "success": True, "latency_ms": 25, "timeout": False, "failure_category": None},
                {"role": "secondary", "type": "direct_dns", "resolver_endpoint": "45.90.30.134", "checked_at": checked_at, "status": "ok", "success": True, "latency_ms": 30, "timeout": False, "failure_category": None},
                {"role": "system", "type": "system_dns", "resolver_endpoint": "system", "checked_at": checked_at, "status": "ok", "success": True, "latency_ms": 20, "timeout": False, "failure_category": None},
            ],
            "https_transaction": {"checked_at": checked_at, "status": "ok", "success": True, "timeout": False, "failure_category": None, "total_duration_ms": 120},
            "failure_counts": {"total": 0},
            "latency_summaries": {},
            "evidence": ["System DNS queries are succeeding normally.", "HTTPS session establishment remains normal."],
            "limitations": [],
        }
        self.module.APPLICATION_EXPERIENCE_IN.write_text(json.dumps(payload))

    def marked_recent_wan_samples(self, generated_at, internet_p95=None, resolver_p95=None):
        internet_p95 = internet_p95 or []
        resolver_p95 = resolver_p95 or []
        rows = []
        offset = max(len(internet_p95), len(resolver_p95), 1)
        for idx, p95 in enumerate(internet_p95):
            rows.append(self.dashboard_sample(generated_at - dt.timedelta(minutes=offset - idx), "1.1.1.1", p95))
        for idx, p95 in enumerate(resolver_p95):
            rows.append(self.dashboard_sample(generated_at - dt.timedelta(minutes=offset - idx), "45.90.28.134", p95))
        return self.module.mark_persistent_wan_bad(sorted(rows, key=lambda sample: sample["t"]))

    def recent_lan_samples(self, generated_at, p95_values):
        offset = max(len(p95_values), 1)
        return [
            self.dashboard_sample(generated_at - dt.timedelta(minutes=offset - idx), "192.168.1.1", p95)
            for idx, p95 in enumerate(p95_values)
        ]

    def test_old_csv_rows_gain_target_metadata_and_grouped_json(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.write_rows([
            self.telemetry_row((now - dt.timedelta(minutes=10)).isoformat(), "1.1.1.1", 25),
            self.telemetry_row((now - dt.timedelta(minutes=8)).isoformat(), "9.9.9.9", 30),
            self.telemetry_row((now - dt.timedelta(minutes=6)).isoformat(), "45.90.28.134", 28),
            self.telemetry_row((now - dt.timedelta(minutes=4)).isoformat(), "45.90.30.134", 29),
            self.telemetry_row((now - dt.timedelta(minutes=2)).isoformat(), "192.168.1.1", 8),
        ])

        self.run_main_capturing_output()

        with self.module.OUT.open("r", newline="") as f:
            rows = list(csv.DictReader(f))
        by_host = {row["host"]: row for row in rows}

        self.assertEqual(by_host["1.1.1.1"]["target_class"], "internet_probe")
        self.assertEqual(by_host["9.9.9.9"]["target_class"], "internet_probe")
        self.assertEqual(by_host["45.90.28.134"]["target_class"], "resolver_probe")
        self.assertEqual(by_host["45.90.30.134"]["target_class"], "resolver_probe")
        self.assertEqual(by_host["192.168.1.1"]["target_class"], "gateway_probe")

        attribution = json.loads(self.module.ATTRIBUTION_OUT.read_text())
        self.assertIn("target_groups", attribution)
        self.assertEqual(attribution["internet_probe_summary"]["sample_count"], 2)
        self.assertEqual(attribution["resolver_probe_summary"]["sample_count"], 2)
        self.assertIn("target_group_facts", attribution["attribution_evidence"])
        observations = json.loads(self.module.OBSERVATIONS_OUT.read_text())
        dashboard_health = json.loads(self.module.DASHBOARD_HEALTH_OUT.read_text())
        investigation = json.loads(self.module.INVESTIGATION_OUT.read_text())
        interval_summary = json.loads(self.module.INTERVAL_SUMMARY_OUT.read_text())
        incident_similarity = json.loads(self.module.INCIDENT_SIMILARITY_OUT.read_text())
        operational_learnings = json.loads(self.module.OPERATIONAL_LEARNINGS_OUT.read_text())
        time_context = json.loads(self.module.TIME_CONTEXT_OUT.read_text())
        investigation_catalog = json.loads((self.viz_dir / "investigation_catalog.json").read_text())
        self.assertEqual(observations["schema_version"], 1)
        self.assertEqual(observations["model_version"], "prime_observer.observation.v1")
        self.assertEqual(dashboard_health["schema_version"], 1)
        self.assertEqual(dashboard_health["model_version"], "prime_observer.dashboard_health.v1")
        self.assertIn("health_dimensions", dashboard_health)
        self.assertIn("dependency_groups", dashboard_health)
        self.assertEqual(dashboard_health["health_dimensions"]["model_version"], "prime_observer.health_dimensions.v1")
        self.assertIn("current_condition", dashboard_health["health_dimensions"])
        self.assertIn("rolling_condition", dashboard_health["health_dimensions"])
        self.assertIn("adaptive_baseline", dashboard_health["health_dimensions"])
        self.assertIn("adaptive_baseline", investigation["health_dimensions"])
        self.assertIn("incident_record", investigation)
        self.assertIn("title", investigation["incident_record"])
        self.assertIn("narrative", investigation["incident_record"])
        self.assertIn("incident_phases", investigation)
        self.assertIn("before", investigation["incident_phases"])
        self.assertIn("during", investigation["incident_phases"])
        self.assertIn("incident_replay", investigation)
        self.assertIn("milestones", investigation["incident_replay"])
        self.assertEqual(interval_summary["schema_version"], 1)
        self.assertEqual(interval_summary["model_version"], "prime_observer.interval_summary.v1")
        self.assertIn("overall_condition", interval_summary)
        self.assertIn("metrics", interval_summary)
        self.assertEqual(incident_similarity["schema_version"], 1)
        self.assertEqual(incident_similarity["model_version"], "prime_observer.incident_similarity.v1")
        self.assertIn("current_incident", incident_similarity)
        self.assertIn("matches", incident_similarity)
        self.assertEqual(operational_learnings["schema_version"], 1)
        self.assertEqual(operational_learnings["model_version"], "prime_observer.operational_learnings.v1")
        self.assertEqual(operational_learnings["learning_version"], "operational_learning.phase_1")
        self.assertIn("insights", operational_learnings)
        self.assertEqual(time_context["schema_version"], 1)
        self.assertEqual(time_context["model_version"], "prime_observer.time_context.v1")
        self.assertEqual(time_context["mode"], "current")
        self.assertIn("overlaps_incident", time_context)
        self.assertEqual(investigation["schema_version"], 2)
        self.assertEqual(investigation["mode"], "automatic")
        self.assertEqual(investigation["artifact_type"], "current_investigation")
        self.assertIn("health_dimensions", investigation)
        self.assertIn("impact_assessment", investigation)
        self.assertIn("dependency_state", investigation)
        self.assertIn("deterministic_operator_interpretation", investigation)
        self.assertFalse(investigation["immutable"])
        self.assertEqual(investigation_catalog["schema_version"], 1)
        self.assertEqual(investigation_catalog["artifact_type"], "investigation_catalog")
        self.assertEqual(investigation_catalog["events"], [])
        self.assertEqual(investigation_catalog["invalid_snapshots"], [])
        self.assertTrue(self.module.OPERATOR_ASSISTANT_INPUT_OUT.exists())
        self.assertEqual(len(observations["observations"]), 2)
        self.assertEqual({item["type"] for item in observations["observations"]}, {"attribution"})
        by_view = {item["scope"]["view"]: item for item in observations["observations"]}
        self.assertEqual(by_view["current_attribution"]["state"]["label"], attribution["attribution_label"])
        self.assertEqual(by_view["window_attribution"]["state"]["label"], attribution["window_attribution"]["label"])

    def test_main_keeps_legacy_attribution_export_and_adds_projection(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.write_rows([
            self.telemetry_row((now - dt.timedelta(minutes=10)).isoformat(), "1.1.1.1", 180),
            self.telemetry_row((now - dt.timedelta(minutes=9)).isoformat(), "1.1.1.1", 181),
            self.telemetry_row((now - dt.timedelta(minutes=8)).isoformat(), "45.90.28.134", 28),
            self.telemetry_row((now - dt.timedelta(minutes=7)).isoformat(), "45.90.28.134", 29),
            self.telemetry_row((now - dt.timedelta(minutes=6)).isoformat(), "192.168.1.1", 8),
        ])

        self.run_main_capturing_output()

        attribution = json.loads(self.module.ATTRIBUTION_OUT.read_text())
        observations = json.loads(self.module.OBSERVATIONS_OUT.read_text())

        self.assertTrue(self.module.ATTRIBUTION_OUT.exists())
        self.assertTrue(self.module.OBSERVATIONS_OUT.exists())
        self.assertIn("attribution_status", attribution)
        self.assertIn("attribution_label", attribution)
        self.assertIn("current_attribution", attribution)
        self.assertIn("window_attribution", attribution)
        self.assertIn("refined_attribution", attribution)
        self.assertEqual(attribution["refined_attribution"]["model_version"], "prime_observer.health_dimensions.v1")
        self.assertEqual(observations["model_version"], "prime_observer.observation.v1")
        self.assertEqual(len(observations["observations"]), 3)
        by_view = {item["scope"]["view"]: item for item in observations["observations"]}
        self.assertEqual(by_view["current_attribution"]["evidence_references"][0]["path"], "viz/network_attribution.json")
        self.assertEqual(by_view["window_attribution"]["evidence_references"][0]["path"], "viz/network_attribution.json")
        episodes = [item for item in observations["observations"] if item["type"] == "episode"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["state"]["status"], "sustained_degradation")
        self.assertEqual(episodes[0]["interval"]["start"], attribution["incidents"][0]["start"])
        self.assertEqual(episodes[0]["interval"]["end"], attribution["incidents"][0]["end"])

    def test_main_adds_turbulence_episode_observation_without_changing_legacy_exports(self):
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        bucket_base = (now - dt.timedelta(minutes=20)).replace(minute=((now - dt.timedelta(minutes=20)).minute // 15) * 15)
        self.write_rows([
            self.telemetry_row((bucket_base + dt.timedelta(minutes=1)).isoformat(), "45.90.28.134", 180),
            self.telemetry_row((bucket_base + dt.timedelta(minutes=2)).isoformat(), "45.90.28.134", 30),
            self.telemetry_row((bucket_base + dt.timedelta(minutes=3)).isoformat(), "45.90.28.134", 181),
            self.telemetry_row((bucket_base + dt.timedelta(minutes=4)).isoformat(), "45.90.28.134", 31),
            self.telemetry_row((bucket_base + dt.timedelta(minutes=5)).isoformat(), "45.90.28.134", 182),
            self.telemetry_row((bucket_base + dt.timedelta(minutes=6)).isoformat(), "45.90.28.134", 32),
            self.telemetry_row((bucket_base + dt.timedelta(minutes=7)).isoformat(), "45.90.28.134", 183),
            self.telemetry_row((bucket_base + dt.timedelta(minutes=7)).isoformat(), "192.168.1.1", 8),
        ])

        self.run_main_capturing_output()

        attribution = json.loads(self.module.ATTRIBUTION_OUT.read_text())
        observations = json.loads(self.module.OBSERVATIONS_OUT.read_text())

        self.assertIn("window_attribution", attribution)
        episodes = [item for item in observations["observations"] if item["type"] == "episode"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["state"]["status"], "turbulence")
        self.assertEqual(episodes[0]["scope"]["target_class"], "resolver_probe")

    def test_stable_elevated_resolver_keeps_observations_but_suppresses_current_incident(self):
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        base = now - dt.timedelta(hours=2)
        rows = []
        for idx in range(12):
            ts = (base + dt.timedelta(minutes=idx * 10)).isoformat()
            rows.extend([
                self.telemetry_row(ts, "192.168.1.1", 8),
                self.telemetry_row(ts, "1.1.1.1", 30),
                self.telemetry_row(ts, "45.90.28.134", 35),
                self.telemetry_row(ts, "45.90.30.134", 176),
            ])
        self.write_rows(rows)
        self.write_application_experience(now)

        self.run_main_capturing_output()

        dashboard_health = json.loads(self.module.DASHBOARD_HEALTH_OUT.read_text())
        observations = json.loads(self.module.OBSERVATIONS_OUT.read_text())
        investigation = json.loads(self.module.INVESTIGATION_OUT.read_text())
        adaptive = next(item for item in dashboard_health["health_dimensions"]["adaptive_baseline"]["resolver_members"] if item["member_id"] == "nextdns_secondary")

        self.assertEqual(adaptive["baseline_state"], "elevated_but_stable")
        self.assertFalse(adaptive["incident_eligible"])
        self.assertEqual(dashboard_health["health_dimensions"]["current_condition"]["state"], "elevated")
        self.assertIsNone(investigation["selected_event"])
        self.assertTrue(investigation["incident_suppressed"])
        self.assertEqual(investigation["suppression_reason"], "established_degraded_baseline")
        episodes = [item for item in observations["observations"] if item["type"] == "episode"]
        self.assertTrue(episodes)
        self.assertEqual(episodes[0]["state"]["status"], "sustained_degradation")

    def test_durable_baseline_history_created_for_separate_resolver_members(self):
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        self.write_rows_file("bakeoff_20260614.csv", self.baseline_history_rows(now - dt.timedelta(days=2), secondary=176, primary=35))
        self.write_rows_file("bakeoff_20260615.csv", self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=176, primary=35))
        self.write_application_experience(now)

        self.run_main_capturing_output()

        history = json.loads(self.module.BASELINE_HISTORY_OUT.read_text())
        secondary_key = "FIBER|resolver_probe|nextdns_secondary"
        primary_key = "FIBER|resolver_probe|nextdns_primary"
        self.assertEqual(history["schema_version"], 1)
        self.assertIn(secondary_key, history["targets"])
        self.assertIn(primary_key, history["targets"])
        self.assertNotEqual(secondary_key, primary_key)
        secondary = history["targets"][secondary_key]
        self.assertEqual(secondary["accepted_state"], "elevated_but_stable")
        self.assertEqual(secondary["sample_count"], 24)
        self.assertEqual(secondary["guardrail_status"], {"status": "clear", "breaches": []})
        self.assertFalse(history["retention"]["raw_samples_stored"])

    def test_durable_baseline_version_increments_with_explanation(self):
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        previous = {
            "schema_version": 1,
            "model_version": "prime_observer.baseline_history.v1",
            "generated_at": (now - dt.timedelta(hours=1)).isoformat(),
            "baseline_version": 3,
            "targets": {
                "FIBER|resolver_probe|nextdns_secondary": {
                    "identity": {"phase": "FIBER", "target_class": "resolver_probe", "member_id": "nextdns_secondary", "host": "45.90.30.134"},
                    "baseline_version": 3,
                    "window": {"start": "2026-06-10T00:00:00+00:00", "end": "2026-06-11T00:00:00+00:00"},
                    "sample_count": 24,
                    "median": 150.0,
                    "p95": 155.0,
                    "accepted_state": "elevated_but_stable",
                    "version_history": [],
                }
            },
        }
        self.module.BASELINE_HISTORY_OUT.write_text(json.dumps(previous))
        self.write_rows_file("bakeoff_20260614.csv", self.baseline_history_rows(now - dt.timedelta(days=2), secondary=176))
        self.write_rows_file("bakeoff_20260615.csv", self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=176))
        self.write_application_experience(now)

        self.run_main_capturing_output()

        history = json.loads(self.module.BASELINE_HISTORY_OUT.read_text())
        secondary = history["targets"]["FIBER|resolver_probe|nextdns_secondary"]
        self.assertGreater(secondary["baseline_version"], 3)
        self.assertEqual(secondary["baseline_change_status"], "changed")
        self.assertIn("Median p95 moved", secondary["change_explanation"])
        self.assertEqual(secondary["previous_baseline_summary"]["baseline_version"], 3)
        self.assertEqual(secondary["version_history"][0]["baseline_version"], 3)

    def test_durable_baseline_blocks_update_for_insufficient_samples_loss_timeout_or_app_failure(self):
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        cases = [
            ("insufficient", [self.telemetry_row((now - dt.timedelta(minutes=idx)).isoformat(), "45.90.30.134", 176) for idx in range(4)], None, "insufficient_samples"),
            ("loss", self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=176, loss=3), None, "packet_loss_above_threshold"),
            ("timeout", self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=176, received="0"), None, "timeout"),
            ("app", self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=176), "failed", "application_checks_unhealthy"),
        ]
        for name, rows, app_secondary, expected in cases:
            with self.subTest(name=name):
                self.tearDown()
                self.setUp()
                self.write_rows_file("bakeoff_20260614.csv", rows)
                self.write_rows_file("bakeoff_20260615.csv", rows)
                self.write_application_experience(now)
                if app_secondary:
                    payload = json.loads(self.module.APPLICATION_EXPERIENCE_IN.read_text())
                    for item in payload["dns_transactions"]:
                        if item.get("role") == "secondary":
                            item["success"] = False
                            item["failure_category"] = "dns_failure"
                    payload["failure_counts"] = {"total": 1}
                    self.module.APPLICATION_EXPERIENCE_IN.write_text(json.dumps(payload))
                self.run_main_capturing_output()
                history = json.loads(self.module.BASELINE_HISTORY_OUT.read_text())
                blocked = history["blocked_targets"].get("FIBER|resolver_probe|nextdns_secondary")
                self.assertIsNotNone(blocked)
                self.assertIn(expected, blocked["reasons"])

    def test_malformed_durable_baseline_falls_back_and_is_rewritten_atomically(self):
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        self.module.BASELINE_HISTORY_OUT.write_text("{bad")
        self.write_rows_file("bakeoff_20260614.csv", self.baseline_history_rows(now - dt.timedelta(days=2), secondary=176))
        self.write_rows_file("bakeoff_20260615.csv", self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=176))
        self.write_application_experience(now)

        self.run_main_capturing_output()

        history = json.loads(self.module.BASELINE_HISTORY_OUT.read_text())
        self.assertIn("FIBER|resolver_probe|nextdns_secondary", history["targets"])
        self.assertFalse(self.module.BASELINE_HISTORY_OUT.with_suffix(".json.tmp").exists())

    def test_durable_baseline_feeds_adaptive_classification_and_guardrails_still_override(self):
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        previous = {
            "schema_version": 1,
            "model_version": "prime_observer.baseline_history.v1",
            "generated_at": now.isoformat(),
            "baseline_version": 2,
            "targets": {
                "FIBER|resolver_probe|nextdns_secondary": {
                    "identity": {"phase": "FIBER", "target_class": "resolver_probe", "member_id": "nextdns_secondary", "host": "45.90.30.134"},
                    "baseline_version": 2,
                    "window": {"start": (now - dt.timedelta(days=3)).isoformat(), "end": (now - dt.timedelta(days=1)).isoformat()},
                    "sample_count": 48,
                    "median": 176.0,
                    "p75": 177.0,
                    "p90": 178.0,
                    "p95": 179.0,
                    "accepted_state": "elevated_but_stable",
                    "baseline_change_status": "accepted",
                    "change_explanation": "Initial durable baseline learned from historical telemetry.",
                    "guardrail_status": {"status": "clear", "breaches": []},
                    "version_history": [],
                }
            },
        }
        result = self.module.evaluate_health_dimensions(
            self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=176),
            generated_at=now,
            application_experience=json.loads(json.dumps({
                "is_current": True,
                "failure_counts": {"total": 0},
                "dns_transactions": [
                    {"role": "primary", "resolver_endpoint": "45.90.28.134", "success": True, "timeout": False},
                    {"role": "secondary", "resolver_endpoint": "45.90.30.134", "success": True, "timeout": False},
                    {"role": "system", "success": True, "timeout": False},
                ],
                "https_transaction": {"success": True, "timeout": False},
            })),
            baseline_history=previous,
        )
        adaptive = next(item for item in result["adaptive_baseline"]["resolver_members"] if item["member_id"] == "nextdns_secondary")
        self.assertEqual(adaptive["baseline_source"], "durable")
        self.assertEqual(adaptive["baseline_state"], "elevated_but_stable")
        self.assertFalse(adaptive["incident_eligible"])
        self.assertEqual(adaptive["durable_baseline_version"], 2)

        worsening = self.module.evaluate_health_dimensions(
            self.baseline_history_rows(now - dt.timedelta(hours=2), secondary=260),
            generated_at=now,
            application_experience=result["application_experience"],
            baseline_history=previous,
        )
        adaptive = next(item for item in worsening["adaptive_baseline"]["resolver_members"] if item["member_id"] == "nextdns_secondary")
        self.assertTrue(adaptive["incident_eligible"])
        self.assertIn(adaptive["baseline_state"], {"degraded_from_baseline", "anomalous"})

    def test_dashboard_consumes_observations_for_attribution_and_episode_projection_only(self):
        dashboard_html = INDEX_HTML_PATH.read_text()
        investigation_html = INVESTIGATE_HTML_PATH.read_text()

        self.assertIn("./latest.csv", dashboard_html)
        self.assertIn("./dashboard_health.json", dashboard_html)
        self.assertIn("./incident_similarity.json", dashboard_html)
        self.assertIn("./operational_learnings.json", dashboard_html)
        self.assertIn("./time_context.json", dashboard_html)
        self.assertIn("./observations.json", dashboard_html)
        self.assertIn("./network_attribution.json", dashboard_html)
        self.assertIn("selectEpisodeObservations(observationsPayload)", dashboard_html)
        self.assertIn("./nextdns_summary.json", dashboard_html)
        self.assertIn("./internet_conditions.json", dashboard_html)
        self.assertIn("./aps_power_context.json", dashboard_html)
        self.assertIn("./investigation.json", investigation_html)
        self.assertIn("./incident_similarity.json", investigation_html)
        self.assertIn("./operational_learnings.json", investigation_html)
        self.assertIn("renderIncidentSimilarityDashboard", dashboard_html)
        self.assertNotIn("calculateSimilarity", dashboard_html)
        self.assertNotIn("scoreIncidentSimilarity", dashboard_html)
        self.assertNotIn("observations.json", investigation_html)
        self.assertNotIn("internet_conditions.json", investigation_html)
        self.assertNotIn("aps_power_context.json", investigation_html)

    def test_transform_module_does_not_call_openrouter_or_output_producer(self):
        source = MODULE_PATH.read_text()
        self.assertIn("OPERATOR_ASSISTANT_GENERATION_STATE_OUT", source)
        self.assertIn("pending_generation_state", source)
        self.assertNotIn("build_operator_assistant_output", source)
        self.assertNotIn("openrouter", source.lower())

    def test_transform_reads_application_experience_artifact_without_network_calls(self):
        source = MODULE_PATH.read_text()

        self.assertIn("APPLICATION_EXPERIENCE_IN", source)
        self.assertIn("load_application_experience", source)
        self.assertIn("build_interval_summary", source)
        self.assertIn("build_incident_similarity", source)
        self.assertIn("build_operational_learnings", source)
        self.assertIn("build_time_context", source)
        self.assertIn("OPERATOR_IMPACT_FEEDBACK_IN", source)
        self.assertIn("load_operator_impact_feedback", source)
        self.assertNotIn("socket.", source)
        self.assertNotIn("urlopen", source)

    def test_browser_files_render_phase_4_dashboard_hierarchy(self):
        dashboard_html = INDEX_HTML_PATH.read_text()
        investigation_html = INVESTIGATE_HTML_PATH.read_text()

        self.assertIn("payload.health_dimensions", dashboard_html)
        self.assertIn("payload.dependency_groups", dashboard_html)
        self.assertIn("refined_attribution", dashboard_html)
        self.assertIn("Current Summary", dashboard_html)
        self.assertNotIn("Deterministic Operator Summary", dashboard_html)
        self.assertNotIn("id=\"operatorSummarySource\"", dashboard_html)
        self.assertIn("Router path", dashboard_html)
        self.assertNotIn(">Connection<", dashboard_html)
        self.assertIn("Likely issue", dashboard_html)
        self.assertIn("DNS &amp; Web Health", dashboard_html)
        self.assertIn("Historical Patterns", dashboard_html)
        self.assertIn("DNS Activity", dashboard_html)
        self.assertIn("Why this looks upstream", dashboard_html)
        self.assertIn("Internet Conditions", dashboard_html)
        self.assertIn("Power Infrastructure", dashboard_html)
        self.assertNotIn("Legacy Model Disclosure", dashboard_html)
        self.assertNotIn("User Noticeability", dashboard_html)
        self.assertNotIn("Network Attribution", dashboard_html)
        self.assertIn("Legacy Noticeability", dashboard_html)
        self.assertIn("Legacy Attribution", dashboard_html)
        self.assertIn("legacyCompatibilityDetails", dashboard_html)
        self.assertIn("setLegacyCompatibilityMode(!hasDimensions)", dashboard_html)
        self.assertIn("mobileLikelyCauseValue", dashboard_html)
        self.assertIn("mobileHistoricalPatternsCard", dashboard_html)
        self.assertNotIn("mobileCurrentActionValue", dashboard_html)
        self.assertIn("matchingAssistantReview", dashboard_html)
        self.assertIn("matchedReview?.headline", dashboard_html)
        self.assertIn("plainFallbackHeadline(dimensions, dependency, refined)", dashboard_html)
        self.assertIn("Cloudflare Radar", dashboard_html)
        self.assertIn("Power Infrastructure", dashboard_html)
        self.assertNotIn("DNS Security", dashboard_html)
        self.assertNotIn("Normal ASN traffic does not prove a measured path is healthy", dashboard_html)
        self.assertNotIn("Limiting or corroborating context only", dashboard_html)
        self.assertIn("Current status", investigation_html)
        self.assertIn("User-facing impact", investigation_html)
        self.assertIn("Affected and healthy checks", investigation_html)
        self.assertIn("Confidence and limits", investigation_html)
        self.assertIn("data.health_dimensions", investigation_html)
        self.assertIn("data.dependency_state", investigation_html)
        self.assertIn("data.deterministic_operator_interpretation", investigation_html)
        self.assertNotIn("https://openrouter.ai", dashboard_html)
        self.assertNotIn("OpenRouter", dashboard_html)
        self.assertNotIn("OPENROUTER", dashboard_html)
        self.assertNotIn("OpenRouter", investigation_html)
        self.assertNotIn("OPENROUTER", investigation_html)
        self.assertIn("adaptive_baseline_state", dashboard_html)
        self.assertIn("No active incident is detected", dashboard_html)
        self.assertNotIn("incident_eligible", dashboard_html)
        self.assertIn("adaptive_baseline_state", investigation_html)
        self.assertNotIn("incident_eligible", investigation_html)

    def test_dashboard_health_projection_matches_python_classification(self):
        base = dt.datetime(2026, 6, 15, 20, 0, tzinfo=dt.timezone.utc)
        rows = [
            self.dashboard_sample(base + dt.timedelta(minutes=0), "1.1.1.1", 180),
            self.dashboard_sample(base + dt.timedelta(minutes=1), "1.1.1.1", 181),
            self.dashboard_sample(base + dt.timedelta(minutes=2), "45.90.28.134", 180),
            self.dashboard_sample(base + dt.timedelta(minutes=3), "45.90.28.134", 30),
            self.dashboard_sample(base + dt.timedelta(minutes=4), "45.90.28.134", 181),
            self.dashboard_sample(base + dt.timedelta(minutes=5), "45.90.28.134", 31),
            self.dashboard_sample(base + dt.timedelta(minutes=6), "45.90.28.134", 182),
            self.dashboard_sample(base + dt.timedelta(minutes=7), "45.90.28.134", 32),
            self.dashboard_sample(base + dt.timedelta(minutes=8), "45.90.28.134", 183),
            self.dashboard_sample(base + dt.timedelta(minutes=9), "192.168.1.1", 130),
            self.dashboard_sample(base + dt.timedelta(minutes=10), "192.168.1.1", 131),
            self.dashboard_sample(base + dt.timedelta(minutes=11), "192.168.1.1", 132),
        ]
        rows_out = [
            self.telemetry_row(sample["t"].isoformat(), sample["host"], sample["p95"], jitter=sample["jitter"], loss=sample["loss"])
            for sample in rows
        ]
        for row in rows_out:
            row.update(self.module.target_metadata(row["host"]))

        generated_at = base + dt.timedelta(minutes=12)
        attribution = self.module.compute_network_attribution(rows_out, generated_at)
        dashboard_health = self.module.build_dashboard_health(rows_out, attribution, generated_at)
        lan_series, wan_series = self.module.to_dashboard_series(rows_out)
        marked = self.module.mark_persistent_wan_bad(wan_series)
        buckets = self.module.classify_buckets(marked)

        self.assertNotIn("thresholds", dashboard_health)

        legacy_sample_classification = {
            (sample["t"].isoformat(), sample["target_class"], sample["host"]): (sample["raw_bad"], sample["is_bad"])
            for sample in marked
        }
        projected_sample_classification = {
            (sample["ts"], sample["targetClass"], sample["host"]): (sample["rawBad"], sample["isBad"])
            for sample in dashboard_health["wan_samples"]
        }
        self.assertEqual(projected_sample_classification, legacy_sample_classification)

        projected_buckets = dashboard_health["wan_target_group_buckets"]
        self.assertEqual(len(projected_buckets), len(buckets))
        projected_by_group = {bucket["targetClass"]: bucket for bucket in projected_buckets}
        legacy_bucket_semantics = {
            bucket["target_class"]: {
                "bad": bucket["bad"],
                "rawBad": bucket["raw_bad"],
                "isBadBucket": bucket["bad"] > 0,
                "isTurbulence": bucket["is_turbulence"],
                "maxRawRun": bucket["max_raw_run"],
            }
            for bucket in buckets
        }
        projected_bucket_semantics = {
            target_class: {
                "bad": bucket["bad"],
                "rawBad": bucket["rawBad"],
                "isBadBucket": bucket["isBadBucket"],
                "isTurbulence": bucket["isTurbulence"],
                "maxRawRun": bucket["maxRawRun"],
            }
            for target_class, bucket in projected_by_group.items()
        }
        self.assertEqual(projected_bucket_semantics, legacy_bucket_semantics)

        composite = dashboard_health["composite_wan_buckets"][0]
        legacy_lan = self.module.lan_elevation(lan_series)
        legacy_selected_evidence = {
            "internetSustainedBadSamples": legacy_bucket_semantics["internet_probe"]["bad"],
            "internetRawBadSamples": legacy_bucket_semantics["internet_probe"]["rawBad"],
            "internetSamples": projected_by_group["internet_probe"]["total"],
            "resolverSustainedBadSamples": legacy_bucket_semantics["resolver_probe"]["bad"],
            "resolverRawBadSamples": legacy_bucket_semantics["resolver_probe"]["rawBad"],
            "resolverSamples": projected_by_group["resolver_probe"]["total"],
            "lanElevatedSamples": len(legacy_lan["elevated"]),
            "lanSamples": len(lan_series),
        }
        projected_selected_evidence = {
            key: composite["selectedEvidence"][key]
            for key in legacy_selected_evidence
        }
        self.assertEqual(projected_selected_evidence, legacy_selected_evidence)

        legacy_composite_semantics = {
            "isBadBucket": any(bucket["isBadBucket"] for bucket in legacy_bucket_semantics.values()),
            "isTurbulence": False,
            "bad": sum(bucket["bad"] for bucket in legacy_bucket_semantics.values()),
            "rawBad": sum(bucket["rawBad"] for bucket in legacy_bucket_semantics.values()),
        }
        legacy_composite_semantics["isTurbulence"] = (
            not legacy_composite_semantics["isBadBucket"]
            and any(bucket["isTurbulence"] for bucket in legacy_bucket_semantics.values())
        )
        projected_composite_semantics = {
            "isBadBucket": composite["isBadBucket"],
            "isTurbulence": composite["isTurbulence"],
            "bad": composite["bad"],
            "rawBad": composite["rawBad"],
        }
        self.assertEqual(projected_composite_semantics, legacy_composite_semantics)

        window_counts = self.module.attribution_evidence_counts(
            self.module.target_group_summary(marked),
            lan_series,
            self.module.lan_elevation(lan_series)["elevated"],
        )
        self.assertEqual(
            dashboard_health["attribution_evidence_counts"],
            self.module.camelize_classification_counts(window_counts),
        )

    def test_dashboard_health_preserves_rolling_condition_while_current_window_is_healthy(self):
        base = dt.datetime(2026, 6, 15, 20, 0, tzinfo=dt.timezone.utc)
        rows_out = [
            self.telemetry_row((base + dt.timedelta(minutes=0)).isoformat(), "45.90.30.134", 260),
            self.telemetry_row((base + dt.timedelta(minutes=1)).isoformat(), "45.90.30.134", 270),
            self.telemetry_row((base + dt.timedelta(minutes=25)).isoformat(), "192.168.1.1", 8),
            self.telemetry_row((base + dt.timedelta(minutes=25)).isoformat(), "1.1.1.1", 25),
            self.telemetry_row((base + dt.timedelta(minutes=25)).isoformat(), "45.90.28.134", 35),
            self.telemetry_row((base + dt.timedelta(minutes=25)).isoformat(), "45.90.30.134", 36),
            self.telemetry_row((base + dt.timedelta(minutes=26)).isoformat(), "192.168.1.1", 8),
            self.telemetry_row((base + dt.timedelta(minutes=26)).isoformat(), "1.1.1.1", 25),
            self.telemetry_row((base + dt.timedelta(minutes=26)).isoformat(), "45.90.28.134", 35),
            self.telemetry_row((base + dt.timedelta(minutes=26)).isoformat(), "45.90.30.134", 36),
        ]
        for row in rows_out:
            row.update(self.module.target_metadata(row["host"]))
        generated_at = base + dt.timedelta(minutes=27)
        health_dimensions = self.module.evaluate_health_dimensions(rows_out, generated_at=generated_at)
        attribution = self.module.compute_network_attribution(rows_out, generated_at, health_dimensions=health_dimensions)
        dashboard_health = self.module.build_dashboard_health(rows_out, attribution, generated_at, health_dimensions=health_dimensions)

        self.assertEqual(dashboard_health["health_dimensions"]["technical_condition"]["state"], "severe")
        self.assertEqual(dashboard_health["health_dimensions"]["rolling_condition"]["state"], "severe")
        self.assertEqual(dashboard_health["health_dimensions"]["current_condition"]["state"], "healthy")

    def test_bad_bucket_can_be_driven_by_loss_even_when_p95_is_low(self):
        base = dt.datetime(2026, 6, 15, 20, 15, tzinfo=dt.timezone.utc)
        rows = [
            self.module.normalize_dashboard_sample(
                self.telemetry_row((base + dt.timedelta(minutes=i)).isoformat(), "1.1.1.1", 30, jitter=20, loss=10)
            )
            for i in range(3)
        ]

        marked = self.module.mark_persistent_wan_bad(rows)
        buckets = self.module.classify_buckets(marked)

        self.assertEqual([sample["raw_bad"] for sample in marked], [True, True, True])
        self.assertEqual([sample["is_bad"] for sample in marked], [False, True, True])
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]["bad"], 2)
        self.assertEqual(buckets[0]["raw_bad"], 3)
        self.assertFalse(buckets[0]["is_turbulence"])

    def test_turbulence_bucket_requires_raw_bad_without_sustained_run(self):
        base = dt.datetime(2026, 6, 15, 20, 0, tzinfo=dt.timezone.utc)
        p95_values = [180, 30, 181, 31, 182, 32, 183]
        rows = [
            self.module.normalize_dashboard_sample(
                self.telemetry_row((base + dt.timedelta(minutes=i)).isoformat(), "1.1.1.1", p95)
            )
            for i, p95 in enumerate(p95_values)
        ]

        marked = self.module.mark_persistent_wan_bad(rows)
        buckets = self.module.classify_buckets(marked)

        self.assertTrue(buckets[0]["is_turbulence"])
        self.assertEqual(buckets[0]["bad"], 0)
        self.assertEqual(buckets[0]["raw_bad"], 4)
        self.assertEqual(buckets[0]["max_raw_run"], 1)

    def test_sustained_bad_is_tracked_independently_by_target_group(self):
        base = dt.datetime(2026, 6, 15, 20, 0, tzinfo=dt.timezone.utc)
        internet_first = self.module.normalize_dashboard_sample(
            self.telemetry_row(base.isoformat(), "1.1.1.1", 180)
        )
        resolver_middle = self.module.normalize_dashboard_sample(
            self.telemetry_row((base + dt.timedelta(minutes=1)).isoformat(), "45.90.28.134", 25)
        )
        internet_second = self.module.normalize_dashboard_sample(
            self.telemetry_row((base + dt.timedelta(minutes=2)).isoformat(), "1.1.1.1", 181)
        )

        marked = self.module.mark_persistent_wan_bad([internet_first, resolver_middle, internet_second])
        by_target = [(sample["target_class"], sample["raw_bad"], sample["is_bad"]) for sample in marked]

        self.assertEqual(by_target, [
            ("internet_probe", True, False),
            ("resolver_probe", False, False),
            ("internet_probe", True, True),
        ])

    def test_buckets_are_separated_by_phase_and_target_group(self):
        timestamp = dt.datetime(2026, 6, 15, 20, 0, tzinfo=dt.timezone.utc)
        fiber = self.module.normalize_dashboard_sample(
            self.telemetry_row(timestamp.isoformat(), "1.1.1.1", 180)
        )
        alternate = dict(fiber)
        alternate["phase"] = "TMOBILE"
        resolver = self.module.normalize_dashboard_sample(
            self.telemetry_row(timestamp.isoformat(), "45.90.28.134", 181)
        )

        marked = self.module.mark_persistent_wan_bad([fiber, alternate, resolver])
        buckets = self.module.classify_buckets(marked)

        self.assertEqual(
            sorted((bucket["phase"], bucket["target_class"], bucket["total"]) for bucket in buckets),
            [
                ("FIBER", "internet_probe", 1),
                ("FIBER", "resolver_probe", 1),
                ("TMOBILE", "internet_probe", 1),
            ],
        )

    def test_wan_dominant_evidence_does_not_become_local_attribution(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        wan = self.marked_recent_wan_samples(
            now,
            internet_p95=[180, 181, 182, 183, 184, 185, 186, 187],
            resolver_p95=[175, 176, 177, 178, 179, 180, 181, 182],
        )
        lan = self.recent_lan_samples(now, [130, 131, 132, 133, 134, 40, 41, 42, 43, 44])

        attribution = self.module.compute_recent_attribution(lan, wan, now)

        self.assertEqual(attribution["attribution_label"], "Likely upstream (ISP / path)")
        counts = attribution["attribution_evidence"]["classification_counts"]
        self.assertTrue(counts["internet_probe_degraded"])
        self.assertTrue(counts["resolver_probe_degraded"])
        self.assertEqual(counts["lan_elevated_samples"], 5)

    def test_mixed_evidence_can_produce_mixed_attribution(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        wan = self.marked_recent_wan_samples(now, internet_p95=[180, 181, 35, 36])
        lan = self.recent_lan_samples(now, [130, 131, 132, 40, 41])

        attribution = self.module.compute_recent_attribution(lan, wan, now)

        self.assertEqual(attribution["attribution_label"], "Mixed evidence")
        self.assertEqual(attribution["attribution_confidence"], "Medium")
        self.assertEqual(attribution["attribution_status"], "mixed_evidence")

    def test_lan_dominant_evidence_still_produces_local_attribution(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        wan = self.marked_recent_wan_samples(now, internet_p95=[180, 181])
        lan = self.recent_lan_samples(now, [130, 131, 132, 133, 134])

        attribution = self.module.compute_recent_attribution(lan, wan, now)

        self.assertEqual(attribution["attribution_label"], "Likely local (LAN / Wi\u2011Fi)")
        counts = attribution["attribution_evidence"]["classification_counts"]
        self.assertEqual(counts["internet_bad_buckets"], 1)
        self.assertEqual(counts["lan_elevated_samples"], 5)


if __name__ == "__main__":
    unittest.main()
