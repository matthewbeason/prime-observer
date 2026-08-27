# Storage

This document defines Prime Observer's durable-storage boundary. Phases 1-3 add
the SQLite shadow copy, bounded equivalence tooling, backup/restore, CSV rebuild,
and operator health tooling. Storage Phase 4 makes SQLite the preferred source
only for the low-risk raw history written to `viz/latest.csv`; CSV remains its
immediate fallback and every semantic branch's authority. See
`docs/storage-phase2-read-evaluation.md`.

## Phase 1 authority boundary

- `data/bakeoff_YYYYMMDD.csv` remains authoritative for collection history and
  every production semantic reader.
- `data/prime_observer.db` is a disposable, rebuildable shadow store used by
  Python-owned ingestion, approved low-risk reads, and validation tooling.
- `bin/storage.py` owns SQLite persistence and raw queries;
  `bin/raw_observation_source.py` centrally owns production source selection,
  verification, diagnostics, and CSV fallback. The browser never opens SQLite.
- `bin/transform_latest.py` uses the boundary only for raw chart rows written to
  `viz/latest.csv`. Its separate semantic input remains direct CSV.
- Deterministic CSV/JSON files remain the browser and consumer contracts.
- Completed incident snapshots under `viz/investigations/` remain immutable,
  write-once files.
- Mesh Signal storage remains externally owned and read-only to Prime Observer.
- Making SQLite authoritative requires a separate explicit phase and new
  equivalence evidence. Phase 1 does not authorize that cutover.

Deleting, locking, corrupting, or making the shadow database unavailable causes
the approved chart reader to report the reason and use CSV. The collector still
finishes its CSV append first and reports a warning if shadow writing fails.

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

Production raw-reader inventory after Phase 4:

| Reader | Classification | Phase 4 source |
| --- | --- | --- |
| `bin/transform_latest.py` raw input written to `viz/latest.csv` | Low-risk presentation canary | SQLite preferred; CSV fallback |
| `bin/transform_latest.py` health, attribution, observation, interval, incident, similarity, and learning input | Semantic-critical | Direct authoritative CSV |
| `bin/transform_latest.py` hourly and durable baseline history | Semantic-critical | Direct authoritative CSV |
| `bin/build_investigation.py` requested-window history | Semantic-critical investigation evidence | Direct authoritative CSV |
| `bin/build_hourly_baseline.py` standalone baseline history | Semantic-critical baseline | Direct authoritative CSV |

Repository inspection found no additional Category A raw readers, so the
post-canary bounded bulk was empty. JSON consumers and browser renderers are not
raw `bakeoff_*.csv` readers and remain database-unaware.

## Phase 4 production read policy

`read_raw_observations(start, end, filters..., source_policy=...)` returns raw
canonical observations plus source used, fallback reason, row count, time
bounds, file/row work, elapsed time, reconciliation state, and optional exact
comparison results. It performs no semantic classification.

SQLite is eligible only when it opens read-only, schema version 1 is supported,
`PRAGMA quick_check(1)` is `ok`, every selected CSV has zero rejected rows and
matching stored multiplicity, and reconciliation covers the interval. Exact
file fingerprints are accepted. For an append-only collector file, a request
ending at or before its recorded latest reconciled timestamp is also safe when
the full recorded byte prefix still hashes exactly; a request reaching into the
unreconciled append tail uses CSV. Full `PRAGMA integrity_check` remains an
operator/status action rather than a per-read cost.

`prefer_sqlite` is the production default. `verify_sqlite` reads both sources
and compares row count, every raw field and null, ordering, identities,
timestamps, and multiplicity; any mismatch returns CSV.
`PRIME_OBSERVER_RAW_READ_POLICY=verify_sqlite` enables verification, while
`csv_only` provides an explicit authoritative comparison. Missing, locked,
corrupt, incompatible, stale, or invalid SQLite reads are reported as
`raw_observation_read` diagnostics and fall back to CSV.

Phase 5 remains required before semantic-critical reader migration or raw
authority cutover. CSV remains authoritative raw evidence and fallback;
collection still writes CSV first and SQLite second.

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

- Rollback journal (`DELETE`) is retained for one local writer plus short-lived,
  bounded Phase 4 reads; predictable durability and a simple quiesced-file
  backup boundary still matter more than read concurrency. WAL is not justified.
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

## Phase 3 operator workflow

Normal recovery does not require the SQLite shell, manual PRAGMAs, journal-file
handling, or copying a live database. The primary commands are:

```bash
python3 bin/storage.py status
python3 bin/storage.py backup
python3 bin/storage.py backups
python3 bin/storage.py verify-backup <backup-name-or-path>
python3 bin/storage.py restore <backup-name-or-path>
python3 bin/storage.py restore-latest
python3 bin/storage.py rebuild-from-csv
```

`status` reports the database as missing or unusable without a traceback and
detects integrity failure, unsupported schema, stale CSV reconciliation, an
overdue backup (more than 36 hours), and absence of a verified compatible
backup. These are operator/storage exceptions only; no dashboard card or health
semantic consumes them. Add `--verbose` for the Phase 1 per-target and
per-source diagnostic counts.

### Verified backup mechanism and destination

`backup` uses Python's SQLite backup API to create a transactionally consistent
standalone database in a private local temporary directory beside the shadow
store. It verifies schema, runs `PRAGMA integrity_check`, and records row count
and observation timestamp bounds before copying that already-consistent file to
the destination through a private partial file and atomic rename. The manifest
is written last, so a database or manifest left by interruption is never a
verified backup. The live database is never copied directly and never moved
into iCloud Drive.

The destination precedence is:

1. command `--backup-directory`
2. `PRIME_OBSERVER_BACKUP_DIR`
3. `~/Library/Mobile Documents/com~apple~CloudDocs/Prime Observer Backups`

The default is derived from the current macOS home directory; production logic
does not contain a user-specific home path. For an alternate iCloud folder:

```bash
export PRIME_OBSERVER_BACKUP_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Prime Observer/Backups"
python3 bin/storage.py backup
```

If the destination is absent or unwritable, backup fails visibly and leaves the
live database and collection path unchanged. A custom destination is created
when its parent is available. Each completed database and manifest is mode
`0600`.

The inspectable `.manifest.json` sidecar records format and schema versions,
UTC creation time, row count, earliest/latest observation, integrity result,
source location and size, backup size and SHA-256, repository commit when
available, and `validation_status: verified`. It contains no credentials or
telemetry payloads.

### Retention and capacity

After a successful backup, deterministic generational retention keeps the
newest backup in each of the newest 7 UTC daily buckets, 4 ISO-week buckets,
and 3 UTC month buckets. A single backup satisfying multiple buckets is stored
only once. Unverified or malformed files are not selected as recovery points
and are preserved for diagnosis rather than silently deleted.

At the current approximately 600 MiB database size, the absolute upper bound of
14 distinct retained generations is about 8.2 GiB (600 MiB x 14). Bucket
overlap normally makes the actual footprint smaller; with a daily run spanning
several months it is commonly about 6.4 GiB for 11 distinct copies. SQLite
backups are not compressed.

### Defensive restore

`restore` requires a backup file and verified manifest. Before changing the
live path it checks the manifest hash, supported schema, integrity, row count,
timestamp bounds, the Prime maintenance lock, and SQLite write contention. It
then copies the verified backup to a temporary local file, verifies that file,
preserves the current database and any journal sidecars under a timestamped
`pre-restore` quarantine name, atomically places the candidate, and verifies the
result again. The prior database is never silently deleted.

Any failure before placement leaves the live database untouched. If
post-placement validation fails, Prime quarantines the failed candidate,
automatically restores the preserved prior database when one existed, and
reports the recovery. Successful output explicitly says collection can resume.
The collector participates in the same short-lived Prime maintenance lock; CSV
collection remains authoritative and a lock only makes optional shadow
ingestion fail safely for that cycle.

`restore-latest` examines backups newest first and performs full verification.
It skips corrupt, incomplete, hash-mismatched, or schema-incompatible candidates
with reasons, then restores the newest verified compatible backup. It never
selects by filename alone and never restores an unverified candidate.

### CSV rebuild escape hatch

While CSV remains authoritative, `rebuild-from-csv` is the final recovery path.
It acquires the maintenance lock, builds a clean temporary schema, ingests all
matching retained CSVs, rejects any rebuild with malformed rows, requires exact
source reconciliation and clean integrity, and only then performs the same
preserve-and-atomic-place operation as restore. Failure leaves the current
database untouched. Run `status` after success before resuming any manually
stopped jobs.

## Automated daily backup on macOS

The tracked `launchd/com.mbeason.prime-observer.storage-backup.plist` runs a
separate backup process daily at 03:15 local time. It does not run collection,
and backup failure cannot fail or delay the collector. Standard output and
errors go to `logs/storage-backup.log`. The default iCloud destination needs no
secret or plist environment entry; customize the plist's `EnvironmentVariables`
with `PRIME_OBSERVER_BACKUP_DIR` if required.

Install and enable it using the same per-user LaunchAgent convention as the
other Prime jobs:

```bash
mkdir -p "$HOME/Library/LaunchAgents" logs
cp launchd/com.mbeason.prime-observer.storage-backup.plist \
  "$HOME/Library/LaunchAgents/com.mbeason.prime-observer.storage-backup.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.mbeason.prime-observer.storage-backup.plist"
```

Disable and re-enable it with:

```bash
launchctl bootout "gui/$(id -u)/com.mbeason.prime-observer.storage-backup"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.mbeason.prime-observer.storage-backup.plist"
```

The recovery objective is one straightforward verified backup per day, a
one-command latest-good restore, and a tested CSV rebuild path while CSV remains
authoritative. Prime does not add replication, high availability, or manual DBA
procedures for this personal local-first system.

## Storage Phase 4 entry gates

There is no arbitrary bake-period gate. A separately authorized reader cutover
may proceed immediately only when all of these are current and passing:

- verified backup creation and explicit verification
- `restore-latest`, including corrupt-newest fallback
- corruption recovery and automatic rollback tests
- CSV rebuild with clean integrity and exact reconciliation
- clean live SQLite integrity
- exact live CSV/SQLite reconciliation
- exact Phase 2 full-row read equivalence

Phase 4 passed these gates for the single approved low-risk chart path. SQLite
remains shadow, rebuildable, and non-authoritative; CSV remains the authoritative
raw source and immediate fallback; the browser remains a generated-artifact
consumer with no database access; Mesh Signal remains a separate read-only
source; and immutable incident snapshots remain files.
