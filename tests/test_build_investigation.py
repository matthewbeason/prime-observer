import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "build_investigation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_investigation", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildInvestigationTest(unittest.TestCase):
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
        self.module.OUT = self.viz_dir / "investigation.json"
        self.module.INDEX_OUT = self.viz_dir / "investigation_index.json"
        self.module.NEXTDNS_SUMMARY = self.viz_dir / "nextdns_summary.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_rows(self, rows):
        path = self.data_dir / "bakeoff_20260608.csv"
        fields = [
            "ts",
            "host",
            "phase_label",
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
            for row in rows:
                writer.writerow(row)

    def telemetry_row(self, ts, host, p95, jitter=5, loss=0):
        return {
            "ts": ts,
            "host": host,
            "phase_label": "wan",
            "sent": "10",
            "received": "10",
            "loss_pct": str(loss),
            "avg_ms": "20",
            "p50_ms": "20",
            "p95_ms": str(p95),
            "max_ms": str(p95 + 10),
            "jitter_ms": str(jitter),
        }

    def test_investigation_metadata_is_additive(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
            self.telemetry_row("2026-06-08T12:05:00+00:00", "9.9.9.9", 180),
            self.telemetry_row("2026-06-08T12:10:00+00:00", "1.1.1.1", 190),
            self.telemetry_row("2026-06-08T12:15:00+00:00", "192.168.1.1", 8),
        ])

        payload = self.module.build_investigation(
            "2026-06-08T12:05:00+00:00",
            "2026-06-08T12:10:00+00:00",
            15,
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("periods", payload)
        self.assertIn("timeline_samples", payload)
        self.assertIn("dns_context", payload)
        self.assertTrue(payload["id"].startswith("investigation-"))
        self.assertEqual(payload["status"], "available")
        self.assertGreaterEqual(len(payload["events"]), 2)
        self.assertEqual(payload["navigation"]["first_event"], payload["events"][0]["id"])
        self.assertEqual(payload["navigation"]["last_event"], payload["events"][-1]["id"])
        for event in payload["events"]:
            self.assertIn(event["id"], payload["navigation"]["events"])
        neighborhoods = {item["event_id"]: item for item in payload["event_neighborhoods"]}
        self.assertEqual(set(neighborhoods), {event["id"] for event in payload["events"]})
        self.assertTrue(any(
            "temporal_proximity" in nearby["signals"]
            for item in neighborhoods.values()
            for nearby in item["nearby_events"]
        ))

    def test_index_upserts_catalog_entry(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        out = self.viz_dir / "investigation.json"
        index = self.viz_dir / "investigation_index.json"
        first = self.module.update_index(index, payload, out)
        second = self.module.update_index(index, payload, out)
        written = json.loads(index.read_text())

        self.assertEqual(len(first["investigations"]), 1)
        self.assertEqual(len(second["investigations"]), 1)
        self.assertEqual(len(written["investigations"]), 1)
        entry = written["investigations"][0]
        self.assertEqual(entry["id"], payload["id"])
        self.assertEqual(entry["title"], payload["title"])
        self.assertEqual(entry["created_at"], payload["generated_at"])
        self.assertEqual(entry["event_count"], len(payload["events"]))
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["path"], "viz/investigation.json")


if __name__ == "__main__":
    unittest.main()
