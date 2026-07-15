# AGENTS.md

## Purpose

This file is the working contract for coding agents operating in this
repository.

Prime Observer is a local-first network experience observability system. It
uses flat CSV/JSON artifacts, deterministic heuristics, and a static dashboard
to answer whether network behavior is healthy, unusual, attributable, sustained,
and likely noticeable to users.

Current release: `v0.9.0`

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
- Preserve the separation between Evidence, Observation, Investigation, and
  Projection described in `README.md`.
- Do not move Core Signal interpretation, recommendations, correlations, event
  confidence scoring, or higher-level meaning into Prime Observer.

## Primary Files

- `bin/transform_latest.py` generates `viz/latest.csv`,
  `viz/network_attribution.json`, `viz/observations.json`, and
  `viz/dashboard_health.json`.
- `bin/build_investigation.py` generates `viz/investigation.json` and
  `viz/investigation_index.json`.
- `bin/build_operator_assistant_input.py` generates
  `viz/operator_assistant_input.json`.
- `bin/build_operator_assistant_output.py` generates
  `viz/operator_assistant_output.json`.
- `bin/fetch_nextdns_summary.py` generates `viz/nextdns_summary.json`.
- `bin/fetch_cloudflare_radar.py` generates `viz/internet_conditions.json`.
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
- commit local secrets or generated runtime artifacts
- expand Prime Observer into DNS analytics, alerting, or interpretive AI
  behavior unless the repository direction changes explicitly

## Generated And Local Files

These are local/generated artifacts and must not be committed:

- `viz/latest.csv`
- `viz/network_attribution.json`
- `viz/observations.json`
- `viz/dashboard_health.json`
- `viz/investigation.json`
- `viz/investigation_index.json`
- `viz/operator_assistant_input.json`
- `viz/operator_assistant_output.json`
- `viz/nextdns_summary.json`
- `viz/internet_conditions.json`
- `.env.nextdns`
- `.env.cloudflare`
- `.env.openrouter`

## Validation

For documentation-only changes:

- verify the updated docs stay consistent with repository files and git history
- run `git diff --check`

See `docs/validation.md` for the canonical validation guide.

For code changes, also run validation appropriate to the affected area. Use the
existing tests and scripts in the repository as the guide.

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
