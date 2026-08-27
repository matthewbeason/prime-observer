#!/usr/bin/env python3
"""Prime Observer storage boundary for raw probe observations.

CSV remains authoritative. SQLite is a preferred, non-authoritative source for
approved low-risk production readers and a shadow target for ingestion. This
module owns raw persistence and bounded raw queries only; it owns no semantics.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence


BASE = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = BASE / "data" / "prime_observer.db"
DEFAULT_PATTERN = "bakeoff_*.csv"
SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 2_000
BACKUP_ENVIRONMENT = "PRIME_OBSERVER_BACKUP_DIR"
DEFAULT_BACKUP_DIRECTORY = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "Prime Observer Backups"
)
DEFAULT_DAILY_BACKUPS = 7
DEFAULT_WEEKLY_BACKUPS = 4
DEFAULT_MONTHLY_BACKUPS = 3
BACKUP_OVERDUE_HOURS = 36
BACKUP_PREFIX = "prime-observer-"
BACKUP_SUFFIX = ".sqlite3"
MANIFEST_SUFFIX = ".manifest.json"

CSV_FIELDS = (
    "ts",
    "phase_label",
    "host",
    "target_label",
    "target_class",
    "sent",
    "received",
    "loss_pct",
    "avg_ms",
    "p50_ms",
    "p95_ms",
    "max_ms",
    "jitter_ms",
    "traceroute_snip",
    "speedtest_down_mbps",
    "speedtest_up_mbps",
    "speedtest_ping_ms",
    "speedtest_raw_json",
)
LEGACY_CSV_FIELDS = tuple(
    field for field in CSV_FIELDS if field not in ("target_label", "target_class")
)
REQUIRED_FIELDS = ("ts", "phase_label", "host")
INTEGER_FIELDS = ("sent", "received")
REAL_FIELDS = (
    "loss_pct",
    "avg_ms",
    "p50_ms",
    "p95_ms",
    "max_ms",
    "jitter_ms",
    "speedtest_down_mbps",
    "speedtest_up_mbps",
    "speedtest_ping_ms",
)
TEXT_FIELDS = (
    "phase_label",
    "host",
    "target_label",
    "target_class",
    "traceroute_snip",
    "speedtest_raw_json",
)


class StorageError(RuntimeError):
    """Base error for shadow storage failures."""


class DatabaseNotInitialized(StorageError):
    """The database is missing or does not contain the Phase 1 schema."""


class UnsupportedSchemaVersion(StorageError):
    """The database schema is newer than this code understands."""


class MalformedObservation(ValueError):
    """One CSV record cannot be represented as a raw observation."""


class BackupValidationError(StorageError):
    """A backup is incomplete, incompatible, corrupt, or inconsistent."""


class StorageBusy(StorageError):
    """A storage writer is active, so destructive maintenance must wait."""


@dataclass(frozen=True)
class RejectedRow:
    source_file: str
    source_row: int | None
    reason: str


@dataclass(frozen=True)
class IngestionResult:
    source_file: str
    rows_considered: int
    valid_rows: int
    inserted_rows: int
    duplicate_rows: int
    rejected_rows: int
    earliest_timestamp: str | None
    latest_timestamp: str | None
    rejections: tuple[RejectedRow, ...] = ()


@dataclass(frozen=True)
class CorpusResult:
    source_files: int
    rows_considered: int
    valid_rows: int
    inserted_rows: int
    duplicate_rows: int
    rejected_rows: int
    earliest_timestamp: str | None
    latest_timestamp: str | None
    files: tuple[IngestionResult, ...]


SCHEMA_SQL = """
CREATE TABLE schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    initialized_at TEXT NOT NULL
);

CREATE TABLE raw_probe_observations (
    observation_id BLOB PRIMARY KEY,
    observed_at TEXT NOT NULL,
    observed_at_epoch_us INTEGER NOT NULL,
    phase_label TEXT NOT NULL,
    host TEXT NOT NULL,
    target_label TEXT,
    target_class TEXT,
    sent INTEGER,
    received INTEGER,
    loss_pct REAL,
    avg_ms REAL,
    p50_ms REAL,
    p95_ms REAL,
    max_ms REAL,
    jitter_ms REAL,
    traceroute_snip TEXT,
    speedtest_down_mbps REAL,
    speedtest_up_mbps REAL,
    speedtest_ping_ms REAL,
    speedtest_raw_json TEXT,
    created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE INDEX raw_probe_observations_time_idx
ON raw_probe_observations(observed_at_epoch_us, host, observation_id);

CREATE INDEX raw_probe_observations_host_time_idx
ON raw_probe_observations(host, observed_at_epoch_us, observation_id);

CREATE TABLE source_files (
    source_id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    UNIQUE(source_file)
);

CREATE TABLE observation_sources (
    observation_id BLOB NOT NULL REFERENCES raw_probe_observations(observation_id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES source_files(source_id) ON DELETE CASCADE,
    source_row INTEGER,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('historical_csv', 'collector_shadow')),
    first_ingested_at TEXT NOT NULL,
    PRIMARY KEY (observation_id, source_id)
) WITHOUT ROWID;

CREATE INDEX observation_sources_file_idx ON observation_sources(source_id);

CREATE TABLE ingestion_sources (
    source_id INTEGER PRIMARY KEY REFERENCES source_files(source_id) ON DELETE CASCADE,
    source_size INTEGER,
    source_mtime_ns INTEGER,
    source_sha256 TEXT,
    rows_considered INTEGER NOT NULL,
    valid_rows INTEGER NOT NULL,
    rejected_rows INTEGER NOT NULL,
    earliest_timestamp TEXT,
    latest_timestamp TEXT,
    last_successful_ingest_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def epoch_microseconds(timestamp: dt.datetime) -> int:
    utc = timestamp.astimezone(dt.timezone.utc)
    delta = utc - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return ((delta.days * 86_400) + delta.seconds) * 1_000_000 + delta.microseconds


def _configure(connection: sqlite3.Connection) -> None:
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = FULL")
    journal_mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    if str(journal_mode).lower() != "delete":
        raise StorageError(f"SQLite refused rollback-journal mode: {journal_mode}")


def connect(database: Path | str = DEFAULT_DATABASE) -> sqlite3.Connection:
    path = Path(database)
    if not path.exists():
        raise DatabaseNotInitialized(f"shadow database does not exist: {path}")
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    try:
        verify_schema(connection)
        _configure(connection)
    except Exception:
        connection.close()
        raise
    return connection


def connect_read_only(database: Path | str = DEFAULT_DATABASE) -> sqlite3.Connection:
    """Open the shadow database without write authority for diagnostic reads."""
    path = Path(database)
    if not path.exists():
        raise DatabaseNotInitialized(f"shadow database does not exist: {path}")
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA query_only = ON")
        verify_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


def initialize_database(database: Path | str = DEFAULT_DATABASE) -> int:
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        connection.row_factory = sqlite3.Row
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_metadata'"
        ).fetchone()
        if current > SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"database schema {current} is newer than supported schema {SCHEMA_VERSION}"
            )
        _configure(connection)
        if current == SCHEMA_VERSION and tables:
            verify_schema(connection)
            return current
        if current != 0 or tables:
            raise DatabaseNotInitialized(
                f"database has unsupported partial schema state (user_version={current})"
            )
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL)
            connection.execute(
                "INSERT INTO schema_metadata(singleton, schema_version, initialized_at) VALUES (1, ?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        verify_schema(connection)
        return SCHEMA_VERSION
    finally:
        connection.close()
        if path.exists():
            os.chmod(path, 0o600)


def verify_schema(connection: sqlite3.Connection) -> int:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"database schema {version} is newer than supported schema {SCHEMA_VERSION}"
        )
    if version == 0:
        raise DatabaseNotInitialized("shadow database is uninitialized")
    if version != SCHEMA_VERSION:
        raise DatabaseNotInitialized(
            f"database schema {version} is not supported by schema {SCHEMA_VERSION} code"
        )
    try:
        recorded = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE singleton = 1"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise DatabaseNotInitialized("shadow database schema metadata is missing") from exc
    if recorded is None or int(recorded[0]) != version:
        raise DatabaseNotInitialized("shadow database schema metadata does not match user_version")
    return version


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _canonical_decimal(value: object, field: str) -> tuple[str | None, float | int | None]:
    if value is None or str(value).strip() == "":
        return None, None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise MalformedObservation(f"{field} is not numeric") from exc
    if not number.is_finite():
        raise MalformedObservation(f"{field} must be finite")
    canonical = format(number.normalize(), "f")
    if canonical == "-0":
        canonical = "0"
    if field in INTEGER_FIELDS:
        if number != number.to_integral_value():
            raise MalformedObservation(f"{field} must be an integer")
        return canonical, int(number)
    return canonical, float(number)


def normalize_observation(row: Mapping[str, object]) -> dict[str, object]:
    if None in row:
        raise MalformedObservation("record has fields beyond the declared CSV header")
    missing = [field for field in REQUIRED_FIELDS if not str(row.get(field) or "").strip()]
    if missing:
        raise MalformedObservation(f"missing required field(s): {', '.join(missing)}")

    observed_at = str(row["ts"]).strip()
    try:
        timestamp = dt.datetime.fromisoformat(observed_at)
    except ValueError as exc:
        raise MalformedObservation("ts is not an ISO 8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise MalformedObservation("ts must include a UTC offset")
    epoch_us = epoch_microseconds(timestamp)

    values: dict[str, object] = {
        "observed_at": observed_at,
        "observed_at_epoch_us": epoch_us,
    }
    canonical: dict[str, object] = {"ts": observed_at}
    for field in TEXT_FIELDS:
        value = _optional_text(row.get(field))
        if field in ("phase_label", "host") and value is not None:
            value = value.strip()
        values[field] = value
        canonical[field] = value
    for field in (*INTEGER_FIELDS, *REAL_FIELDS):
        encoded, value = _canonical_decimal(row.get(field), field)
        values[field] = value
        canonical[field] = encoded

    identity_bytes = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    values["observation_id"] = hashlib.sha256(identity_bytes).digest()
    return values


def _source_name(path: Path) -> str:
    try:
        return path.resolve().relative_to(BASE.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _source_fingerprint(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


def source_name(path: Path | str) -> str:
    """Return the canonical storage provenance name for one CSV path."""
    return _source_name(Path(path))


def source_read_status(
    connection: sqlite3.Connection,
    paths: Sequence[Path | str],
    *,
    requested_end: str,
) -> dict[str, object]:
    """Report whether reconciled SQLite rows are safe for a bounded read.

    Exact source fingerprints are accepted. An append-only lag is accepted only
    when the already-reconciled byte prefix is unchanged and the requested end
    is no later than that source's recorded latest timestamp.
    """
    end_dt = dt.datetime.fromisoformat(requested_end)
    if end_dt.tzinfo is None:
        raise ValueError("bounded query timestamps must include UTC offsets")
    requested_end_us = epoch_microseconds(end_dt)
    results = []
    safe = bool(paths)
    for configured in paths:
        path = Path(configured)
        name = _source_name(path)
        row = connection.execute(
            """
            SELECT i.source_size, i.source_mtime_ns, i.source_sha256,
                   i.valid_rows, i.rejected_rows, i.earliest_timestamp,
                   i.latest_timestamp,
                   (SELECT COUNT(*) FROM observation_sources os
                    JOIN raw_probe_observations ro USING (observation_id)
                    WHERE os.source_id = i.source_id) AS stored_observation_rows
            FROM ingestion_sources i
            JOIN source_files f USING (source_id)
            WHERE f.source_file = ?
            """,
            (name,),
        ).fetchone()
        item: dict[str, object] = {"path": name, "status": "untracked", "safe": False}
        if row is None:
            safe = False
            results.append(item)
            continue
        try:
            stat = path.stat()
        except OSError as exc:
            safe = False
            item.update(status="unavailable", reason=str(exc))
            results.append(item)
            continue
        recorded_size = int(row["source_size"])
        recorded_hash = str(row["source_sha256"])
        latest_timestamp = row["latest_timestamp"]
        item.update(
            recorded_latest_timestamp=latest_timestamp,
            valid_rows=int(row["valid_rows"]),
            rejected_rows=int(row["rejected_rows"]),
            stored_observation_rows=int(row["stored_observation_rows"]),
        )
        if int(row["rejected_rows"]):
            safe = False
            item.update(status="rejected_rows", reason="last reconciliation rejected CSV rows")
            results.append(item)
            continue
        if int(row["stored_observation_rows"]) != int(row["valid_rows"]):
            safe = False
            item.update(
                status="row_count_mismatch",
                reason="stored source multiplicity does not match the reconciled CSV",
            )
            results.append(item)
            continue
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                remaining = recorded_size
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            safe = False
            item.update(status="unavailable", reason=str(exc))
            results.append(item)
            continue
        prefix_matches = (
            stat.st_size >= recorded_size
            and remaining == 0
            and digest.hexdigest() == recorded_hash
        )
        if prefix_matches and stat.st_size == recorded_size:
            item.update(status="exact", safe=True)
            results.append(item)
            continue
        latest_us = None
        if latest_timestamp:
            try:
                latest_us = epoch_microseconds(dt.datetime.fromisoformat(str(latest_timestamp)))
            except ValueError:
                latest_us = None
        if prefix_matches and latest_us is not None and requested_end_us <= latest_us:
            item.update(status="append_only_lag_safe", safe=True)
        else:
            safe = False
            item.update(
                status="stale_for_interval",
                reason="CSV reconciliation does not cover the requested interval",
            )
        results.append(item)
    return {"safe": safe, "sources": results}


def _insert_batch(
    connection: sqlite3.Connection,
    observations: Sequence[tuple[dict[str, object], int | None]],
    source_file: str,
    source_kind: str,
    source_fingerprint: tuple[int, int, str] | None,
    rows_considered: int,
    rejected_rows: int,
) -> tuple[int, int]:
    now = utc_now()
    inserted = 0
    duplicates = 0
    earliest = min(
        observations,
        key=lambda item: (int(item[0]["observed_at_epoch_us"]), str(item[0]["observed_at"])),
        default=None,
    )
    latest = max(
        observations,
        key=lambda item: (int(item[0]["observed_at_epoch_us"]), str(item[0]["observed_at"])),
        default=None,
    )
    earliest_timestamp = str(earliest[0]["observed_at"]) if earliest else None
    latest_timestamp = str(latest[0]["observed_at"]) if latest else None
    with connection:
        connection.execute(
            "INSERT INTO source_files(source_file) VALUES (?) ON CONFLICT(source_file) DO NOTHING",
            (source_file,),
        )
        source_id = int(
            connection.execute(
                "SELECT source_id FROM source_files WHERE source_file = ?", (source_file,)
            ).fetchone()[0]
        )
        for values, source_row in observations:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO raw_probe_observations(
                    observation_id, observed_at, observed_at_epoch_us, phase_label, host,
                    target_label, target_class, sent, received, loss_pct, avg_ms, p50_ms,
                    p95_ms, max_ms, jitter_ms, traceroute_snip, speedtest_down_mbps,
                    speedtest_up_mbps, speedtest_ping_ms, speedtest_raw_json, created_at
                ) VALUES (
                    :observation_id, :observed_at, :observed_at_epoch_us, :phase_label, :host,
                    :target_label, :target_class, :sent, :received, :loss_pct, :avg_ms, :p50_ms,
                    :p95_ms, :max_ms, :jitter_ms, :traceroute_snip, :speedtest_down_mbps,
                    :speedtest_up_mbps, :speedtest_ping_ms, :speedtest_raw_json, :created_at
                )
                """,
                {**values, "created_at": now},
            )
            if cursor.rowcount == 1:
                inserted += 1
            else:
                duplicates += 1
            connection.execute(
                """
                INSERT INTO observation_sources(
                    observation_id, source_id, source_row, source_kind, first_ingested_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(observation_id, source_id) DO UPDATE SET
                    source_row = COALESCE(observation_sources.source_row, excluded.source_row)
                """,
                (values["observation_id"], source_id, source_row, source_kind, now),
            )
        if source_fingerprint is not None:
            connection.execute(
                """
                INSERT INTO ingestion_sources(
                    source_id, source_size, source_mtime_ns, source_sha256,
                    rows_considered, valid_rows, rejected_rows,
                    earliest_timestamp, latest_timestamp, last_successful_ingest_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_size = excluded.source_size,
                    source_mtime_ns = excluded.source_mtime_ns,
                    source_sha256 = excluded.source_sha256,
                    rows_considered = excluded.rows_considered,
                    valid_rows = excluded.valid_rows,
                    rejected_rows = excluded.rejected_rows,
                    earliest_timestamp = excluded.earliest_timestamp,
                    latest_timestamp = excluded.latest_timestamp,
                    last_successful_ingest_at = excluded.last_successful_ingest_at
                """,
                (
                    source_id,
                    *source_fingerprint,
                    rows_considered,
                    len(observations),
                    rejected_rows,
                    earliest_timestamp,
                    latest_timestamp,
                    now,
                ),
            )
    return inserted, duplicates


def ingest_rows(
    connection: sqlite3.Connection,
    rows: Iterable[Mapping[str, object]],
    *,
    source_file: str,
    source_kind: str = "collector_shadow",
) -> IngestionResult:
    observations: list[tuple[dict[str, object], int | None]] = []
    rejections: list[RejectedRow] = []
    considered = 0
    for row in rows:
        considered += 1
        try:
            observations.append((normalize_observation(row), None))
        except (MalformedObservation, ValueError) as exc:
            rejections.append(RejectedRow(source_file, None, str(exc)))
    inserted, duplicates = _insert_batch(
        connection,
        observations,
        source_file,
        source_kind,
        None,
        considered,
        len(rejections),
    )
    ordered = sorted(
        observations,
        key=lambda item: (int(item[0]["observed_at_epoch_us"]), str(item[0]["observed_at"])),
    )
    return IngestionResult(
        source_file,
        considered,
        len(observations),
        inserted,
        duplicates,
        len(rejections),
        str(ordered[0][0]["observed_at"]) if ordered else None,
        str(ordered[-1][0]["observed_at"]) if ordered else None,
        tuple(rejections),
    )


def ingest_csv(
    connection: sqlite3.Connection,
    path: Path | str,
    *,
    source_kind: str = "historical_csv",
) -> IngestionResult:
    csv_path = Path(path)
    source_file = _source_name(csv_path)
    observations: list[tuple[dict[str, object], int | None]] = []
    rejections: list[RejectedRow] = []
    considered = 0
    with csv_path.open("r", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header or any(field not in header for field in REQUIRED_FIELDS):
            raise MalformedObservation(f"{source_file}: missing required CSV header fields")
        unknown = set(header) - set(CSV_FIELDS)
        if unknown:
            raise MalformedObservation(
                f"{source_file}: unsupported CSV header field(s): {', '.join(sorted(unknown))}"
            )
        for record in reader:
            considered += 1
            if len(record) == len(header):
                row: Mapping[str | None, object] = dict(zip(header, record))
            elif tuple(header) == LEGACY_CSV_FIELDS and len(record) == len(CSV_FIELDS):
                # One retained transition-day file continued using its legacy
                # header after the collector added target metadata. The exact
                # current-width record is unambiguous and can be losslessly
                # aligned without rewriting the source CSV.
                row = dict(zip(CSV_FIELDS, record))
            else:
                row = dict(zip(header, record))
                if len(record) > len(header):
                    row[None] = record[len(header):]
            try:
                observations.append((normalize_observation(row), reader.line_num))
            except (MalformedObservation, ValueError) as exc:
                rejections.append(RejectedRow(source_file, reader.line_num, str(exc)))
    fingerprint = _source_fingerprint(csv_path)
    inserted, duplicates = _insert_batch(
        connection,
        observations,
        source_file,
        source_kind,
        fingerprint,
        considered,
        len(rejections),
    )
    ordered = sorted(
        observations,
        key=lambda item: (int(item[0]["observed_at_epoch_us"]), str(item[0]["observed_at"])),
    )
    return IngestionResult(
        source_file,
        considered,
        len(observations),
        inserted,
        duplicates,
        len(rejections),
        str(ordered[0][0]["observed_at"]) if ordered else None,
        str(ordered[-1][0]["observed_at"]) if ordered else None,
        tuple(rejections),
    )


def ingest_corpus(
    connection: sqlite3.Connection,
    data_directory: Path | str = BASE / "data",
    pattern: str = DEFAULT_PATTERN,
) -> CorpusResult:
    paths = sorted(Path(data_directory).glob(pattern), key=lambda item: item.name)
    results = tuple(ingest_csv(connection, path) for path in paths)
    timestamps = [
        timestamp
        for item in results
        for timestamp in (item.earliest_timestamp, item.latest_timestamp)
        if timestamp
    ]
    earliest = min(timestamps, key=lambda value: dt.datetime.fromisoformat(value).timestamp(), default=None)
    latest = max(timestamps, key=lambda value: dt.datetime.fromisoformat(value).timestamp(), default=None)
    return CorpusResult(
        len(results),
        sum(item.rows_considered for item in results),
        sum(item.valid_rows for item in results),
        sum(item.inserted_rows for item in results),
        sum(item.duplicate_rows for item in results),
        sum(item.rejected_rows for item in results),
        earliest,
        latest,
        results,
    )


def summarize_results(results: Sequence[IngestionResult]) -> CorpusResult:
    items = tuple(results)
    timestamps = [
        timestamp
        for item in items
        for timestamp in (item.earliest_timestamp, item.latest_timestamp)
        if timestamp
    ]
    return CorpusResult(
        len(items),
        sum(item.rows_considered for item in items),
        sum(item.valid_rows for item in items),
        sum(item.inserted_rows for item in items),
        sum(item.duplicate_rows for item in items),
        sum(item.rejected_rows for item in items),
        min(timestamps, key=lambda value: dt.datetime.fromisoformat(value).timestamp(), default=None),
        max(timestamps, key=lambda value: dt.datetime.fromisoformat(value).timestamp(), default=None),
        items,
    )


def observation_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM raw_probe_observations").fetchone()[0])


def observations_between(
    connection: sqlite3.Connection, start: str, end: str
) -> list[dict[str, object]]:
    """Return validation-oriented rows, including storage metadata.

    Kept for the Phase 1 command contract.  Production readers do not call it.
    Storage Phase 2 raw-reader evaluation uses ``raw_observations_between``.
    """
    start_dt = dt.datetime.fromisoformat(start)
    end_dt = dt.datetime.fromisoformat(end)
    if start_dt.tzinfo is None or end_dt.tzinfo is None:
        raise ValueError("bounded query timestamps must include UTC offsets")
    rows = connection.execute(
        """
        SELECT * FROM raw_probe_observations
        WHERE observed_at_epoch_us BETWEEN ? AND ?
        ORDER BY observed_at_epoch_us, host, observation_id
        """,
        (epoch_microseconds(start_dt), epoch_microseconds(end_dt)),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["observation_id"] = bytes(item["observation_id"]).hex()
        result.append(item)
    return result


RAW_OBSERVATION_SELECT = """
SELECT
    observed_at AS ts,
    phase_label,
    host,
    target_label,
    target_class,
    sent,
    received,
    loss_pct,
    avg_ms,
    p50_ms,
    p95_ms,
    max_ms,
    jitter_ms,
    traceroute_snip,
    speedtest_down_mbps,
    speedtest_up_mbps,
    speedtest_ping_ms,
    speedtest_raw_json
FROM raw_probe_observations
"""


def bounded_raw_observation_query(
    start: str,
    end: str,
    *,
    hosts: Sequence[str] = (),
    phases: Sequence[str] = (),
    source_files: Sequence[str] = (),
) -> tuple[str, tuple[object, ...]]:
    """Build a bounded raw-observation query without semantic classification."""
    start_dt = dt.datetime.fromisoformat(start)
    end_dt = dt.datetime.fromisoformat(end)
    if start_dt.tzinfo is None or end_dt.tzinfo is None:
        raise ValueError("bounded query timestamps must include UTC offsets")
    start_us = epoch_microseconds(start_dt)
    end_us = epoch_microseconds(end_dt)
    if start_us > end_us:
        raise ValueError("bounded query start must not be after end")

    host_values = tuple(dict.fromkeys(str(value) for value in hosts))
    phase_values = tuple(dict.fromkeys(str(value) for value in phases))
    clauses = ["observed_at_epoch_us BETWEEN ? AND ?"]
    parameters: list[object] = [start_us, end_us]
    if host_values:
        clauses.append(f"host IN ({','.join('?' for _ in host_values)})")
        parameters.extend(host_values)
    if phase_values:
        clauses.append(f"phase_label IN ({','.join('?' for _ in phase_values)})")
        parameters.extend(phase_values)
    source_values = tuple(dict.fromkeys(str(value) for value in source_files))
    if source_values:
        clauses.append(
            "EXISTS (SELECT 1 FROM observation_sources os "
            "JOIN source_files sf USING (source_id) "
            "WHERE os.observation_id = raw_probe_observations.observation_id "
            f"AND sf.source_file IN ({','.join('?' for _ in source_values)}))"
        )
        parameters.extend(source_values)
    sql = (
        RAW_OBSERVATION_SELECT
        + " WHERE "
        + " AND ".join(clauses)
        + " ORDER BY observed_at_epoch_us, host, observation_id"
    )
    return sql, tuple(parameters)


def raw_observations_between(
    connection: sqlite3.Connection,
    start: str,
    end: str,
    *,
    hosts: Sequence[str] = (),
    phases: Sequence[str] = (),
    source_files: Sequence[str] = (),
) -> list[dict[str, object]]:
    """Return typed raw CSV fields for one explicit inclusive interval.

    SQLite remains non-authoritative. This helper performs no health, baseline,
    incident, attribution, or other semantic classification.
    """
    sql, parameters = bounded_raw_observation_query(
        start, end, hosts=hosts, phases=phases, source_files=source_files
    )
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def integrity_check(connection: sqlite3.Connection) -> str:
    return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def backup_directory(configured: Path | str | None = None) -> Path:
    if configured is not None:
        return Path(configured).expanduser()
    environment = os.environ.get(BACKUP_ENVIRONMENT)
    if environment:
        return Path(environment).expanduser()
    return DEFAULT_BACKUP_DIRECTORY


def manifest_path(backup: Path | str) -> Path:
    path = Path(backup)
    return path.with_name(path.name + MANIFEST_SUFFIX)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_summary(connection: sqlite3.Connection) -> dict[str, object]:
    bounds = connection.execute(
        "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM raw_probe_observations"
    ).fetchone()
    return {
        "schema_version": verify_schema(connection),
        "observation_count": int(bounds[0]),
        "earliest_observation": bounds[1],
        "latest_observation": bounds[2],
        "integrity": integrity_check(connection),
    }


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=BASE,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.partial-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def exclusive_storage_lock(
    database: Path | str = DEFAULT_DATABASE, *, blocking: bool = False
) -> Iterator[None]:
    """Coordinate Prime-owned writers and destructive storage maintenance."""
    lock_path = Path(database).with_name(Path(database).name + ".maintenance.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        os.chmod(lock_path, 0o600)
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(handle.fileno(), operation)
        except BlockingIOError as exc:
            raise StorageBusy("collection or storage writing is active; try again shortly") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _assert_sqlite_writer_idle(database: Path) -> None:
    if not database.exists():
        return
    connection = sqlite3.connect(database, timeout=0.1)
    try:
        connection.execute("PRAGMA busy_timeout = 100")
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.rollback()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise StorageBusy("SQLite writing is active; try again shortly") from exc
            raise
        except sqlite3.DatabaseError:
            # A damaged live database is a valid restore target. The Prime lock
            # still excludes cooperating collector/storage writers.
            pass
    finally:
        connection.close()


def _resolve_backup_reference(reference: Path | str, directory: Path) -> Path:
    path = Path(reference).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        return path
    direct = directory / path
    if direct.exists():
        return direct
    with_suffix = directory / f"{path}{BACKUP_SUFFIX}"
    return with_suffix if with_suffix.exists() else direct


def verify_backup(backup: Path | str) -> dict[str, object]:
    path = Path(backup).expanduser()
    sidecar = manifest_path(path)
    if not path.is_file():
        raise BackupValidationError(f"backup does not exist: {path}")
    if not sidecar.is_file():
        raise BackupValidationError(f"backup manifest does not exist: {sidecar}")
    try:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupValidationError(f"backup manifest is unreadable: {sidecar}") from exc
    if not isinstance(manifest, dict) or manifest.get("validation_status") != "verified":
        raise BackupValidationError("backup manifest is not marked verified")
    if manifest.get("format_version") != 1:
        raise BackupValidationError("backup manifest format is unsupported")
    try:
        _backup_timestamp(manifest.get("created_at"))
    except ValueError as exc:
        raise BackupValidationError("backup manifest creation time is invalid") from exc
    if manifest.get("backup_size_bytes") != path.stat().st_size:
        raise BackupValidationError("backup file size does not match its manifest")
    expected_hash = manifest.get("sha256")
    if not isinstance(expected_hash, str) or _sha256_file(path) != expected_hash:
        raise BackupValidationError("backup file hash does not match its manifest")
    try:
        with contextlib.closing(connect_read_only(path)) as connection:
            summary = _database_summary(connection)
    except (sqlite3.Error, StorageError) as exc:
        raise BackupValidationError(f"backup database validation failed: {exc}") from exc
    if summary["integrity"] != "ok":
        raise BackupValidationError(f"backup integrity check failed: {summary['integrity']}")
    for field in (
        "schema_version",
        "observation_count",
        "earliest_observation",
        "latest_observation",
        "integrity",
    ):
        if manifest.get(field) != summary[field]:
            raise BackupValidationError(f"backup manifest {field} does not match database")
    return {**manifest, "backup": str(path.resolve()), "manifest": str(sidecar.resolve())}


def _backup_timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("missing creation time")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("creation time has no UTC offset")
    return parsed.astimezone(dt.timezone.utc)


def list_backups(directory: Path | str | None = None) -> list[dict[str, object]]:
    destination = backup_directory(directory)
    if not destination.is_dir():
        return []
    records: list[dict[str, object]] = []
    for path in destination.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        sidecar = manifest_path(path)
        record: dict[str, object] = {
            "backup": str(path.resolve()),
            "manifest": str(sidecar.resolve()),
            "verified": False,
        }
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                record.update(payload)
                record["verified"] = payload.get("validation_status") == "verified"
        except (OSError, json.JSONDecodeError):
            record["error"] = "missing or unreadable manifest"
        records.append(record)
    return sorted(
        records,
        key=lambda item: str(item.get("created_at") or Path(str(item["backup"])).name),
        reverse=True,
    )


def apply_backup_retention(
    directory: Path | str,
    *,
    daily: int = DEFAULT_DAILY_BACKUPS,
    weekly: int = DEFAULT_WEEKLY_BACKUPS,
    monthly: int = DEFAULT_MONTHLY_BACKUPS,
) -> list[str]:
    records: list[tuple[dt.datetime, Path]] = []
    for item in list_backups(directory):
        if not item.get("verified"):
            continue
        try:
            records.append((_backup_timestamp(item.get("created_at")), Path(str(item["backup"]))))
        except ValueError:
            continue
    records.sort(reverse=True)

    def newest_by_bucket(key, limit: int) -> set[Path]:
        selected: dict[object, Path] = {}
        for created, path in records:
            selected.setdefault(key(created), path)
        return set(list(selected.values())[: max(0, limit)])

    keep = newest_by_bucket(lambda value: value.date(), daily)
    keep |= newest_by_bucket(lambda value: value.isocalendar()[:2], weekly)
    keep |= newest_by_bucket(lambda value: (value.year, value.month), monthly)
    removed: list[str] = []
    for _, path in records:
        if path in keep:
            continue
        manifest_path(path).unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        removed.append(str(path.resolve()))
    if Path(directory).is_dir():
        _fsync_directory(Path(directory))
    return removed


def create_backup(
    database: Path | str = DEFAULT_DATABASE,
    directory: Path | str | None = None,
    *,
    now: dt.datetime | None = None,
    retain: bool = True,
) -> dict[str, object]:
    source_path = Path(database)
    destination = backup_directory(directory)
    if not source_path.is_file():
        raise DatabaseNotInitialized(f"shadow database does not exist: {source_path}")
    icloud_root = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if destination == DEFAULT_BACKUP_DIRECTORY and not icloud_root.is_dir():
        raise StorageError(
            f"default iCloud Drive destination is unavailable; set {BACKUP_ENVIRONMENT}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    if not os.access(destination, os.W_OK):
        raise StorageError(f"backup destination is not writable: {destination}")
    created = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    stamp = created.strftime("%Y%m%dT%H%M%S.%fZ")
    final_path = destination / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    if final_path.exists() or manifest_path(final_path).exists():
        raise StorageError(f"backup already exists: {final_path}")

    with tempfile.TemporaryDirectory(prefix="prime-observer-backup-", dir=source_path.parent) as temp:
        consistent = Path(temp) / "consistent.sqlite3"
        with contextlib.closing(connect_read_only(source_path)) as source:
            verify_schema(source)
            with contextlib.closing(sqlite3.connect(consistent)) as target:
                source.backup(target)
        os.chmod(consistent, 0o600)
        with contextlib.closing(connect_read_only(consistent)) as verified:
            backup_summary = _database_summary(verified)
        if backup_summary["integrity"] != "ok":
            raise BackupValidationError("consistent backup failed integrity validation")

        partial = destination / f".{final_path.name}.partial-{os.getpid()}"
        try:
            with consistent.open("rb") as source, partial.open("xb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(partial, 0o600)
            with contextlib.closing(connect_read_only(partial)) as copied:
                copied_summary = _database_summary(copied)
            if copied_summary != backup_summary:
                raise BackupValidationError("destination copy failed database validation")
            sha256 = _sha256_file(partial)
            os.replace(partial, final_path)
            _fsync_directory(destination)
        finally:
            partial.unlink(missing_ok=True)

    manifest: dict[str, object] = {
        "format_version": 1,
        "validation_status": "verified",
        "created_at": created.isoformat(timespec="microseconds"),
        "schema_version": backup_summary["schema_version"],
        "observation_count": backup_summary["observation_count"],
        "earliest_observation": backup_summary["earliest_observation"],
        "latest_observation": backup_summary["latest_observation"],
        "integrity": backup_summary["integrity"],
        "source_database": str(source_path.resolve()),
        "source_size_bytes": source_path.stat().st_size,
        "backup_size_bytes": final_path.stat().st_size,
        "sha256": sha256,
        "prime_observer_commit": _git_revision(),
    }
    _write_json_atomic(manifest_path(final_path), manifest)
    _fsync_directory(destination)
    verified_manifest = verify_backup(final_path)
    verified_manifest["retention_removed"] = (
        apply_backup_retention(destination) if retain else []
    )
    return verified_manifest


def _validate_database_file(path: Path) -> dict[str, object]:
    try:
        with contextlib.closing(connect_read_only(path)) as connection:
            summary = _database_summary(connection)
    except (sqlite3.Error, StorageError) as exc:
        raise BackupValidationError(f"database validation failed for {path}: {exc}") from exc
    if summary["integrity"] != "ok":
        raise BackupValidationError(f"database integrity check failed: {summary['integrity']}")
    return summary


def _quarantine_name(database: Path, label: str, now: dt.datetime | None = None) -> Path:
    created = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    stamp = created.strftime("%Y%m%dT%H%M%S.%fZ")
    return database.with_name(f"{database.name}.{label}-{stamp}")


def _place_database_atomically(
    candidate: Path,
    database: Path,
    *,
    expected: Mapping[str, object],
    label: str,
) -> dict[str, object]:
    database.parent.mkdir(parents=True, exist_ok=True)
    quarantine = _quarantine_name(database, label)
    previous_sidecars: list[tuple[Path, Path]] = []
    previous_preserved = False
    try:
        if database.exists():
            os.replace(database, quarantine)
            previous_preserved = True
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = database.with_name(database.name + suffix)
            if sidecar.exists():
                preserved = quarantine.with_name(quarantine.name + suffix)
                os.replace(sidecar, preserved)
                previous_sidecars.append((sidecar, preserved))
        os.replace(candidate, database)
        os.chmod(database, 0o600)
        _fsync_directory(database.parent)
        placed = _validate_database_file(database)
        for field in (
            "schema_version",
            "observation_count",
            "earliest_observation",
            "latest_observation",
        ):
            if placed[field] != expected[field]:
                raise BackupValidationError(f"restored database {field} changed after placement")
        return {
            "database": str(database.resolve()),
            "previous_database": str(quarantine.resolve()) if previous_preserved else None,
            "validation": placed,
            "collection_can_resume": True,
        }
    except Exception as exc:
        failed = None
        if database.exists():
            failed = _quarantine_name(database, "failed-restored")
            os.replace(database, failed)
        if previous_preserved and quarantine.exists():
            os.replace(quarantine, database)
            for original, preserved in previous_sidecars:
                if preserved.exists():
                    os.replace(preserved, original)
            _fsync_directory(database.parent)
        recovery = "previous database automatically recovered" if previous_preserved else "no previous database existed"
        raise StorageError(
            f"post-placement validation failed; {recovery}; failed candidate preserved at {failed}: {exc}"
        ) from exc


def restore_backup(
    backup: Path | str,
    database: Path | str = DEFAULT_DATABASE,
    directory: Path | str | None = None,
) -> dict[str, object]:
    database_path = Path(database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    selected = _resolve_backup_reference(backup, backup_directory(directory))
    metadata = verify_backup(selected)
    expected = {
        field: metadata[field]
        for field in (
            "schema_version",
            "observation_count",
            "earliest_observation",
            "latest_observation",
        )
    }
    with exclusive_storage_lock(database_path):
        _assert_sqlite_writer_idle(database_path)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{database_path.name}.restore-", dir=database_path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with selected.open("rb") as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(temporary, 0o600)
            temporary_summary = _validate_database_file(temporary)
            if any(temporary_summary[field] != expected[field] for field in expected):
                raise BackupValidationError("temporary restore does not match verified backup")
            result = _place_database_atomically(
                temporary,
                database_path,
                expected=expected,
                label="pre-restore",
            )
        finally:
            temporary.unlink(missing_ok=True)
    return {"restored_from": str(selected.resolve()), **result}


def restore_latest(
    database: Path | str = DEFAULT_DATABASE,
    directory: Path | str | None = None,
) -> dict[str, object]:
    destination = backup_directory(directory)
    skipped: list[dict[str, str]] = []
    for item in list_backups(destination):
        selected = Path(str(item["backup"]))
        try:
            verify_backup(selected)
        except (OSError, sqlite3.Error, StorageError, ValueError) as exc:
            skipped.append({"backup": str(selected), "reason": str(exc)})
            continue
        result = restore_backup(selected, database, destination)
        result["skipped_newer_backups"] = skipped
        return result
    reasons = "; ".join(f"{item['backup']}: {item['reason']}" for item in skipped)
    raise BackupValidationError(
        "no verified compatible backup is available" + (f" ({reasons})" if reasons else "")
    )


def rebuild_from_csv(
    database: Path | str = DEFAULT_DATABASE,
    data_directory: Path | str = BASE / "data",
    pattern: str = DEFAULT_PATTERN,
) -> dict[str, object]:
    database_path = Path(database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_storage_lock(database_path):
        _assert_sqlite_writer_idle(database_path)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{database_path.name}.rebuild-", dir=database_path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.unlink()
        try:
            initialize_database(temporary)
            with contextlib.closing(connect(temporary)) as connection:
                corpus = ingest_corpus(connection, data_directory, pattern)
                status = database_status(connection, temporary, data_directory, pattern)
            if corpus.rejected_rows:
                raise StorageError(
                    f"CSV rebuild rejected {corpus.rejected_rows} row(s); live database untouched"
                )
            if status["integrity"] != "ok" or not status["reconciliation_current"]:
                raise StorageError("CSV rebuild did not pass integrity and exact reconciliation")
            expected = {
                "schema_version": status["schema_version"],
                "observation_count": status["observation_count"],
                "earliest_observation": status["earliest_timestamp"],
                "latest_observation": status["latest_timestamp"],
            }
            result = _place_database_atomically(
                temporary,
                database_path,
                expected=expected,
                label="pre-rebuild",
            )
        finally:
            temporary.unlink(missing_ok=True)
    return {"csv_rebuild": asdict(corpus), **result}


def backup_health(directory: Path | str | None = None) -> dict[str, object]:
    destination = backup_directory(directory)
    availability_probe = destination
    while not availability_probe.exists() and availability_probe != availability_probe.parent:
        availability_probe = availability_probe.parent
    destination_available = (
        availability_probe.is_dir() and os.access(availability_probe, os.W_OK)
    )
    records = list_backups(destination)
    compatible = [
        item
        for item in records
        if item.get("verified") and item.get("schema_version") == SCHEMA_VERSION
    ]
    latest = None
    invalid_candidates: list[dict[str, str]] = []
    for item in compatible:
        try:
            latest = verify_backup(Path(str(item["backup"])))
            break
        except (OSError, sqlite3.Error, StorageError, ValueError) as exc:
            invalid_candidates.append({"backup": str(item["backup"]), "reason": str(exc)})
    overdue = True
    if latest is not None:
        try:
            age = dt.datetime.now(dt.timezone.utc) - _backup_timestamp(latest.get("created_at"))
            overdue = age > dt.timedelta(hours=BACKUP_OVERDUE_HOURS)
        except ValueError:
            overdue = True
    return {
        "backup_directory": str(destination.resolve()),
        "destination_available": destination_available,
        "valid_compatible_backup_available": latest is not None,
        "latest_verified_backup": latest.get("backup") if latest else None,
        "latest_verified_backup_created_at": latest.get("created_at") if latest else None,
        "backup_overdue": overdue,
        "recorded_verified_backup_count": len(compatible),
        "invalid_newer_candidates": invalid_candidates,
    }


def database_status(
    connection: sqlite3.Connection,
    database: Path | str = DEFAULT_DATABASE,
    data_directory: Path | str = BASE / "data",
    pattern: str = DEFAULT_PATTERN,
) -> dict[str, object]:
    database_path = Path(database)
    earliest_row = connection.execute(
        "SELECT observed_at FROM raw_probe_observations ORDER BY observed_at_epoch_us, observation_id LIMIT 1"
    ).fetchone()
    latest_row = connection.execute(
        "SELECT observed_at FROM raw_probe_observations ORDER BY observed_at_epoch_us DESC, observation_id DESC LIMIT 1"
    ).fetchone()
    by_target = {
        row[0]: int(row[1])
        for row in connection.execute(
            "SELECT host, COUNT(*) FROM raw_probe_observations GROUP BY host ORDER BY host"
        )
    }
    by_source = {
        row[0]: int(row[1])
        for row in connection.execute(
            """
            SELECT f.source_file, COUNT(*)
            FROM observation_sources s JOIN source_files f USING (source_id)
            GROUP BY f.source_file ORDER BY f.source_file
            """
        )
    }
    latest = connection.execute(
        """
        SELECT o.observed_at, f.source_file, s.source_kind
        FROM raw_probe_observations o
        JOIN observation_sources s USING (observation_id)
        JOIN source_files f USING (source_id)
        ORDER BY o.observed_at_epoch_us DESC, f.source_file DESC LIMIT 1
        """
    ).fetchone()
    latest_shadow = connection.execute(
        """
        SELECT o.observed_at, f.source_file, s.source_kind
        FROM raw_probe_observations o
        JOIN observation_sources s USING (observation_id)
        JOIN source_files f USING (source_id)
        WHERE s.source_kind = 'collector_shadow'
        ORDER BY o.observed_at_epoch_us DESC, f.source_file DESC LIMIT 1
        """
    ).fetchone()
    tracked = {
        row["source_file"]: row
        for row in connection.execute(
            "SELECT i.*, f.source_file FROM ingestion_sources i JOIN source_files f USING (source_id)"
        )
    }
    current = True
    paths = sorted(Path(data_directory).glob(pattern), key=lambda item: item.name)
    expected_names = {_source_name(path) for path in paths}
    if expected_names != set(tracked):
        current = False
    else:
        for path in paths:
            row = tracked[_source_name(path)]
            stat = path.stat()
            if stat.st_size != row["source_size"] or stat.st_mtime_ns != row["source_mtime_ns"]:
                current = False
                break
            if _source_fingerprint(path)[2] != row["source_sha256"]:
                current = False
                break
    return {
        "database_exists": database_path.exists(),
        "database_path": str(database_path.resolve()),
        "database_size_bytes": database_path.stat().st_size if database_path.exists() else 0,
        "schema_version": verify_schema(connection),
        "integrity": integrity_check(connection),
        "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
        "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
        "foreign_keys": bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
        "busy_timeout_ms": int(connection.execute("PRAGMA busy_timeout").fetchone()[0]),
        "observation_count": observation_count(connection),
        "earliest_timestamp": earliest_row[0] if earliest_row else None,
        "latest_timestamp": latest_row[0] if latest_row else None,
        "last_successfully_ingested_observation": dict(latest) if latest else None,
        "last_successful_shadow": dict(latest_shadow) if latest_shadow else None,
        "reconciliation_current": current,
        "counts_by_target": by_target,
        "counts_by_source_file": by_source,
    }


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _print_json(value: object) -> None:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True, default=_json_default))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prime Observer shadow storage")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="explicitly initialize a missing database")
    ingest = subparsers.add_parser("ingest", help="ingest retained telemetry CSV files")
    ingest.add_argument("paths", nargs="*", type=Path)
    ingest.add_argument("--data-directory", type=Path, default=BASE / "data")
    ingest.add_argument("--pattern", default=DEFAULT_PATTERN)
    status = subparsers.add_parser("status", help="show validation-oriented database status")
    status.add_argument("--data-directory", type=Path, default=BASE / "data")
    status.add_argument("--pattern", default=DEFAULT_PATTERN)
    status.add_argument("--backup-directory", type=Path)
    status.add_argument("--verbose", action="store_true", help="include per-target/source detail")
    query = subparsers.add_parser("query", help="query a bounded observation range")
    query.add_argument("--start", required=True)
    query.add_argument("--end", required=True)
    subparsers.add_parser("integrity", help="run SQLite integrity_check")
    backup = subparsers.add_parser("backup", help="create and retain a verified backup")
    backup.add_argument("--backup-directory", type=Path)
    backups = subparsers.add_parser("backups", help="list backup manifests")
    backups.add_argument("--backup-directory", type=Path)
    verify = subparsers.add_parser("verify-backup", help="fully verify one backup")
    verify.add_argument("backup", type=Path)
    verify.add_argument("--backup-directory", type=Path)
    restore = subparsers.add_parser("restore", help="defensively restore one verified backup")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--backup-directory", type=Path)
    restore_latest_parser = subparsers.add_parser(
        "restore-latest", help="restore the newest verified compatible backup"
    )
    restore_latest_parser.add_argument("--backup-directory", type=Path)
    rebuild = subparsers.add_parser(
        "rebuild-from-csv", help="atomically rebuild the shadow database from authoritative CSV"
    )
    rebuild.add_argument("--data-directory", type=Path, default=BASE / "data")
    rebuild.add_argument("--pattern", default=DEFAULT_PATTERN)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            version = initialize_database(args.database)
            _print_json({"database": str(args.database.resolve()), "schema_version": version})
            return 0
        if args.command == "backup":
            _print_json(create_backup(args.database, args.backup_directory))
            return 0
        if args.command == "backups":
            _print_json(
                {
                    "backup_directory": str(backup_directory(args.backup_directory).resolve()),
                    "backups": list_backups(args.backup_directory),
                }
            )
            return 0
        if args.command == "verify-backup":
            selected = _resolve_backup_reference(
                args.backup, backup_directory(args.backup_directory)
            )
            _print_json(verify_backup(selected))
            return 0
        if args.command == "restore":
            _print_json(restore_backup(args.backup, args.database, args.backup_directory))
            return 0
        if args.command == "restore-latest":
            _print_json(restore_latest(args.database, args.backup_directory))
            return 0
        if args.command == "rebuild-from-csv":
            _print_json(rebuild_from_csv(args.database, args.data_directory, args.pattern))
            return 0
        if args.command == "status":
            health = backup_health(args.backup_directory)
            try:
                with contextlib.closing(connect(args.database)) as status_connection:
                    database = database_status(
                        status_connection, args.database, args.data_directory, args.pattern
                    )
                exceptions = []
                if database["integrity"] != "ok":
                    exceptions.append("database integrity failure")
                if not database["reconciliation_current"]:
                    exceptions.append("CSV reconciliation is stale")
                if not args.verbose:
                    database = {
                        key: value
                        for key, value in database.items()
                        if key not in ("counts_by_target", "counts_by_source_file")
                    } | {
                        "target_count": len(database["counts_by_target"]),
                        "tracked_source_file_count": len(database["counts_by_source_file"]),
                    }
            except (OSError, sqlite3.Error, StorageError) as exc:
                database = {
                    "database_exists": Path(args.database).exists(),
                    "database_path": str(Path(args.database).resolve()),
                    "error": str(exc),
                }
                exceptions = ["database missing or unusable"]
            if health["backup_overdue"]:
                exceptions.append("verified backup is overdue")
            if not health["valid_compatible_backup_available"]:
                exceptions.append("no verified compatible backup is available")
            if not health["destination_available"]:
                exceptions.append("backup destination is unavailable")
            _print_json(
                {
                    "overall": "action_required" if exceptions else "ok",
                    "exceptions": exceptions,
                    "database": database,
                    "backup": health,
                    "authority": "CSV",
                    "sqlite_role": "shadow_non_authoritative",
                }
            )
            return 1 if exceptions else 0
        connection = connect(args.database)
        try:
            if args.command == "ingest":
                with exclusive_storage_lock(args.database):
                    result = (
                        summarize_results(
                            tuple(
                                ingest_csv(connection, path)
                                for path in sorted(args.paths, key=lambda path: str(path))
                            )
                        )
                        if args.paths
                        else ingest_corpus(connection, args.data_directory, args.pattern)
                    )
                output = asdict(result)
                output["sqlite_final_row_count"] = observation_count(connection)
                _print_json(output)
            elif args.command == "query":
                _print_json(observations_between(connection, args.start, args.end))
            elif args.command == "integrity":
                _print_json({"integrity": integrity_check(connection)})
        finally:
            connection.close()
        return 0
    except (OSError, sqlite3.Error, StorageError, MalformedObservation, ValueError) as exc:
        print(f"storage error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
