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
  local scripts, and static views rather than a cloud backend.
- Generated JSON and CSV artifacts remain canonical production contracts.
  `data/prime_observer.db` is currently a rebuildable, non-authoritative shadow
  copy of Prime-owned raw observations; see `docs/storage.md`. A future
  authority cutover requires a separate explicit phase.
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

- Collection: Python-owned. `bin/collector.py` appends authoritative local
  telemetry to `data/bakeoff_YYYYMMDD.csv`, then attempts non-authoritative
  shadow ingestion through `bin/storage.py`. Optional provider summaries come
  from local Python fetchers.
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
- Local application-experience probes are synthetic checks, not provider context;
  they are collected separately and consumed by transform as local evidence.
- Mesh Signal is an optional local infrastructure evidence source, not
  Environmental Context. Prime Observer consumes only its normalized artifact;
  it does not own router collection, credentials, transport, or firmware logic.
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
  `attribution_evidence`; Phase 2 adds `refined_attribution` with
  provider-neutral attribution domain, confidence, supporting evidence, evidence
  against, and unresolved evidence while retaining the legacy fields unchanged
- Unavailable behavior: no dedicated unavailable artifact; current-attribution
  rendering can fall back to `viz/observations.json`, but the browser does not
  compute attribution when both generated sources are unavailable
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
- Unavailable behavior: no dedicated unavailable artifact; the dashboard may
  render the legacy generated attribution export, but attribution remains
  unavailable when generated sources are missing. Investigation generation
  records unavailable projection provenance if the file is missing or unreadable.
- Authoritative: yes
- Generated: yes
- Should be committed: no

### `viz/dashboard_health.json`

- Producer: `bin/transform_latest.py`
- Consumers: `viz/index.html`; evidence source for automatic investigation
- Purpose: Python-owned dashboard health projection for WAN sample
  classification, target-group buckets, composite WAN buckets, LAN evidence, and
  additive Phase 2 health dimensions
- Required fields: `schema_version`, `generated_at`, `model_version`,
  `semantic_model_version`,
  `dashboard_window`, `wan_samples`, `wan_target_group_buckets`,
  `composite_wan_buckets`, `lan_evidence`, and `attribution_evidence_counts`
- Optional fields: Phase 2 adds `health_dimensions` and `dependency_groups`
  without changing existing fields or renderer behavior. Semantic consistency
  fields expose absolute excursions, learned-normal state, guardrail breaches,
  operator badness, incident eligibility, and the shared evaluator version.
  Compatibility `rawBad` counts now represent operator-bad samples; explicit
  `absoluteExcursions` counts preserve fixed-threshold facts separately.
- Unavailable behavior: the dashboard keeps raw latency charts available but
  marks semantic health and the heatmap unavailable. It does not reclassify raw
  telemetry in JavaScript.
- Authoritative: yes, for generated dashboard health classification and Phase 2
  health dimensions
- Generated: yes
- Should be committed: no

### `viz/mesh_context.json`

- Producer: `bin/mesh_context.py`, invoked by `bin/transform_latest.py`
- Consumers: `viz/index.html` for compact LAN-panel context only
- Purpose: Prime-owned projection of current normalized Mesh Signal evidence
  plus minimized historical change timing from the owner-only SQLite store
- Required fields: `schema_version`, `model_version`, `generated_at`, `status`,
  `freshness`, `source`, `latest_attempt`, `last_good`, `lan_evidence`,
  `history_evidence`, `warnings`,
  `limitations`, `summary`, `privacy`, and `provenance`
- Input selection: process `MESH_SIGNAL_ARTIFACT_PATH`, then the same key in the
  ignored repository-local `.env.mesh`; relative paths resolve from the Prime
  Observer repository root. Missing configuration or input is a normal
  unavailable state.
- History input selection: process `MESH_SIGNAL_HISTORY_PATH`, then the same key
  in `.env.mesh`, then Mesh Signal's standard owner-only Application Support
  path. The database is opened with SQLite `mode=ro` and `query_only`; Prime
  performs no schema creation, migration, retention, or collection.
- Freshness: fresh through 12 minutes, stale after 12 minutes, and unusable for
  current semantics after 30 minutes; freshness does not rewrite collection
  validity
- Lineage: `latest_attempt` describes only the most recent artifact;
  `last_good` retains independently timestamped complete family facts from a
  previous valid projection. Retained facts are never labeled current, and an
  identity-epoch change prevents cross-epoch retention.
- Privacy: friendly names and pseudonymous stable IDs remain local-only;
  rejection errors are bounded and never echo source values or the full source
  path. Source schema 0.3 requires `privacy.exposure_mode`; `minimal` identity
  fields are not assumed, while optional `local`/`full` address identity can be
  consumed transiently for probe-host matching. Prime never persists source
  local addresses or MAC addresses.
- Unavailable behavior: missing, malformed, unsupported, privacy-rejected, or
  failed input still produces an atomic bounded projection and cannot stop the
  ordinary transform
- Authoritative: yes, only for Prime's Mesh evidence projection, freshness,
  lineage boundaries, and deterministic before/during/after interval alignment;
  no, for causation, health, attribution, Observation, investigation, or
  Operator Assistant semantics
- Historical: `history_evidence` is a bounded read-only projection of Mesh
  history schema 0.1. It copies no canonical snapshot JSON, evidence JSON,
  source IDs, entity IDs, identity epochs, or lineage IDs and writes no history.
- UI boundary: `lan_evidence` is an address-, MAC-, client-name-, and stable-ID-
  free presentation projection for the LAN panel. Its `probe_host` block may
  contain the local associated-node display name plus medium, band, raw relative
  signal, and apparent link rate after a unique Python-owned address match. The
  browser renders those emitted facts, states, counts, and lineage but does not
  derive health or attribution. Current Mesh context is not point-in-time
  evidence for historical LAN samples.
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
  `provider_display_name`, `fallback_used`, `model_version`, `checked_window`,
  `signal_results`, `degradation`, `limitations`, and item-level `reference` or
  route-leak event metadata, plus item-level `provider_event_id` and
  `detected_at` where supplied; `scope.region` may be `null`; `items` may be empty
  for `normal` or `unavailable`
- Internet Conditions v2 keeps Cloudflare Radar as the implemented provider and,
  in configured ASN mode, independently records AS traffic anomalies,
  ASN-involved BGP route leaks, broad US outages, and broad US traffic anomalies.
  Each signal result records availability, status, item count, summary, latest
  signal time, and secret-free query metadata.
- Unavailable behavior: the producer writes an explicit `unavailable` artifact
  instead of failing the dashboard. Partial provider failures preserve successful
  signal results and list unavailable lanes under `degradation`.
- Authoritative: yes, for the current local Internet Conditions summary
- Generated: yes
- Should be committed: no

### `viz/aps_power_context.json`

- Producer: `bin/fetch_aps_power_context.py`
- Consumers: `viz/index.html`; `bin/build_investigation.py`;
  `bin/transform_latest.py` through `bin/time_context.py`
- Purpose: optional current APS Power Infrastructure snapshot
- Required fields: `schema_version`, `generated_at`, `provider`, `status`,
  `summary`, `scope`, `signals_checked`, and `items`
- Optional fields: dataset-level `provider_updated_at`; item-level
  `provider_event_id`, `event_start`, `estimated_restoration_time`,
  `provider_status`, `data_status`, `source_layer`, `affected_area`,
  `customer_count`, and `source_reference`
- Temporal behavior: `event_start` comes only from APS `off`;
  `provider_updated_at` comes from update-layer `TIMESTAMP`; estimated
  restoration remains forecast detail and is never an event end
- Unavailable behavior: the producer writes an explicit `unavailable` artifact
  instead of failing the dashboard
- Authoritative: yes, for the current local APS summary only
- Generated: yes
- Should be committed: no

### `viz/diagnostic_evidence.json`

- Producer: optional manual/import tooling; not required by the transform
- Consumers: `bin/transform_latest.py` through `bin/health_dimensions.py`
- Purpose: optional provider-neutral diagnostic evidence that can refine health
  dimensions, attribution confidence, and impact assessment without overriding
  telemetry
- Required fields when present: `schema_version`, `model_version`, `status`, and
  `items`
- Optional item fields: `type`, `observed_at`, `ingested_at`, `freshness`,
  `provenance`, `confidence`, `target_association`, `incident_association`,
  `summary`, `details`, and type-specific fields such as resolver route, direct
  DNS query, traceroute, active dependency path, provider/PoP assignment,
  application symptom, user report, operator observation, or provider diagnostic
  reference details
- Unavailable behavior: an absent file is normal; malformed content is reported
  as diagnostic-evidence limitations inside the generated health-dimensions block
  and does not stop artifact generation
- Authoritative: no; telemetry remains authoritative for measured conditions
- Generated: optional/local
- Should be committed: no

### `viz/application_experience.json`

- Producer: `bin/fetch_application_experience.py`
- Consumers: `bin/transform_latest.py` through `bin/health_dimensions.py`
- Purpose: deterministic local synthetic DNS and HTTPS transaction evidence for
  impact-v2 estimation
- Required fields: `schema_version`, `model_version`, `generated_at`, `status`,
  `overall_status`, `freshness`, `dns_transactions`, `https_transaction`,
  `failure_counts`, `latency_summaries`, `source`, `config`, and `limitations`
- DNS transaction fields: role, target hostname, resolver endpoint when known,
  checked timestamp, success/failure status, latency, timeout, response code, and
  failure category
- HTTPS transaction fields: target URL without query string, checked timestamp,
  DNS duration when measurable, TCP connect duration, TLS duration, time to first
  byte, total duration, HTTP status, timeout, and failure category
- Unavailable behavior: absent, malformed, or stale artifacts are normalized as
  unavailable evidence and do not affect current estimated impact
- Authoritative: no, for attribution; yes, as local synthetic transaction
  evidence considered by `estimated_user_impact`
- Generated: yes
- Should be committed: no

### `viz/operator_impact_feedback.json`

- Producer: `bin/record_operator_impact.py`
- Consumers: `bin/transform_latest.py` through `bin/health_dimensions.py`;
  `bin/build_operator_assistant_input.py` through generated investigation
  health dimensions
- Purpose: local operator-observed impact feedback for the current investigation
  incident
- Required fields: `schema_version`, `model_version`, `incident_id`,
  `observed_at`, `impact_state`, `note`, `source`, and `freshness`
- Supported impact states: `none_observed`, `minor_slowness`,
  `intermittent_failures`, `major_disruption`, `full_outage`, and `unknown`
- Unavailable behavior: absent, malformed, stale, cleared, or mismatched-incident
  feedback normalizes to unavailable and does not affect current observed impact
- Authoritative: yes, for local operator feedback only; no, for telemetry,
  attribution, technical severity, or estimated impact
- Generated: local runtime artifact
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
- Optional fields: `incident_record`; `incident_phases`; `incident_replay`;
  `internet_conditions_context`; observation references inside event details;
  empty evidence sections when no samples are present; automatic `message` when
  no sustained incident is present; Phase 2 adds `health_dimensions`,
  `impact_assessment`, `dependency_state`, and
  `deterministic_operator_interpretation` additively for current artifacts and
  for new snapshots only
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

`incident_record` is additive and Python-owned. It contains deterministic
operator-facing story fields: `incident_id`, `title`, `incident_type`,
`started_at`, `confirmed_at`, `latest_affected_at`, `duration_minutes`,
`status`, `affected_services`, `healthy_comparisons`, `likely_issue`,
`user_facing_impact`, `confidence`, `narrative`, and `evidence_refs`. The browser
renders these fields but does not infer or rewrite them. Older schema 2 artifacts
and existing immutable snapshots without `incident_record` continue to render
through the established fallback sections.

`incident_phases` is additive and Python-owned. It contains `before`, `during`,
and optional `after` phase objects with `available`, `start`, `end`, `status`,
`headline`, `summary`, `affected_services`, `healthy_comparisons`,
`representative_metrics`, `maximum_excursions`, `evidence_refs`, and
`limitations`. `before` describes the pre-incident comparison window without
calling it healthy unless the evidence supports that. `during` starts at the
first anomaly and explains persistence, affected services, healthy comparisons,
application checks, and likely issue when supported. `after` is present only when
recovery candidate evidence exists and distinguishes candidate, started, and
confirmed recovery states.

`incident_replay` is additive and Python-owned. It contains ordered `milestones`
for deterministic operator replay. Each milestone includes `id`, `timestamp`,
`state`, `title`, `summary`, `affected_services`, `healthy_services`,
`likely_issue`, `confidence`, `evidence_refs`, and `metrics_snapshot`. The
browser renders the sequence and expandable evidence details without calculating
ordering, meaning, lifecycle, likely issue, or metrics. Older artifacts without
`incident_replay` continue to use the established timeline fallback.

Adaptive Baseline Phase A introduced resolver-member metadata under
`health_dimensions.adaptive_baseline.resolver_members` and each resolver
dependency member's `adaptive_baseline` object. These fields include baseline
state, model/version identifiers, learned range, baseline/evidence windows,
deviation from baseline, absolute threshold state, guardrail breaches,
incident-eligibility metadata, suppression reason, and confidence. The current
shared semantic evaluator uses those inputs for dashboard health, interval
summary, observation/attribution production, and current investigation
production. Raw measurements and absolute excursions remain factual and
separate from operator-facing badness. Immutable completed snapshots are not
rewritten. Current investigation artifacts may add `incident_suppressed`,
`suppression_reason`, `adaptive_recovery`, and `baseline_transition` to explain
accepted stable elevated behavior.

Adaptive Baseline Phase C adds generated `viz/baseline_history.json` for durable
per-target baseline memory. It contains schema/model versions, generated time,
global baseline version, target entries keyed by `phase|target_class|member`,
accepted windows, sample counts, p95 distribution summaries, jitter range,
loss/timeout rates, confidence, accepted state, prior baseline summaries,
guardrail status, source files, source time coverage, and limited version
history. The active baseline is recency aware: it is learned from the newest two
eligible telemetry source files, older accepted ranges remain in
`version_history`, and a `post_recovery_stabilizing` guardrail blocks retraining
when the older observed median is materially better than the most recent
samples. The artifact is compact, stores no raw telemetry duplicates, and is
published atomically. If missing, malformed, stale, incompatible, insufficient,
or guardrail-blocked, health evaluation falls back to in-window adaptive baseline
behavior. Historical investigation snapshots are not rewritten.

Temporal Memory Phase 1 adds generated `viz/interval_summary.json` for one
selected interval. It is produced by Python during the automatic transform and
contains schema/model versions, generated time, interval start/end/duration,
current-or-historical classification, coverage, overall condition, user impact,
application summary, likely issue, affected services, healthy services, incident
overlap, baseline comparison, confidence, deterministic summary text, evidence
references, interval metrics, and the shared semantic model version. Resolver
classification uses the same evaluator and eligible baseline context as
dashboard health. Current-only application and baseline projections are not
substituted for arbitrary historical intervals. The browser only renders this artifact when
its `start` and `end` exactly match the requested interval route. It does not
infer interval health, issue type, affected scope, overlap, or narrative in
JavaScript.

Incident Intelligence Phase E adds generated `viz/incident_similarity.json` for
deterministic current-incident similarity. It contains schema/model versions,
generated time, `current_incident`, and scored `matches` against completed
incident snapshots. Each match includes incident id, score, deterministic pattern
label, summary, per-dimension weighted breakdown, matching and different
dimensions, previous duration, recovery, user impact, operator feedback, evidence
references, and confidence. Python owns all scoring and pattern labeling; the
browser only renders the artifact and hides it when it does not match the current
incident.

Operational Learning Phase 1 adds generated `viz/operational_learnings.json` for
deterministic operational knowledge accumulated from completed incident snapshots
and durable baseline history. It contains schema/model versions, generated time,
`learning_version`, and `insights`. Each insight includes id, category, title,
summary, confidence, supporting incidents, supporting intervals, supporting
baselines, first/last seen timestamps, observation count, stability, and evidence
references. Python owns all insight creation, confidence, conflict reduction, and
retirement. The browser only renders active artifact-provided insights and does
not summarize, score, infer recurrence, or call an LLM.

### `viz/operational_learnings.json`

- Producer: `bin/transform_latest.py` via `bin/operational_learnings.py`
- Consumers: `viz/index.html`; `viz/investigate.html`
- Purpose: compact deterministic operational lessons from repeated completed
  incident evidence and durable baselines
- Required fields: `schema_version`, `model_version`, `generated_at`,
  `learning_version`, `insights`; each insight includes `id`, `category`,
  `title`, `summary`, `confidence`, `supporting_incidents`,
  `supporting_intervals`, `supporting_baselines`, `first_seen`, `last_seen`,
  `times_observed`, `stability`, and `evidence_refs`
- Optional fields: no optional Phase 1 fields beyond empty support arrays
- Unavailable behavior: dashboard and Investigation hide the learning card/section
  if the artifact is missing, malformed, or contains no active insights
- Authoritative: yes, for generated operational learning
- Generated: yes
- Should be committed: no

Temporal Workspace Phase 1 adds generated `viz/time_context.json` for the
default selected time context. It contains schema/model versions, mode, start,
end, optional selected/overlapping incident ids, incident overlap, external
context overlap, Python-aligned external events, and generated time. Python owns the generated default context.
The dashboard can project heatmap selection into that same shape from existing
Python-owned bucket and interval artifacts, but it does not classify interval
health, infer incident overlap, call collectors, or invoke OpenRouter.

### `viz/time_context.json`

- Producer: `bin/transform_latest.py` via `bin/time_context.py`
- Consumers: `viz/index.html`
- Purpose: additive selected-time context record for the dashboard workspace
- Required fields: `schema_version`, `model_version`, `mode`, `start`, `end`,
  `overlaps_incident`, `overlaps_external_context`, `external_events`,
  `external_context_note`, and `generated_at`
- Optional fields: `selected_incident_id`, `incident_id`
- External event alignment is produced by `bin/external_context_history.py` from
  provider event times. Collection time, provider update time, and APS estimated
  restoration are never substituted for event start/end. Snapshot-only records
  remain explicitly unaligned.
- Unavailable behavior: dashboard falls back to a safe current context and keeps
  existing dashboard and Investigation behavior
- Authoritative: yes, for the generated default time context
- Generated: yes
- Should be committed: no

Investigation URL semantics are explicit. `?view=current` loads the mutable
current artifact. `?view=interval&start=<ISO>&end=<ISO>` displays a matching
`viz/interval_summary.json` when available; otherwise it displays a safe selected
interval request and does not load `viz/investigation.json` as a substitute.
`?view=incident&event=<event-id>` loads an immutable snapshot through the
catalog. Legacy `?event=<event-id>` links remain supported when the catalog
contains the event.

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
  plus Phase 2 deterministic `health_dimensions`, `dependency_groups`,
  `impact_assessment`, and `deterministic_operator_interpretation`
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
- `data/prime_observer.db` is a validation-only shadow of Prime-owned raw probe
  observations. It is not an artifact consumed by transforms or the browser,
  and it is not authoritative in Storage Phase 1.
- `viz/network_attribution.json` is a preserved compatibility export, not the
  authoritative Observation projection.
- `viz/observations.json` is Observation, not raw evidence.
- `viz/nextdns_summary.json` is DNS/security context, not DNS interpretation or
  recommendations.
- `viz/internet_conditions.json` is Environmental Context, not attribution,
  health scoring, or noticeability logic.
- `bin/external_context_history.py` defines provider identity, observation
  lifecycle, and interval alignment without selecting a persistent backend.
- `viz/mesh_context.json` is current and historical local infrastructure
  evidence, not Environmental Context, attribution, health scoring, a causal
  conclusion, an Observation, investigation evidence, or Operator Assistant
  input.
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
