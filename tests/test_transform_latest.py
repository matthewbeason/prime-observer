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


if __name__ == "__main__":
    unittest.main()
