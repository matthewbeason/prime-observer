#!/usr/bin/env python3
"""Canonical projection over immutable completed-investigation snapshots."""

from pathlib import Path
import copy
import datetime as dt
import json


CATALOG_SCHEMA_VERSION = 2
IDENTITY_VERSION = "completed-incident.v1"
UTC = dt.timezone.utc


def parse_timestamp(value):
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_iso(value):
    parsed = parse_timestamp(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None


def canonical_completed_event_id(target_class, recovered_at):
    target = str(target_class or "").strip().lower()
    recovered = parse_timestamp(recovered_at)
    if not target or recovered is None:
        return None
    stamp = recovered.isoformat().replace("+00:00", "Z")
    safe_target = "".join(ch if ch.isalnum() else "-" for ch in target).strip("-")
    safe_stamp = "".join(ch if ch.isalnum() else "-" for ch in stamp).strip("-").lower()
    return f"completed-v1-{safe_target}-{safe_stamp}"


def selected_event(snapshot):
    return snapshot.get("selected_event") if isinstance(snapshot, dict) and isinstance(snapshot.get("selected_event"), dict) else {}


def snapshot_interval(snapshot):
    selected = selected_event(snapshot)
    record = snapshot.get("incident_record") if isinstance(snapshot, dict) and isinstance(snapshot.get("incident_record"), dict) else {}
    start = parse_timestamp(
        selected.get("first_anomalous_at")
        or selected.get("start")
        or record.get("started_at")
        or record.get("first_seen")
    )
    end = parse_timestamp(
        selected.get("last_anomalous_at")
        or selected.get("end")
        or record.get("latest_affected_at")
        or selected.get("recovered_at")
    )
    return start, end


def intervals_overlap(records):
    starts = [item["interval_start"] for item in records]
    ends = [item["interval_end"] for item in records]
    if any(value is None for value in starts + ends):
        return False
    return max(starts) <= min(ends)


def invalid_entry(path, error_type, error_message, detected_at=None):
    return {
        "snapshot_path": f"investigations/{path.name}",
        "event_id": path.stem or None,
        "error_type": error_type,
        "error_message": error_message,
        "detected_at": utc_iso(detected_at),
    }


def load_snapshot(path):
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        return None, "unreadable", str(exc)
    except json.JSONDecodeError as exc:
        return None, "malformed_json", str(exc)
    if not isinstance(payload, dict):
        return None, "structurally_invalid", "Snapshot JSON root is not an object."
    selected = selected_event(payload)
    if selected.get("lifecycle_state") != "complete" or not selected.get("id"):
        return None, "structurally_invalid", "Snapshot does not contain a completed selected_event with an id."
    if payload.get("artifact_type") not in {None, "completed_investigation_snapshot"}:
        return None, "structurally_invalid", f"Unsupported artifact_type: {payload.get('artifact_type')}."
    return payload, None, None


def publication_sort_key(record):
    snapshot = record["snapshot"]
    written = parse_timestamp(snapshot.get("snapshot_written_at"))
    generated = parse_timestamp(snapshot.get("generated_at"))
    return (written or generated or dt.datetime.max.replace(tzinfo=UTC), record["snapshot_path"])


def canonical_snapshot(snapshot, canonical_id):
    projected = copy.deepcopy(snapshot)
    selected = selected_event(projected)
    selected["id"] = canonical_id
    record = projected.get("incident_record") if isinstance(projected.get("incident_record"), dict) else None
    if record is not None:
        record["incident_id"] = canonical_id
    projected["canonical_completed_event_id"] = canonical_id
    return projected


def event_entry(record, records, canonical_id, recovered_at):
    snapshot = record["snapshot"]
    selected = selected_event(snapshot)
    starts = sorted(item["interval_start"] for item in records if item["interval_start"] is not None)
    legacy_ids = [item["legacy_event_id"] for item in records]
    return {
        "event_id": canonical_id,
        "canonical_event_id": canonical_id,
        "identity_version": IDENTITY_VERSION,
        "legacy_event_id": record["legacy_event_id"],
        "legacy_event_ids": legacy_ids,
        "lifecycle": "complete",
        "first_anomalous_at": utc_iso(record["interval_start"]),
        "recovered_at": utc_iso(recovered_at),
        "severity": selected.get("severity"),
        "confidence": selected.get("confidence"),
        "target_class": selected.get("target_class"),
        "affected_targets": selected.get("affected_targets") or [],
        "duration": round((recovered_at - record["interval_start"]).total_seconds() / 60.0, 1) if record["interval_start"] else None,
        "snapshot_path": record["snapshot_path"],
        "representative_snapshot_path": record["snapshot_path"],
        "representative_selected_by": "earliest_snapshot_written_at_then_generated_at_then_path",
        "physical_snapshot_count": len(records),
        "duplicate_alias_count": max(0, len(records) - 1),
        "reconstruction": {
            "count": len(records),
            "earliest_start": utc_iso(starts[0]) if starts else None,
            "latest_start": utc_iso(starts[-1]) if starts else None,
        },
        "evidence_scope": "representative_snapshot_only",
    }


def build_completed_history_projection(investigations_dir, generated_at=None, generator=None):
    root = Path(investigations_dir)
    physical_paths = sorted(root.glob("*.json")) if root.exists() else []
    invalid = []
    incomplete = []
    grouped = {}

    for path in physical_paths:
        snapshot, error_type, error_message = load_snapshot(path)
        if error_type:
            invalid.append(invalid_entry(path, error_type, error_message, generated_at))
            continue
        selected = selected_event(snapshot)
        target_class = str(selected.get("target_class") or "").strip()
        recovered = parse_timestamp(selected.get("recovered_at"))
        start, end = snapshot_interval(snapshot)
        identity_errors = []
        if not target_class:
            identity_errors.append("missing_target_class")
        if recovered is None:
            identity_errors.append("missing_or_invalid_recovered_at")
        if start is None:
            identity_errors.append("missing_or_invalid_interval_start")
        if end is None or (start is not None and end < start):
            identity_errors.append("missing_or_invalid_interval_end")
        if identity_errors:
            incomplete.append({
                "snapshot_path": f"investigations/{path.name}",
                "event_id": selected.get("id"),
                "target_class": target_class or None,
                "recovered_at": selected.get("recovered_at"),
                "identity_errors": identity_errors,
            })
            continue
        canonical_id = canonical_completed_event_id(target_class, recovered)
        item = {
            "snapshot_path": f"investigations/{path.name}",
            "path": path,
            "snapshot": snapshot,
            "legacy_event_id": selected.get("id"),
            "target_class": target_class,
            "recovered_at": recovered,
            "interval_start": start,
            "interval_end": end,
        }
        grouped.setdefault(canonical_id, []).append(item)

    canonical_events = []
    aliases = []
    conflicts = []
    canonical_snapshots = []
    for canonical_id, records in sorted(grouped.items()):
        records.sort(key=publication_sort_key)
        if not intervals_overlap(records):
            conflicts.append({
                "canonical_event_id": canonical_id,
                "identity_version": IDENTITY_VERSION,
                "target_class": records[0]["target_class"],
                "recovered_at": utc_iso(records[0]["recovered_at"]),
                "error_type": "identity_conflict",
                "error_message": "Snapshots share a completed identity but their degradation intervals do not overlap.",
                "records": [{
                    "legacy_event_id": item["legacy_event_id"],
                    "snapshot_path": item["snapshot_path"],
                    "interval_start": utc_iso(item["interval_start"]),
                    "interval_end": utc_iso(item["interval_end"]),
                } for item in records],
            })
            continue
        representative = records[0]
        canonical_events.append(event_entry(representative, records, canonical_id, representative["recovered_at"]))
        canonical_snapshots.append((
            representative["snapshot_path"],
            canonical_snapshot(representative["snapshot"], canonical_id),
            canonical_id,
        ))
        for item in records[1:]:
            aliases.append({
                "legacy_event_id": item["legacy_event_id"],
                "canonical_event_id": canonical_id,
                "snapshot_path": item["snapshot_path"],
                "representative_snapshot_path": representative["snapshot_path"],
                "evidence_scope": "original_snapshot_only",
            })

    canonical_events.sort(key=lambda item: (parse_timestamp(item.get("recovered_at")) or dt.datetime.min.replace(tzinfo=UTC), item["event_id"]), reverse=True)
    aliases.sort(key=lambda item: (item["legacy_event_id"], item["snapshot_path"]))
    invalid.sort(key=lambda item: item["snapshot_path"])
    incomplete.sort(key=lambda item: item["snapshot_path"])
    conflicts.sort(key=lambda item: item["canonical_event_id"])
    catalog = {
        "artifact_type": "investigation_catalog",
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at": utc_iso(generated_at),
        "generator": dict(generator or {}),
        "identity_version": IDENTITY_VERSION,
        "counts": {
            "physical_snapshots": len(physical_paths),
            "canonical_events": len(canonical_events),
            "duplicate_aliases": len(aliases),
            "invalid_snapshots": len(invalid),
            "identity_incomplete": len(incomplete),
            "identity_conflicts": len(conflicts),
        },
        "physical_snapshot_count": len(physical_paths),
        "canonical_event_count": len(canonical_events),
        "duplicate_alias_count": len(aliases),
        "canonical_events": canonical_events,
        "legacy_aliases": aliases,
        "identity_conflicts": conflicts,
        "identity_incomplete_snapshots": incomplete,
        "invalid_snapshots": invalid,
    }
    return {"catalog": catalog, "canonical_snapshots": canonical_snapshots}
