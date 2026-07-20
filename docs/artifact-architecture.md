# Artifact Architecture

This document is the authoritative reference for how Prime Observer uses local
artifacts.

Use the repository as the source of truth. If a claim cannot be supported by
repository evidence, mark it:

`Needs Matthew Review`

## Purpose

Prime Observer uses artifacts to keep the product local-first, deterministic,
and explicit.

- Artifacts let Python generate bounded CSV/JSON outputs from local telemetry
  and optional provider summaries.
- Python owns semantics because the repository places deterministic health
  modeling, attribution, Observation projection, and investigation generation in
  local scripts under `bin/`.
- The browser is renderer-only because `viz/index.html` and
  `viz/investigate.html` consume generated local artifacts rather than calling
  providers directly or owning the primary semantic model.
- Artifacts are local-first because the repository operates through local files,
  local scripts, and static views rather than a database or cloud backend.
- Generated JSON and CSV artifacts are canonical. A future PostgreSQL or
  Supabase database, if justified by query, collaboration, or multi-user needs,
  should consume artifacts as an optional index/projection rather than replace
  them.
- Additive artifacts are preferred because Prime Observer preserves existing
  contracts such as `viz/latest.csv` and `viz/network_attribution.json` while
  introducing newer projections such as `viz/observations.json` and optional
  provider summaries.

## Lifecycle

Prime Observer's artifact lifecycle is:

Collection
-> Normalization
-> Artifact Generation
-> Rendering
-> Investigation
-> Historical Review

Stage ownership:

- Collection: Python-owned. Local telemetry comes from
  `data/bakeoff_YYYYMMDD.csv`. Optional provider summaries come from local
  Python fetchers.
- Normalization: Python-owned. `bin/transform_latest.py` adds target metadata,
  baseline fields, grouped WAN/LAN evidence, and deterministic attribution
  inputs.
- Artifact Generation: Python-owned. Scripts under `bin/` write the CSV/JSON
  artifacts in `viz/`.
- Rendering: browser-owned, renderer-only. `viz/index.html` and
  `viz/investigate.html` read local artifacts and render views.
- Investigation: Python-owned generation plus browser rendering.
  `bin/investigation_model.py` generates the automatic current-event package
  during `bin/transform_latest.py`, `bin/build_investigation.py` retains the
  manual requested-window historical package, and `viz/investigate.html` renders
  generated fields without owning lifecycle or health semantics.
- Historical Review: browser-owned review of generated evidence packages
  through `viz/investigate.html`.

## Principles

- Deterministic generation: artifact producers are local Python scripts with
  explicit rules and thresholds.
- Explainable output: outputs preserve evidence, source-file references,
  thresholds, and supporting facts instead of opaque scoring.
- Additive architecture: new artifacts should extend the pipeline without
  replacing stable contracts casually.
- Graceful degradation: optional providers write usable unavailable states
  instead of breaking the dashboard.
- Failure hiding for operator interpretation: provider/configuration failures are
  recorded in generation state, while the Investigation page renders either a
  matching valid LLM interpretation or deterministic fallback assessment.
- Optional providers: NextDNS and Cloudflare Radar are summary-only and
  fail-safe.
- Bounded schemas: artifacts stay small, explicit, and tied to Prime Observer's
  six dashboard questions.
- Provider independence: provider summaries remain separate from Observation,
  Attribution, Health, and Noticeability logic.
- Provenance preservation: artifacts keep generated timestamps, source files,
  evidence references, and producer information where appropriate.
- Fail-safe unavailable state: optional context artifacts use explicit
  `unavailable` status instead of shifting browser logic into secret-backed or
  live API calls.

## Artifact Catalog

### `viz/latest.csv`

- Producer: `bin/transform_latest.py`
- Consumers: `viz/index.html`
- Purpose: current 24-hour telemetry window and factual dashboard chart input
- Required fields: `ts`, `phase_label`, `host`, `target_label`,
  `target_class`, `p95_ms`, `jitter_ms`, `loss_pct`, `baseline_p95`,
  `baseline_delta_pct`, `baseline_sample_count`
- Optional fields: legacy telemetry columns preserved when present, including
  `sent`, `received`, `avg_ms`, `p50_ms`, `max_ms`, `traceroute_snip`,
  `speedtest_*`
- Unavailable behavior: no dedicated unavailable artifact; the dashboard expects
  the generated file to exist
- Authoritative: yes, for the current dashboard telemetry window
- Generated: yes
- Should be committed: no

### `viz/network_attribution.json`

- Producer: `bin/transform_latest.py`
- Consumers: `viz/index.html`; evidence reference for Observation projection
- Purpose: backward-compatible legacy attribution export plus grouped WAN
  evidence summaries and incident intervals
- Required fields: `attribution_status`, `attribution_label`,
  `attribution_confidence`, `attribution_evidence`, `current_attribution`,
  `window_attribution`, `target_groups`, `internet_probe_summary`,
  `resolver_probe_summary`, `incidents`, `generated_at`, `observation_window`
- Optional fields: additive nested metrics and target-group facts inside
  `current_attribution`, `window_attribution`, `incidents`, and
  `attribution_evidence`
- Unavailable behavior: no dedicated unavailable artifact; current-attribution
  rendering can fall back to `viz/observations.json` first and to browser-side
  deterministic computation last
- Authoritative: no, for current Observation semantics; yes, as the preserved
  backward-compatible attribution export
- Generated: yes
- Should be committed: no

### `viz/observations.json`

- Producer: `bin/transform_latest.py` via `bin/observation_domain/`
- Consumers: `viz/index.html`; `bin/build_investigation.py`
- Purpose: authoritative Observation projection for deterministic semantics
  Prime Observer owns, including attribution and episode observations
- Required fields: top-level `schema_version`, `generated_at`, `model_version`,
  `observations`; each observation currently includes `id`, `type`, `scope`,
  `interval`, `state`, `supporting_facts`, `evidence_references`,
  `provenance`, `model_version`, and `generated_at`
- Optional fields: observation `confidence`, `explanation`, and provenance
  materialization details
- Unavailable behavior: no dedicated unavailable artifact; the dashboard falls
  back to legacy attribution and then browser computation, while investigation
  generation records unavailable projection provenance if the file is missing or
  unreadable
- Authoritative: yes
- Generated: yes
- Should be committed: no

### `viz/nextdns_summary.json`

- Producer: `bin/fetch_nextdns_summary.py`
- Consumers: `viz/index.html`; `bin/build_investigation.py`
- Purpose: optional public-safe DNS/security summary for dashboard context and
  copied investigation evidence
- Required fields: `schema_version`, `source`, `profile_id_suffix`, `window`,
  `generated_at`, `status`; `summary` when status is `ok`; `error` when status
  is `unavailable`
- Optional fields: warnings plus additive summary fields such as `top_reasons`,
  `top_queries`, `top_blocked`, `top_entities`, and redaction flags
- Unavailable behavior: the producer writes an explicit `unavailable` artifact
  with `summary: null` and an `error` payload
- Authoritative: yes, for the current local NextDNS summary
- Generated: yes
- Should be committed: no

### `viz/internet_conditions.json`

- Producer: `bin/fetch_cloudflare_radar.py`
- Consumers: `viz/index.html`; `bin/build_investigation.py`
- Purpose: optional Environmental Context summary about current Internet
  conditions
- Required fields: `schema_version`, `generated_at`, `provider`, `status`,
  `summary`, `scope`, `signals_checked`, `items`
- Optional fields: `query_mode`, `query_target_label`, `query_target_id`,
  `provider_display_name`, `fallback_used`, item-level `reference`;
  `scope.region` may be `null`; `items` may be empty for `normal` or
  `unavailable`
- Unavailable behavior: the producer writes an explicit `unavailable` artifact
  instead of failing the dashboard
- Authoritative: yes, for the current local Internet Conditions summary
- Generated: yes
- Should be committed: no

### `viz/investigation.json`

- Producer: `bin/transform_latest.py` via `bin/investigation_model.py` for
  automatic mode; `bin/build_investigation.py` for manual historical mode
- Consumers: `viz/investigate.html`
- Purpose: factual investigation package. Schema 2 automatic mode selects the
  current confirmed event and renders baseline/degradation/recovery lifecycle
  evidence. Schema 1 manual mode preserves requested-window historical evidence.
- Required schema 2 fields: `schema_version`, `mode`, `generated_at`, `id`,
  `title`, `status`, `artifact_state`, `freshness`, `selected_event`,
  `operator_brief`, `scope_impact`, `recovery_progress`, `episode_summary`,
  `evidence_argument`, `evidence_buckets`, `secondary_context`, `windows`,
  `timeline`, `periods`, `thresholds`, `target_groups`,
  `observation_references`, `events`, `timeline_samples`, `sources`,
  `provenance`, `notes`
- Required schema 1/manual fields: `schema_version`, `mode`, `generated_at`,
  `id`, `title`, `status`, `artifact_state`, `input`, `requested_window`,
  `context_window`, `event_window`, `thresholds`, `sources`, `target_groups`,
  `periods`, `observation_references`, `events`, `navigation`,
  `event_neighborhoods`, `timeline_samples`, `dns_context`, `provenance`,
  `notes`
- Optional fields: `internet_conditions_context`; observation references inside
  event details; empty evidence sections when no samples are present;
  automatic `message` when no sustained incident is present
- Unavailable behavior: no dedicated unavailable artifact; the script still
  writes a valid investigation payload and uses `status: "no_samples"` when no
  telemetry matches the selected source window. Automatic mode emits a valid
  no-incident artifact when no sustained event exists.
- Authoritative: yes, for the generated investigation package
- Generated: yes
- Should be committed: no

Automatic freshness and lifecycle are separate. `artifact_state` reports whether
the artifact is current, stale, historical, active, recovering, completed, or a
no-incident package. `freshness` reports generated, latest telemetry, and latest
evidence timestamps. A completed event can be current when it was generated from
the latest transform telemetry.

Automatic timeline rows include `phase_summary` so the renderer can show
representative p95, sustained-bad samples and buckets, phase duration, sample
count, and maximum excursions separately. A stable baseline with one high
isolated maximum must not be presented as worse than a sustained degradation
phase.

### `viz/investigations/<event-id>.json`

- Producer: `bin/transform_latest.py` via `bin/investigation_model.py`
- Consumers: `viz/investigate.html`, selected through the investigation catalog
- Purpose: immutable schema 2 evidence snapshot for one completed automatic event
- Required fields: top-level `artifact_type:
  "completed_investigation_snapshot"`, `schema_version`, `snapshot_written_at`,
  `generator`, `immutable: true`, the schema 2 investigation fields, a
  `selected_event` whose `lifecycle_state` is `complete`, and an
  `artifact_state` whose `is_historical` is `true`
- Optional fields: the same additive fields as `viz/investigation.json`
- Unavailable behavior: active and recovering events intentionally have no
  snapshot; an existing valid snapshot is preserved byte-for-byte without
  rewriting. Snapshot publication is atomic and write-once. Malformed or
  structurally invalid existing snapshot files are preserved on disk, excluded
  from valid history, and reported in `viz/investigation_catalog.json`.
- Authoritative: yes, for the completed event evidence recorded at first write
- Generated: yes
- Should be committed: no

### `viz/investigation_catalog.json`

- Producer: `bin/transform_latest.py` via `bin/investigation_model.py`
- Consumers: `viz/investigate.html`
- Purpose: newest-first catalog of immutable completed-event snapshots
- Required fields: top-level `artifact_type: "investigation_catalog"`,
  `schema_version`, `generated_at`, `generator`, `events`, and
  `invalid_snapshots`; each valid event includes `event_id`, `lifecycle`,
  `first_anomalous_at`, `recovered_at`, `severity`, `confidence`,
  `target_class`, `affected_targets`, `duration`, and `snapshot_path`
- Optional fields: additive fields inside future event or invalid-snapshot rows
- Unavailable behavior: the renderer shows a calm History panel when the catalog
  is missing, malformed, or contains no completed events. Invalid snapshot rows
  do not prevent valid snapshots from appearing.
- Authoritative: yes, for locally available automatic investigation snapshots
- Generated: yes
- Should be committed: no

### `viz/investigation_index.json`

- Producer: `bin/build_investigation.py`
- Consumers: local investigation catalog workflows; not consumed by the current
  browser views
- Purpose: local catalog of generated investigations
- Required fields: top-level `schema_version`, `generated_at`,
  `investigations`; each entry includes `id`, `title`, `created_at`,
  `event_count`, `status`, `path`
- Optional fields: none shown by the current producer
- Unavailable behavior: if unreadable or missing, the producer rebuilds a valid
  empty catalog shape before updating it
- Authoritative: yes, for the local investigation catalog
- Generated: yes
- Should be committed: no

Current limitation: index entries are catalog rows and generally point to the
mutable `viz/investigation.json` path. Automatic current-event generation does
not add entries to this historical index. Manual callers that need immutable
historical artifacts should pass a unique `--out` path.

### `viz/operator_assistant_input.json`

- Producer: `bin/build_operator_assistant_input.py`
- Consumers: `bin/run_operator_assistant_worker.py`,
  `bin/build_operator_assistant_output.py`, and `viz/investigate.html` for
  renderer-only current-hash comparison
- Purpose: compact deterministic evidence package derived from
  `viz/investigation.json` for bounded operator-assistant interpretation.
  Schema 2 inputs prefer `selected_event`, `windows`, `timeline`, `freshness`,
  and `artifact_state`; schema 1 inputs fall back to `requested_window`,
  `periods.during`, and existing observation references.
- Required fields: top-level `schema_version`, `semantic_schema_version`,
  `generated_at`, `input_hash`, `investigation`, `selected_event`,
  `operator_brief`, `scope_impact`, `recovery_progress`, `episode_summary`,
  `evidence_argument`, `phase_summaries`, `evidence_buckets`, `observations`,
  `attribution`, `episode`, `evidence`, `environmental_context`,
  `claim_boundaries`, `prohibited_claims`,
  `recommended_safe_diagnostic_categories`, `limitations`, and `provenance`
- Optional fields: additive provider details inside `environmental_context`
- Unavailable behavior: if `viz/investigation.json` is missing or unreadable,
  the producer still writes a valid minimal package with empty evidence and
  explicit limitations
- Authoritative: no; Prime Observer remains authoritative through the source
  investigation and upstream artifacts
- Generated: yes
- Should be committed: no

### `viz/operator_assistant_output.json`

- Producer: `bin/build_operator_assistant_output.py`
- Consumers: `viz/investigate.html`
- Purpose: local operator-assistant interpretation artifact derived from
  `viz/operator_assistant_input.json`
- Required fields: top-level `schema_version`, `generated_at`, `status`,
  `provider`, `input_hash`, `requested_model`, `source_file`, `headline`,
  `assessment`, `what_is_happening`, `affected_scope`, `healthy_scope`,
  `likely_fault_domain`, `confidence`, `uncertainty`, `evidence`,
  `limitations`, `next_steps`, `evidence_that_would_change_assessment`,
  `monitoring_guidance`, and `note`
- Optional fields: `provider_model`, `reason`, `usage`, and
  `provider_response_id`
- Unavailable behavior: the producer does not publish an unavailable artifact
  over a valid prior output. It records failure in
  `viz/operator_assistant_generation_state.json`; when no valid output exists,
  the browser renders deterministic fallback from `viz/investigation.json`.
- Prompt contract: the producer composes `docs/operator-charter.md`, the
  deterministic evidence package, and the unchanged response schema; model
  selection does not redefine operator communication behavior
- Execution behavior: a matching valid output is reused by default; `--force`
  requests a new provider call for the same input hash.
- Reuse behavior: safe reuse requires matching input hash, valid output shape,
  and matching requested model. Unsafe stale output is not presented as current.
- Authoritative: no; Prime Observer evidence and deterministic observations
  remain authoritative
- Generated: yes
- Should be committed: no

### `viz/operator_assistant_generation_state.json`

- Producers: `bin/transform_latest.py` and
  `bin/build_operator_assistant_input.py` for pending state;
  `bin/run_operator_assistant_worker.py` for generating, retry-wait, complete,
  duplicate-in-progress, and terminal failed state; the explicit output producer
  may also write direct-run provenance
- Consumers: `bin/run_operator_assistant_worker.py` and operator/provenance
  tooling; not primary UI content
- Purpose: atomic generated provenance and scheduling state for asynchronous
  assistant generation without overwriting valid interpretation output
- Required fields: top-level `schema_version`, `status`, `provider`,
  `input_hash`, `requested_at`, `updated_at`, and `attempt_count`
- Optional fields: `requested_model`, `provider_model`, `started_at`,
  `completed_at`, `next_retry_at`, `last_error_category`, `last_error`,
  `output_input_hash`, `output_validation_result`, `worker_id`, `requested_by`,
  and `reason`
- State behavior: semantic hash change resets to `pending`; due work or active
  lock ownership uses `generating`; transient failure moves to `retry_wait`;
  valid output moves to `complete`; exhausted or persistent failure moves to
  `failed`
- Concurrency behavior: an exclusive generated lock suppresses duplicate provider
  requests and may be replaced after the existing 900-second stale timeout
- Unavailable behavior: if missing, the Investigation page still renders from
  `viz/investigation.json` and any valid matching assistant output
- Authoritative: yes, for assistant generation provenance only
- Generated: yes
- Should be committed: no

## Relationships

- `viz/latest.csv` is factual telemetry projection, not attribution.
- `viz/network_attribution.json` is a preserved compatibility export, not the
  authoritative Observation projection.
- `viz/observations.json` is Observation, not raw evidence.
- `viz/nextdns_summary.json` is DNS/security context, not DNS interpretation or
  recommendations.
- `viz/internet_conditions.json` is Environmental Context, not attribution,
  health scoring, or noticeability logic.
- `viz/investigation.json` consumes telemetry plus additive Observation and
  provider context snapshots, but it does not rewrite those upstream artifacts
  into new semantics.
- `viz/investigation.json` is mutable current evidence in automatic mode;
  `viz/investigations/<event-id>.json` is immutable completed-event evidence.
- `viz/investigation_index.json` is catalog metadata, not investigation
  evidence.
- `viz/operator_assistant_input.json` is a compact downstream evidence package,
  not a replacement for `viz/investigation.json`, `viz/observations.json`, or
  any authoritative Prime Observer artifact.
- `viz/operator_assistant_output.json` is derived interpretation, not a source of
  telemetry truth, attribution truth, or deterministic Prime Observer semantics.
  The browser presents it as primary operator interpretation only when its
  `input_hash` matches the producer-generated `input_hash` in
  `viz/operator_assistant_input.json`; otherwise it renders deterministic
  fallback without showing provider failure as the product experience.
- The browser consumes artifacts and renders views, but it does not create the
  primary semantic meaning Prime Observer owns.

## Guidance For Future Contributors

New artifacts should:

- solve a distinct problem already supported by repository direction
- remain additive rather than replacing stable artifacts casually
- preserve existing contracts unless a broader change is explicitly approved
- keep Python as the owner of semantics
- avoid moving provider access or secret-backed logic into browser code
- degrade safely with an explicit unavailable or no-data path when optional
- remain deterministic, bounded, and explainable
- preserve provenance such as source files, timestamps, thresholds, and
  evidence references when relevant

Before adding an artifact, confirm:

- which existing artifact does not already solve the problem
- which Python producer owns the contract
- which browser or downstream consumer reads it
- what the unavailable behavior is
- whether the artifact should remain local-only and uncommitted

If those answers are not clear from repository evidence, mark the proposal:

`Needs Matthew Review`
