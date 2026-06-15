import csv
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "transform_latest.py"


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


if __name__ == "__main__":
    unittest.main()
