import csv
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


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
        self.module.INVESTIGATION_OUT = self.viz_dir / "investigation.json"
        self.module.OPERATOR_ASSISTANT_INPUT_OUT = self.viz_dir / "operator_assistant_input.json"

    def tearDown(self):
        self.tmp.cleanup()

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

        self.module.main()

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
        investigation_catalog = json.loads((self.viz_dir / "investigation_catalog.json").read_text())
        self.assertEqual(observations["schema_version"], 1)
        self.assertEqual(observations["model_version"], "prime_observer.observation.v1")
        self.assertEqual(dashboard_health["schema_version"], 1)
        self.assertEqual(dashboard_health["model_version"], "prime_observer.dashboard_health.v1")
        self.assertEqual(investigation["schema_version"], 2)
        self.assertEqual(investigation["mode"], "automatic")
        self.assertEqual(investigation["artifact_type"], "current_investigation")
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

        self.module.main()

        attribution = json.loads(self.module.ATTRIBUTION_OUT.read_text())
        observations = json.loads(self.module.OBSERVATIONS_OUT.read_text())

        self.assertTrue(self.module.ATTRIBUTION_OUT.exists())
        self.assertTrue(self.module.OBSERVATIONS_OUT.exists())
        self.assertIn("attribution_status", attribution)
        self.assertIn("attribution_label", attribution)
        self.assertIn("current_attribution", attribution)
        self.assertIn("window_attribution", attribution)
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

        self.module.main()

        attribution = json.loads(self.module.ATTRIBUTION_OUT.read_text())
        observations = json.loads(self.module.OBSERVATIONS_OUT.read_text())

        self.assertIn("window_attribution", attribution)
        episodes = [item for item in observations["observations"] if item["type"] == "episode"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["state"]["status"], "turbulence")
        self.assertEqual(episodes[0]["scope"]["target_class"], "resolver_probe")

    def test_dashboard_consumes_observations_for_attribution_and_episode_projection_only(self):
        dashboard_html = INDEX_HTML_PATH.read_text()
        investigation_html = INVESTIGATE_HTML_PATH.read_text()

        self.assertIn("./latest.csv", dashboard_html)
        self.assertIn("./dashboard_health.json", dashboard_html)
        self.assertIn("./observations.json", dashboard_html)
        self.assertIn("./network_attribution.json", dashboard_html)
        self.assertIn("selectEpisodeObservations(observationsPayload)", dashboard_html)
        self.assertIn("./nextdns_summary.json", dashboard_html)
        self.assertIn("./internet_conditions.json", dashboard_html)
        self.assertIn("./aps_power_context.json", dashboard_html)
        self.assertIn("./investigation.json", investigation_html)
        self.assertNotIn("observations.json", investigation_html)
        self.assertNotIn("internet_conditions.json", investigation_html)
        self.assertNotIn("aps_power_context.json", investigation_html)

    def test_transform_module_does_not_call_openrouter_or_output_producer(self):
        source = MODULE_PATH.read_text()
        self.assertNotIn("openrouter", source.lower())
        self.assertNotIn("build_operator_assistant_output", source)

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
