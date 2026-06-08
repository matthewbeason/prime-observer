# Prime Observer

**Lightweight network observability focused on user experience**

Prime Observer is a local-first network observability dashboard focused on whether network behavior is likely to be noticeable to real users.

It combines telemetry, historical context, attribution, and DNS security signals into a single local-first dashboard.

It is not a generic network monitor, and it is no longer primarily an ISP comparison or bakeoff tool. The historical data files still use the `bakeoff_YYYYMMDD.csv` naming convention, but the current product focus is WAN health, attribution, pattern awareness, DNS security context, and operational simplicity.

Current release: **v0.6.0**

Portfolio context: Prime Observer demonstrates local-first observability,
user-noticeability scoring, privacy-aware DNS/security summaries, and historical
trend context using flat CSV/JSON artifacts.

## Dashboard

Current v0.6.0 dashboard

Prime Observer Dashboard

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
        +--> viz/network_attribution.json
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

Historical investigation:

```text
data/bakeoff_YYYYMMDD.csv
        |
        v
bin/build_investigation.py
        |
        +--> viz/investigation_index.json
        |
        v
viz/investigation.json
        |
        v
viz/investigate.html
```

### Key Files

- `data/bakeoff_YYYYMMDD.csv`
  Historical telemetry files. The name is legacy; the product is now framed around WAN health and user experience observability.

- `bin/transform_latest.py`
  Reads the newest historical telemetry file, keeps the last 24 hours, computes hourly WAN baselines, adds Pattern Confidence fields, writes `viz/latest.csv`, and exports Network Attribution results.

- `viz/latest.csv`
  Generated dashboard input containing the current 24-hour telemetry window.

- `viz/network_attribution.json`
  Generated machine-readable Network Attribution export for downstream tools. The legacy top-level `attribution_*` fields describe current/recent attribution for the last 15 minutes. The export also includes `current_attribution`, `window_attribution`, and per-incident `incidents` so reports can attribute sustained slowdown intervals across the 24-hour observation window.

- `bin/fetch_nextdns_summary.py`
  Optional local NextDNS analytics summary fetcher. Uses Python standard library only.

- `viz/nextdns_summary.json`
  Generated local DNS summary. It may include aggregate top-N domain names from analytics endpoints, but must not contain API keys, raw logs, client IPs, device names, per-query records, or full profile IDs.

- `bin/build_investigation.py`
  Builds a local read-only investigation JSON for a historical time window using existing telemetry files. The output is factual evidence, not interpretation. It also updates a generated investigation catalog by default.

- `viz/investigation.json`
  Generated local investigation evidence for a selected window. Wave 1 metadata is additive and includes deterministic event navigation plus factual nearby-event discovery.

- `viz/investigation_index.json`
  Generated local investigation catalog. Entries summarize available investigations with an ID, title, creation time, event count, status, and output path.

- `viz/investigate.html`
  Static historical evidence view for `viz/investigation.json`.

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

Quick start:

```bash
python3 bin/transform_latest.py
python3 -m http.server 8000 --directory viz
```

Then open:

```text
http://localhost:8000
```

Generate the latest telemetry window:

```bash
python3 bin/transform_latest.py
```

If using NextDNS, generate the optional DNS summary:

```bash
python3 bin/fetch_nextdns_summary.py
```

For automated NextDNS summary refresh on macOS, use the LaunchAgent documented in `docs/nextdns-launchagent.md`.

Generate a historical investigation:

```bash
python3 bin/build_investigation.py --start 2026-05-30T17:30:00-07:00 --end 2026-05-30T18:00:00-07:00
```

Then open:

```text
http://localhost:8000/investigate.html
```

Open the investigation view through the local server, not directly from disk.
Direct `file://` access can prevent the browser from loading
`investigation.json`.

See `docs/investigation-workflow.md` for details and future Olivaw deep-link shape.

The investigation workflow also maintains an optional generated Investigation
Index at `viz/investigation_index.json`. The index is a local catalog of
generated investigations with `id`, `title`, `created_at`, `event_count`,
`status`, and `path` fields so downstream tools can discover available evidence
files without interpreting them.

Investigation exports include Historical Navigation metadata:

- `first_event`
- `previous_event`
- `next_event`
- `last_event`

Exports also include factual Event Neighborhood Discovery. Nearby-event records
describe temporal proximity, shared investigation membership, and shared
evidence windows only.

Prime Observer provides discovery only. Investigation discovery does not provide
recommendations, event confidence scores, causal analysis, or event correlation.

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
NEXTDNS_EXPORT_DOMAIN_NAMES=1
NEXTDNS_TOP_ENTITIES_LIMIT=5
```

`NEXTDNS_EXPORT_DOMAIN_NAMES` defaults to `1`, so local downstream briefings can name aggregate top queried, resolved, and blocked domains. Set it to `0` to redact domain names while preserving counts, shares, and entity labels.

The generated DNS summary includes additive downstream-friendly fields such as `dns_block_rate`, `dns_encrypted_rate`, `top_queried_domain`, `top_resolved_domain`, `top_blocked_domain`, `top_blocked_reason`, `top_entity_share`, and `top_entity_dominance_ratio`.

Security rules:

- Do not commit `.env.nextdns`.
- Do not put secrets in `viz/index.html`.
- Do not expose API keys in generated JSON.
- Do not include raw DNS logs, client IPs, device names, per-query records, user attribution, or full profile IDs.
- Use NextDNS analytics endpoints only; the dashboard must not call the NextDNS API directly.
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

## Current Release Notes: v0.6.0

v0.6.0 includes:

- Added Investigation Index support for a generated catalog of local investigation exports
- Added investigation navigation metadata for first, previous, next, and last events
- Added factual nearby-event discovery based on temporal proximity, shared investigation membership, and shared evidence windows
- Improved investigation workflow usability for downstream navigation
- Preserved strict evidence-first architecture: Prime Observer provides observations, evidence, investigations, timelines, and event discovery only
- Excluded recommendations, event confidence scores, causal analysis, and event correlation from investigation discovery outputs
- Maintained backward compatibility for existing v0.5.0 investigation exports through additive metadata

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

Prime Observer is released under the MIT License.

See the LICENSE file for details.
