# Prime Observer

**Lightweight network observability focused on user experience**

Prime Observer is a local-first network observability dashboard focused on whether network behavior is likely to be noticeable to real users.

It combines telemetry, historical context, attribution, and DNS security signals into a single local-first dashboard.

It is not a generic network monitor, and it is no longer primarily an ISP comparison or bakeoff tool. The historical data files still use the `bakeoff_YYYYMMDD.csv` naming convention, but the current product focus is WAN health, attribution, pattern awareness, DNS security context, and operational simplicity.

Current release: **v0.4.1**

## Dashboard

Current v0.4.1 dashboard

Prime Observer Dashboard

Required asset: no dashboard screenshot file is currently present in the repository. Add one before publishing if the README should render a screenshot on GitHub.

The dashboard is designed to answer:

- Is the network healthy?
- Is behavior unusual?
- Is the issue local or upstream?
- Is the issue sustained?
- Would users notice?
- Is there useful DNS/security context?

## Why It Exists

Home networks often fail in ways that raw latency graphs do not explain well.

A connection can look mostly fine on average while still producing short periods of latency, jitter, or loss that affect video calls, streaming, remote work, gaming, or other interactive applications. Prime Observer was built to turn those raw measurements into useful operational context.

The goal is clarity, not benchmark theater.

## How It Is Different

Traditional network monitoring often emphasizes uptime, averages, raw charts, or infrastructure-level alerts.

Prime Observer is more opinionated:

- It focuses on user noticeability rather than raw latency alone.
- It distinguishes turbulence from sustained degradation.
- It compares current WAN behavior against historical behavior for the same hour of day.
- It attempts lightweight LAN vs WAN attribution.
- It can include optional DNS/security context without becoming a DNS analytics platform.
- It runs locally with flat CSV and JSON files.
- Optional integrations fail safely.
- Deterministic telemetry remains the source of truth.

No cloud backend, database, or heavy observability stack is required.

## Major Concepts

### User Noticeability

User Noticeability is a synthesized score estimating whether current WAN conditions are likely to affect users.

Inputs include recent WAN latency, persistence, bad moments, jitter, loss, and trend behavior. The score is intentionally simple and deterministic.

### Pattern Awareness

Pattern Awareness compares current WAN p95 latency against historical WAN behavior for the same hour of day.

It helps answer:

> Is this unusual?

rather than:

> Is this broken?

The dashboard labels behavior as better than usual, normal, slightly elevated, or highly elevated for the current time of day.

### Pattern Confidence

Pattern Confidence is based on `baseline_sample_count`, the number of historical WAN samples contributing to the baseline for that hour.

Sparse baselines are treated as learning or low confidence so the dashboard does not overstate weak historical context.

### Network Attribution

Prime Observer attempts to distinguish:

- No network issue detected
- Likely upstream issue
- Likely local LAN / Wi-Fi issue
- Inconclusive

LAN attribution requires persistent elevated LAN evidence. Isolated LAN spikes should not cause the dashboard to blame the local network.

### Sustained Bad Moments

Raw WAN degradation is currently defined as:

- p95 latency > 140 ms
- or jitter > 50 ms
- or packet loss > 1%

Sustained degradation requires persistence across consecutive samples.

### Turbulence

Turbulence represents noisy WAN degradation that does not meet sustained thresholds.

It is informational rather than operationally significant. This keeps brief instability visually distinct from sustained bad moments.

### WAN Health Summary

The WAN Health Summary shows current WAN experience across monitored internet targets, including:

- median WAN p95
- 95th percentile WAN p95
- 95th percentile jitter
- bad rate
- bad minutes per hour
- sample count

This section is intended as a health summary, not a provider-comparison scoreboard.

### DNS Security

Prime Observer can optionally include DNS/security context from NextDNS.

The integration is local, read-only, and summary-only. A local fetch script writes a public-safe JSON summary consumed by the dashboard.

The dashboard does not call the NextDNS API directly and does not expose secrets.

## Architecture

Prime Observer uses a lightweight local file pipeline.

```text
data/bakeoff_YYYYMMDD.csv
        |
        v
bin/transform_latest.py
        |
        v
viz/latest.csv
        |
        v
viz/index.html
```

Optional DNS context:

```text
.env.nextdns or environment variables
        |
        v
bin/fetch_nextdns_summary.py
        |
        v
viz/nextdns_summary.json
        |
        v
viz/index.html
```

### Key Files

- `data/bakeoff_YYYYMMDD.csv`
  Historical telemetry files. The name is legacy; the product is now framed around WAN health and user experience observability.

- `bin/transform_latest.py`
  Reads the newest historical telemetry file, keeps the last 24 hours, computes hourly WAN baselines, adds Pattern Confidence fields, and writes `viz/latest.csv`.

- `viz/latest.csv`
  Generated dashboard input containing the current 24-hour telemetry window.

- `bin/fetch_nextdns_summary.py`
  Optional local NextDNS analytics summary fetcher. Uses Python standard library only.

- `viz/nextdns_summary.json`
  Generated public-safe DNS summary. It must not contain API keys, raw domains, client IPs, device names, or full profile IDs.

- `viz/index.html`
  Static D3 dashboard. Loads local CSV and JSON files with `cache: "no-store"` and renders the observability UI.

## Data Sources

Monitored LAN target:

- `192.168.1.1`

Monitored WAN targets:

- `1.1.1.1`
- `9.9.9.9`

Optional DNS/security context:

- NextDNS analytics summary via local generated JSON

## Running The Dashboard

Generate the latest telemetry window:

```bash
python3 bin/transform_latest.py
```

If using NextDNS, generate the optional DNS summary:

```bash
python3 bin/fetch_nextdns_summary.py
```

Serve the dashboard locally:

```bash
python3 -m http.server 8000 --directory viz
```

Open:

```text
http://localhost:8000
```

The dashboard refreshes automatically.

## Optional NextDNS Configuration

NextDNS support is optional.

Configuration can come from process environment variables or a local `.env.nextdns` file.

Required:

```bash
NEXTDNS_PROFILE_ID=your_profile_id
NEXTDNS_API_KEY=your_api_key
```

Optional:

```bash
NEXTDNS_WINDOW=-24h
NEXTDNS_TIMEOUT_SECONDS=8
```

Security rules:

- Do not commit `.env.nextdns`.
- Do not put secrets in `viz/index.html`.
- Do not expose API keys in generated JSON.
- Do not include raw DNS logs, domain lists, client IPs, device names, or full profile IDs.
- The dashboard must continue working if NextDNS data is missing, stale, invalid, or unavailable.

## Design Principles

Prime Observer favors:

- deterministic local logic
- simple heuristics
- transparent calculations
- flat CSV/JSON files
- small, reviewable changes
- graceful failure of optional features
- user-experience context over raw metric volume

Prime Observer avoids:

- heavy observability stacks
- cloud dependencies
- unnecessary databases
- complex frameworks
- browser-side secrets
- black-box scoring
- alert noise

## What This Is Not

Prime Observer is not:

- a full DNS analytics platform
- a SIEM
- a cloud monitoring product
- a replacement for packet captures or detailed network forensics
- an alerting system
- an AI-driven diagnosis engine

It is a focused local dashboard for understanding whether network behavior is healthy, unusual, attributable, sustained, and likely noticeable.

## Current Release Notes: v0.4.1

v0.4.1 includes:

- Pattern Confidence using `baseline_sample_count`
- Optional DNS Security card backed by local NextDNS summary JSON
- Compact Connection card
- WAN Health Summary visual refocus
- Subtle semantic health accents
- Visual hierarchy refinement without changing scoring or telemetry logic

## Future Directions

Near-term work should be guided by real-world observation rather than feature expansion.

Areas to watch:

- stable-but-noticeable false negatives
- noisy-but-masked false positives
- whether Pattern Confidence feels trustworthy
- whether DNS Security adds context or clutter
- whether turbulence is informative or distracting
- whether attribution confidence matches lived experience

Possible future improvements, only if justified by usage:

- current vs typical WAN context in tooltips
- clearer baseline explanations
- modest portability improvements
- small test coverage around scoring and attribution thresholds

Explicitly deferred for now:

- raw DNS logs
- domain lists
- device-level DNS analytics
- alerts and notifications
- weather, power, or ISP status correlation
- LLM explanations
- major dashboard refactors

## License

Personal project / home lab tooling. License details TBD.
