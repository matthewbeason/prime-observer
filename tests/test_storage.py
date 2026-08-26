import csv
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import collector
import evaluate_storage_read_path
import storage


CURRENT_FIELDS = list(storage.CSV_FIELDS)
LEGACY_FIELDS = [field for field in CURRENT_FIELDS if field not in ("target_label", "target_class")]


def sample_row(**overrides):
    row = {
        "ts": "2026-08-26T12:00:00-07:00",
        "phase_label": "steady",
        "host": "1.1.1.1",
        "target_label": "Cloudflare",
        "target_class": "internet_probe",
        "sent": "10",
        "received": "10",
        "loss_pct": "0.0",
        "avg_ms": "12.125",
        "p50_ms": "12.0",
        "p95_ms": "13.5",
        "max_ms": "14.0",
        "jitter_ms": "0.75",
        "traceroute_snip": "1 192.0.2.1 | 2 198.51.100.1",
        "speedtest_down_mbps": "500.25",
        "speedtest_up_mbps": "40.5",
        "speedtest_ping_ms": "8.25",
        "speedtest_raw_json": '{"type":"result"}',
    }
    row.update(overrides)
    return row


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data = self.base / "data"
        self.data.mkdir()
        self.database = self.data / "prime_observer.db"

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self):
        storage.initialize_database(self.database)
        return closing(storage.connect(self.database))

    def write_csv(self, name, rows, fields=CURRENT_FIELDS):
        path = self.data / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_schema_initialization_and_permissions(self):
        self.assertEqual(storage.initialize_database(self.database), storage.SCHEMA_VERSION)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)
        with closing(storage.connect(self.database)) as connection:
            self.assertEqual(storage.verify_schema(connection), 1)
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertIn("raw_probe_observations", tables)
        self.assertIn("observation_sources", tables)

    def test_unsupported_newer_schema_fails_safely(self):
        storage.initialize_database(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        with self.assertRaises(storage.UnsupportedSchemaVersion):
            storage.connect(self.database)

    def test_missing_and_recreated_database(self):
        with self.assertRaises(storage.DatabaseNotInitialized):
            storage.connect(self.database)
        with self.initialize() as connection:
            storage.ingest_rows(connection, [sample_row()], source_file="data/one.csv")
            self.assertEqual(storage.observation_count(connection), 1)
        self.database.unlink()
        storage.initialize_database(self.database)
        with closing(storage.connect(self.database)) as connection:
            self.assertEqual(storage.observation_count(connection), 0)

    def test_read_only_connection_enforces_query_only(self):
        storage.initialize_database(self.database)
        with closing(storage.connect_read_only(self.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO source_files(source_file) VALUES ('not-allowed.csv')"
                )

    def test_historical_csv_ingestion_supports_legacy_and_current_schema(self):
        current = self.write_csv("bakeoff_20260826.csv", [sample_row()])
        legacy_row = sample_row(ts="2026-08-25T12:00:00-07:00", host="9.9.9.9")
        legacy = self.write_csv("bakeoff_20260825.csv", [legacy_row], LEGACY_FIELDS)
        with self.initialize() as connection:
            result = storage.ingest_corpus(connection, self.data)
            self.assertEqual(result.source_files, 2)
            self.assertEqual(result.rows_considered, 2)
            self.assertEqual(result.inserted_rows, 2)
            rows = storage.observations_between(
                connection,
                "2026-08-25T00:00:00-07:00",
                "2026-08-27T00:00:00-07:00",
            )
        self.assertEqual([row["host"] for row in rows], ["9.9.9.9", "1.1.1.1"])
        self.assertIsNone(rows[0]["target_label"])
        self.assertEqual(rows[1]["target_class"], "internet_probe")
        self.assertTrue(current.exists() and legacy.exists())

    def test_transition_file_recovers_current_width_rows_under_legacy_header(self):
        path = self.data / "bakeoff_20260615.csv"
        legacy = sample_row(ts="2026-06-15T10:29:01-07:00")
        current = sample_row(ts="2026-06-15T10:30:17-07:00", host="192.168.1.1")
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(LEGACY_FIELDS)
            writer.writerow([legacy[field] for field in LEGACY_FIELDS])
            writer.writerow([current[field] for field in CURRENT_FIELDS])
        with self.initialize() as connection:
            result = storage.ingest_csv(connection, path)
            rows = list(
                connection.execute(
                    "SELECT observed_at, host, target_label, target_class FROM raw_probe_observations ORDER BY observed_at_epoch_us"
                )
            )
        self.assertEqual((result.valid_rows, result.rejected_rows), (2, 0))
        self.assertIsNone(rows[0]["target_label"])
        self.assertEqual(rows[1]["target_label"], "Cloudflare")
        self.assertEqual(rows[1]["target_class"], "internet_probe")

    def test_duplicate_ingestion_is_idempotent(self):
        path = self.write_csv("bakeoff_20260826.csv", [sample_row()])
        with self.initialize() as connection:
            first = storage.ingest_csv(connection, path)
            second = storage.ingest_csv(connection, path)
            self.assertEqual((first.inserted_rows, first.duplicate_rows), (1, 0))
            self.assertEqual((second.inserted_rows, second.duplicate_rows), (0, 1))
            self.assertEqual(storage.observation_count(connection), 1)

    def test_deterministic_identity_uses_normalized_raw_evidence(self):
        first = sample_row(sent="10", loss_pct="0.0")
        second = dict(reversed(list(sample_row(sent="10.0", loss_pct="0").items())))
        self.assertEqual(
            storage.normalize_observation(first)["observation_id"],
            storage.normalize_observation(second)["observation_id"],
        )
        distinct = sample_row(p95_ms="13.6")
        self.assertNotEqual(
            storage.normalize_observation(first)["observation_id"],
            storage.normalize_observation(distinct)["observation_id"],
        )

    def test_failed_batch_rolls_back_all_valid_rows(self):
        with self.initialize() as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_bad_host BEFORE INSERT ON raw_probe_observations
                WHEN NEW.host = 'bad.example'
                BEGIN SELECT RAISE(ABORT, 'injected write failure'); END
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                storage.ingest_rows(
                    connection,
                    [sample_row(), sample_row(host="bad.example")],
                    source_file="data/batch.csv",
                )
            self.assertEqual(storage.observation_count(connection), 0)

    def test_locked_database_rejects_shadow_batch_without_partial_write(self):
        storage.initialize_database(self.database)
        with (
            closing(storage.connect(self.database)) as writer,
            closing(storage.connect(self.database)) as shadow,
        ):
            shadow.execute("PRAGMA busy_timeout = 1")
            writer.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(sqlite3.OperationalError):
                storage.ingest_rows(
                    shadow,
                    [sample_row()],
                    source_file="data/locked.csv",
                )
            writer.rollback()
            self.assertEqual(storage.observation_count(shadow), 0)

    def test_malformed_records_are_visible_and_valid_records_commit(self):
        path = self.write_csv(
            "bakeoff_20260826.csv",
            [sample_row(), sample_row(ts="not-a-time"), sample_row(p95_ms="NaN")],
        )
        with self.initialize() as connection:
            result = storage.ingest_csv(connection, path)
            self.assertEqual(storage.observation_count(connection), 1)
        self.assertEqual(result.rows_considered, 3)
        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.rejected_rows, 2)
        self.assertIn("timestamp", result.rejections[0].reason)
        self.assertIn("finite", result.rejections[1].reason)

    def test_bounded_query_is_chronological_and_inclusive(self):
        rows = [
            sample_row(ts="2026-08-26T12:02:00-07:00", host="9.9.9.9"),
            sample_row(ts="2026-08-26T12:01:00-07:00", host="1.1.1.1"),
            sample_row(ts="2026-08-26T12:03:00-07:00", host="192.168.1.1"),
        ]
        with self.initialize() as connection:
            storage.ingest_rows(connection, rows, source_file="data/bounds.csv")
            selected = storage.observations_between(
                connection,
                "2026-08-26T12:01:00-07:00",
                "2026-08-26T12:02:00-07:00",
            )
        self.assertEqual([row["host"] for row in selected], ["1.1.1.1", "9.9.9.9"])

    def test_phase_two_raw_query_is_typed_filtered_and_deterministic(self):
        rows = [
            sample_row(ts="2026-08-26T12:02:00-07:00", host="9.9.9.9", phase_label="busy"),
            sample_row(ts="2026-08-26T12:01:00-07:00", host="1.1.1.1", avg_ms=""),
            sample_row(ts="2026-08-26T12:01:00-07:00", host="9.9.9.9", phase_label="busy"),
        ]
        with self.initialize() as connection:
            storage.ingest_rows(connection, rows, source_file="data/raw-query.csv")
            selected = storage.raw_observations_between(
                connection,
                "2026-08-26T12:00:00-07:00",
                "2026-08-26T12:02:00-07:00",
                hosts=["9.9.9.9"],
                phases=["busy"],
            )
        self.assertEqual([row["ts"] for row in selected], [rows[2]["ts"], rows[0]["ts"]])
        self.assertEqual(tuple(selected[0]), storage.CSV_FIELDS)
        self.assertIsInstance(selected[0]["sent"], int)
        self.assertIsInstance(selected[0]["p95_ms"], float)

    def test_phase_two_raw_query_rejects_invalid_bounds(self):
        with self.initialize() as connection:
            with self.assertRaisesRegex(ValueError, "UTC offsets"):
                storage.raw_observations_between(
                    connection, "2026-08-26T12:00:00", "2026-08-26T13:00:00"
                )
            with self.assertRaisesRegex(ValueError, "after end"):
                storage.raw_observations_between(
                    connection,
                    "2026-08-26T13:00:00-07:00",
                    "2026-08-26T12:00:00-07:00",
                )

    def test_csv_sqlite_field_equivalence_and_null_semantics(self):
        row = sample_row(avg_ms="", traceroute_snip="", speedtest_raw_json="")
        path = self.write_csv("bakeoff_20260826.csv", [row])
        with self.initialize() as connection:
            storage.ingest_csv(connection, path)
            stored = dict(connection.execute("SELECT * FROM raw_probe_observations").fetchone())
        self.assertEqual(stored["observed_at"], row["ts"])
        self.assertEqual(stored["phase_label"], row["phase_label"])
        self.assertEqual(stored["host"], row["host"])
        self.assertEqual(stored["received"], 10)
        self.assertEqual(stored["loss_pct"], 0.0)
        self.assertEqual(stored["p95_ms"], 13.5)
        self.assertIsNone(stored["avg_ms"])
        self.assertIsNone(stored["traceroute_snip"])
        self.assertIsNone(stored["speedtest_raw_json"])

    def test_source_provenance_and_reconciliation_status(self):
        path = self.write_csv("bakeoff_20260826.csv", [sample_row()])
        with self.initialize() as connection:
            storage.ingest_csv(connection, path)
            source = connection.execute(
                """
                SELECT s.*, f.source_file
                FROM observation_sources s JOIN source_files f USING (source_id)
                """
            ).fetchone()
            status = storage.database_status(connection, self.database, self.data)
        self.assertEqual(source["source_file"], str(path.resolve()))
        self.assertEqual(source["source_row"], 2)
        self.assertEqual(source["source_kind"], "historical_csv")
        self.assertTrue(status["reconciliation_current"])
        self.assertEqual(
            status["last_successfully_ingested_observation"]["source_file"],
            str(path.resolve()),
        )
        self.assertIsNone(status["last_successful_shadow"])
        with path.open("a") as handle:
            handle.write("\n")
        with closing(storage.connect(self.database)) as connection:
            self.assertFalse(storage.database_status(connection, self.database, self.data)["reconciliation_current"])

    def test_integrity_check(self):
        with self.initialize() as connection:
            self.assertEqual(storage.integrity_check(connection), "ok")

    def test_phase_two_harness_full_row_equivalence_and_duplicate_visibility(self):
        path = self.write_csv(
            "bakeoff_20260826.csv",
            [
                sample_row(avg_ms="", traceroute_snip=""),
                sample_row(ts="2026-08-26T12:01:00-07:00", received="0", loss_pct="100", p95_ms=""),
            ],
        )
        with self.initialize() as connection:
            storage.ingest_csv(connection, path)
            csv_rows, _ = evaluate_storage_read_path.read_csv_window(
                self.data,
                storage.DEFAULT_PATTERN,
                "2026-08-26T12:00:00-07:00",
                "2026-08-26T12:02:00-07:00",
            )
            sqlite_rows, _ = evaluate_storage_read_path.read_sqlite_window(
                connection,
                "2026-08-26T12:00:00-07:00",
                "2026-08-26T12:02:00-07:00",
            )
        comparison = evaluate_storage_read_path.compare_rows(csv_rows, sqlite_rows)
        self.assertTrue(comparison["equivalent"])
        self.assertIsNone(csv_rows[0]["avg_ms"])
        self.assertEqual(csv_rows[1]["received"], 0)
        self.assertEqual(csv_rows[1]["loss_pct"], 100.0)
        self.assertIsNone(csv_rows[1]["p95_ms"])

        duplicate = evaluate_storage_read_path.compare_rows(
            csv_rows + [csv_rows[0]], sqlite_rows
        )
        self.assertFalse(duplicate["equivalent"])
        self.assertEqual(duplicate["csv_duplicate_occurrences"], 1)


class CollectorShadowFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmp.name) / "data"
        self.outdir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_shadow_failure_does_not_break_authoritative_csv(self):
        ping_result = {
            "sent": 10,
            "received": 0,
            "loss_pct": 100.0,
            "avg_ms": "",
            "p50_ms": "",
            "p95_ms": "",
            "max_ms": "",
            "jitter_ms": "",
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(collector, "OUTDIR", self.outdir),
            mock.patch.object(collector, "TARGETS", ("192.168.1.1",)),
            mock.patch.object(collector, "read_phase", return_value="test"),
            mock.patch.object(collector, "ping_target", return_value=ping_result),
            mock.patch.object(collector, "traceroute_snip", return_value=""),
            mock.patch.object(collector, "run_ookla_speedtest", return_value=None),
            mock.patch.object(storage, "connect", side_effect=sqlite3.OperationalError("locked")),
            mock.patch("sys.stderr", new=stderr),
        ):
            collector.main()
        files = list(self.outdir.glob("bakeoff_*.csv"))
        self.assertEqual(len(files), 1)
        with files[0].open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["host"], "192.168.1.1")
        self.assertIn("CSV collection remains authoritative", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
