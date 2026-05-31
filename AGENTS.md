# AGENTS.md

## Prime Observer

Prime Observer is a lightweight personal network observability and user-experience telemetry system.

The goal is not simply to measure network performance. The goal is to determine whether network behavior is likely to be noticeable to users and to provide useful attribution and context.

Current production release: v0.4.1

---

## Current File Map

Primary dashboard and transforms:

- `viz/index.html` - static dashboard, scoring, attribution, charts, DNS Security card, and visual hierarchy.
- `bin/transform_latest.py` - converts recent historical telemetry into `viz/latest.csv` and adds pattern baseline fields.
- `bin/fetch_nextdns_summary.py` - optional local NextDNS summary fetcher.
- `launchd/` - macOS LaunchAgent definitions.
- `launchd/com.mbeason.prime-observer.nextdns-refresh.plist` - refreshes the NextDNS summary every 30 minutes.
- `docs/nextdns-launchagent.md` - install, status, unload, and remove commands for the NextDNS LaunchAgent.
- `docs/images/prime-observer-v041.png` - current dashboard screenshot for v0.4.1 documentation.
- `LICENSE` - MIT license.

Generated/local runtime files:

- `viz/latest.csv` - generated 24-hour dashboard telemetry window.
- `viz/nextdns_summary.json` - generated public-safe NextDNS summary consumed by the dashboard.
- `.env.nextdns` - local NextDNS secrets/config; must not be committed.
- `.env.nextdns.example` - placeholder example config; safe to commit.

Historical telemetry:

- `data/bakeoff_YYYYMMDD.csv`

Although the historical files use the word `bakeoff`, the current product is no longer framed as an ISP comparison tool. Prime Observer now focuses on WAN health and user experience observability.

---

## Architecture

Prime Observer uses a lightweight local CSV/JSON flow:

1. Historical telemetry lives in `data/bakeoff_YYYYMMDD.csv`.
2. `bin/transform_latest.py` reads the newest telemetry file, keeps the last 24 hours, computes WAN baseline context, and writes `viz/latest.csv`.
3. `bin/fetch_nextdns_summary.py`, when configured, reads NextDNS analytics locally and writes `viz/nextdns_summary.json`.
4. `viz/index.html` reads local generated files with `cache: "no-store"` and renders the dashboard.

The dashboard must continue functioning if optional features fail, including missing, stale, invalid, or unavailable NextDNS data.

No heavy observability stack, database, cloud service, or framework is required.

---

## Data Sources

Monitored endpoints:

LAN:

- `192.168.1.1`

WAN:

- `1.1.1.1`
- `9.9.9.9`

Optional DNS/security context:

- NextDNS analytics summary, generated locally by `bin/fetch_nextdns_summary.py`.
- NextDNS is optional, read-only, and summary-only.
- Do not call raw logs endpoints for the dashboard.

Key metrics and context:

- p95 latency
- jitter
- packet loss
- sustained degradation
- turbulence
- baseline deviation
- baseline confidence
- DNS blocked-query summary, when available

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

- `baseline_p95`
- `baseline_delta_pct`
- `baseline_sample_count`

`baseline_sample_count` provides Pattern confidence. The dashboard should treat sparse baselines as learning/low confidence rather than presenting strong conclusions.

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

```python
WAN_BAD_PERSISTENCE = 2
```

---

### Turbulence

Turbulence represents noisy degradation that does not meet sustained thresholds.

Purpose:

Separate brief instability from meaningful degradation.

Current threshold:

```python
TURBULENCE_MIN_RAW_BAD = 4
```

Visual language:

- Amber heatmap buckets
- Amber chart bands

Turbulence is informational.

Sustained degradation is operationally significant.

---

### DNS Security

The NextDNS integration adds optional DNS/security context without making Prime Observer a DNS analytics platform.

Rules:

- NextDNS data is fetched only by `bin/fetch_nextdns_summary.py`, never directly by `viz/index.html`.
- The script uses local configuration from environment variables or `.env.nextdns`.
- `NEXTDNS_PROFILE_ID` and `NEXTDNS_API_KEY` are secrets/config and must never be committed.
- `viz/nextdns_summary.json` is generated public-safe output.
- `viz/nextdns_summary.json` must not include secrets, raw domains, client IPs, device names, or full profile IDs.
- The dashboard must display an unavailable/stale state without affecting network telemetry.

---

## v0.4.1 Includes

- Pattern confidence using `baseline_sample_count`.
- Optional DNS Security card backed by local `viz/nextdns_summary.json`.
- Visual hierarchy refinement.
- Compact Connection card replacing the heavier Phase card.
- WAN Health Summary refocused away from provider-comparison presentation.
- Subtle semantic health accents for stable/watch/risk states.

---

## Current Dashboard Components

- Connection card
- Telemetry card
- User Noticeability card
- Network Attribution card
- DNS Security card
- WAN Health Summary
- WAN bad moments heatmap
- WAN p95 latency chart
- LAN gateway p95 latency chart

Do not add new dashboard components without first confirming they answer one of the dashboard philosophy questions.

---

## Dashboard Philosophy

Prime Observer should answer:

1. Is the network healthy?
2. Is behavior unusual?
3. Is the issue local or upstream?
4. Is the issue sustained?
5. Would users notice?
6. Is there useful DNS/security context?

Avoid adding metrics that do not help answer one of these questions.

The dashboard should remain professional, restrained, and observability-focused. Avoid NOC-style alert walls, gaming-dashboard styling, and decorative visual noise.

---

## Development Principles

Prefer:

- deterministic logic
- simple heuristics
- small diffs
- local execution
- transparent calculations
- lightweight CSV/JSON handoff files
- optional features that fail safely

Avoid:

- unnecessary dependencies
- heavy infrastructure
- cloud requirements
- complex frameworks
- black-box scoring
- browser-side secrets

The dashboard should continue functioning even if optional features fail.

---

## Security Rules

- Never commit `.env.nextdns`.
- Never commit API keys or local secrets.
- Never put secrets in `viz/index.html`.
- Never fetch the NextDNS API directly from browser code.
- Only expose public-safe generated summaries to the dashboard.
- Keep `.gitignore` protecting local secrets and generated telemetry outputs.

---

## Workflow

Before modifying code:

1. Read affected files.
2. Explain current behavior.
3. Propose minimal changes.
4. Preserve existing functionality unless explicitly changing it.

Do not commit changes unless explicitly requested.

Do not tag releases unless explicitly requested.

When editing, keep changes scoped. Avoid unrelated refactors, especially in `viz/index.html`, unless the user explicitly asks for them.

---

## Do Not Expand Yet

Avoid adding these until real usage shows a clear need:

- raw DNS logs
- domain lists
- device-level DNS analytics
- alerts or notifications
- LLM explanations
- weather correlation
- power outage correlation
- ISP status correlation
- major `viz/index.html` refactor

These may be useful later, but they would currently increase complexity faster than observability quality.

---

## Next Observation Period

Live with v0.4.1 for several days before expanding functionality.

Watch for:

- stable-but-noticeable false negatives
- noisy-but-masked false positives
- whether DNS Security adds context or clutter
- whether Pattern confidence feels trustworthy
- whether turbulence is informative or distracting
- whether attribution confidence matches real experience
- whether the compact Connection card and refocused WAN Health Summary improve scanning

---

## Product Positioning

Prime Observer is not a generic network monitor.

Prime Observer is a network experience observability system focused on:

- noticeability
- attribution
- context
- trend awareness
- operational simplicity
