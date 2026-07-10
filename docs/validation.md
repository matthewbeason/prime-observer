# Validation

This document is Prime Observer's canonical validation reference.

Use the repository as the source of truth. If a validation expectation cannot
be supported directly from repository files, scripts, tests, or documented
workflow, mark it:

`Needs Matthew Review`

## Purpose

Prime Observer validates changes to preserve the repository's existing working
contract:

- deterministic output from local scripts and explicit heuristics
- reproducible local artifact generation and static rendering
- additive evolution rather than casual contract replacement
- graceful degradation for optional providers
- provider independence from Observation, Attribution, Health, and
  Noticeability logic
- documentation accuracy against the repository's actual behavior
- architectural boundaries between Evidence, Observation, Investigation, and
  Projection

Validation should match the change being made. Prime Observer does not require
the same proof for a Markdown wording change that it requires for a generator,
artifact contract, or dashboard behavior change.

## Validation By Change Type

### Documentation-only changes

Examples:

- `README.md`
- `AGENTS.md`
- `HANDOFF.md`
- `ROADMAP.md`
- `DECISIONS.md`
- `docs/*.md`

Expected validation:

- confirm the updated text matches repository behavior, terminology, and
  documented boundaries
- confirm any commands, artifact names, and file paths are real
- run:

```bash
git diff --check
```

This is usually sufficient because documentation-only changes do not modify
runtime behavior.

### Python changes

Examples:

- `bin/transform_latest.py`
- `bin/build_investigation.py`
- `bin/fetch_nextdns_summary.py`
- `bin/fetch_cloudflare_radar.py`
- `bin/fetch_aps_power_context.py`
- `bin/observation_domain/*.py`

Expected validation:

- run the full unit test suite
- confirm Python files still compile
- run `git diff --check`
- run the affected script or scripts
- confirm generated artifacts or outputs match the intended scope
- confirm generated local artifacts remain uncommitted

Canonical commands:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q bin tests
git diff --check
```

Add script-specific validation when relevant:

```bash
python3 bin/transform_latest.py
python3 bin/build_investigation.py --start 2026-05-30T17:30:00-07:00 --end 2026-05-30T18:00:00-07:00
python3 bin/fetch_nextdns_summary.py
python3 bin/fetch_cloudflare_radar.py
python3 bin/fetch_aps_power_context.py
```

### Dashboard changes

Examples:

- `viz/index.html`
- `viz/investigate.html`
- dashboard CSS inside the HTML files
- renderer JavaScript inside the HTML files

Expected validation:

- run the full unit test suite
- run `python3 -m compileall -q bin tests`
- run `git diff --check`
- regenerate the dashboard telemetry window when dashboard logic depends on
  generated artifacts
- serve `viz/` over local HTTP
- verify the affected page in a browser through local HTTP, not `file://`
- confirm optional-context unavailable behavior still renders safely when that
  area is affected

Typical command set:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q bin tests
python3 bin/transform_latest.py
python3 -m http.server 8000 --directory viz
git diff --check
```

Typical pages:

- `http://localhost:8000`
- `http://localhost:8000/investigate.html`

### Artifact changes

Examples:

- schema additions
- optional fields
- new generated artifact relationships
- new provider artifact fields

Expected validation:

- validate the producing tests
- validate the consuming tests
- run the relevant producer locally
- confirm the artifact remains additive where the repository preserves existing
  contracts
- confirm unavailable behavior remains explicit for optional artifacts
- confirm browser consumers still read generated artifacts rather than secrets
  or live provider APIs

Typical validation scope:

- `viz/latest.csv`: rerun `bin/transform_latest.py`
- `viz/network_attribution.json`: rerun `bin/transform_latest.py`
- `viz/observations.json`: rerun `bin/transform_latest.py`
- `viz/investigation.json` and `viz/investigation_index.json`: rerun
  `bin/build_investigation.py`
- `viz/nextdns_summary.json`: rerun `bin/fetch_nextdns_summary.py`
- `viz/internet_conditions.json`: rerun `bin/fetch_cloudflare_radar.py`
- `viz/aps_power_context.json`: rerun `bin/fetch_aps_power_context.py`

## Environmental Context Provider Validation

The repository's current Environmental Context examples are:

- Internet Conditions via `bin/fetch_cloudflare_radar.py` ->
  `viz/internet_conditions.json`
- APS Power Infrastructure via `bin/fetch_aps_power_context.py` ->
  `viz/aps_power_context.json`

Environmental Context validation should prove these provider rules still hold:

- successful provider fetch writes a bounded local JSON artifact
- unavailable provider state writes an explicit `unavailable` artifact
- malformed provider data degrades safely instead of breaking the dashboard
- explicit configured-scope logic remains honest about the requested target and
  any fallback used
- dashboard rendering remains artifact-driven and renderer-only
- investigation integration remains additive supporting evidence
- provider evidence stays separate from Observation, Attribution, Health, and
  Noticeability logic
- one provider's presence or failure does not become a dependency for another
  provider or for core local telemetry rendering

Expected validation evidence:

- unit tests for the provider fetcher and affected consumers
- local producer run for the affected provider
- manual dashboard verification for the affected provider card or section
- manual investigation verification when provider context is copied into
  `viz/investigation.json`

For optional provider scheduling changes, also validate the existing wrapper:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q bin tests
git diff --check
bin/refresh_optional_context.sh
```

The wrapper should refresh NextDNS, Cloudflare Radar, and APS Power
Infrastructure in sequence while keeping each provider non-fatal.

## Standard Commands

These are the canonical validation commands already used by the repository's
tests, scripts, and documentation:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q bin tests
git diff --check
python3 bin/transform_latest.py
python3 bin/build_investigation.py --start 2026-05-30T17:30:00-07:00 --end 2026-05-30T18:00:00-07:00
python3 bin/fetch_nextdns_summary.py
python3 bin/fetch_cloudflare_radar.py
python3 bin/fetch_aps_power_context.py
python3 -m http.server 8000 --directory viz
bin/refresh_optional_context.sh
```

Use the smallest set that proves the actual change. Do not run provider fetches
or investigation generation for a documentation-only update unless the change
itself depends on proving those workflows.

## Manual Verification

Manual verification should stay focused on the changed surface:

- dashboard rendering: serve `viz/` locally and verify `http://localhost:8000`
- investigation rendering: generate `viz/investigation.json` and verify
  `http://localhost:8000/investigate.html`
- optional-provider unavailable behavior: confirm the dashboard still renders
  when the provider writes an `unavailable` artifact
- artifact generation: confirm the expected generated file was refreshed by the
  intended producer
- clean output hygiene: confirm generated local artifacts and local `.env.*`
  files are not committed
- working tree review: confirm the final diff matches the intended scope

When verifying investigation rendering, open the page through local HTTP, not
`file://`.

## Release Checklist

Use this concise checklist before release-oriented commits or tags:

- validation passed for the actual change scope
- repository docs are updated if the behavior or contract changed
- tests are updated when behavior or contracts changed
- generated local artifacts are not committed
- local `.env.nextdns` and `.env.cloudflare` are not committed
- working tree matches the intended release scope

Whether Prime Observer wants a stricter standing release checklist beyond these
repository-backed items is:

`Needs Matthew Review`

## Guidance For AI Agents

- Start with the smallest validation scope that honestly proves the change.
- For documentation-only changes, repo-consistency review plus
  `git diff --check` is normally enough.
- For Python, dashboard, artifact, or provider changes, full validation is the
  default because those changes can alter generated outputs or renderer
  behavior.
- Choose validation by ownership boundary:
  `bin/` changes need tests and script runs; `viz/` changes need tests and
  local HTTP verification; artifact-contract changes need producer and consumer
  proof.
- Do not over-validate docs-only edits just to simulate rigor. Extra script
  runs are unnecessary when no runtime surface changed.
- Do not under-validate additive artifact or provider changes. Prime Observer
  relies on deterministic projections, safe optional-provider degradation, and
  preserved boundaries.
- If the repository does not clearly establish a validation expectation, mark
  it `Needs Matthew Review` instead of inventing one.
