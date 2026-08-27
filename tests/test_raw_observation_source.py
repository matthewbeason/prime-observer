import csv
import datetime as dt
import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import raw_observation_source
import storage


FIELDS = list(storage.CSV_FIELDS)


def observation(ts="2026-08-26T12:00:00-07:00", host="1.1.1.1", p95="25"):
    return {
        "ts": ts,
        "phase_label": "FIBER",
        "host": host,
        "target_label": "Cloudflare",
        "target_class": "internet_probe",
        "sent": "10",
        "received": "10",
        "loss_pct": "0",
        "avg_ms": "20",
        "p50_ms": "19",
        "p95_ms": p95,
        "max_ms": "30",
        "jitter_ms": "2",
        "traceroute_snip": "",
        "speedtest_down_mbps": "",
        "speedtest_up_mbps": "",
        "speedtest_ping_ms": "",
        "speedtest_raw_json": "",
    }


class RawObservationSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.database = self.data / "prime_observer.db"
        self.csv_path = self.data / "bakeoff_20260826.csv"
        self.rows = [
            observation(),
            observation("2026-08-26T12:10:00-07:00", "9.9.9.9", "30"),
        ]
        self.write_csv(self.rows)
        storage.initialize_database(self.database)
        with closing(storage.connect(self.database)) as connection:
            storage.ingest_csv(connection, self.csv_path)

    def tearDown(self):
        self.tmp.cleanup()

    def write_csv(self, rows):
        with self.csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def read(self, policy=raw_observation_source.PREFER_SQLITE, end="2026-08-26T12:15:00-07:00"):
        return raw_observation_source.read_raw_observations(
            "2026-08-26T11:55:00-07:00",
            end,
            data_directory=self.data,
            database=self.database,
            source_files=(self.csv_path,),
            source_policy=policy,
        )

    def test_healthy_sqlite_is_used(self):
        result = self.read()
        self.assertEqual(result.diagnostics.source_used, "sqlite")
        self.assertIsNone(result.diagnostics.fallback_reason)
        self.assertEqual(result.diagnostics.row_count, 2)
        self.assertEqual(result.diagnostics.files_scanned, 0)

    def test_missing_sqlite_falls_back_to_csv(self):
        self.database.unlink()
        result = self.read()
        self.assertEqual(result.diagnostics.source_used, "csv")
        self.assertIn("does not exist", result.diagnostics.fallback_reason)
        self.assertEqual(len(result.rows), 2)

    def test_schema_incompatible_falls_back_to_csv(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(f"PRAGMA user_version = {storage.SCHEMA_VERSION + 1}")
        result = self.read()
        self.assertEqual(result.diagnostics.source_used, "csv")
        self.assertIn("newer than supported", result.diagnostics.fallback_reason)

    def test_sqlite_read_failure_falls_back_to_csv(self):
        with mock.patch.object(
            raw_observation_source.storage,
            "connect_read_only",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            result = self.read()
        self.assertEqual(result.diagnostics.source_used, "csv")
        self.assertIn("database is locked", result.diagnostics.fallback_reason)

    def test_corrupt_sqlite_falls_back_to_csv(self):
        self.database.write_bytes(b"not a sqlite database")
        result = self.read()
        self.assertEqual(result.diagnostics.source_used, "csv")
        self.assertIsNotNone(result.diagnostics.fallback_reason)

    def test_stale_reconciliation_affecting_interval_falls_back(self):
        self.rows.append(observation("2026-08-26T12:20:00-07:00", "1.1.1.1", "40"))
        self.write_csv(self.rows)
        result = self.read(end="2026-08-26T12:25:00-07:00")
        self.assertEqual(result.diagnostics.source_used, "csv")
        self.assertIn("does not safely cover", result.diagnostics.fallback_reason)
        self.assertEqual(len(result.rows), 3)

    def test_append_only_lag_before_safe_through_uses_sqlite(self):
        self.rows.append(observation("2026-08-26T12:20:00-07:00", "1.1.1.1", "40"))
        self.write_csv(self.rows)
        result = self.read(end="2026-08-26T12:05:00-07:00")
        self.assertEqual(result.diagnostics.source_used, "sqlite")
        self.assertEqual(
            result.diagnostics.reconciliation["sources"][0]["status"],
            "append_only_lag_safe",
        )
        self.assertEqual(len(result.rows), 1)

    def test_verification_mismatch_returns_csv(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE raw_probe_observations SET p95_ms = 999 WHERE host = ?",
                ("9.9.9.9",),
            )
            connection.commit()
        result = self.read(raw_observation_source.VERIFY_SQLITE)
        self.assertEqual(result.diagnostics.source_used, "csv")
        self.assertIn("equivalence", result.diagnostics.fallback_reason)
        self.assertFalse(result.diagnostics.verification["equivalent"])
        self.assertEqual(len(result.rows), 2)

    def test_stored_multiplicity_validation_failure_falls_back(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "DELETE FROM raw_probe_observations WHERE host = ?", ("9.9.9.9",)
            )
            connection.commit()
        result = self.read()
        self.assertEqual(result.diagnostics.source_used, "csv")
        self.assertEqual(
            result.diagnostics.reconciliation["sources"][0]["status"],
            "row_count_mismatch",
        )
        self.assertEqual(len(result.rows), 2)

    def test_csv_fallback_preserves_canonical_rows(self):
        sqlite_result = self.read(raw_observation_source.VERIFY_SQLITE)
        self.database.unlink()
        fallback_result = self.read()
        self.assertEqual(sqlite_result.rows, fallback_result.rows)

    def test_diagnostics_log_source_and_fallback_reason(self):
        self.database.unlink()
        result = self.read()
        stream = io.StringIO()
        raw_observation_source.log_read_diagnostics("canary", result.diagnostics, stream=stream)
        text = stream.getvalue()
        self.assertIn('"source_used": "csv"', text)
        self.assertIn('"fallback_reason":', text)
        self.assertIn('"reader": "canary"', text)

    def test_browser_remains_database_unaware(self):
        dashboard = (ROOT / "viz" / "index.html").read_text()
        investigation = (ROOT / "viz" / "investigate.html").read_text()
        for source in (dashboard, investigation):
            self.assertNotIn("prime_observer.db", source)
            self.assertNotIn("sqlite", source.lower())


if __name__ == "__main__":
    unittest.main()
