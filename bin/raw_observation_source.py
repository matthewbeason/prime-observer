#!/usr/bin/env python3
"""Central source selection for Prime-owned raw historical observations.

SQLite is authoritative. CSV remains available only through explicit recovery,
export, and diagnostic policies; an unavailable authoritative database never
silently turns a possibly stale CSV export into production semantic truth.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import storage

CSV_ONLY = "csv_only"
PREFER_SQLITE = "prefer_sqlite"
VERIFY_SQLITE = "verify_sqlite"
VERIFY_SQLITE_USE_CSV = "verify_sqlite_use_csv"
SQLITE_ONLY = "sqlite_only"
SOURCE_POLICIES = (
    CSV_ONLY,
    PREFER_SQLITE,
    VERIFY_SQLITE,
    VERIFY_SQLITE_USE_CSV,
    SQLITE_ONLY,
)


@dataclass(frozen=True)
class ReadDiagnostics:
    source_used: str
    fallback_reason: str | None
    row_count: int
    earliest_timestamp: str | None
    latest_timestamp: str | None
    files_scanned: int
    rows_scanned_or_fetched: int
    elapsed_seconds: float
    verification: Mapping[str, object] | None = None
    reconciliation: Mapping[str, object] | None = None


@dataclass(frozen=True)
class RawObservationRead:
    rows: tuple[dict[str, object], ...]
    diagnostics: ReadDiagnostics


def _csv_records(path: Path):
    with path.open("r", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header or any(field not in header for field in storage.REQUIRED_FIELDS):
            raise storage.MalformedObservation(f"{path}: missing required CSV header fields")
        unknown = set(header) - set(storage.CSV_FIELDS)
        if unknown:
            raise storage.MalformedObservation(
                f"{path}: unsupported CSV header field(s): {', '.join(sorted(unknown))}"
            )
        for record in reader:
            if len(record) == len(header):
                row: Mapping[str | None, object] = dict(zip(header, record))
            elif tuple(header) == storage.LEGACY_CSV_FIELDS and len(record) == len(storage.CSV_FIELDS):
                row = dict(zip(storage.CSV_FIELDS, record))
            else:
                row = dict(zip(header, record))
                if len(record) > len(header):
                    row[None] = record[len(header):]
            yield row, reader.line_num


def _raw_projection(row: Mapping[str, object]) -> dict[str, object]:
    normalized = storage.normalize_observation(row)
    return {
        field: normalized["observed_at"] if field == "ts" else normalized.get(field)
        for field in storage.CSV_FIELDS
    }


def _identity(row: Mapping[str, object]) -> bytes:
    return bytes(storage.normalize_observation(row)["observation_id"])


def _bounds(rows: Sequence[Mapping[str, object]]) -> tuple[str | None, str | None]:
    if not rows:
        return None, None
    return str(rows[0]["ts"]), str(rows[-1]["ts"])


def _ordered(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            storage.epoch_microseconds(dt.datetime.fromisoformat(str(row["ts"]))),
            str(row.get("host") or ""),
            _identity(row),
        ),
    )


def read_csv_observations(
    start: str,
    end: str,
    *,
    data_directory: Path,
    pattern: str = storage.DEFAULT_PATTERN,
    hosts: Sequence[str] = (),
    phases: Sequence[str] = (),
    source_files: Sequence[Path] = (),
    include_provenance: bool = False,
) -> tuple[list[dict[str, object]], int, int, float]:
    start_dt = dt.datetime.fromisoformat(start)
    end_dt = dt.datetime.fromisoformat(end)
    if start_dt.tzinfo is None or end_dt.tzinfo is None:
        raise ValueError("bounded query timestamps must include UTC offsets")
    start_us = storage.epoch_microseconds(start_dt)
    end_us = storage.epoch_microseconds(end_dt)
    if start_us > end_us:
        raise ValueError("bounded query start must not be after end")
    paths = (
        list(source_files)
        if source_files
        else sorted(data_directory.glob(pattern), key=lambda path: path.name)
    )
    host_filter = set(hosts)
    phase_filter = set(phases)
    rows = []
    scanned = 0
    started = time.perf_counter()
    for path in paths:
        for raw, line_number in _csv_records(path):
            scanned += 1
            try:
                row = _raw_projection(raw)
                if include_provenance:
                    row["_source_file"] = storage.source_name(path)
                    row["_source_row"] = line_number
                observed_us = storage.epoch_microseconds(
                    dt.datetime.fromisoformat(str(row["ts"]))
                )
            except (ValueError, storage.MalformedObservation) as exc:
                raise storage.MalformedObservation(f"{path}:{line_number}: {exc}") from exc
            if not start_us <= observed_us <= end_us:
                continue
            if host_filter and row["host"] not in host_filter:
                continue
            if phase_filter and row["phase_label"] not in phase_filter:
                continue
            rows.append(row)
    return _ordered(rows), len(paths), scanned, time.perf_counter() - started


def compare_raw_rows(csv_rows, sqlite_rows, *, mismatch_limit: int = 5) -> dict[str, object]:
    csv_ids = [_identity(row).hex() for row in csv_rows]
    sqlite_ids = [_identity(row).hex() for row in sqlite_rows]
    mismatches = []
    for index, (csv_row, sqlite_row) in enumerate(zip(csv_rows, sqlite_rows)):
        if csv_row != sqlite_row:
            mismatches.append({"index": index, "csv": csv_row, "sqlite": sqlite_row})
            if len(mismatches) >= mismatch_limit:
                break
    return {
        "equivalent": csv_rows == sqlite_rows,
        "csv_row_count": len(csv_rows),
        "sqlite_row_count": len(sqlite_rows),
        "ordering_equal": csv_ids == sqlite_ids,
        "identity_multiset_equal": Counter(csv_ids) == Counter(sqlite_ids),
        "mismatches": mismatches,
    }


def _result(
    rows,
    *,
    source_used,
    fallback_reason,
    files_scanned,
    scanned,
    elapsed,
    verification=None,
    reconciliation=None,
):
    earliest, latest = _bounds(rows)
    return RawObservationRead(
        tuple(rows),
        ReadDiagnostics(
            source_used,
            fallback_reason,
            len(rows),
            earliest,
            latest,
            files_scanned,
            scanned,
            elapsed,
            verification,
            reconciliation,
        ),
    )


def read_raw_observations(
    start: str,
    end: str,
    *,
    data_directory: Path,
    database: Path = storage.DEFAULT_DATABASE,
    pattern: str = storage.DEFAULT_PATTERN,
    hosts: Sequence[str] = (),
    phases: Sequence[str] = (),
    source_files: Sequence[Path] = (),
    source_policy: str = SQLITE_ONLY,
    include_provenance: bool = False,
) -> RawObservationRead:
    """Read bounded canonical raw observations under an explicit authority policy."""
    if source_policy not in SOURCE_POLICIES:
        raise ValueError(f"unsupported raw observation source policy: {source_policy}")

    csv_cache = None

    def csv_read():
        nonlocal csv_cache
        if csv_cache is None:
            csv_cache = read_csv_observations(
                start,
                end,
                data_directory=data_directory,
                pattern=pattern,
                hosts=hosts,
                phases=phases,
                source_files=source_files,
                include_provenance=include_provenance,
            )
        return csv_cache

    if source_policy == CSV_ONLY:
        rows, files, scanned, elapsed = csv_read()
        return _result(
            rows,
            source_used="csv",
            fallback_reason=None,
            files_scanned=files,
            scanned=scanned,
            elapsed=elapsed,
        )

    reconciliation = None
    verification = None
    try:
        started = time.perf_counter()
        with closing(storage.connect_read_only(database)) as connection:
            # Integrity is an operator/backup gate after authority cutover. A
            # full-file quick_check on every routine bounded read dominates the
            # query cost; explicit verification and fallback policies retain it.
            if source_policy != SQLITE_ONLY:
                quick_check = str(connection.execute("PRAGMA quick_check(1)").fetchone()[0])
                if quick_check != "ok":
                    raise storage.StorageError(f"SQLite quick check failed: {quick_check}")
            selected_paths = (
                list(source_files)
                if source_files
                else sorted(data_directory.glob(pattern), key=lambda path: path.name)
            )
            if source_policy != SQLITE_ONLY:
                reconciliation = storage.source_read_status(
                    connection, selected_paths, requested_end=end
                )
                if not reconciliation["safe"]:
                    raise storage.StorageError("SQLite reconciliation does not safely cover the requested interval")
            names = [storage.source_name(path) for path in selected_paths]
            sqlite_rows = storage.raw_observations_between(
                connection,
                start,
                end,
                hosts=hosts,
                phases=phases,
                source_files=names,
                include_provenance=include_provenance,
            )
        sqlite_rows = _ordered(sqlite_rows)
        elapsed = time.perf_counter() - started
        if source_policy in (VERIFY_SQLITE, VERIFY_SQLITE_USE_CSV):
            csv_rows, _, _, _ = csv_read()
            verification = compare_raw_rows(csv_rows, sqlite_rows)
            if not verification["equivalent"]:
                raise storage.StorageError("SQLite/CSV exact equivalence check failed")
            if source_policy == VERIFY_SQLITE_USE_CSV:
                rows, files, scanned, csv_elapsed = csv_read()
                return _result(
                    rows,
                    source_used="csv_verified_against_sqlite",
                    fallback_reason=None,
                    files_scanned=files,
                    scanned=scanned,
                    elapsed=csv_elapsed,
                    verification=verification,
                    reconciliation=reconciliation,
                )
        return _result(
            sqlite_rows,
            source_used="sqlite",
            fallback_reason=None,
            files_scanned=0,
            scanned=len(sqlite_rows),
            elapsed=elapsed,
            verification=verification,
            reconciliation=reconciliation,
        )
    except (OSError, ValueError, sqlite3.Error, storage.StorageError) as exc:
        if source_policy == SQLITE_ONLY:
            raise storage.StorageError(
                f"authoritative SQLite raw observation read failed: {type(exc).__name__}: {exc}"
            ) from exc
        rows, files, scanned, elapsed = csv_read()
        reason = f"{type(exc).__name__}: {exc}"
        return _result(
            rows,
            source_used="csv",
            fallback_reason=reason,
            files_scanned=files,
            scanned=scanned,
            elapsed=elapsed,
            verification=verification,
            reconciliation=reconciliation,
        )


def log_read_diagnostics(label: str, diagnostics: ReadDiagnostics, *, stream=None) -> None:
    stream = stream or sys.stderr
    print(
        "raw_observation_read "
        + json.dumps({"reader": label, **asdict(diagnostics)}, sort_keys=True),
        file=stream,
    )
