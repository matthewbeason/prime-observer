# Operator Assistant Evidence Package

This document describes Prime Observer's deterministic evidence package and
OpenRouter-backed Operator Assistant interpretation workflow.

Prime Observer uses deterministic evidence generation and an LLM interpretation
layer. The evidence remains authoritative; the LLM provides the primary
operator-facing diagnosis and recommended actions. A deterministic
interpretation remains available when a safe current LLM result is unavailable.

## Purpose

The evidence package lets Prime Observer send a bounded, structured set of facts
to OpenRouter without dumping the full investigation artifact.

Prime Observer remains authoritative for:

- telemetry evidence
- deterministic Observation output
- event boundaries and lifecycle
- affected and unaffected scope
- thresholds, classifications, and confidence inputs
- semantic hashing and freshness
- claim boundaries, prohibited claims, and deterministic fallback guidance

The LLM may synthesize likely meaning, uncertainty, and safe next actions. It is
not a source of telemetry, attribution truth, lifecycle state, scope, or causal
proof.

## Artifacts

- `viz/operator_assistant_input.json`: compact deterministic evidence package
- `viz/operator_assistant_output.json`: last valid OpenRouter interpretation for
  a matching input hash
- `viz/operator_assistant_generation_state.json`: pending/current/failed
  generation provenance

The browser remains renderer-only. It reads local artifacts and never calls
OpenRouter.

## Included Evidence

The input package includes bounded fields derived from `viz/investigation.json`:

- event ID, lifecycle, start, end, last-seen, and recovery timestamps
- selected target class and affected endpoints
- unaffected comparison groups
- thresholds and sample counts
- raw-bad and sustained-bad counts
- affected, stable, turbulence, and isolated-excursion bucket counts
- representative latency and maximum excursions
- packet loss, baseline, degradation, and recovery phase summaries
- attribution result and confidence
- optional DNS, Internet Conditions, and Power context summaries
- supporting, limiting, contradictory, and missing evidence
- claim boundaries and prohibited claims
- safe diagnostic categories
- provenance and source semantic hash

It intentionally excludes full raw samples, full event-neighborhood rows, raw
provider payloads, source-file dumps, secrets, and browser-only state.

## Output Contract

The OpenRouter response is published only when it validates structurally and its
`input_hash` matches the current evidence package.

Required interpretation fields include:

- `headline`
- `assessment`
- `what_is_happening`
- `affected_scope`
- `healthy_scope`
- `likely_fault_domain`
- `confidence`
- `uncertainty`
- `evidence`
- `limitations`
- `next_steps[]` with action label, reason, expected observation, and assessment change
- `evidence_that_would_change_assessment`
- `monitoring_guidance`

## Reuse Policy

Safe to reuse:

- identical input hash and requested model
- freshness-only telemetry advance that does not change semantic evidence
- recovery elapsed-time advance while event identity, lifecycle consequence, and
  conclusion remain unchanged
- metadata-only updates

Unsafe to reuse:

- selected event changes
- target class changes
- affected endpoints change materially
- lifecycle changes in a way that alters operator action
- severity or confidence changes materially
- new contradictory evidence appears
- fault-domain attribution changes
- current/stale interpretive state changes materially

`bin/build_operator_assistant_output.py` reuses a matching valid output by
default. Use `--force` to request a fresh interpretation for the same input hash.

## Automatic Generation

`bin/transform_latest.py` and the standalone input producer atomically mark a
changed semantic input hash `pending`. The separate
`bin/run_operator_assistant_worker.py` consumes pending or due retry state and
reuses the output producer for OpenRouter, validation, and atomic publication.
The tracked LaunchAgent checks every 60 seconds without `KeepAlive`; it does not
embed secrets and does not affect collector or transform exit status.

The worker performs at most three generation runs per semantic hash, waiting 5
minutes after the first failed run and 15 minutes after the second. HTTP
429/500/502/503/504, transport failure, and invalid or truncated provider output
are retryable while attempts remain. Configuration, input, non-retryable HTTP,
and output-contract failures become terminal for that hash. A new semantic hash
resets pending work; a freshness-only rebuild does not.

An exclusive generated lock suppresses duplicate provider requests. The lock may
be replaced after 900 seconds when stale. See
`docs/operator-assistant-worker.md` for state fields, installation, inspection,
and disable commands.

## Failure Behavior

Provider, configuration, and validation failures are recorded in
`viz/operator_assistant_generation_state.json`. They do not overwrite a valid
prior `viz/operator_assistant_output.json`.

If no safe current LLM output exists, `viz/investigate.html` renders the
deterministic `operator_brief` fallback from `viz/investigation.json`. The page
does not present provider failure as the main operator experience.

## Claim Boundaries

Observed facts must be directly supported by deterministic evidence. Inferences
must be phrased as likely interpretations. Unknowns must remain explicit when
evidence is insufficient.

The assistant must not claim definite ISP, DNS provider, local network, routing,
or power fault unless the supplied evidence actually supports that certainty. It
must not invent measurements, devices, services, users, unavailable tests, or
external outages.
