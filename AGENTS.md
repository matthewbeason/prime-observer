# AGENTS.md

## Purpose

This file is the working contract for coding agents operating in this
repository.

Prime Observer is a local-first network experience observability system. It
uses flat CSV/JSON artifacts, deterministic heuristics, learned baselines, an
LLM interpretation layer, and a static dashboard to answer whether network
behavior is healthy, unusual, attributable, sustained, and likely noticeable to
users.

Current release: `v0.9.0` (latest tag). `HEAD` is ahead of the tag with
committed lifecycle, adaptive-baseline, interval, similarity, operational
learning, temporal workspace, and dashboard presentation work described in
`HANDOFF.md` and `ROADMAP.md`.

See:

- `README.md` for product overview, architecture, setup, and release notes
- `HANDOFF.md` for current repository state
- `ROADMAP.md` for milestone progression and current watch period
- `DECISIONS.md` for settled architectural decisions
- `docs/artifact-architecture.md` for the authoritative artifact reference
- `docs/validation.md` for the authoritative validation reference
- `docs/health-model.md` for canonical health-model semantics
- `docs/investigation-workflow.md` for historical evidence workflow
- `docs/environmental-context.md` for Environmental Context boundaries and
  provider guidance

## Source Of Truth

Use the repository as the source of truth:

- `README.md`
- `docs/`
- source code under `bin/` and `viz/`
- tests under `tests/`
- project structure
- git history, tags, and releases

If something cannot be supported from the repository, mark it:

`Needs Matthew Review`

## Architecture To Preserve

- Keep Prime Observer local-first.
- Keep CSV/JSON artifact handoffs lightweight and explicit.
- Keep logic deterministic and transparent.
- Keep optional integrations fail-safe.
- Keep Prime Observer factual and bounded.
- Preserve the separation between Evidence, Observation, Investigation,
  Interpretation, and Projection described in `README.md`.
- Preserve the visualization-first product posture: operators should see first,
  understand second, and investigate only when necessary. The heatmap and
  latency line charts are core product functionality and must be preserved.
- Preserve the learned-baseline model: normal is learned from observed behavior
  over the most recent eligible telemetry, static thresholds remain safety
  guardrails only, and loss, timeout, DNS/HTTPS failure, gateway problems,
  correlated degradation, rapid worsening, severe excursions, and confirmed or
  major impact must not be normalized away. Raw observations and history remain
  factual even when adaptive semantics suppress a bad moment.
- Deterministic Python remains authoritative for facts, evidence, thresholds,
  event boundaries, lifecycle, affected scope, classifications, baselines,
  freshness, semantic hashing, safety constraints, and fallback guidance.
- The browser is renderer-only. It renders generated artifacts and maps emitted
  fields to presentation; it must not own health, attribution, baseline,
  incident, or next-action semantics.
- OpenRouter-backed Operator Assistant output is the primary operator-facing
  interpretation when it is valid for the current evidence package. It may
  synthesize likely meaning, uncertainty, and safe next actions, but it must not
  invent facts or contradict deterministic evidence.
- Do not move network interpretation, OpenRouter calls, or next-action
  generation into browser JavaScript.

## Primary Files

- `bin/transform_latest.py` generates `viz/latest.csv`,
  `viz/network_attribution.json`, `viz/observations.json`,
  `viz/dashboard_health.json`, `viz/baseline_history.json`,
  `viz/interval_summary.json`, `viz/incident_similarity.json`,
  `viz/operational_learnings.json`, `viz/time_context.json`, the current
  `viz/investigation.json`, immutable completed-event snapshots under
  `viz/investigations/`, and `viz/investigation_catalog.json`. Completed
  snapshots are atomically published, write-once runtime artifacts; malformed
  existing snapshots are preserved and reported through the generated catalog
  instead of overwritten.
- `bin/build_investigation.py` generates `viz/investigation.json` and
  `viz/investigation_index.json`.
- `bin/build_operator_assistant_input.py` generates
  `viz/operator_assistant_input.json` and marks changed semantic input pending.
- `bin/run_operator_assistant_worker.py` consumes pending or due retry state in a
  separate process and delegates provider work to the output producer.
- `bin/build_operator_assistant_output.py` owns OpenRouter requests, validation,
  atomic `viz/operator_assistant_output.json` publication, and last-known-good
  preservation.
- `viz/operator_assistant_generation_state.json` records asynchronous worker
  state separately from valid output.
- `bin/fetch_nextdns_summary.py` generates `viz/nextdns_summary.json`.
- `bin/fetch_cloudflare_radar.py` generates `viz/internet_conditions.json`.
- `bin/fetch_aps_power_context.py` generates `viz/aps_power_context.json`.
- `bin/fetch_application_experience.py` generates
  `viz/application_experience.json`.
- `bin/record_operator_impact.py` generates `viz/operator_impact_feedback.json`.
- `viz/index.html` renders the dashboard from generated local artifacts.
- `viz/investigate.html` renders historical investigation evidence.

## Working Rules

Before changing code or docs:

1. Read the affected files.
2. Confirm current behavior from repository evidence.
3. Keep changes scoped and additive unless a broader change is explicitly
   requested.
4. Preserve existing terminology and architecture boundaries.

Do not:

- invent roadmap items, history, or intent not supported by the repository
- add browser-side secrets
- fetch NextDNS or Cloudflare directly from browser code
- call OpenRouter directly from browser code or page load
- overwrite valid Operator Assistant output with a provider/configuration
  failure
- commit local secrets or generated runtime artifacts
- expand Prime Observer into DNS analytics, alerting, or interpretive AI
  behavior unless the repository direction changes explicitly
- add a database unless a concrete scaling, query, collaboration, or multi-user
  requirement justifies an optional projection over canonical artifacts

## Generated And Local Files

These are local/generated artifacts and must not be committed:

- `viz/latest.csv`
- `viz/network_attribution.json`
- `viz/observations.json`
- `viz/dashboard_health.json`
- `viz/baseline_history.json`
- `viz/interval_summary.json`
- `viz/incident_similarity.json`
- `viz/operational_learnings.json`
- `viz/time_context.json`
- `viz/investigation.json`
- `viz/investigation_index.json`
- `viz/investigation_catalog.json`
- `viz/investigations/`
- `viz/operator_assistant_input.json`
- `viz/operator_assistant_output.json`
- `viz/operator_assistant_generation_state.json`
- `viz/.operator_assistant_generation.lock`
- `viz/nextdns_summary.json`
- `viz/internet_conditions.json`
- `viz/aps_power_context.json`
- `viz/application_experience.json`
- `viz/operator_impact_feedback.json`
- `.env.nextdns`
- `.env.cloudflare`
- `.env.openrouter`
- `.env.application_experience`

## Validation

For documentation-only changes:

- verify the updated docs stay consistent with repository files and git history
- run `git diff --check`

See `docs/validation.md` for the canonical validation guide.

For code changes, also run validation appropriate to the affected area. Use the
existing tests and scripts in the repository as the guide.

The project currently has approximately 466 tests. Test count is not a goal;
focus test maintenance on unique/high-value coverage, removal of duplicate or
obsolete tests, integration coverage, and a small set of critical browser smoke
checks. Do not add tests merely to grow the count, and do not delete tests
without confirming the behavior is covered elsewhere.

## Dashboard Scope

The dashboard should continue answering these six questions described in the
repository:

1. Is the network healthy?
2. Is behavior unusual?
3. Is the issue local or upstream?
4. Is the issue sustained?
5. Would users notice?
6. Is there useful DNS/security context?

Do not add dashboard semantics or components casually. Preserve the restrained,
observability-focused product posture described in the repository.

Any renderer, initialization, or time-context change affecting `viz/index.html`
requires browser smoke validation through local HTTP covering:

- the heatmap
- all primary line charts
- tooltips and interactions
- selected-time behavior
- no fatal console errors
