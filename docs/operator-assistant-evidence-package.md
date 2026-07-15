# Operator Assistant Evidence Package

This document describes the Phase 1 prototype for a compact deterministic
evidence package derived from `viz/investigation.json`.

Use the repository as the source of truth. If a claim cannot be supported by
repository evidence, mark it:

`Needs Matthew Review`

## Purpose

The operator-assistant evidence package exists to prove that Prime Observer can
curate the right evidence for later LLM interpretation without sending the full
investigation artifact.

Prime Observer remains authoritative for:

- telemetry evidence
- deterministic Observation output
- generated provider context
- investigation generation

The future LLM is a downstream consumer of curated evidence. It is not a source
of telemetry, attribution truth, observations, or causal conclusions.

## Current Prototype

Phase 1 adds:

- producer: `bin/build_operator_assistant_input.py`
- source artifact: `viz/investigation.json`
- generated output: `viz/operator_assistant_input.json`

The output is local-only and generated. It must not be committed.

## Local Review Surface

The current local review prototype also includes:

- communication contract: `docs/operator-charter.md`
- review producer: `bin/build_operator_assistant_output.py`
- review artifact: `viz/operator_assistant_output.json`
- review surface: `viz/investigate.html`

The browser remains renderer-only. It reads the generated input and review
artifacts and does not call OpenRouter directly. The deterministic input
producer computes the normalized evidence-package hash and stores it as
`input_hash` in `viz/operator_assistant_input.json`. The review producer copies
that hash into `viz/operator_assistant_output.json`. The hash protects browser
artifact freshness: the browser presents a review as current only when input and
output hashes match. It is not currently used to reuse provider responses.

For this phase, run `python3 bin/build_operator_assistant_output.py` explicitly
to generate a review. Every valid execution requests a fresh OpenRouter review
using the current Operator Charter and prompt, replacing any prior output.
Successful-output reuse is temporarily disabled during prompt and charter
refinement. The scheduled optional-context refresh rebuilds the input package
but does not invoke OpenRouter.

Safe reuse may later be restored with a request fingerprint that includes the
evidence input hash, requested model, Operator Charter content or version,
prompt template or version, and response schema version. That fingerprint is
not implemented in this phase.

The review prompt is composed from three reusable parts:

```text
Operator Charter + Evidence Package + Response Schema = Prompt
```

Prime Observer determines evidence. The Operator Charter defines how a model
communicates its interpretation, including evidence-first wording, explicit
uncertainty, and useful follow-up observations. The selected model may change;
operator behavior should remain consistent with the charter.

The review artifact includes:

- `schema_version`
- `generated_at`
- `status`
- `input_hash`
- requested model (`google/gemini-3.5-flash` by default, configurable locally)
- concrete provider model returned by OpenRouter, when available
- structured assessment fields
- unavailable or failure reason, when applicable
- provider usage metadata, when available

`viz/investigate.html` treats both assistant artifacts as optional and the
review as non-authoritative. It only renders the assessment as current when the
input and output artifact `input_hash` values match. It does not reconstruct or
hash the evidence package in JavaScript.

## Included Evidence

The prototype keeps only bounded fields already present in
`viz/investigation.json`:

- requested investigation window metadata
- current attribution observation
- window attribution observation
- one overlapping episode observation when present
- bounded during-window WAN and LAN evidence summaries
- compact optional DNS, Internet Conditions, and Power context summaries
- limitations and provenance for downstream grounding

## Intentionally Excluded

The prototype intentionally excludes:

- full `timeline_samples`
- full `events`
- full `event_neighborhoods`
- full bucket-row copies
- raw source-file listings
- threshold dictionaries
- renderer-only investigation fields
- secrets or local configuration

The package is a compact derivative, not a second full investigation export.

## Grounding Requirements

Downstream model communication is governed by the canonical
`docs/operator-charter.md`. This evidence-package document defines what is
supplied; the charter defines how it is interpreted and communicated.

## Failure Behavior

If `viz/investigation.json` is missing, unreadable, or invalid, the producer
still writes a minimal package with:

- empty evidence
- unavailable status for the investigation source
- explicit limitations describing the failure

This keeps the downstream contract deterministic and bounded.

## Why The LLM Is Not Authoritative

Prime Observer owns local deterministic evidence generation. The LLM will only
consume the compact package for interpretation experiments.

That boundary matters because:

- attribution remains Prime Observer output
- episode detection remains Prime Observer output
- provider context remains supporting evidence only
- the model must not invent new facts or replace the source investigation

## Manual Prompt Composition

For manual OpenCode or OpenRouter experiments, compose the complete contents of
`docs/operator-charter.md`, the generated `viz/operator_assistant_input.json`,
and the unchanged response shape below. Do not copy communication rules into a
model-specific prompt.

```text
Return JSON only. Do not include markdown fences or extra narration.

Required response shape:
{
  "assessment": "string",
  "confidence": "low | medium | high",
  "evidence": [
    "string"
  ],
  "limitations": [
    "string"
  ],
  "next_steps": [
    {
      "id": "string",
      "label": "string",
      "reason": "string"
    }
  ]
}

Suggested next-step IDs when appropriate:
- EXTEND_WINDOW
- CHECK_GATEWAY
- COMPARE_RESOLVER_AND_INTERNET
- RECHECK_PROVIDER_CONTEXT

Evidence package:
{{operator_assistant_input_json}}
```
