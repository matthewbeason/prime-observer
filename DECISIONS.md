# DECISIONS.md

## Purpose

This file records architectural and product-boundary decisions that are already
settled in the repository.

If a decision cannot be supported directly from repository evidence, it should
not be added here without marking it:

`Needs Matthew Review`

## Settled Decisions

### Prime Observer Is Local-First

Prime Observer operates through local scripts, local telemetry files, generated
CSV/JSON artifacts, and static HTML views. The repository does not require a
database, cloud backend, or heavy observability stack.

### Prime Observer Optimizes For User Experience Observability

The product is not framed as a generic network monitor. The repository
describes it as focused on noticeability, attribution, pattern awareness,
historical evidence, DNS/security context, and operational simplicity.

### Evidence, Observation, Investigation, And Projection Are Distinct

The repository explicitly separates:

- Evidence: measured telemetry and factual summaries
- Observation: deterministic conclusions Prime Observer owns
- Investigation: historical evidence packages
- Projection: generated local artifacts consumed by views

This separation should be preserved.

### Prime Observer Must Remain Bounded

Prime Observer may present factual evidence, deterministic attribution and
episode semantics, generated context, and historical evidence organization.

It must not absorb:

- Core Signal interpretation
- recommendations
- event confidence scoring
- causal correlation
- higher-level meaning beyond the deterministic semantics it already owns

### Optional Integrations Must Fail Safely

NextDNS and Cloudflare Radar are optional, read-only, summary-only integrations.
If configuration is missing or the provider is unavailable, the repository
expects generated `unavailable` artifacts rather than dashboard failure.

### Environmental Context Providers Contribute Evidence Only

Environmental Context providers are external-context integrations that may add
bounded evidence about systems outside the local home network.

They must:

- contribute evidence only
- remain separate from Observation and Attribution logic
- avoid prediction claims
- use independent additive generated artifacts
- degrade safely when unavailable
- preserve renderer-only browser consumption from generated artifacts

They must not turn provider summaries into deterministic Prime Observer
conclusions or stronger causal claims than the evidence supports.

### Browser Code Must Consume Generated Artifacts, Not Secrets

The dashboard reads generated local JSON/CSV artifacts. The repository
explicitly forbids direct browser calls to NextDNS or Cloudflare and forbids
putting secrets in browser code.

### Observation-Backed Semantics Preserve Compatibility

The repository now treats `viz/observations.json` as the authoritative
Observation projection for deterministic semantics Prime Observer owns, while
preserving backward-compatible exports such as `viz/network_attribution.json`
and retaining deterministic browser fallbacks.

### Dashboard Scope Is Intentionally Narrow

The repository defines six dashboard questions:

1. Is the network healthy?
2. Is behavior unusual?
3. Is the issue local or upstream?
4. Is the issue sustained?
5. Would users notice?
6. Is there useful DNS/security context?

Changes should be judged against those questions rather than broadening the
product casually.

## Documented Caveats

These are documented in the repository, but not fully resolved into additional
implementation work:

- sustained-persistence grouping differs slightly between current export and
  investigation generation
- threshold constants are duplicated across Python and browser code
- Pattern Awareness remains internet-probe based

Whether any of these should become the next implementation milestone is:

`Needs Matthew Review`
