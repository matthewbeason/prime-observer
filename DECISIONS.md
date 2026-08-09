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

### Normal Is Learned, Not Defined By Static Thresholds

Normal is learned from observed telemetry over the most recent eligible
telemetry source files. The active durable baseline uses the newest two
telemetry source files, minimum existing sample and source requirements remain,
and older baseline versions are preserved as historical memory in
`viz/baseline_history.json`. Sustained stable new behavior can become current
normal.

Static WAN thresholds remain safety guardrails only. Loss, timeout, DNS/HTTPS
failure, gateway problems, correlated degradation, rapid worsening, severe
excursions, and confirmed or major impact must not be normalized away. Raw
observations and history remain factual even when adaptive semantics suppress a
bad moment.

### Sharp Post-Recovery Transitions Do Not Immediately Retrain

A durable baseline is not retrained when the older observed median is
materially better than the most recent samples. This `post_recovery_stabilizing`
guardrail prevents an incident recovery from immediately being learned away as
normal and keeps the stable recovery window factual.

### Visualization First, Narrative Second, Details Last

Operators should see first, understand second, and investigate only when
necessary. The heatmap and latency line charts are core product functionality
and must be preserved. A visualization must reduce cognitive load, not decorate
the UI; pie charts are not part of the Prime Observer visual vocabulary. New
features should first ask whether they can be communicated visually, and the
dashboard should not grow cards simply because a new artifact or capability
exists.

### Python Owns Semantics; The Browser Is Renderer-Only

Deterministic Python is authoritative for facts, evidence, thresholds, event
boundaries, lifecycle, affected scope, classifications, baselines, freshness,
semantic hashing, safety constraints, and fallback guidance. The browser renders
generated artifacts and maps emitted fields to presentation; it must not own
health, attribution, baseline, incident, or next-action semantics.

### AI Interprets Evidence, It Does Not Own It

Valid/current OpenRouter-backed Operator Assistant output is the primary
operator-facing interpretation when it is valid for the current evidence
package, and a deterministic fallback remains available when it is not. The LLM
may synthesize likely meaning, uncertainty, and safe next actions from
deterministic evidence, but it must not invent facts or contradict deterministic
evidence, and it must not replace the visualization.

### Renderer Changes Require Browser Smoke Validation

Any renderer, initialization, or time-context change affecting `viz/index.html`
requires browser smoke validation through local HTTP covering the heatmap, all
primary line charts, tooltips and interactions, selected-time behavior, and no
fatal console errors.

## Documented Caveats

These are documented in the repository, but not fully resolved into additional
implementation work:

- sustained-persistence grouping differs slightly between current export and
  investigation generation
- threshold constants are duplicated across Python and browser code
- Pattern Awareness remains internet-probe based

Whether any of these should become the next implementation milestone is:

`Needs Matthew Review`
