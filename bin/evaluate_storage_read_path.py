#!/usr/bin/env python3
"""Compare bounded authoritative CSV reads with shadow SQLite reads.

Diagnostic only: this command reads retained telemetry and the shadow database,
prints a report, and never writes runtime artifacts or changes production
authority.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
import statistics
import sys
import time
import tracemalloc
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import storage


@dataclass(frozen=True)
class ReadMeasurement:
    elapsed_seconds: float
    files_opened: int
    rows_scanned: int
    rows_returned: int
    fetch_seconds: float | None = None
    materialize_seconds: float | None = None


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


def _project_normalized(values: Mapping[str, object]) -> dict[str, object]:
    return {
        field: values["observed_at"] if field == "ts" else values.get(field)
        for field in storage.CSV_FIELDS
    }


def _identity(row: Mapping[str, object]) -> bytes:
    normalized = storage.normalize_observation(row)
    return bytes(normalized["observation_id"])


def read_csv_window(
    data_directory: Path,
    pattern: str,
    start: str,
    end: str,
    *,
    hosts: Sequence[str] = (),
    phases: Sequence[str] = (),
) -> tuple[list[dict[str, object]], ReadMeasurement]:
    start_us = storage.epoch_microseconds(dt.datetime.fromisoformat(start))
    end_us = storage.epoch_microseconds(dt.datetime.fromisoformat(end))
    if start_us > end_us:
        raise ValueError("bounded query start must not be after end")
    host_filter = set(hosts)
    phase_filter = set(phases)
    paths = sorted(data_directory.glob(pattern), key=lambda item: item.name)
    rows: list[tuple[int, str, bytes, dict[str, object]]] = []
    scanned = 0
    started = time.perf_counter()
    for path in paths:
        for row, line_number in _csv_records(path):
            scanned += 1
            raw_timestamp = str(row.get("ts") or "").strip()
            try:
                timestamp = dt.datetime.fromisoformat(raw_timestamp)
                if timestamp.tzinfo is None:
                    continue
                observed_us = storage.epoch_microseconds(timestamp)
            except ValueError:
                continue
            if not start_us <= observed_us <= end_us:
                continue
            if host_filter and str(row.get("host") or "").strip() not in host_filter:
                continue
            if phase_filter and str(row.get("phase_label") or "").strip() not in phase_filter:
                continue
            try:
                normalized = storage.normalize_observation(row)
            except (storage.MalformedObservation, ValueError) as exc:
                raise storage.MalformedObservation(f"{path}:{line_number}: {exc}") from exc
            projected = _project_normalized(normalized)
            rows.append((observed_us, str(normalized["host"]), bytes(normalized["observation_id"]), projected))
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    result = [item[3] for item in rows]
    elapsed = time.perf_counter() - started
    return result, ReadMeasurement(elapsed, len(paths), scanned, len(result))


def read_sqlite_window(
    connection,
    start: str,
    end: str,
    *,
    hosts: Sequence[str] = (),
    phases: Sequence[str] = (),
) -> tuple[list[dict[str, object]], ReadMeasurement]:
    sql, parameters = storage.bounded_raw_observation_query(
        start, end, hosts=hosts, phases=phases
    )
    started = time.perf_counter()
    fetched = connection.execute(sql, parameters).fetchall()
    fetched_at = time.perf_counter()
    rows = [dict(row) for row in fetched]
    completed = time.perf_counter()
    return rows, ReadMeasurement(
        completed - started,
        1,
        len(fetched),
        len(rows),
        fetched_at - started,
        completed - fetched_at,
    )


def compare_rows(csv_rows, sqlite_rows, *, mismatch_limit: int = 5) -> dict[str, object]:
    csv_identities = [_identity(row).hex() for row in csv_rows]
    sqlite_identities = [_identity(row).hex() for row in sqlite_rows]
    mismatches = []
    for index, (csv_row, sqlite_row) in enumerate(zip(csv_rows, sqlite_rows)):
        if csv_row != sqlite_row:
            mismatches.append({"index": index, "csv": csv_row, "sqlite": sqlite_row})
            if len(mismatches) >= mismatch_limit:
                break
    csv_counts = Counter(csv_identities)
    sqlite_counts = Counter(sqlite_identities)
    duplicate_multiplicity = sum(count - 1 for count in csv_counts.values() if count > 1)
    return {
        "equivalent": csv_rows == sqlite_rows,
        "csv_row_count": len(csv_rows),
        "sqlite_row_count": len(sqlite_rows),
        "ordering_equal": csv_identities == sqlite_identities,
        "identity_multiset_equal": csv_counts == sqlite_counts,
        "csv_duplicate_occurrences": duplicate_multiplicity,
        "sqlite_duplicate_occurrences": sum(
            count - 1 for count in sqlite_counts.values() if count > 1
        ),
        "mismatches": mismatches,
    }


def _peak_bytes(function):
    tracemalloc.start()
    try:
        function()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _summarize(measurements: Sequence[ReadMeasurement]) -> dict[str, object]:
    elapsed = [item.elapsed_seconds for item in measurements]
    result = {
        "runs": [asdict(item) for item in measurements],
        "first_seconds": elapsed[0],
        "repeated_median_seconds": statistics.median(elapsed[1:] or elapsed),
        "files_opened": measurements[0].files_opened,
        "rows_scanned_or_fetched": measurements[0].rows_scanned,
        "rows_returned": measurements[0].rows_returned,
    }
    if measurements[0].fetch_seconds is not None:
        result["fetch_median_seconds"] = statistics.median(
            item.fetch_seconds for item in measurements if item.fetch_seconds is not None
        )
        result["materialize_median_seconds"] = statistics.median(
            item.materialize_seconds
            for item in measurements
            if item.materialize_seconds is not None
        )
    return result


def evaluate(args) -> dict[str, object]:
    csv_measurements = []
    sqlite_measurements = []
    first_csv = None
    first_sqlite = None
    with closing(storage.connect_read_only(args.database)) as connection:
        sql, parameters = storage.bounded_raw_observation_query(
            args.start, args.end, hosts=args.host, phases=args.phase
        )
        plan = [dict(row) for row in connection.execute("EXPLAIN QUERY PLAN " + sql, parameters)]
        for _ in range(args.runs):
            csv_rows, csv_measurement = read_csv_window(
                args.data_directory,
                args.pattern,
                args.start,
                args.end,
                hosts=args.host,
                phases=args.phase,
            )
            sqlite_rows, sqlite_measurement = read_sqlite_window(
                connection,
                args.start,
                args.end,
                hosts=args.host,
                phases=args.phase,
            )
            first_csv = first_csv if first_csv is not None else csv_rows
            first_sqlite = first_sqlite if first_sqlite is not None else sqlite_rows
            csv_measurements.append(csv_measurement)
            sqlite_measurements.append(sqlite_measurement)

        csv_peak = None
        sqlite_peak = None
        if args.measure_memory:
            csv_peak = _peak_bytes(
                lambda: read_csv_window(
                    args.data_directory,
                    args.pattern,
                    args.start,
                    args.end,
                    hosts=args.host,
                    phases=args.phase,
                )
            )
            sqlite_peak = _peak_bytes(
                lambda: read_sqlite_window(
                    connection,
                    args.start,
                    args.end,
                    hosts=args.host,
                    phases=args.phase,
                )
            )
    return {
        "diagnostic_only": True,
        "authority": "csv",
        "window": {"start": args.start, "end": args.end},
        "filters": {"hosts": args.host, "phases": args.phase},
        "comparison": compare_rows(first_csv or [], first_sqlite or []),
        "csv": {**_summarize(csv_measurements), "python_peak_bytes": csv_peak},
        "sqlite": {**_summarize(sqlite_measurements), "python_peak_bytes": sqlite_peak},
        "sqlite_query_plan": plan,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnostic CSV -> SQLite bounded read comparison and benchmark"
    )
    parser.add_argument("--database", type=Path, default=storage.DEFAULT_DATABASE)
    parser.add_argument("--data-directory", type=Path, default=storage.BASE / "data")
    parser.add_argument("--pattern", default=storage.DEFAULT_PATTERN)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--phase", action="append", default=[])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--measure-memory",
        action="store_true",
        help="run an additional allocation-traced pass for each reader",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.runs < 1:
        print("evaluation error: --runs must be at least 1", file=sys.stderr)
        return 2
    try:
        result = evaluate(args)
    except (OSError, ValueError, sqlite3.Error, storage.StorageError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["comparison"]["equivalent"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
