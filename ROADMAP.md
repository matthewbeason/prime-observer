# ROADMAP.md

## Purpose

This file records milestone-level project progression and the current repository
staging point. It is not a speculative feature list.

If a next step is not explicit in the repository, it is marked:

`Needs Matthew Review`

## Completed Milestones

Repository tags and release labels show this progression:

- `v0.1.0`: Initial release
- `v0.3.1`: Rename from `net-bakeoff` to Prime Observer
- `v0.3.12`: Attribution accuracy improvements
- `v0.3.13`: Baseline pattern awareness
- `v0.3.14`: WAN health refocus
- `v0.5.0`: Historical investigation workflow
- `v0.6.0`: Investigation workflow improvements
- `v0.7.2`: Dashboard and mobile observability refresh
- `v0.8.0`: Observation domain foundation
- `v0.8.1`: Bucket selection alignment
- `v0.8.2`: Dashboard operator polish
- `v0.9.0`: Internet Conditions external context

Post-`v0.9.0` committed work (ahead of the `v0.9.0` tag, no new release yet):

- event-aligned automatic investigation lifecycle and immutable completed-event
  history
- operator-first Investigation redesign as an Incident Record with deterministic
  fallback assessment and asynchronous Operator Assistant generation
- multidimensional health evaluator (Phase 1 calibration document, Phase 2
  deterministic evaluator, Phase 3 browser rendering of emitted fields)
- Operator Assistant model pinned to `google/gemini-3.5-flash` by default with
  impact-v2 estimated/observed impact separation
- application-experience probes and local operator impact feedback
- Incident Intelligence Phases 1-3: explicit entry points, `incident_record`,
  `incident_phases`, and deterministic replay
- Adaptive Baseline Phases A-C: resolver adaptive classification metadata,
  adaptive incident eligibility, and durable baseline memory
- recency-aware durable baselines with a newest-two source-file active window
  and post-recovery stabilization protection
- Temporal Memory Phase 1: one deterministic selected-interval summary
- Incident Intelligence Phase E: deterministic current-incident similarity
- Operational Learning Phase 1: deterministic repeated lessons
- Temporal Workspace Phase 1: default selected-time context
- dashboard presentation work: consolidated hierarchy, unified time context,
  and restored visualization rendering
- semantic consistency phase: one Python-owned evaluator across dashboard
  health, latest interval summary, observation/attribution production, and
  current investigation production; browser semantic fallbacks removed
- Storage Phases 1-2: rebuildable SQLite shadow ingestion plus exact read-only
  bounded raw-history equivalence tooling
- Storage Phase 3: verified backup, retention, restore/restore-latest,
  corruption recovery, CSV rebuild, storage health, and daily backup automation
- read-only Mesh Signal history schema 0.1 projection with identity-minimized
  derived change markers and deterministic before/during/after context

Current uncommitted working milestone:

- Storage Phase 5 complete semantic-reader migration and SQLite authority
  cutover, with explicit CSV diagnostic/recovery use and fail-closed production

## Current State

The current repository state is `HEAD` ahead of the `v0.9.0` tag, with Storage
Phase 5 present only as uncommitted working-tree changes. There is no newer
release tag yet.

The product is a visualization-first local network observability workspace.
Python owns deterministic health, attribution, baseline, incident, lifecycle,
interval, similarity, and learning semantics. The browser is renderer-only.
The heatmap and latency line charts are core product functionality and must be
preserved.

Current watch period:

- verify learned-normal resolver intervals remain semantically identical across
  dashboard, interval summary, and current investigation projections
- observe whether noticeability misses stable-but-noticeable problems
- observe whether turbulence or pattern confidence create misleading signals
- observe whether DNS Security, Internet Conditions, and APS Power context add
  useful context or clutter
- observe whether the current dashboard scanning and investigation workflow
  improve operator understanding
- observe whether the recency-aware active baseline and post-recovery
  stabilization behave as expected over time
- observe whether the committed post-`v0.9.0` investigation and baseline work
  holds up before new functionality is added

## Product Design Principles

These principles are settled repository direction and should guide new work.

1. Visualization first. Narrative second. Details last.

2. A visualization must reduce cognitive load, not decorate the UI.

3. Prefer:
   - heatmaps
   - timelines
   - line charts
   - sparklines
   - event lanes
   - compact bars
   - interval bands
   - baseline/deviation overlays
   - annotations

4. Avoid decorative visualization. Pie charts are not part of the Prime
   Observer visual vocabulary.

5. AI should interpret visualized/deterministic evidence, not replace the
   visualization.

6. New features should first ask: "Can this be communicated visually?"

7. Do not add dashboard cards simply because a new artifact or capability
   exists.

8. The main dashboard should remain quiet, concise, and visualization-forward.

9. Any renderer, initialization, or time-context change affecting
   `viz/index.html` requires browser smoke validation of:
   - the heatmap
   - all primary line charts
   - tooltips and interactions
   - selected-time behavior
   - no fatal console errors

## Future Roadmap

The following are future directions, not implemented behavior.

### Priority 1 — Main UI Visual Refinement

The current dashboard hierarchy is liked and should not be redesigned wholesale.

Goal: reduce text density in the upper cards using subtle deterministic
micro-visualizations where they genuinely replace prose.

Potential directions:

- DNS & Web Health: compact resolver/application status or latency indicators
- Historical Patterns: small recurrence/time visualization
- Internet Conditions: compact event timeline/strip
- Power Infrastructure: compact event timeline/strip
- DNS Activity: compact proportional bars for blocked/encrypted activity

`Current Summary` and `Likely issue` should remain primarily concise
narrative/interpretation.

Do not compete visually with the existing heatmap and primary line charts.

### Priority 2 — Analyze Incident / Investigation Overhaul

The current Investigation/Evidence experience is considered ineffective and
should eventually be redesigned from first principles.

Preserve the underlying incident intelligence and artifacts, but do not assume
the current UI should survive.

Concept direction: build an Incident Explorer / Incident Workbench centered on
time.

Operator interaction model:

- See
- Zoom
- Ask why
- Inspect evidence
- Compare

Potential experience:

- annotated incident timeline
- before / during / after regions
- zoomable/drillable time window
- 15-minute interval investigation
- event lanes above/below the timeline
- resolver, gateway, application, Cloudflare, APS, operator-feedback markers
- baseline vs observed behavior
- visual markers explaining why an interval qualified as an incident
- supporting evidence appears contextually for the selected interval
- AI explains why the deterministic system classified the selected slice the
  way it did
- ability eventually to compare visually similar incidents

Inspiration:

- financial/stock-chart time exploration
- forecasting history/current/future visual grammar, without claiming future
  prediction unless supported
- visual/faceted search where characteristics of the data drive exploration

Longer-term possibility: a deterministic visualization grammar where incident
characteristics determine which approved visual representations receive
emphasis.

Python continues to own semantic facts. The visualization layer presents those
facts. The LLM explains them.

### Priority 3 — Deployment/Productization (Someday)

Prime Observer is technically portable but not yet a polished self-service
deployment.

Future possibilities:

- fresh-machine deployment audit
- guided configuration
- bootstrap/setup script
- dependency checks
- scheduler installation
- secrets/provider configuration
- clean initialization without another installation's learned runtime data
- INSTALL documentation
- logo / visual identity / favicon

Do not make productization a near-term priority.

## Deferred Or Explicitly Avoided Areas

The repository explicitly says not to expand into these areas yet:

- raw DNS logs
- domain lists as a product expansion
- device-level DNS analytics
- alerts or notifications
- unbounded or browser-side LLM explanations
- weather correlation
- ISP status correlation
- major `viz/index.html` refactor (beyond approved micro-visualization work)
- database-backed storage replacing canonical artifacts
- event comparison or similarity detection in the browser (Python-owned
  `viz/incident_similarity.json` is the committed deterministic mechanism)

If a future database becomes useful, it should be an optional search/index
projection that consumes canonical JSON/CSV artifacts. It should not replace the
artifact evidence model.
