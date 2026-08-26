#!/usr/bin/env python3
"""Prime Observer shadow storage for raw probe observations.

CSV remains authoritative.  This module is intentionally isolated from every
semantic producer and provides only ingestion and diagnostic bounded reads.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping, Sequence


BASE = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = BASE / "data" / "prime_observer.db"
DEFAULT_PATTERN = "bakeoff_*.csv"
SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 2_000

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
) -> tuple[str, tuple[object, ...]]:
    """Build the bounded raw query used only by diagnostic storage readers."""
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
) -> list[dict[str, object]]:
    """Return typed raw CSV fields for one explicit inclusive interval.

    This is a non-authoritative Storage Phase 2 helper.  It performs no health,
    baseline, incident, attribution, or other semantic classification.
    """
    sql, parameters = bounded_raw_observation_query(
        start, end, hosts=hosts, phases=phases
    )
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def integrity_check(connection: sqlite3.Connection) -> str:
    return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


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
    query = subparsers.add_parser("query", help="query a bounded observation range")
    query.add_argument("--start", required=True)
    query.add_argument("--end", required=True)
    subparsers.add_parser("integrity", help="run SQLite integrity_check")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            version = initialize_database(args.database)
            _print_json({"database": str(args.database.resolve()), "schema_version": version})
            return 0
        connection = connect(args.database)
        try:
            if args.command == "ingest":
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
            elif args.command == "status":
                _print_json(database_status(connection, args.database, args.data_directory, args.pattern))
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
