# Historical Investigation Workflow

Prime Observer can generate a read-only evidence view for a historical telemetry
window. The workflow is intended for questions like:

> Show me what happened between 17:30 and 18:00.

Prime Observer owns the local telemetry evidence and timeline. Core Signal owns
interpretation, pattern reasoning, correlations, and recommendations. Olivaw can
eventually navigate to this evidence view, but it should not duplicate the view.

## Generate An Investigation

Run from the repository root:

```bash
python3 bin/build_investigation.py \
  --start 2026-05-30T17:30:00-07:00 \
  --end 2026-05-30T18:00:00-07:00
```

The default context window is 30 minutes before and 30 minutes after the event.
Use `--pad-minutes` to change that.

```bash
python3 bin/build_investigation.py \
  --start 2026-05-30T17:30:00-07:00 \
  --end 2026-05-30T18:00:00-07:00 \
  --pad-minutes 45
```

The generated output is:

```text
viz/investigation.json
viz/investigation_index.json
```

`viz/investigation.json` and `viz/investigation_index.json` are local generated
outputs and should not be committed.

`viz/investigation_index.json` is an additive catalog of generated
investigations. Each entry contains:

- `id`
- `title`
- `created_at`
- `event_count`
- `status`
- `path`

Use `--no-index` to generate only `viz/investigation.json`.

## View The Evidence

Serve the dashboard files locally from the repository root:

```bash
python3 -m http.server 8000 --directory viz
```

Open:

```text
http://localhost:8000/investigate.html
```

Do not open `viz/investigate.html` directly from disk. The investigation view
loads `investigation.json` through browser `fetch`, and direct `file://` access
can prevent the JSON file from loading.

The page loads `investigation.json` with `cache: "no-store"` and renders:

- event start, end, duration, and context window
- before, during, and after WAN/LAN summaries
- factual target classes and labels for internet, resolver, and gateway probes
- WAN p95, jitter, loss, sample counts, bad counts, and turbulence buckets by target group where available
- deterministic event navigation metadata
- factual nearby-event metadata
- representative timeline samples
- local generated NextDNS context, when available
- local generated Internet Conditions context, when available
- telemetry source files used

## Deep-Link Shape

The page accepts future URL parameters:

```text
http://localhost:8000/investigate.html?start=2026-05-30T17:30:00-07:00&end=2026-05-30T18:00:00-07:00
```

For the first implementation, the URL parameters are displayed as the requested
window while the evidence still comes from `viz/investigation.json`. A future
Olivaw integration can generate the JSON first, then link to the page with the
same start/end parameters.

## Output Shape

The JSON uses this high-level structure:

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-08T18:00:00+00:00",
  "input": {
    "start": "2026-05-30T17:30:00-07:00",
    "end": "2026-05-30T18:00:00-07:00",
    "pad_minutes": 30
  },
  "event_window": {
    "start": "2026-05-31T00:30:00+00:00",
    "end": "2026-05-31T01:00:00+00:00",
    "duration_minutes": 30.0
  },
  "periods": {
    "before": {},
    "during": {},
    "after": {}
  },
  "target_groups": {},
  "events": [],
  "navigation": {
    "first_event": null,
    "last_event": null,
    "events": {}
  },
  "event_neighborhoods": [],
  "timeline_samples": [],
  "dns_context": {},
  "internet_conditions_context": {},
  "sources": {}
}
```

The `target_groups`, `target_class`, `target_label`, `events`, `navigation`,
and `event_neighborhoods` fields are additive. Older consumers that read the
original v0.5.0 fields can continue to use the export without changes.

## Target Classes

Investigation evidence includes factual target metadata:

- Cloudflare `1.1.1.1` and Quad9 `9.9.9.9` are `internet_probe` targets.
- NextDNS `45.90.28.134` and `45.90.30.134` are `resolver_probe` targets.
- The local gateway is a `gateway_probe`.
- Unknown targets are retained only when safely usable and labeled `unknown_probe`.

These classes help the evidence view show whether bad samples came from general
WAN/path probes, resolver-path probes, or the LAN gateway. They are not causal
interpretation. Core Signal remains responsible for interpreting events and
making recommendations.

## Event Navigation And Neighborhoods

Investigation events are sorted deterministically by start time, end time, and
event ID. The export includes:

- `first_event`
- `last_event`
- per-event `previous_event`
- per-event `next_event`

Event neighborhood discovery is factual only. It may report:

- temporal proximity
- shared investigation membership
- shared evidence windows

It must not report causality, confidence, recommendations, correlations, or
interpretations. A nearby event means only that the generated evidence file
contains events near each other or in the same investigation context.

## Privacy

The investigation builder reads local historical telemetry files and writes a
local generated JSON file. It does not call external services and does not read
raw DNS logs.

DNS context, when present, is copied only from the existing generated
`viz/nextdns_summary.json` fields. It does not include API keys, full profile
IDs, client IPs, local IPs, device names, user attribution, per-query records, or
raw DNS logs.

The copied context is factual and summary-only. It may include total query
counts, blocked query counts, blocked percentage, encrypted percentage,
redacted or aggregate top-domain rows, generation time, window, and profile ID
suffix. It must not include threat analysis, suspicious-activity labels,
recommendations, confidence assessments, or causal explanation.

Internet Conditions context, when present, is copied only from the existing
generated `viz/internet_conditions.json` fields. It represents the closest
available locally generated Environmental Context snapshot, not historical
proof of what happened during the event window.

The copied Internet Conditions context is supporting evidence only. It may
include provider, generated time, status, summary, scope, signals checked,
bounded items, source file, and minutes from the event midpoint. It must not
change attribution, health calculations, noticeability, or investigation
scoring, and it must not be described as causal proof.

## Boundary Rules

Allowed wording for consumers:

- WAN p95 increased during the window.
- Resolver probe p95 increased during the window.
- Internet probes remained below degradation thresholds during the window.
- LAN samples remained below threshold.
- Packet loss was 0% during the window.
- DNS summary closest to the event showed X total queries.
- Internet Conditions closest to the event reported a United States outage advisory.
- A WAN bucket observation was near the requested investigation window.
- Two events share the same generated investigation file.

Avoid interpretive wording in Prime Observer:

- The ISP caused this.
- The VPN caused this.
- This was due to a specific application.
- Users were impacted.
- Recommendation: change provider.
- This event is correlated with another event.
