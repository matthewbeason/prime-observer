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
```

`viz/investigation.json` is local generated output and should not be committed.

## View The Evidence

Serve the dashboard files locally:

```bash
python3 -m http.server 8000 --directory viz
```

Open:

```text
http://localhost:8000/investigate.html
```

The page loads `investigation.json` with `cache: "no-store"` and renders:

- event start, end, duration, and context window
- before, during, and after WAN/LAN summaries
- WAN p95, jitter, loss, sample counts, bad counts, and turbulence buckets
- representative timeline samples
- local generated NextDNS context, when available
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
  "timeline_samples": [],
  "dns_context": {},
  "sources": {}
}
```

## Privacy

The investigation builder reads local historical telemetry files and writes a
local generated JSON file. It does not call external services and does not read
raw DNS logs.

DNS context, when present, is copied only from the existing generated
`viz/nextdns_summary.json` fields. It does not include API keys, full profile
IDs, client IPs, local IPs, device names, user attribution, per-query records, or
raw DNS logs.

## Boundary Rules

Allowed wording for consumers:

- WAN p95 increased during the window.
- LAN samples remained below threshold.
- Packet loss was 0% during the window.
- DNS summary closest to the event showed X total queries.

Avoid interpretive wording in Prime Observer:

- The ISP caused this.
- The VPN caused this.
- This was due to a specific application.
- Users were impacted.
- Recommendation: change provider.
