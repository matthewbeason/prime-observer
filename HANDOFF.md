# HANDOFF.md

## Current State

- Branch: `main`
- Latest release: `v0.10.0`
- Release commit includes the SQLite authority cutover and the completed
  post-`v0.9.0` architecture and product work

Prime Observer currently ships:

- Storage Phase 5 SQLite authority for Prime-owned raw observations, with every
  semantic-critical reader behind the centralized source boundary
- Storage Phase 2 diagnostic read-path evaluation with exact bounded raw-history
  parity and read-only comparison tooling supporting the later authority cutover
- Storage Phase 3 verified SQLite-native backup, deterministic retention,
  defensive restore/restore-latest, atomic CSV rebuild, operator health status,
  and a tracked daily LaunchAgent
- deterministic health modeling over local telemetry
- observation-backed attribution and episode semantics
- automatic current-event investigation generation with incident lifecycle,
  phases, and deterministic replay
- immutable completed-event investigation history with similarity and
  operational learning projections
- operator-first Investigation rendering with deterministic fallback assessment
- OpenRouter-backed Operator Assistant interpretation as the primary
  operator-facing narrative when output is valid for the evidence package
- Python-owned multidimensional health evaluation with additive artifact fields
  rendered in the dashboard and Investigation UI
- adaptive learned baselines with durable baseline memory in
  `viz/baseline_history.json`, a recency-aware active window, and
  post-recovery stabilization protection
- impact-v2 fields that preserve legacy `user_impact` while separating
  estimated user impact from observed reports
- asynchronous pending-work consumption through a separate local worker and
  tracked 60-second LaunchAgent
- manual requested-window investigation generation and viewing
- direct link/bookmark entry points for current, selected-interval, and
  completed incident views
- one deterministic selected-interval summary rendered on exact route match
- optional NextDNS summary context
- optional Cloudflare Radar Internet Conditions context
- optional APS Power Infrastructure context
- optional local Application Experience probes that feed estimated impact only
- optional current and historical Mesh Signal evidence rendered as compact LAN
  context and non-causal timeline markers,
  with no health, attribution, investigation, or assistant consumer
- local operator impact feedback that feeds observed impact only

## Recent Completed Work

Repository-backed recent milestones (all committed):

- `v0.8.0`: Observation domain foundation
- `v0.8.1`: Bucket selection alignment
- `v0.8.2`: Dashboard operator polish
- `v0.9.0`: Internet Conditions external context
- `v0.10.0`: completed post-`v0.9.0` architecture and product milestone,
  including SQLite authority for Prime-owned raw observations
- v0.10.0 event-aligned automatic investigation lifecycle and hardened
  completed-event history
- v0.10.0 operator-first Investigation redesign with asynchronous
  Operator Assistant generation and last-known-good publication behavior
- v0.10.0 health-dimensions work: Phase 1 calibration document, Phase 2
  deterministic evaluator with additive artifact output, and Phase 3 browser
  rendering for emitted multidimensional fields
- v0.10.0 Investigation IA redesign of `viz/investigate.html` as an
  Incident Record over existing artifacts
- v0.10.0 Operator Assistant model selection pinned to
  `google/gemini-3.5-flash` by default, provider auto-routing rejected, and
  additive impact-v2 fields distinguish estimated and observed user impact
- v0.10.0 separate local application-experience collector feeding
  estimated impact only
- v0.10.0 `bin/record_operator_impact.py` and local operator impact
  feedback feeding observed impact only
- v0.10.0 Incident Intelligence Phases 1-3: explicit entry points,
  Python-owned `incident_record`, `incident_phases`, and deterministic replay
- v0.10.0 Adaptive Baseline Phases A-C and recency-aware durable
  baselines: resolver adaptive classification metadata, adaptive incident
  eligibility, and `viz/baseline_history.json` durable memory with a newest-two
  active source window and post-recovery stabilization guardrail
- v0.10.0 Temporal Memory Phase 1: one deterministic selected-interval
  summary in `viz/interval_summary.json`
- v0.10.0 Incident Intelligence Phase E: deterministic current-incident
  similarity in `viz/incident_similarity.json`
- v0.10.0 Operational Learning Phase 1: deterministic lessons in
  `viz/operational_learnings.json`
- v0.10.0 Temporal Workspace Phase 1: default selected-time context in
  `viz/time_context.json`
- v0.10.0 dashboard presentation work: consolidated hierarchy, unified
  time context, simplified operator dashboard, and restored visualization
  rendering

Next conceptual milestone:

- `Needs Matthew Review` after the current committed state is lived with

## Current Architecture State

Current artifact flow:

- authoritative telemetry history in generated `data/prime_observer.db`, with
  optional idempotent `data/bakeoff_YYYYMMDD.csv` export for diagnostics and
  explicit recovery
- centralized raw source routing for every semantic branch; the browser remains
  database-unaware
- `bin/transform_latest.py` generates dashboard, observation, baseline,
  interval, similarity, learning, time-context, mutable current investigation,
  write-once completed snapshot, and investigation catalog artifacts
- `bin/build_investigation.py` generates manual requested-window evidence
  artifacts
- optional fetchers generate DNS, Internet Conditions, and APS Power
  Infrastructure summaries
- `bin/fetch_application_experience.py` generates local DNS/HTTPS transaction
  evidence for impact-v2 corroboration
- `bin/record_operator_impact.py` records bounded local operator feedback for
  the current investigation incident
- `bin/interval_summary.py` builds one deterministic selected-interval summary
  during automatic transform
- `bin/incident_similarity.py` compares the current investigation with completed
  snapshots using deterministic weighted scoring
- `bin/operational_learnings.py` accumulates deterministic operational learning
  from repeated completed incidents and durable baseline history
- `bin/time_context.py` emits the default selected-time context for the dashboard
  workspace
- `bin/mesh_context.py` validates the optional normalized Mesh Signal artifact,
  reads history schema 0.1 through SQLite read-only mode, and atomically emits
  minimized local infrastructure evidence; it performs no router or other
  network calls. Process configuration overrides the ignored `.env.mesh`, and
  the standard Mesh history path needs no tracked private path.
- `viz/index.html` and `viz/investigate.html` consume generated local files

Current projection state:

- `viz/latest.csv` remains the dashboard telemetry input
- `viz/network_attribution.json` remains the backward-compatible attribution
  export
- `viz/observations.json` is the repository-described authoritative Observation
  projection for deterministic semantics Prime Observer owns
- `viz/baseline_history.json` is compact generated durable baseline memory keyed
  by phase, target class, and host/member identity, with a recency-aware active
  window over the newest two telemetry source files
- `viz/interval_summary.json` is the generated deterministic summary for one
  selected interval, rendered only when route start/end match the artifact
- `viz/incident_similarity.json` is the generated current-incident similarity
  projection over completed snapshots, with Python-owned scores and explanations
- `viz/operational_learnings.json` is the generated operational learning artifact
  over repeated completed incidents and durable baselines, with Python-owned
  confidence and retirement handling
- `viz/time_context.json` is the generated default time context used by the
  dashboard when no heatmap interval is selected
- `viz/mesh_context.json` is the generated, uncommitted Mesh evidence projection.
  It keeps latest-attempt and independently aged family-level last-good facts
  separate, adds identity-minimized change points plus Python-aligned
  before/during/after interval context, and is safe to be absent. Schema 0.3
  local identity can uniquely map the Prime host to a client using transient
  local-address intersection; addresses, MACs, client IDs, and client names are
  not persisted.
  Its minimized `lan_evidence` block renders the probe attachment beside the LAN
  chart. `history_evidence` supplies neutral timeline markers and selected-time
  context without copying identifiers or source evidence values, altering chart
  scale, claiming causation, or changing health, attribution, Observation,
  Investigation, or Operator Assistant semantics.
- `viz/investigation.json` is the mutable current investigation artifact
- `viz/investigations/<event-id>.json` contains immutable completed-event
  snapshots published atomically and never overwritten
- `viz/investigation_catalog.json` is a generated projection over valid
  snapshots and any preserved invalid snapshot metadata
- `viz/operator_assistant_input.json` is the deterministic evidence package for
  OpenRouter interpretation
- `viz/operator_assistant_output.json` is last valid matching Operator Assistant
  interpretation and is never replaced by provider/configuration failure
- `viz/operator_assistant_generation_state.json` tracks pending, generating,
  retry-wait, complete, and terminal failed state separately from valid output
- `bin/run_operator_assistant_worker.py` consumes pending/due work without
  blocking collection or deterministic transform; the tracked LaunchAgent is
  implemented but not installed automatically

## Active Watch Period

The v0.10.0 release includes the investigation lifecycle redesign, adaptive
baselines, semantic parity, interval intelligence, incident similarity,
operational learning, historical context, the temporal dashboard workspace, and
the SQLite authority cutover.

Direct links/bookmarks for current, selected-interval, and historical
investigation entry points are explicit: `?view=current`,
`?view=interval&start=<ISO>&end=<ISO>`, and
`?view=incident&event=<event-id>`. Selected interval view uses matching
Python-generated `viz/interval_summary.json` evidence when available and keeps a
safe request state otherwise. Current investigation can show deterministic
`Seen before` similarity from `viz/incident_similarity.json`; completed incident
views remain immutable evidence views. Operational Learning Phase 1 can show
repeated deterministic lessons from completed incidents and durable baselines; it
does not use LLM summarization or browser inference. Legacy
`?event=<event-id>` links remain supported. Multiple stored interval summaries
remain future work.

SQLite is authoritative for Prime-owned raw observations. CSV is an explicit
export, diagnostic, rebuild, and recovery source. Generated CSV/JSON artifacts
remain canonical browser and downstream contracts, and the browser remains
database-unaware. Mesh Signal SQLite remains externally owned and read-only;
immutable completed-incident snapshots remain write-once files.

Watch items currently named in the repository:

- stable-but-noticeable false negatives
- noisy-but-masked false positives
- whether DNS Security adds context or clutter
- whether Pattern confidence feels trustworthy
- whether turbulence is informative or distracting
- whether attribution confidence matches real experience
- whether the compact Connection card and refocused WAN Health Summary improve
  scanning
- whether investigation navigation and nearby-event discovery improve evidence
  review without implying correlation
- whether the recency-aware active baseline and post-recovery stabilization
  behave as expected over time

## Resume Checklist

When resuming work:

1. Confirm the current tag, branch, and working tree state.
2. Read `README.md`, `AGENTS.md`, `ROADMAP.md`, and `DECISIONS.md`.
3. Read the specific docs and code for the area being changed.
4. Preserve the repository’s architecture boundaries and terminology.
5. Mark any unsupported assumption as `Needs Matthew Review`.

## Needs Matthew Review

- What milestone should follow the v0.10.0 release.
- Whether the repository wants a standing handoff file updated each release or
  only when work pauses midstream.
