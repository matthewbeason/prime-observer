#!/usr/bin/env python3
"""Backend-neutral historical-event preparation for external context.

This module intentionally owns no persistence. It normalizes current provider
snapshots into a small canonical contract, folds repeated observations in
memory, and aligns already-collected canonical events with Prime intervals.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json


SCHEMA_VERSION = 1
MODEL_VERSION = "prime_observer.external_context_event.v1"
PROVIDER_LABELS = {
    "cloudflare_radar": "Cloudflare Radar",
    "aps": "APS",
}


def parse_ts(value):
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_utc(value):
    parsed = parse_ts(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def _clean(value):
    return " ".join(str(value or "").strip().lower().split())


def _fingerprint(provider, event_type, fields):
    material = json.dumps(
        [provider, event_type, *[_clean(value) for value in fields]],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _stable_key(provider, event_type, provider_id, fallback_fields):
    native = str(provider_id if provider_id is not None else "").strip()
    if native:
        return f"{provider}:{event_type}:id:{native}", "provider_id"
    return (
        f"{provider}:{event_type}:fp:{_fingerprint(provider, event_type, fallback_fields)}",
        "stable_field_fingerprint",
    )


def _first_identifier(*values):
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def _temporal_fields(event_start, event_end):
    if event_start and event_end:
        return "provider_reported_timestamp", "bounded"
    if event_start:
        return "provider_reported_timestamp", "start_only"
    return "observation_time_only", "snapshot_only"


def _canonical_event(
    *,
    provider,
    event_type,
    provider_id,
    fallback_fields,
    event_start,
    event_end,
    collection_time,
    provider_updated_at,
    status,
    label,
    detail,
    provenance,
    limitations,
):
    event_start = iso_utc(event_start)
    event_end = iso_utc(event_end)
    collection_time = iso_utc(collection_time)
    provider_updated_at = iso_utc(provider_updated_at)
    temporal_precision, temporal_status = _temporal_fields(event_start, event_end)
    stable_event_key, identity_basis = _stable_key(
        provider, event_type, provider_id, fallback_fields
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "provider": provider,
        "event_type": event_type,
        "stable_event_key": stable_event_key,
        "identity_basis": identity_basis,
        "event_start": event_start,
        "event_end": event_end,
        "observed_first": collection_time,
        "observed_last": collection_time,
        "prime_collection_time": collection_time,
        "provider_updated_at": provider_updated_at,
        "temporal_precision": temporal_precision,
        "temporal_status": temporal_status,
        "status": status,
        "resolution": "provider_ended" if event_end else "currently_observed",
        "label": label,
        "supporting_detail": detail,
        "provenance": provenance,
        "limitations": list(limitations),
    }


def _cloudflare_event(item, collection_time):
    signal = str(item.get("signal") or "internet_condition").strip()
    provider_id = _first_identifier(
        item.get("provider_event_id"),
        item.get("uuid"),
        item.get("event_id"),
    )
    started = item.get("started")
    ended = item.get("ended")
    label = str(item.get("description") or signal.replace("_", " ").title()).strip()
    status = "ended" if ended or item.get("finished") is True else "reported"
    limitations = [
        "Provider event timing is supporting context and does not establish causality."
    ]
    if not started:
        limitations.append(
            "No provider event start is available; this observation cannot be historically aligned."
        )
    elif not ended:
        limitations.append(
            "No provider event end is available; duration beyond the reported start is unknown."
        )
    if not provider_id:
        limitations.append(
            "No provider ID was available; identity uses start, reference, region, and signal, so provider edits may change the key and sparse records may collide."
        )
    detail = {
        key: item.get(key)
        for key in (
            "region",
            "reference",
            "entity_type",
            "event_status",
            "finished",
            "detected_at",
            "leak_asn",
            "countries",
            "peer_count",
            "prefix_count",
            "origin_count",
            "leak_count",
        )
        if item.get(key) is not None
    }
    return _canonical_event(
        provider="cloudflare_radar",
        event_type=signal,
        provider_id=provider_id,
        fallback_fields=(iso_utc(started), item.get("reference"), item.get("region"), signal),
        event_start=started,
        event_end=ended,
        collection_time=collection_time,
        provider_updated_at=item.get("provider_updated_at"),
        status=status,
        label=label,
        detail=detail,
        provenance={
            "source_artifact": "viz/internet_conditions.json",
            "provider_event_id": str(provider_id) if provider_id is not None else None,
        },
        limitations=limitations,
    )


def _aps_event(item, collection_time, provider_updated_at):
    event_type = str(item.get("event_type") or "power_event").strip()
    provider_id = _first_identifier(item.get("provider_event_id"), item.get("ticket"))
    event_start = item.get("event_start")
    area = str(item.get("affected_area") or "APS service territory").strip()
    label = f"APS {event_type.replace('_', ' ')} reported for {area}"
    limitations = [
        "APS context is supporting evidence and does not establish causality.",
        "Estimated restoration is a forecast and is not an event end timestamp.",
    ]
    if not event_start:
        limitations.append(
            "APS supplied no outage start; this current-map observation cannot be historically aligned."
        )
    else:
        limitations.append(
            "APS supplied no actual event end; duration after the outage start is unknown."
        )
    if not provider_id:
        limitations.append(
            "No APS ticket was available; identity uses layer, event type, outage start, and area, so area-label edits may change the key and same-area events may collide."
        )
    detail = {
        key: item.get(key)
        for key in (
            "affected_area",
            "customer_count",
            "estimated_restoration_time",
            "source_reference",
            "source_layer",
            "provider_status",
            "data_status",
        )
        if item.get(key) is not None
    }
    return _canonical_event(
        provider="aps",
        event_type=event_type,
        provider_id=provider_id,
        fallback_fields=(
            item.get("source_layer"),
            event_type,
            iso_utc(event_start),
            area,
        ),
        event_start=event_start,
        event_end=None,
        collection_time=collection_time,
        provider_updated_at=provider_updated_at,
        status="reported",
        label=label,
        detail=detail,
        provenance={
            "source_artifact": "viz/aps_power_context.json",
            "provider_event_id": str(provider_id) if provider_id is not None else None,
        },
        limitations=limitations,
    )


def canonical_events_from_snapshot(payload):
    """Normalize one current provider artifact without writing history."""
    if not isinstance(payload, dict) or payload.get("status") in {None, "unavailable"}:
        return []
    provider = str(payload.get("provider") or "").strip().lower()
    collection_time = payload.get("generated_at")
    if parse_ts(collection_time) is None:
        return []
    items = list(payload.get("items")) if isinstance(payload.get("items"), list) else []
    if provider == "cloudflare_radar":
        signal_results = payload.get("signal_results")
        if isinstance(signal_results, dict):
            for result in signal_results.values():
                if isinstance(result, dict) and isinstance(result.get("items"), list):
                    items.extend(result["items"])
        events = [_cloudflare_event(item, collection_time) for item in items if isinstance(item, dict)]
    elif provider == "aps":
        events = [
            _aps_event(item, collection_time, payload.get("provider_updated_at"))
            for item in items
            if isinstance(item, dict)
        ]
    else:
        return []
    return merge_event_observations([], events)


def merge_event_observations(existing, observed):
    """Fold observations by stable key; callers choose and own the backend."""
    merged = {
        item["stable_event_key"]: dict(item)
        for item in existing or []
        if isinstance(item, dict) and item.get("stable_event_key")
    }
    for observation in observed or []:
        if not isinstance(observation, dict) or not observation.get("stable_event_key"):
            continue
        key = observation["stable_event_key"]
        prior = merged.get(key)
        if prior is None:
            merged[key] = dict(observation)
            continue
        updated = dict(prior)
        for field in (
            "event_start",
            "event_end",
            "provider_updated_at",
            "temporal_precision",
            "temporal_status",
            "status",
            "resolution",
            "label",
            "supporting_detail",
            "provenance",
            "limitations",
        ):
            if observation.get(field) is not None:
                updated[field] = observation.get(field)
        first_values = [parse_ts(prior.get("observed_first")), parse_ts(observation.get("observed_first"))]
        last_values = [parse_ts(prior.get("observed_last")), parse_ts(observation.get("observed_last"))]
        first_values = [value for value in first_values if value]
        last_values = [value for value in last_values if value]
        updated["observed_first"] = iso_utc(min(first_values)) if first_values else None
        updated["observed_last"] = iso_utc(max(last_values)) if last_values else None
        updated["prime_collection_time"] = observation.get("prime_collection_time")
        merged[key] = updated
    return sorted(merged.values(), key=lambda event: event["stable_event_key"])


def mark_absent_from_complete_snapshot(existing, *, provider, observed_keys, collection_time):
    """Record disappearance without inventing a provider event end."""
    observed_keys = set(observed_keys or [])
    result = []
    for event in existing or []:
        updated = dict(event)
        if updated.get("provider") == provider and updated.get("stable_event_key") not in observed_keys:
            updated["status"] = "absent_later"
            updated["resolution"] = "absent_from_later_complete_snapshot"
            updated["prime_collection_time"] = iso_utc(collection_time)
            limitations = list(updated.get("limitations") or [])
            note = "Disappearance from a later complete provider response is not proof of the actual event end."
            if note not in limitations:
                limitations.append(note)
            updated["limitations"] = limitations
        result.append(updated)
    return result


def align_event_to_interval(event, interval_start, interval_end):
    start = parse_ts(interval_start)
    end = parse_ts(interval_end)
    event_start = parse_ts(event.get("event_start"))
    event_end = parse_ts(event.get("event_end"))
    observed_first = parse_ts(event.get("observed_first"))
    observed_last = parse_ts(event.get("observed_last"))
    if not start or not end or end <= start:
        return None
    if not event_start:
        relationship = "snapshot_only"
        basis = "prime_observation_only"
    elif event_end:
        if event_start < end and start < event_end:
            relationship = "overlaps"
        elif event_end <= start:
            relationship = "precedes"
        else:
            relationship = "follows"
        basis = "provider_event_range"
    elif start <= event_start < end:
        relationship = "overlaps"
        basis = "provider_event_start_within_interval"
    elif (
        (observed_first and start <= observed_first < end)
        or (observed_last and start <= observed_last < end)
    ):
        relationship = "overlaps"
        basis = "prime_observation_within_interval"
    elif event_start >= end:
        relationship = "follows"
        basis = "provider_event_start"
    else:
        relationship = "uncertain"
        basis = "provider_event_start_without_end"
    return {
        "provider": event.get("provider"),
        "provider_label": PROVIDER_LABELS.get(event.get("provider"), event.get("provider")),
        "event_type": event.get("event_type"),
        "stable_event_key": event.get("stable_event_key"),
        "relationship": relationship,
        "alignment_basis": basis,
        "event_start": event.get("event_start"),
        "event_end": event.get("event_end"),
        "observed_first": event.get("observed_first"),
        "observed_last": event.get("observed_last"),
        "temporal_status": event.get("temporal_status"),
        "status": event.get("status"),
        "label": event.get("label"),
        "limitations": list(event.get("limitations") or []),
    }


def align_events_to_interval(events, interval_start, interval_end):
    aligned = [
        align_event_to_interval(event, interval_start, interval_end)
        for event in events or []
        if isinstance(event, dict)
    ]
    return sorted(
        [item for item in aligned if item is not None],
        key=lambda item: (item["relationship"], item.get("provider") or "", item.get("stable_event_key") or ""),
    )
