# Prime Observer

**Local-first network experience observability engine**

Prime Observer is a local-first network experience observability engine focused on whether network behavior is likely to be noticeable to real users.

It combines measured telemetry, deterministic health modeling, Observation projection, historical investigation evidence, and optional DNS/security context into a single local-first workflow.

It is not a generic network monitor, and it is no longer primarily an ISP comparison or bakeoff tool. The historical data files still use the `bakeoff_YYYYMMDD.csv` naming convention, but the current product focus is WAN health, attribution, pattern awareness, DNS security context, and operational simplicity.

Current release: **v0.9.0**

Previous production release: **v0.7.2**

Portfolio context: Prime Observer demonstrates local-first observability,
user-noticeability scoring, privacy-aware DNS/security summaries, and historical
trend context using flat CSV/JSON artifacts.

## Dashboard

Current v0.9.0 dashboard

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
- Evidence remains the source of truth for measured facts.
- Observation projection is the source of truth for deterministic interpretation that Prime Observer owns.

No cloud backend, database, or heavy observability stack is required.

## Evidence Model

Prime Observer now separates four concerns:

- Evidence: measured telemetry rows, generated factual summaries, and source-file references.
- Observation: deterministic Prime Observer conclusions derived from Evidence, such as current attribution and episode state.
- Investigation: historical evidence packages that organize Evidence and overlapping Observations for a requested window.
- Projection: local JSON artifacts consumed by the dashboard and investigation viewer.

This keeps Prime Observer evidence-first while reducing browser-side reasoning drift. The dashboard is increasingly a projection consumer rather than the sole reasoning engine.

See `docs/artifact-architecture.md` for the authoritative artifact reference.

## Major Concepts

### User Noticeability

User Noticeability is a synthesized score estimating whether current WAN conditions are likely to affect users.

Inputs include recent composite WAN latency from internet and resolver probes,
composite WAN bad moment buckets, composite turbulence buckets, and recent
elevated p95 streaks. The score is intentionally simple and deterministic.

### Pattern Awareness

Pattern Awareness compares current general internet probe p95 latency against historical WAN behavior for the same hour of day.

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
- Mixed evidence
- Inconclusive

LAN attribution requires persistent elevated LAN evidence. Isolated LAN spikes
should not cause the dashboard to blame the local network, and LAN elevation no
longer automatically overrides WAN evidence.

Prime Observer also records factual target classes so summaries can distinguish
general internet probes, resolver probes, and the local gateway. These classes
are evidence labels only. Core Signal remains responsible for interpretation,
recommendations, and higher-level meaning.

### Sustained Bad Moments

Raw WAN degradation is currently defined as:

- p95 latency > 140 ms
- or jitter > 50 ms
- or packet loss > 1%

Sustained degradation requires persistence across consecutive samples.

A canonical WAN bad moment is a 15-minute bucket in which one or more WAN
target groups shows sustained degradation according to these thresholds. WAN
target groups are `internet_probe` and `resolver_probe`. LAN gateway evidence
is not a WAN target group.

### Turbulence

Turbulence represents noisy WAN degradation that does not meet sustained thresholds.

It is informational rather than operationally significant. This keeps brief instability visually distinct from sustained bad moments.

### Heatmap And Charts

The WAN bad moments heatmap uses composite WAN evidence from internet probes
and resolver probes after target grouping. Within each target group, samples
are collapsed by timestamp to the worst p95 sample before the dashboard marks
raw and sustained bad moments.

The two visualizations answer different questions:

- The WAN Internet Probe and WAN Resolver Probe chart lines show p95 latency
  values over time for their target groups.
- The heatmap shows 15-minute composite WAN buckets.
- A raw bad sample is any internet or resolver probe sample with p95 latency >
  140 ms, jitter > 50 ms, or packet loss > 1%.
- A dark gray bucket means at least one WAN target group had sustained
  degradation in that bucket.
- An amber bucket means turbulence: one or more WAN target groups had enough
  raw bad samples to be notable, but did not meet the sustained persistence
  rule.
- Heatmap tooltips show whether internet probes, resolver probes, or both
  contributed evidence. LAN gateway counts are shown separately for the same
  interval.

Because bad-moment evidence includes jitter and packet loss, the p95 latency
line can appear to improve while the heatmap remains dark. That is expected
when packet loss or jitter is persistent even though p95 latency has fallen.

### WAN Health Summary

The WAN Health Summary shows current WAN experience by target group, including:

- median WAN p95
- 95th percentile WAN p95
- 95th percentile jitter
- bad rate
- bad minutes per hour
- sample count

This section is intended as a health summary, not a provider-comparison
scoreboard. Cloudflare and Quad9 remain general internet reachability probes.
The configured NextDNS resolver IPs are shown as resolver-path probes so their
latency is not silently blended into general WAN/path latency.

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
        +--> viz/latest.csv
        |
        +--> viz/network_attribution.json
        |
        +--> viz/observations.json
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

Optional Internet Conditions context:

```text
.env.cloudflare or environment variables
        |
        v
bin/fetch_cloudflare_radar.py
        |
        v
viz/internet_conditions.json
        |
        v
viz/index.html
```

Optional Power Infrastructure context:

```text
bin/fetch_aps_power_context.py
        |
        v
viz/aps_power_context.json
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
        +--> reads viz/observations.json
        |
        v
viz/investigation.json
        |
        v
viz/investigate.html
```

Operator Assistant evidence package prototype:

```text
viz/investigation.json
        |
        v
bin/build_operator_assistant_input.py
        |
        v
viz/operator_assistant_input.json
```

Projection roles:

- `viz/latest.csv` remains the dashboard telemetry window and factual chart input.
- `viz/network_attribution.json` remains the backward-compatible legacy export.
- `viz/observations.json` is the authoritative Observation projection for deterministic attribution and episode semantics owned by Prime Observer.
- `viz/investigation.json` organizes factual evidence and additive Observation references for a requested historical window.
- `viz/operator_assistant_input.json` is a compact deterministic evidence package derived from `viz/investigation.json` for future assistant interpretation experiments.

### Key Files

- `data/bakeoff_YYYYMMDD.csv`
  Historical telemetry files. The name is legacy; the product is now framed around WAN health and user experience observability.

- `bin/transform_latest.py`
  Reads the newest historical telemetry file, keeps the last 24 hours, adds target label/class metadata, computes hourly WAN baselines from general internet probes, adds Pattern Confidence fields, writes `viz/latest.csv`, and exports Network Attribution results.

- `viz/latest.csv`
  Generated dashboard input containing the current 24-hour telemetry window.

- `viz/network_attribution.json`
  Generated machine-readable Network Attribution export for downstream tools. The legacy top-level `attribution_*` fields describe current/recent attribution for the last 15 minutes. The export also includes factual `target_groups`, `internet_probe_summary`, `resolver_probe_summary`, `current_attribution`, `window_attribution`, and per-incident `incidents` so reports can attribute sustained slowdown intervals across the 24-hour observation window.

- `viz/observations.json`
  Generated Observation projection. This is Prime Observer's authoritative local projection for deterministic interpretation it owns, including attribution observations and episode observations, while preserving legacy compatibility exports for downstream consumers.

- `bin/fetch_nextdns_summary.py`
  Optional local NextDNS analytics summary fetcher. Uses Python standard library only.

- `viz/nextdns_summary.json`
  Generated local DNS summary. It may include aggregate top-N domain names from analytics endpoints, but must not contain API keys, raw logs, client IPs, device names, per-query records, or full profile IDs.

- `bin/fetch_cloudflare_radar.py`
  Optional local Cloudflare Radar Internet Conditions fetcher. Uses Python standard library only, can load `CLOUDFLARE_API_TOKEN` from the process environment or a local `.env.cloudflare` file, and optionally supports an explicitly configured provider ASN for provider-scoped traffic anomaly checks.

- `viz/internet_conditions.json`
  Generated local Internet Conditions summary for dashboard context. By default it remains US-scoped. If an explicit provider ASN is configured, the script targets Cloudflare Radar traffic anomalies for that ASN and falls back to the US-scoped check if the ASN query fails. If the token is missing or Cloudflare Radar is unavailable, the script writes an `unavailable` artifact instead of failing the dashboard.

- `bin/fetch_aps_power_context.py`
  Optional local APS Power Infrastructure summary fetcher. Uses public APS outage-viewer data and Python standard library only.

- `viz/aps_power_context.json`
  Generated local APS Power Infrastructure summary for dashboard context. If APS data is unavailable or malformed, the script writes an `unavailable` artifact instead of failing the dashboard.

- `bin/build_investigation.py`
  Builds a local read-only investigation JSON for a historical time window using existing telemetry files and additive Observation references. The output remains evidence-first and does not move Core Signal interpretation into Prime Observer. It also updates a generated investigation catalog by default.

- `viz/investigation.json`
  Generated local investigation evidence for a selected window. Metadata is additive and includes target labels/classes, deterministic event navigation, factual nearby-event discovery, overlapping Observation references from the current projection when available, and optional copied provider-specific context such as local DNS summary or Internet Conditions evidence snapshots.

- `viz/investigation_index.json`
  Generated local investigation catalog. Entries summarize available investigations with an ID, title, creation time, event count, status, and output path.

- `bin/build_operator_assistant_input.py`
  Builds a compact deterministic evidence package from `viz/investigation.json` for future operator-assistant interpretation experiments. The output stays bounded, evidence-only, and additive; it does not call any model and does not change Prime Observer's existing investigation contract.

- `viz/operator_assistant_input.json`
  Generated local operator-assistant evidence package. It preserves requested-window metadata, current and window attribution scope, overlapping episode observations, bounded during-window evidence summaries, and optional environmental-context summaries without copying the full investigation artifact.

- `viz/investigate.html`
  Static historical evidence view for `viz/investigation.json`.

- `viz/index.html`
  Static D3 dashboard. Loads local CSV and JSON files with `cache: "no-store"` and renders the observability UI.

## Data Sources

Monitored LAN target:

- `192.168.1.1`

Monitored internet probe targets:

- `1.1.1.1` - Cloudflare
- `9.9.9.9` - Quad9

Monitored resolver probe targets:

- `45.90.28.134` - NextDNS primary
- `45.90.30.134` - NextDNS secondary

Target classes in generated files:

- `internet_probe` for general WAN/path reachability probes
- `resolver_probe` for configured DNS resolver path probes
- `gateway_probe` for the local LAN gateway
- `unknown_probe` when a target cannot be safely classified

New telemetry collection includes both NextDNS resolver probes. Existing older
telemetry files that only contain raw IP targets remain compatible; transform
and investigation generation add target metadata when possible.

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

This refreshes:

- `viz/latest.csv`
- `viz/network_attribution.json`
- `viz/observations.json`

If using NextDNS, generate the optional DNS summary:

```bash
python3 bin/fetch_nextdns_summary.py
```

If using Cloudflare Radar, generate the optional Internet Conditions summary:

```bash
python3 bin/fetch_cloudflare_radar.py
```

If using APS Power Infrastructure context, generate the optional provider summary:

```bash
python3 bin/fetch_aps_power_context.py
```

For automated macOS refresh of the scheduled optional context artifacts, use the LaunchAgent documented in `docs/nextdns-launchagent.md`. It runs `bin/refresh_optional_context.sh`, which refreshes NextDNS summary context, Cloudflare Radar Internet Conditions, and APS Power Infrastructure context without storing tokens in the plist.

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

See `docs/investigation-workflow.md` for details.

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

It also includes compact evidence fields for direct consumers:

```json
{
  "generated_at": "2026-06-15T18:02:09Z",
  "window": "-24h",
  "status": "ok",
  "summary": {
    "queries": 94818,
    "blocked": 2823,
    "blocked_percent": 3.0,
    "encrypted_percent": 13.0,
    "top_queries": [],
    "top_blocked": []
  }
}
```

Historical investigations copy a factual `dns_context` from this generated file when it is available. That context is evidence only; it does not classify DNS activity, assess threats, recommend action, or infer confidence.

Security rules:

- Do not commit `.env.nextdns`.
- Do not put secrets in `viz/index.html`.
- Do not expose API keys in generated JSON.
- Do not include raw DNS logs, client IPs, device names, per-query records, user attribution, or full profile IDs.
- Use NextDNS analytics endpoints only; the dashboard must not call the NextDNS API directly.
- The dashboard must continue working if NextDNS data is missing, stale, invalid, or unavailable.

## Optional Cloudflare Radar Configuration

Cloudflare Radar support is optional.

Configuration can come from process environment variables or a local `.env.cloudflare` file. Process environment values win over `.env.cloudflare`.

Create a token in Cloudflare:

1. Log into Cloudflare.
2. Open API Tokens.
3. Create a Custom Token.
4. Add permission: Account -> Radar -> Read.
5. Scope it to the current account or the smallest scope Cloudflare allows.
6. Optionally set a TTL and restrict client IPs if practical.
7. Copy the token once and store it locally.

Required:

```bash
CLOUDFLARE_API_TOKEN=replace-with-token
```

Optional:

```bash
CLOUDFLARE_RADAR_DATE_RANGE=7d
CLOUDFLARE_RADAR_TIMEOUT_SECONDS=8
CLOUDFLARE_RADAR_LIMIT=10
```

Optional ISP-scoped Internet Conditions:

```bash
# .env.cloudflare
PRIME_OBSERVER_INTERNET_ASN=22773
PRIME_OBSERVER_INTERNET_PROVIDER_LABEL=Cox
```

Usage notes:

- `.env.example` contains placeholder values only. Copy it to `.env.cloudflare` for local use.
- Do not commit `.env.cloudflare`.
- Do not put Cloudflare tokens in browser code or generated artifacts.
- `PRIME_OBSERVER_INTERNET_ASN` and `PRIME_OBSERVER_INTERNET_PROVIDER_LABEL` are optional. Prime Observer does not require them.
- If both optional ASN settings are omitted, Internet Conditions stays in the current US-scoped mode.
- `PRIME_OBSERVER_INTERNET_ASN` enables explicit ASN-scoped traffic anomaly checks only. Prime Observer does not attempt automatic public-IP or ISP discovery.
- `PRIME_OBSERVER_INTERNET_PROVIDER_LABEL` is optional but recommended when ASN mode is used so operator-facing diagnostics can show a label such as `Cox` instead of a generic network label.
- If the configured ASN query fails, `bin/fetch_cloudflare_radar.py` falls back to the existing US-scoped Internet Conditions behavior and marks the artifact with fallback metadata.
- If the token is missing, `bin/fetch_cloudflare_radar.py` writes an `unavailable` `viz/internet_conditions.json` artifact and exits successfully.
- The scheduled macOS refresh path also works with the repo-local `.env.cloudflare` file because `bin/fetch_cloudflare_radar.py` loads it directly. Do not put the token in a plist or shell profile just for Prime Observer.

Historical investigations may also copy a factual `internet_conditions_context`
from this generated file when it is available. That context is the closest
locally generated Environmental Context snapshot only; it does not provide
historical proof, attribution, noticeability, health changes, or investigation
scoring.

## Optional APS Power Infrastructure Context

APS Power Infrastructure support is optional.

The provider uses public APS outage-viewer data. No local secrets or browser
network calls are required.

Usage notes:

- `bin/fetch_aps_power_context.py` writes `viz/aps_power_context.json`.
- If the APS provider is unavailable or returns malformed data, the script
  writes an `unavailable` artifact and exits successfully.
- The dashboard reads only the generated local artifact and hides the Power
  Infrastructure card when the artifact is unavailable.
- Historical investigations may also copy a factual
  `power_infrastructure_context` from this generated file when it is available.
  That context is the closest locally generated provider snapshot only; it does
  not provide historical proof, attribution, noticeability, health changes, or
  investigation scoring.

## Design Principles

Prime Observer favors:

- deterministic local logic
- deterministic health modeling
- simple heuristics
- transparent calculations
- flat CSV/JSON files
- small, reviewable changes
- graceful failure of optional features
- evidence-first architecture
- privacy-first local execution
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

## Release Notes: v0.8.0

v0.8.0 prepares Prime Observer's initial public Observation architecture release without changing deferred areas such as Noticeability, Pattern Awareness, health summaries, or selected-bucket evidence.

v0.8.0 includes:

- Introduced the Observation domain model for deterministic Prime Observer interpretation.
- Added Observation materialization policy and stable projection metadata.
- Added attribution Observation projection backed by `viz/observations.json`.
- Added episode Observation projection for sustained-degradation and turbulence intervals.
- Added additive Observation references to historical investigations.
- Updated the dashboard to source attribution from Observations first, with legacy export and browser fallbacks preserved.
- Updated the dashboard to source episode state from Observations first, with deterministic browser classification retained as fallback.
- Preserved backward-compatible legacy exports for downstream consumers.
- Kept Evidence authoritative for measured facts and Investigations authoritative for evidence organization.
- Continued to exclude Core Signal interpretation, recommendations, causal claims, cloud dependencies, and browser-side secrets.

## Release Notes: v0.7.2

v0.7.2 refined the dashboard presentation and production-scanning experience before the Observation release.

v0.7.2 included:

- Refreshed the dashboard and investigation UI for a clearer observability-focused experience.
- Improved mobile dashboard scanning and compact current-state presentation.
- Preserved the deterministic health model, evidence-first investigation workflow, and local-first DNS summary boundaries.

## Release Notes: v0.7.1

v0.7.1 stabilizes the canonical health model and attribution evidence
calibration introduced after v0.7.0 while preserving Prime Observer's
evidence-first responsibility boundary.

v0.7.1 includes:

- Calibrated attribution evidence weighting so WAN evidence is not automatically overridden by LAN elevation.
- Added a Mixed Evidence attribution state for balanced WAN and LAN evidence.
- Added attribution evidence transparency through exported evidence counts and target-group summaries.
- Added synchronized cross-chart investigation navigation from selected heatmap buckets.
- Added factual bucket evidence summaries for internet probes, resolver probes, and LAN gateway samples.
- Introduced canonical health model documentation in `docs/health-model.md`.
- Documented the health-model consistency audit in `docs/health-model-audit.md`.
- Aligned heatmap, User Noticeability, attribution, and investigations around the same WAN target-group model.
- Improved operator trust through health-model consistency while preserving deterministic local evidence.
- Preserved strict evidence-first architecture: Prime Observer provides observations, evidence, investigations, timelines, target classification, and external evidence collection only.
- Excluded DNS analysis, DNS attribution, recommendations, event confidence scores, causal analysis, anomaly analysis, interpretive conclusions, and LLM functionality.

## Release Boundaries

v0.8.0 intentionally does not migrate or expand:

- User Noticeability semantics
- Pattern Awareness semantics
- WAN Health Summary semantics
- selected-bucket evidence behavior
- raw DNS logs
- device-level DNS analytics
- alerts and notifications
- weather, power, or ISP status correlation
- LLM explanations
- major dashboard refactors

## License

Prime Observer is released under the MIT License.

See the LICENSE file for details.
