# AGENTS.md

## Prime Observer

Prime Observer is a lightweight personal network observability and user-experience telemetry system.

The goal is not simply to measure network performance. The goal is to determine whether network behavior is likely to be noticeable to users and to provide useful attribution and context.

Current production release: v0.8.2

---

## Current File Map

Primary dashboard and transforms:

- `viz/index.html` - static dashboard, scoring, attribution, charts, DNS Security card, and visual hierarchy.
- `bin/transform_latest.py` - converts recent historical telemetry into `viz/latest.csv` and adds pattern baseline fields.
- `bin/fetch_nextdns_summary.py` - optional local NextDNS summary fetcher.
- `bin/fetch_cloudflare_radar.py` - optional local Cloudflare Radar Internet Conditions summary fetcher.
- `bin/build_investigation.py` - builds read-only historical investigation evidence JSON.
- `viz/investigate.html` - static historical investigation evidence view.
- `launchd/` - macOS LaunchAgent definitions.
- `launchd/com.mbeason.prime-observer.nextdns-refresh.plist` - refreshes the NextDNS summary every 30 minutes.
- `docs/nextdns-launchagent.md` - install, status, unload, and remove commands for the NextDNS LaunchAgent.
- `docs/images/prime-observer-v041.png` - dashboard screenshot image asset.
- `LICENSE` - MIT license.

Generated/local runtime files:

- `viz/latest.csv` - generated 24-hour dashboard telemetry window.
- `viz/investigation.json` - generated historical investigation evidence for a selected window.
- `viz/investigation_index.json` - generated catalog of local investigation evidence files.
- `viz/nextdns_summary.json` - generated public-safe NextDNS summary consumed by the dashboard.
- `viz/internet_conditions.json` - generated public-safe Cloudflare Radar Internet Conditions summary consumed by the dashboard.
- `.env.cloudflare` - local Cloudflare Radar token/config; must not be committed.
- `.env.example` - placeholder Cloudflare Radar config; safe to commit.
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
4. `bin/fetch_cloudflare_radar.py`, when configured, reads Cloudflare Radar outage annotations locally and writes `viz/internet_conditions.json`.
5. `bin/build_investigation.py`, when requested, reads historical telemetry and writes `viz/investigation.json`.
6. `viz/index.html` and `viz/investigate.html` read local generated files with `cache: "no-store"` and render dashboard or investigation views.

The dashboard must continue functioning if optional features fail, including missing, stale, invalid, or unavailable NextDNS data.
The dashboard must continue functioning if optional features fail, including missing, stale, invalid, or unavailable Cloudflare Radar data.

No heavy observability stack, database, cloud service, or framework is required.

---

## Data Sources

Monitored endpoints:

LAN:

- `192.168.1.1`

Internet probes:

- `1.1.1.1`
- `9.9.9.9`

Resolver probes:

- `45.90.28.134`
- `45.90.30.134`

Optional DNS/security context:

- NextDNS analytics summary, generated locally by `bin/fetch_nextdns_summary.py`.
- NextDNS is optional, read-only, and summary-only.
- Do not call raw logs endpoints for the dashboard.

Optional external Internet Conditions context:

- Cloudflare Radar outage summary, generated locally by `bin/fetch_cloudflare_radar.py`.
- Cloudflare Radar is optional, read-only, and summary-only.
- Do not call the Cloudflare API directly from the dashboard.

Key metrics and context:

- p95 latency
- jitter
- packet loss
- sustained degradation
- turbulence
- baseline deviation
- baseline confidence
- DNS blocked-query summary, when available

Historical investigation is factual evidence only. Prime Observer may show WAN
and LAN samples, target classes, target-group metadata, counts, thresholds,
buckets, source files, generated DNS summary context, investigation catalogs,
navigation metadata, and factual nearby-event discovery around a requested
window. It must not move Core Signal interpretation, correlations, event
confidence scoring, recommendations, or higher-level meaning into Prime
Observer.

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
- Mixed evidence
- Inconclusive

Important principle:

Do not blame LAN for isolated spikes.

LAN attribution should require evidence of persistence and elevated rate.
LAN elevation should not automatically override WAN evidence.

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
- `viz/nextdns_summary.json` is generated local/private output.
- `viz/nextdns_summary.json` may include aggregate top-N domain names from analytics endpoints, but must not include secrets, raw DNS logs, client IPs, local IPs, device names, user attribution, per-query records, or full profile IDs.
- DNS domain-name export defaults to enabled for local downstream briefings; `NEXTDNS_EXPORT_DOMAIN_NAMES=0` must keep aggregate domain names redacted while preserving counts and shares.
- The dashboard must display an unavailable/stale state without affecting network telemetry.

### Internet Conditions

The Cloudflare Radar integration adds optional external context without changing Prime Observer semantics.

Rules:

- Cloudflare Radar data is fetched only by `bin/fetch_cloudflare_radar.py`, never directly by `viz/index.html`.
- The script uses local configuration from environment variables or `.env.cloudflare`.
- `CLOUDFLARE_API_TOKEN` is secret/config and must never be committed.
- `.env.example` must contain placeholder values only.
- `viz/internet_conditions.json` is generated local/private output.
- If the token is missing or the API is unavailable, the script must write an unavailable artifact without affecting network telemetry.

---

## v0.8.0 Includes

- Observation domain model.
- Observation materialization policy.
- Attribution Observation projection.
- Episode Observation projection.
- Investigation Observation references.
- Dashboard attribution sourced from Observations.
- Dashboard episode state sourced from Observations.
- Backward-compatible legacy exports.
- Deterministic browser fallbacks.

---

## v0.7.1 Includes

- Calibrated attribution evidence weighting.
- Mixed Evidence attribution state.
- Attribution evidence counts and target-group evidence exports.
- WAN evidence no longer automatically overridden by LAN elevation.
- Cross-chart bucket synchronization across WAN internet, WAN resolver, and LAN gateway charts.
- Bucket evidence tooltips for internet, resolver, and LAN counts.
- Canonical health model documentation in `docs/health-model.md`.
- Health model audit documentation in `docs/health-model-audit.md`.
- Composite WAN bad moments using internet and resolver target groups.
- Heatmap aligned with the canonical WAN model.
- User Noticeability aligned with the canonical WAN model.
- Improved heatmap, attribution, and investigation consistency.
- Historical investigation workflow, navigation metadata, event neighborhoods, and DNS context from v0.7.0 remain in place.
- Architecture boundary preservation: Prime Observer evidence, Core Signal interpretation, Olivaw synthesis and navigation.

---

## Current Dashboard Components

- Connection card
- Telemetry card
- User Noticeability card
- Network Attribution card
- DNS Security card
- WAN Health Summary
- WAN bad moments heatmap
- WAN Internet Probe p95 latency chart
- WAN Resolver Probe p95 latency chart
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

- Never commit `.env.cloudflare`.
- Never commit `.env.nextdns`.
- Never commit API keys or local secrets.
- Never put secrets in `viz/index.html`.
- Never fetch the Cloudflare API directly from browser code.
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

Live with v0.8.2 for several days before expanding functionality.

Watch for:

- stable-but-noticeable false negatives
- noisy-but-masked false positives
- whether DNS Security adds context or clutter
- whether Pattern confidence feels trustworthy
- whether turbulence is informative or distracting
- whether attribution confidence matches real experience
- whether the compact Connection card and refocused WAN Health Summary improve scanning
- whether investigation navigation and nearby-event discovery improve evidence review without implying correlation

---

## Product Positioning

Prime Observer is not a generic network monitor.

Prime Observer is a network experience observability system focused on:

- noticeability
- attribution
- context
- trend awareness
- operational simplicity
