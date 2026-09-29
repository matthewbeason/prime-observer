# Pre-soak stabilization record — 2026-09-29

This record covers only the bounded stabilization checks immediately before the
planned one-week soak.

## Backup restore verification

The existing local backup
`prime-observer-20260929T101505.584080Z.sqlite3` was selected by:

```bash
python3 bin/storage.py restore-latest --dry-run
```

The command reported `restore_ready: true`, no skipped newer backups, and
`live_database_unchanged: true`. It did not replace or mutate
`data/prime_observer.db`. A separate `python3 bin/storage.py integrity` check
reported `integrity: ok`.

## Zero-minute event semantics

Completed snapshots with `first_anomalous_at == last_anomalous_at` store an
event-window and incident-record duration of `0.0`. Their recovery start and
completed recovery timestamps remain separate and later, so zero consistently
means one anomalous observation rather than a missing recovery duration. No
detector, lifecycle, or stored-event semantic was changed. Any display wording
is parked for the UI-first review.

## Daily CSV exports

The daily `data/bakeoff_YYYYMMDD.csv` files are not used by normal production
rendering, but they remain actual inputs to the explicit `csv_only` diagnostic
path and the documented `rebuild-from-csv` recovery path. Their generation was
therefore left enabled.

## Runtime and log checks

The installed core LaunchDaemons point to this checkout, run as documented, and
reported clean collector/transform exits, SQLite `quick_check: ok`, fresh
dashboard artifacts, one loopback-only HTTP listener, and healthy local backup
readiness. The installed rotation configuration, script, and plist are
byte-identical to the repository. The rotation job had 22 successful runs;
compressed archives were mode `0600`, and active transform logs continued after
archive creation.

The documented Operator Assistant worker LaunchAgent was absent while generation
state was pending. The repository plist was installed at
`~/Library/LaunchAgents/com.mbeason.prime-observer.operator-assistant.plist` and
bootstrapped in `gui/501`. Its installed file is byte-identical to the repository,
it survived a bootout/bootstrap validation cycle, and subsequent runs exited 0.
Provider response validation remains fail-safe in separate generation state and
does not replace a valid prior output.

## Disabled derived claims

Normal transform runs now replace prior incident-similarity and
operational-learning artifacts with explicit `status: disabled` placeholders.
They contain no matches, scores, or insights, so both renderers hide the related
sections. The retained implementations are reversible and do not feed health,
attribution, lifecycle, completed history, collection, or raw telemetry.

## DNS/HTTPS evidence history

Application Experience now appends the exact safe latest-state payload to a
mode-`0600` UTC-daily JSONL file under `data/` and fsyncs it before replacing
`viz/application_experience.json`. Current consumers and semantics remain
latest-state only; no historical analysis, correlation, schema migration, or
retention subsystem was added.

## Validation caveat and parking

The full 635-test suite, Python compilation, plist lint, shell syntax, and
`git diff --check` passed. A live semantic parity harness matched eight of ten
generated artifacts and a fixed-window manual investigation, but reported
`latest.csv` and `baseline_history.json` unequal across its sequential live CSV
and SQLite runs. A separate bounded raw comparison over the same current day
returned 8,270 rows per source with exact field, order, identity, and
multiplicity equivalence. Deeper parity-harness investigation is parked because
it is outside this stabilization pass and the affected generator logic was not
changed here.
