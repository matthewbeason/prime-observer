# Storage Phase 2 Read-Path Evaluation

## Decision

**Ready for first reader migration.**

This decision applies only to a future, separately authorized migration of a
bounded raw 24-hour probe-history reader. CSV remains production authority in
this phase. No semantic producer, browser code, dashboard, Investigation,
baseline, health, incident, interval, attribution, Mesh Signal, Internet
Conditions, or APS call site reads SQLite.

The live measurements below were taken on 2026-08-26 while collection remained
active. The evaluation used fixed interval endpoints, so returned rows remained
stable; the current CSV file and shadow database continued to grow outside the
selected windows.

## Historical query selected

Retrieve every Prime-owned raw probe observation in one explicit inclusive UTC
interval, optionally filtered by raw `host` and/or `phase_label`, ordered by
timestamp, host, and deterministic observation identity. The output is the 18
typed raw CSV fields and is suitable as the factual input to a 24-hour Network
History or primary latency-history projection. Python remains responsible for
target metadata fallback, timeout interpretation, chart grouping, health, and
all higher-level semantics.

## Existing CSV read path

The closest current bounded historical reader is
`bin/build_investigation.py:read_samples`:

1. `telemetry_files()` sorts and scans every `data/bakeoff_*.csv` path. The live
   corpus contained 195 files.
2. Each file is opened with `csv.DictReader`; every row is parsed.
3. `row_to_sample()` parses `ts`, treats a naive timestamp as UTC, strips the
   host, retains only configured gateway/WAN targets, and drops rows without a
   numeric `p95_ms`.
4. The reader applies an inclusive UTC start/end filter after normalization.
5. Target label/class fall back to Python-owned target metadata. Phase becomes
   uppercase or `UNKNOWN`; numeric fields become floats; missing loss and jitter
   become `0.0`; other missing numeric fields remain null.
6. The sample shape contains `ts`, `ts_utc`, `source_file`, `phase`, `host`,
   `target_label`, `target_class`, `kind`, `sent`, `received`, `loss_pct`,
   `avg_ms`, `p50_ms`, `p95_ms`, `max_ms`, and `jitter_ms`.
7. Results sort by `(ts_utc, host)`. Only source files with matches are listed in
   the returned provenance summary.

One live execution of that exact production CSV function took 6.58 seconds for
24 hours, 6.48 seconds for 7 days, and 6.81 seconds for 30 days. It returned
8,210, 58,373, and 248,815 visualization-ready samples respectively. The raw
comparison below deliberately retains timeout/loss rows that this current
semantic normalization drops when `p95_ms` is missing.

## SQLite helper and comparison harness

`bin/storage.py:raw_observations_between` is the smallest new read helper. It
requires offset-aware start/end timestamps, supports repeatable host and phase
filters, uses inclusive bounds, selects only the 18 raw fields, and orders by
`observed_at_epoch_us`, `host`, and `observation_id`. SQL performs no semantic
classification.

`bin/storage.py:connect_read_only` opens the database with SQLite `mode=ro` and
`PRAGMA query_only = ON`. `bin/evaluate_storage_read_path.py` performs:

```text
CSV query -> SQLite query -> full-row compare -> benchmark -> query plan
```

The harness preserves legacy missing target metadata as null, converts empty
optional values to null, uses typed finite numeric values, includes
`sent`/`received`/`loss_pct` and empty latency fields for timeouts, compares
ordering and the full identity multiset, and reports duplicate occurrences. It
does not write runtime artifacts.

## Live equivalence and benchmark

Each timed benchmark ran three times. `First` is the first run in that process;
`repeated` is the median of runs two and three. CSV opens and scans the complete
retained corpus each time. SQLite fetch time includes executing and fetching all
rows; materialization is the separate conversion to Python dictionaries.

| Window | Rows | CSV first | CSV repeated | SQLite first | SQLite repeated | SQLite fetch median | Python materialization median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 24 hours | 8,210 | 4.104 s | 3.950 s | 0.804 s | 0.203 s | 0.317 s | 0.019 s |
| 7 days | 58,377 | 4.769 s | 4.849 s | 3.049 s | 1.547 s | 1.962 s | 0.147 s |
| 30 days | 249,059 | 7.673 s | 8.204 s | 4.418 s | 4.063 s | 3.262 s | 0.817 s |

All three windows were exactly equivalent: row count, timestamps, host identity,
phase, target metadata, all supported numeric/raw fields, nulls, timeout/loss
evidence, ordering, and identity multiplicity matched. There were no duplicate
raw identities in any selected window and no mismatch samples. The three-run
CSV scans observed 1,294,813 to 1,294,823 rows as the active daily file grew;
the fixed-window result did not change.

Python allocation tracing was measured separately so it did not contaminate the
timed runs. Peak traced allocations were 9,328,422 bytes for CSV and 8,977,637
bytes for SQLite at 24 hours. At 30 days they were 287,370,614 and 284,019,036
bytes respectively. These values exclude the operating-system file cache and
SQLite native allocations.

The broader live reconciliation check accounted for all 1,294,768 rows present
at its snapshot: 195 tracked CSV files, 1,294,768 valid rows, zero rejections,
1,294,768 SQLite observations, and no repeated observation identity across
provenance rows. Because collection was live, later benchmark scan counts were
slightly higher; the collector continued shadow reconciliation normally.

## Query plan and indexes

The unfiltered, ordered range uses:

```text
SEARCH raw_probe_observations USING INDEX raw_probe_observations_time_idx
    (observed_at_epoch_us>? AND observed_at_epoch_us<?)
```

A single-host range, with or without a phase filter, uses:

```text
SEARCH raw_probe_observations USING INDEX raw_probe_observations_host_time_idx
    (host=? AND observed_at_epoch_us>? AND observed_at_epoch_us<?)
```

A phase-only bounded query uses the time index and filters phase within that
range. Multiple hosts use the host/time index and a temporary B-tree to merge
the host ranges into global timestamp order. This is a bounded optional-filter
cost, not an unnecessary full-table scan. No new index was added: a phase index
is not justified by the selected workload, and a covering index for all 18 raw
fields would materially amplify storage for a workload already served by the
current indexes.

## Storage amplification

At the storage snapshot, retained CSV occupied 145,248,231 bytes and SQLite
occupied 632,979,456 bytes, a 4.36x amplification. SQLite used 154,536 4-KiB
pages and had zero freelist pages.

| SQLite object | Allocated bytes | Payload bytes | Unused bytes |
| --- | ---: | ---: | ---: |
| `raw_probe_observations` | 291,766,272 | 241,719,371 | 44,012,870 |
| `observation_sources` | 122,617,856 | 104,126,374 | 14,247,770 |
| time index | 82,948,096 | 68,645,204 | 10,175,400 |
| host/time index | 78,315,520 | 68,645,204 | 5,556,396 |
| provenance file index | 57,257,984 | 47,175,854 | 6,029,902 |
| source, ingestion, schema objects | 73,728 | 46,120 | 24,758 |

The raw table is 46.1% of allocated storage. The two raw query indexes are
25.5%. Provenance tables plus their file index are 28.4%. The 32-byte SHA-256
identity is stored in the `WITHOUT ROWID` raw table and provenance table and is
also carried by their secondary-index entries; its direct repeated byte
contribution is approximately 207 MB before B-tree record encoding and page
overhead. Recorded unused page space is about 74 MB; the remaining gap between
payload and allocated bytes is B-tree structure, record encoding, and page
headers. There are no free pages to reclaim.

The 633 MB local shadow is acceptable for the measured 24-hour query benefit
and still modest in absolute size. The declining advantage at 30 days argues
against adding a wide covering index solely for this experiment.

The database measured 632,950,784 bytes before evaluation and 632,979,456 bytes
afterward. No schema or index change occurred and the read harness used a
read-only connection; the 28,672-byte increase came from normal live shadow
collection during the evaluation, not the read path.

## Ingestion, idempotency, reconciliation, and isolation

No read-path index or schema change was made, so ingestion executes the same
writes as Phase 1. A temporary-database check ingested the 8,297-row
`bakeoff_20260825.csv` file in 0.223 seconds. Re-ingestion took 0.196 seconds,
inserted zero rows, reported all 8,297 as duplicates, retained 8,297 total rows,
and passed `integrity_check`.

The live database passed `integrity_check` and reconciliation was current at the
full-corpus snapshot. The helper is reachable only through Python diagnostic
tooling; no collector or production semantic call site was changed. Read-only
mode and `query_only` enforce collector isolation at the SQLite connection
boundary.

## Recommended first production reader

In a separate explicitly authorized phase, migrate the Python-owned raw input
for the current 24-hour Network History/primary latency history projection
first. Keep every existing target-metadata fallback, normalization, grouping,
timeout, and chart semantic in Python, and require a production-level parity
test before cutover. Do not start with baseline learning, incident generation,
attribution, Investigation lifecycle, interval semantics, or operational
learning.
