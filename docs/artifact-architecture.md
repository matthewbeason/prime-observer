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
  `bin/build_investigation.py` generates the factual investigation package, and
  `viz/investigate.html` renders it.
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

- Producer: `bin/build_investigation.py`
- Consumers: `viz/investigate.html`
- Purpose: factual historical investigation package for a requested window,
  including evidence organization, event navigation, and additive Observation or
  provider context snapshots when available
- Required fields: `schema_version`, `generated_at`, `id`, `title`, `status`,
  `input`, `requested_window`, `context_window`, `event_window`, `thresholds`,
  `sources`, `target_groups`, `periods`, `observation_references`, `events`,
  `navigation`, `event_neighborhoods`, `timeline_samples`, `dns_context`,
  `provenance`, `notes`
- Optional fields: `internet_conditions_context`; observation references inside
  event details; empty evidence sections when no samples are present
- Unavailable behavior: no dedicated unavailable artifact; the script still
  writes a valid investigation payload and uses `status: "no_samples"` when no
  telemetry matches the requested window
- Authoritative: yes, for the generated investigation package
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

### `viz/operator_assistant_input.json`

- Producer: `bin/build_operator_assistant_input.py`
- Consumers: `bin/build_operator_assistant_output.py` and
  `viz/investigate.html` for renderer-only current-hash comparison
- Purpose: compact deterministic evidence package derived from
  `viz/investigation.json` for bounded operator-assistant interpretation tests
- Required fields: top-level `schema_version`, `generated_at`, `input_hash`,
  `investigation`, `observations`, `attribution`, `episode`, `evidence`,
  `environmental_context`, `limitations`, and `provenance`
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
- Purpose: local operator-assistant review artifact derived from
  `viz/operator_assistant_input.json`
- Required fields: top-level `schema_version`, `generated_at`, `status`,
  `provider`, `input_hash`, `requested_model`, `source_file`, `assessment`,
  `confidence`, `evidence`, `limitations`, `next_steps`, and `note`
- Optional fields: `provider_model`, `reason`, `usage`, and
  `provider_response_id`
- Unavailable behavior: the producer writes an explicit `unavailable` artifact
  when the input artifact is missing, OpenRouter is not configured, the request
  fails, or the provider response is invalid
- Reuse behavior: the producer skips a new OpenRouter request and preserves the
  existing successful artifact only when the input producer's deterministic
  hash and requested model are unchanged
- Authoritative: no; Prime Observer evidence and deterministic observations
  remain authoritative
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
- `viz/investigation_index.json` is catalog metadata, not investigation
  evidence.
- `viz/operator_assistant_input.json` is a compact downstream evidence package,
  not a replacement for `viz/investigation.json`, `viz/observations.json`, or
  any authoritative Prime Observer artifact.
- `viz/operator_assistant_output.json` is a derived review artifact, not a
  source of telemetry truth, attribution truth, or deterministic Prime Observer
  semantics. The browser should only present an assistant assessment as current
  when its `input_hash` matches the producer-generated `input_hash` in
  `viz/operator_assistant_input.json`.
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
