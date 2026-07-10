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

Downstream consumers should:

- use only supplied evidence
- preserve current versus window attribution scope
- treat unavailable context as unavailable
- avoid causal claims beyond the evidence
- avoid overriding Prime Observer's deterministic output
- keep interpretation concise and factual

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

## Manual Prompt Template

Use this template for manual OpenCode or OpenRouter experiments only after
generating `viz/operator_assistant_input.json`:

```text
You are reviewing a Prime Observer operator-assistant evidence package.

Return JSON only. Do not include markdown fences or extra narration.

Use only the supplied evidence package.
Do not invent facts.
Do not claim causality beyond the evidence.
Distinguish deterministic Prime Observer output from hypothesis.
Treat unavailable evidence as unavailable.
Preserve conflicting current and window attribution scopes when they differ.
Do not override Prime Observer.
Keep the answer concise.
Recommend only practical follow-up checks supported by the package.

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
