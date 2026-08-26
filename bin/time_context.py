#!/usr/bin/env python3
"""Python-owned selected time context artifact for Prime Observer."""

import datetime as dt
import json
from pathlib import Path

from external_context_history import (
    align_events_to_interval,
    canonical_events_from_snapshot,
    merge_event_observations,
)


SCHEMA_VERSION = 2
MODEL_VERSION = "prime_observer.time_context.v2"

# Canonical labels for each external-context provider, keyed by the provider
# field emitted by the respective fetch scripts.
EXTERNAL_CONTEXT_SOURCE_LABELS = {
    "cloudflare_radar": "Cloudflare Radar",
    "aps": "APS",
}


def overlapping_external_sources(aligned_events):
    """Return source labels only for producer-confirmed provider-time overlap."""
    seen: set = set()
    sources = []
    for event in aligned_events or []:
        if not isinstance(event, dict) or event.get("relationship") != "overlaps":
            continue
        raw_provider = str(event.get("provider") or "").strip().lower()
        label = EXTERNAL_CONTEXT_SOURCE_LABELS.get(raw_provider)
        if not label or label in seen:
            continue
        sources.append(label)
        seen.add(label)
    return sources


def parse_ts(value):
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def iso(value):
    return value.isoformat() if isinstance(value, dt.datetime) else None


def interval_overlaps(a_start, a_end, b_start, b_end):
    return bool(a_start and a_end and b_start and b_end and a_start < b_end and b_start < a_end)


def incident_window(incident):
    return (
        parse_ts(incident.get("start") or incident.get("first_anomalous_at")),
        parse_ts(incident.get("end") or incident.get("last_anomalous_at") or incident.get("recovered_at")),
    )


def build_time_context(*, start, end, generated_at, incidents=None, selected_incident_id=None, mode="current", external_contexts=None, external_events=None):
    start_ts = parse_ts(start)
    end_ts = parse_ts(end)
    generated_ts = parse_ts(generated_at) or dt.datetime.now(dt.timezone.utc)
    if start_ts is None or end_ts is None or end_ts <= start_ts:
        start_ts = generated_ts
        end_ts = generated_ts

    overlapping_incident = None
    for incident in incidents or []:
        inc_start, inc_end = incident_window(incident or {})
        if interval_overlaps(start_ts, end_ts, inc_start, inc_end):
            overlapping_incident = incident
            break

    canonical_events = list(external_events or [])
    for snapshot in external_contexts or []:
        canonical_events.extend(canonical_events_from_snapshot(snapshot))
    canonical_events = merge_event_observations([], canonical_events)
    aligned_external_events = align_events_to_interval(canonical_events, start_ts, end_ts)
    ext_sources = overlapping_external_sources(aligned_external_events)
    external_overlap = bool(ext_sources)

    incident_id = None
    if isinstance(overlapping_incident, dict):
        incident_id = overlapping_incident.get("id") or overlapping_incident.get("incident_id")

    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "mode": mode,
        "start": iso(start_ts),
        "end": iso(end_ts),
        "selected_incident_id": selected_incident_id,
        "overlaps_incident": bool(overlapping_incident),
        "incident_id": incident_id,
        "overlaps_external_context": external_overlap,
        "overlapping_external_event_sources": ext_sources,
        "external_events": aligned_external_events,
        "external_context_note": "Provider event-time overlap is supporting evidence only; it does not determine health, attribution, incident eligibility, impact, or causality.",
        "generated_at": iso(generated_ts),
    }


def load_json(path):
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Build Prime Observer time context artifact.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", default="viz/time_context.json")
    args = parser.parse_args(argv)
    payload = build_time_context(start=args.start, end=args.end, generated_at=dt.datetime.now(dt.timezone.utc))
    out = Path(args.out)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(out)


if __name__ == "__main__":
    main()
