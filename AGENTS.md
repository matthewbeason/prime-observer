# AGENTS.md

## Prime Observer

Prime Observer is a lightweight personal network observability and user-experience telemetry system.

The goal is not simply to measure network performance. The goal is to determine whether network behavior is likely to be noticeable to users and to provide useful attribution and context.

Current production release: v0.3.14

---

## Architecture

Primary files:

- viz/index.html
- bin/transform_latest.py
- viz/latest.csv

Historical telemetry:

- data/bakeoff_YYYYMMDD.csv

Although the historical files use the word "bakeoff", the current product is no longer framed as an ISP comparison tool.

Prime Observer now focuses on WAN health and user experience observability.

---

## Data Sources

Monitored endpoints:

LAN:

- 192.168.1.1

WAN:

- 1.1.1.1
- 9.9.9.9

Key metrics:

- p95 latency
- jitter
- packet loss
- sustained degradation
- turbulence
- baseline deviation

---

## Core Concepts

### User Noticeability

A synthesized score intended to estimate whether network conditions are likely to affect real users.

Inputs include:

- WAN bad moments
- WAN persistence
- latency
- jitter
- loss
- recent trends

Noticeability is intentionally opinionated.

---

### Pattern Awareness

Pattern awareness compares current WAN behavior against historical behavior for the same hour of day.

Implemented via:

- baseline_p95
- baseline_delta_pct

Pattern messaging should provide context, not alerts.

Examples:

- Better than usual for this time of day
- Normal for this time of day
- Slightly elevated for this time of day
- Highly elevated for this time of day

Pattern awareness helps answer:

> "Is this unusual?"

not

> "Is this broken?"

---

### Network Attribution

Attribution attempts to identify where degradation originates.

Possible outcomes:

- No network issue detected
- Likely upstream (ISP / path)
- Likely local (LAN / Wi-Fi)
- Inconclusive

Important principle:

Do not blame LAN for isolated spikes.

LAN attribution should require evidence of persistence and elevated rate.

---

### Sustained Bad Moments

Raw WAN degradation is defined as:

- p95 > 140 ms
- OR jitter > 50 ms
- OR loss_pct > 1%

Sustained degradation requires persistence.

Current threshold:

python WAN_BAD_PERSISTENCE = 2 

---

### Turbulence

Turbulence represents noisy degradation that does not meet sustained thresholds.

Purpose:

Separate brief instability from meaningful degradation.

Current threshold:

python TURBULENCE_MIN_RAW_BAD = 4 

Visual language:

- Amber heatmap buckets
- Amber chart bands

Turbulence is informational.

Sustained degradation is operationally significant.

---

## Dashboard Philosophy

Prime Observer should answer:

1. Is the network healthy?
2. Is behavior unusual?
3. Is the issue local or upstream?
4. Is the issue sustained?
5. Would users notice?

Avoid adding metrics that do not help answer one of these questions.

---

## Development Principles

Prefer:

- deterministic logic
- simple heuristics
- small diffs
- local execution
- transparent calculations

Avoid:

- unnecessary dependencies
- heavy infrastructure
- cloud requirements
- complex frameworks
- black-box scoring

The dashboard should continue functioning even if optional features fail.

---

## Workflow

Before modifying code:

1. Read affected files.
2. Explain current behavior.
3. Propose minimal changes.
4. Preserve existing functionality unless explicitly changing it.

Do not commit changes unless explicitly requested.

Do not tag releases unless explicitly requested.

---

## Near-Term Roadmap

Potential future improvements:

- Pattern confidence scoring
- Current vs typical WAN metrics
- Tooltip baseline context
- Weather correlation
- Power outage correlation
- ISP status correlation

Potential future LLM integration:

- Local model only
- Explanation layer only
- Never the source of truth
- Deterministic telemetry remains authoritative

---

## Product Positioning

Prime Observer is not a generic network monitor.

Prime Observer is a network experience observability system focused on:

- noticeability
- attribution
- context
- trend awareness
- operational simplicity
