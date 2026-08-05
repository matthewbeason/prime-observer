# HANDOFF.md

## Current State

- Branch: `main`
- Current release: `v0.9.0`
- Latest tag: `v0.9.0`
- `HEAD` is ahead of the `v0.9.0` release tag and includes later lifecycle work

Prime Observer currently ships:

- deterministic health modeling over local telemetry
- observation-backed attribution and episode semantics
- automatic current-event investigation generation
- immutable completed-event investigation history
- operator-first Investigation rendering with deterministic fallback assessment
- OpenRouter-backed Operator Assistant interpretation as the primary
  operator-facing narrative when output is valid for the evidence package
- Python-owned multidimensional health evaluation with additive artifact fields
  rendered in the dashboard and Investigation UI
- impact-v2 fields that preserve legacy `user_impact` while separating
  estimated user impact from observed reports
- asynchronous pending-work consumption through a separate local worker and
  tracked 60-second LaunchAgent
- manual requested-window investigation generation and viewing
- optional NextDNS summary context
- optional Cloudflare Radar Internet Conditions context
- optional local Application Experience probes that feed estimated impact only
- local operator impact feedback that feeds observed impact only

## Recent Completed Work

Repository-backed recent milestones:

- `v0.8.0`: Observation domain foundation
- `v0.8.1`: Bucket selection alignment
- `v0.8.2`: Dashboard operator polish
- `v0.9.0`: Internet Conditions external context
- post-`v0.9.0` uncommitted work: event-aligned automatic investigation
  lifecycle, hardened completed-event history, and operator-first Investigation
  redesign with asynchronous Operator Assistant generation and last-known-good
  publication behavior
- post-`v0.9.0` health-dimensions work: Phase 1 calibration document, Phase 2
  deterministic evaluator with additive artifact output, and Phase 3 browser
  rendering for emitted multidimensional fields
- post-`v0.9.0` Investigation IA work: Phase 4.2 redesigns
  `viz/investigate.html` as an Incident Record over existing artifacts, keeping
  evaluator semantics and schemas unchanged
- post-`v0.9.0` Phase 4.3 work: Operator Assistant model selection is explicitly
  pinned to `google/gemini-3.5-flash` by default, provider auto-routing is
  rejected, and additive impact-v2 fields distinguish estimated and observed
  user impact
- post-`v0.9.0` Phase 4.4 work adds a separate local application-experience
  collector and `viz/application_experience.json`; transform reads the artifact
  without network calls and uses fresh evidence only for estimated user impact
- post-`v0.9.0` Phase 4.5 work adds `bin/record_operator_impact.py`, local
  `viz/operator_impact_feedback.json`, split estimated/observed impact
  presentation, and dashboard/Investigation rendering for Application Experience
  evidence
- post-`v0.9.0` Incident Intelligence Phase 1 makes Investigation entry points
  explicit (`view=current`, `view=interval`, `view=incident`) and adds an
  additive Python-owned `incident_record` story to newly generated automatic
  investigations
- post-`v0.9.0` Incident Intelligence Phase 2 adds additive Python-owned
  `incident_phases` for Before, During, and optional Recovery story sections;
  the browser renders these fields without inferring phase semantics
- post-`v0.9.0` Incident Intelligence Phase 3 adds additive Python-owned
  `incident_replay` milestones so Investigation can render deterministic replay
  without browser-side inference or historical similarity
- post-`v0.9.0` Adaptive Baseline Phase C adds generated durable baseline memory
  in `viz/baseline_history.json`; transform learns compact per-target summaries
  across recent telemetry files and health dimensions prefer valid durable
  resolver baselines before falling back to in-window evidence

Recent commits before `v0.9.0` show this sequence:

- added Internet Conditions context
- refreshed it in the scheduled workflow
- enriched the Internet Conditions artifact and dashboard presentation
- prepared the `v0.9.0` release

Next conceptual milestone:

- define Environmental Context architecture before evaluating additional
  providers

## Current Architecture State

Current artifact flow:

- telemetry history in `data/bakeoff_YYYYMMDD.csv`
- `bin/transform_latest.py` generates dashboard, observation, mutable current
  investigation, write-once completed snapshot, and investigation catalog
  artifacts
- `bin/build_investigation.py` generates manual requested-window evidence
  artifacts
- optional fetchers generate DNS and Internet Conditions summaries
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
- `viz/index.html` and `viz/investigate.html` consume generated local files

Current projection state:

- `viz/latest.csv` remains the dashboard telemetry input
- `viz/network_attribution.json` remains the backward-compatible attribution
  export
- `viz/observations.json` is the repository-described authoritative Observation
  projection for deterministic semantics Prime Observer owns
- `viz/baseline_history.json` is compact generated durable baseline memory keyed
  by phase, target class, and host/member identity
- `viz/interval_summary.json` is the generated deterministic summary for one
  selected interval, rendered only when route start/end match the artifact
- `viz/incident_similarity.json` is the generated current-incident similarity
  projection over completed snapshots, with Python-owned scores and explanations
- `viz/operational_learnings.json` is the generated operational learning artifact
  over repeated completed incidents and durable baselines, with Python-owned
  confidence and retirement handling
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

The repository currently says to live with `v0.9.0` for several days before
expanding functionality.

The current uncommitted work implements Incident Intelligence Phase E. Direct
links/bookmarks for current, selected-interval, and historical investigation
entry points remain explicit: `?view=current`,
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

Generated JSON and CSV artifacts remain canonical. No database is needed at the
current local scale; any future PostgreSQL or Supabase work should be an optional
artifact consumer/index rather than a replacement for canonical artifacts.

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

## Resume Checklist

When resuming work:

1. Confirm the current tag, branch, and working tree state.
2. Read `README.md`, `AGENTS.md`, `ROADMAP.md`, and `DECISIONS.md`.
3. Read the specific docs and code for the area being changed.
4. Preserve the repository’s architecture boundaries and terminology.
5. Mark any unsupported assumption as `Needs Matthew Review`.

## Needs Matthew Review

- What milestone should follow the current `v0.9.0` observation period.
- Whether the repository wants a standing handoff file updated each release or
  only when work pauses midstream.
