# Storage

This document defines Prime Observer's durable-storage boundary. Phase 1 adds a
SQLite shadow copy of Prime-owned raw probe observations while preserving the
existing CSV pipeline as production authority. Storage Phase 2 adds only a
read-only, bounded diagnostic helper and evaluation harness; it does not cut
over a production reader. See `docs/storage-phase2-read-evaluation.md`.

## Phase 1 authority boundary

- `data/bakeoff_YYYYMMDD.csv` remains authoritative for collection history and
  every production semantic reader.
- `data/prime_observer.db` is a disposable, rebuildable shadow store used only
  by Python-owned ingestion and validation tooling.
- `bin/storage.py` is the only Prime-owned SQLite boundary. The browser never
  opens SQLite and no transform, baseline, attribution, health, interval,
  investigation, similarity, learning, or external-context producer reads it.
- Deterministic CSV/JSON files remain the browser and consumer contracts.
- Completed incident snapshots under `viz/investigations/` remain immutable,
  write-once files.
- Mesh Signal storage remains externally owned and read-only to Prime Observer.
- Making SQLite authoritative requires a separate explicit phase and new
  equivalence evidence. Phase 1 does not authorize that cutover.

Deleting, locking, or making the shadow database unwritable does not change
current production behavior. The collector finishes its CSV append first and
reports a warning if the subsequent shadow write fails.

## Audited collection path

`bin/run_collector.sh` invokes one `bin/collector.py` cycle. The installed local
LaunchAgent runs that wrapper every 30 seconds. A cycle reads `phase.txt`, takes
one timestamp with a UTC offset, optionally collects one speed test for the
cycle, probes each configured target, and appends one row per target to the
local-day file `data/bakeoff_YYYYMMDD.csv`.

The collector creates a missing daily file with a header and otherwise appends
without rewriting or rotating retained evidence. Date-based naming is the only
rotation mechanism. Traceroute runs when the minute-of-day is divisible by 15;
speed test runs when it is divisible by 30.

The current 18-column schema is:

```text
ts, phase_label, host, target_label, target_class, sent, received, loss_pct,
avg_ms, p50_ms, p95_ms, max_ms, jitter_ms, traceroute_snip,
speedtest_down_mbps, speedtest_up_mbps, speedtest_ping_ms,
speedtest_raw_json
```

Retained history also contains the original 16-column generation without
`target_label` and `target_class`; these fields are stored as null when absent.
One audited transition-day file retained the legacy header while later records
used the exact 18-column generation. Ingestion recognizes only that unambiguous
legacy-header/current-width shape and aligns the added target fields without
rewriting the source; other width mismatches remain visible rejections.
There is no separate timeout field. The original evidence represents a timeout
through `received`, `loss_pct`, and empty latency fields. SQLite preserves those
fields without adding a semantic conclusion.

As audited on 2026-08-26, retained history comprised 195 matching CSV files:
123 original-schema files and 72 current-schema files. Duplicate collection
cycles are possible if collection is manually invoked near a scheduled cycle,
and any retained file can be reprocessed during recovery, so storage identity
cannot depend on insertion order or an assumed cadence.

Current production consumers remain:

- `bin/transform_latest.py`, which reads matching CSV files for the current
  projection and learned-baseline inputs
- `bin/build_investigation.py`, which reads matching CSV files for historical
  requested windows
- downstream Python semantic producers that consume the deterministic
  transform/investigation projections
- `viz/index.html` and `viz/investigate.html`, which consume generated CSV/JSON
  only

## Database and schema

The generated database is `data/prime_observer.db`, outside the web-served
`viz/` directory. The database and SQLite sidecars are ignored by Git. Creation
sets the database to mode `0600`; it stores no credentials or secrets.

Schema version 1 uses both `PRAGMA user_version` and a singleton
`schema_metadata` row. Creation is explicit and transactional. A missing or
uninitialized database is not created by collection. A newer or inconsistent
schema fails closed. The deterministic future migration path is: verify the
current version, execute one reviewed version-to-version transaction, update
both version records, then verify. No generalized migration framework exists.

The schema contains only:

- `raw_probe_observations`: the typed raw fields required to reproduce current
  CSV semantics
- `source_files`: normalized source paths referenced by ingestion provenance
- `observation_sources`: source file, CSV record position, and ingestion kind
  for each observation
- `ingestion_sources`: source fingerprint and reconciliation counts for each
  successfully processed CSV
- `schema_metadata`: the explicit schema contract

There are no tables for projections, baselines, incidents, similarity,
operational learning, external context, Mesh Signal, or arbitrary JSON blobs.

## Observation identity and idempotency

`observation_id` is the SHA-256 digest of a canonical JSON object
containing every supported raw CSV field. Text is preserved, required identity
text is trimmed, empty optional values become null, and numeric strings are
normalized through finite decimal values (`0`, `0.0`, and `0.00` are the same
raw value). Keys are sorted and compactly encoded before hashing.

SQLite stores the compact 32-byte digest and validation queries present it as
lowercase hexadecimal. The identity does not contain a database row number,
source row, file name, ingestion time, or insertion order. The primary-key constraint on
`observation_id` makes repeated ingestion idempotent while
`observation_sources` keeps inspectable provenance. A change to any raw
measurement or identifying field produces a distinct observation.

## Ingestion and failure behavior

Historical ingestion processes source paths in deterministic filename order.
Each CSV file is one database transaction. Structurally valid rows commit
together; malformed rows are rejected before the transaction and reported with
source and record position. A database error rolls back the entire valid
portion of that file. Source files are opened read-only and are never modified.

After authoritative CSV append, normal collection re-ingests the bounded current
daily file. Database constraints ignore rows already present. Re-reading the
file also reconciles an earlier collection cycle whose shadow step was locked,
interrupted, or unavailable. Any shadow exception is written to standard error
and the collector still exits according to its existing CSV behavior.

## SQLite configuration

- Rollback journal (`DELETE`) is used because this is a single local writer,
  Phase 1 has no continuous SQLite production readers, and predictable
  durability plus a simple quiesced-file backup boundary matter more than read
  concurrency. WAL is not justified yet.
- `synchronous=FULL` favors durable committed batches over marginal ingestion
  speed.
- `busy_timeout=2000` gives a short local writer time to finish without allowing
  optional shadow work to stall collection for a long period.
- `foreign_keys=ON` enforces provenance cleanup if an observation is removed by
  future explicit tooling.
- Connections are short-lived per collector cycle or command and are always
  closed by the caller. Transactions are scoped to one logical source batch.

These choices must be revisited if a later phase introduces concurrent readers,
long-lived processes, or SQLite authority.

## Initialize, rebuild, reconcile, and inspect

The shadow database is deliberately disposable. First stop the collector so the
CSV corpus is stable. Move the database aside rather than deleting it if it may
be useful for diagnosis, then run:

```bash
python3 bin/storage.py init
python3 bin/storage.py ingest
python3 bin/storage.py status
python3 bin/storage.py integrity
```

Review all counts and rejection reasons before resuming collection. A second
`ingest` run should insert zero observations; all valid rows should be reported
as duplicates and the final row count should remain unchanged. `status` reports
existence, version, size, integrity, timestamp bounds, target/source counts,
the last ingested observation, the last collector-shadow observation, and
whether every retained matching source still has the size, modification time,
and SHA-256 fingerprint recorded by its last successful ingestion.

A bounded validation query is available without changing production readers:

```bash
python3 bin/storage.py query \
  --start 2026-08-26T12:00:00-07:00 \
  --end 2026-08-26T12:05:00-07:00
```

Storage Phase 2's full comparison command is also diagnostic-only:

```bash
python3 bin/evaluate_storage_read_path.py \
  --start 2026-08-25T16:06:04-07:00 \
  --end 2026-08-26T16:06:04-07:00 \
  --runs 3 --measure-memory
```

Repeat `--host` or `--phase` to apply optional raw target or phase filters.
The command opens SQLite with `mode=ro` and `PRAGMA query_only = ON`, scans CSV
without modifying it, compares every returned typed raw field in deterministic
order, reports duplicate multiplicity, benchmarks both paths, and prints the
SQLite query plan. It writes only to standard output.

If collection ran while rebuilding, stop it again and rerun `ingest` before
accepting reconciliation as current. Resume the existing collector only after
status and integrity are satisfactory.

## Backup boundary

Phase 1 needs no elaborate backup system because retained CSV can recreate the
database. For a point-in-time copy, stop the collector, confirm no transaction
or `prime_observer.db-journal` file is active, then copy the single database
file while it is quiescent. SQLite's backup API is also safe for a live source
when implemented by future dedicated tooling. Do not copy an active database
and journal independently and assume they form a consistent backup.
