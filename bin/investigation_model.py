#!/usr/bin/env python3
"""Automatic current-event investigation model for Prime Observer."""

from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import tempfile

from health_model import (
    HEAT_BIN_MINUTES,
    RECOVERY_HEALTHY_PERSISTENCE,
    RECOVERY_STABLE_WINDOW_MINUTES,
    TURBULENCE_MIN_RAW_BAD,
    WAN_BAD,
    WAN_BAD_PERSISTENCE,
    bucket_start,
    is_turbulence_bucket,
)
from health_dimensions import semantic_health_dimensions


BASE = Path(__file__).resolve().parents[1]
VIZ_DIR = BASE / "viz"
INVESTIGATION_OUT = VIZ_DIR / "investigation.json"
INVESTIGATIONS_DIR = VIZ_DIR / "investigations"
INVESTIGATION_CATALOG_OUT = VIZ_DIR / "investigation_catalog.json"
INVESTIGATION_GENERATOR = {
    "name": "bin/investigation_model.py",
    "format_version": "investigation-history.v1",
}


def parse_ts(value):
    if isinstance(value, dt.datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(value):
    return value.isoformat() if value is not None else None


def rounded(value, digits=1):
    if value is None:
        return None
    return round(value, digits)


def safe_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def event_id(target_class, first_anomalous_at):
    stamp = first_anomalous_at.isoformat().replace("+00:00", "Z") if first_anomalous_at else "unknown"
    safe = "".join(ch if ch.isalnum() else "-" for ch in f"{target_class}-{stamp}")
    return f"event-{safe.strip('-').lower()}"


def target_label(target_class):
    return {
        "internet_probe": "Internet probes",
        "resolver_probe": "Resolver probes",
        "gateway_probe": "Gateway",
    }.get(target_class or "", str(target_class or "Unknown").replace("_", " ").title())


def normalize_rows(rows):
    samples = []
    for row in rows:
        t = parse_ts(row.get("ts"))
        if t is None:
            continue
        host = (row.get("host") or "").strip()
        target_class = (row.get("target_class") or row.get("targetClass") or "").strip()
        if target_class not in {"gateway_probe", "internet_probe", "resolver_probe"}:
            continue
        sample = {
            "t": t,
            "host": host,
            "phase": ((row.get("phase_label") or row.get("phase") or "FIBER").strip().upper()),
            "target_class": target_class,
            "target_label": (row.get("target_label") or row.get("targetLabel") or host).strip(),
            "kind": "lan" if target_class == "gateway_probe" else "wan",
            "p95": safe_float(row.get("p95_ms") if "p95_ms" in row else row.get("p95")),
            "jitter": safe_float(row.get("jitter_ms") if "jitter_ms" in row else row.get("jitter")),
            "loss": safe_float(row.get("loss_pct") if "loss_pct" in row else row.get("loss")),
            "max_ms": safe_float(row.get("max_ms"), None),
            "raw_bad": bool(row.get("raw_bad") or row.get("rawBad")),
            "sustained_bad": bool(row.get("is_bad") or row.get("isBad") or row.get("sustained_bad")),
        }
        samples.append(sample)
    return sorted(samples, key=lambda item: (item["t"], item["host"]))


def merge_marked_wan(rows, wan_series_marked):
    samples = normalize_rows(rows)
    marked = {
        (item.get("t"), item.get("host"), item.get("target_class")): item
        for item in wan_series_marked or []
    }
    for sample in samples:
        if sample["kind"] != "wan":
            continue
        match = marked.get((sample["t"], sample["host"], sample["target_class"]))
        if match:
            sample["raw_bad"] = bool(match.get("raw_bad"))
            sample["sustained_bad"] = bool(match.get("is_bad"))
    return samples


def host_counts(samples):
    counts = {}
    for sample in samples:
        host = sample.get("host")
        if host:
            counts[host] = counts.get(host, 0) + 1
    return counts


def target_group_summaries(samples):
    groups = {}
    for sample in samples:
        target_class = sample.get("target_class") or "unknown_probe"
        group = groups.setdefault(target_class, {
            "sample_count": 0,
            "host_counts": {},
            "raw_bad_count": 0,
            "sustained_bad_count": 0,
        })
        group["sample_count"] += 1
        host = sample.get("host")
        if host:
            group["host_counts"][host] = group["host_counts"].get(host, 0) + 1
        if sample.get("raw_bad"):
            group["raw_bad_count"] += 1
        if sample.get("sustained_bad"):
            group["sustained_bad_count"] += 1
    return groups


def percentile(values, pct):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * (pct / 100.0)
    lower = int(pos)
    upper = min(lower + 1, len(vals) - 1)
    weight = pos - lower
    return vals[lower] + ((vals[upper] - vals[lower]) * weight)


def summarize(samples, kind):
    rows = [sample for sample in samples if sample["kind"] == kind]
    p95 = [sample["p95"] for sample in rows]
    jitter = [sample["jitter"] for sample in rows]
    loss = [sample["loss"] for sample in rows]
    summary = {
        "sample_count": len(rows),
        "host_counts": host_counts(rows),
        "median_p95_ms": rounded(percentile(p95, 50)),
        "max_p95_ms": rounded(max(p95), 1) if p95 else None,
        "p95_95th_ms": rounded(percentile(p95, 95)),
        "jitter_95th_ms": rounded(percentile(jitter, 95)),
        "max_loss_pct": rounded(max(loss), 2) if loss else None,
        "target_groups": target_group_summaries(rows),
    }
    if kind == "wan":
        buckets = classify_buckets(rows)
        summary.update({
            "raw_bad_count": len([row for row in rows if row.get("raw_bad")]),
            "sustained_bad_count": len([row for row in rows if row.get("sustained_bad")]),
            "turbulence_bucket_count": len([bucket for bucket in buckets if bucket["turbulence"]]),
            "bad_bucket_count": len([bucket for bucket in buckets if bucket["sustained_bad_count"] > 0]),
        })
    else:
        summary["elevated_p95_count"] = len([row for row in rows if row["p95"] > 120.0])
    return summary


def representative_summary(samples):
    p95_values = [sample["p95"] for sample in samples]
    jitter_values = [sample["jitter"] for sample in samples]
    loss_values = [sample["loss"] for sample in samples]
    return {
        "sample_count": len(samples),
        "median_p95_ms": rounded(percentile(p95_values, 50)),
        "typical_p95_ms": rounded(percentile(p95_values, 50)),
        "p75_p95_ms": rounded(percentile(p95_values, 75)),
        "p90_p95_ms": rounded(percentile(p95_values, 90)),
        "max_p95_ms": rounded(max(p95_values), 1) if p95_values else None,
        "jitter_95th_ms": rounded(percentile(jitter_values, 95)),
        "max_loss_pct": rounded(max(loss_values), 2) if loss_values else None,
    }


def classify_buckets(wan_samples):
    buckets = {}
    bin_seconds = HEAT_BIN_MINUTES * 60
    for sample in wan_samples:
        if sample["kind"] != "wan":
            continue
        start = bucket_start(sample["t"])
        key = (sample.get("target_class"), start)
        bucket = buckets.setdefault(key, {
            "start_dt": dt.datetime.fromtimestamp(start, tz=dt.timezone.utc),
            "end_dt": dt.datetime.fromtimestamp(start + bin_seconds, tz=dt.timezone.utc),
            "target_class": sample.get("target_class") or "unknown_probe",
            "samples": [],
        })
        bucket["samples"].append(sample)
    out = []
    for bucket in buckets.values():
        rows = sorted(bucket["samples"], key=lambda item: item["t"])
        raw_run = 0
        max_raw_run = 0
        for row in rows:
            raw_run = raw_run + 1 if row.get("raw_bad") else 0
            max_raw_run = max(max_raw_run, raw_run)
        raw_bad = len([row for row in rows if row.get("raw_bad")])
        sustained_bad = len([row for row in rows if row.get("sustained_bad")])
        turbulence = is_turbulence_bucket(raw_bad, sustained_bad, max_raw_run)
        if sustained_bad:
            assessment_code = "sustained_degradation"
            assessment_label = "Sustained degradation"
            tone = "risk"
            summary = f"{target_label(bucket['target_class'])} degradation persisted across multiple samples."
        elif turbulence:
            assessment_code = "intermittent_degradation"
            assessment_label = "Intermittent degradation"
            tone = "watch"
            summary = f"{target_label(bucket['target_class'])} had repeated abnormal samples without sustained persistence."
        elif raw_bad:
            assessment_code = "isolated_excursion"
            assessment_label = "Isolated excursion"
            tone = "watch"
            summary = f"One or more {target_label(bucket['target_class']).lower()} excursions occurred but were not sustained."
        else:
            assessment_code = "stable"
            assessment_label = "Stable evidence"
            tone = "ok"
            summary = f"{target_label(bucket['target_class'])} stayed below sustained degradation thresholds."
        out.append({
            "start": iso(bucket["start_dt"]),
            "end": iso(bucket["end_dt"]),
            "target_class": bucket["target_class"],
            "target_hosts": host_counts(rows),
            "sample_count": len(rows),
            "raw_bad_count": raw_bad,
            "sustained_bad_count": sustained_bad,
            "max_raw_bad_run": max_raw_run,
            "turbulence": turbulence,
            "max_p95_ms": rounded(max((row["p95"] for row in rows), default=None)),
            "max_jitter_ms": rounded(max((row["jitter"] for row in rows), default=None)),
            "max_loss_pct": rounded(max((row["loss"] for row in rows), default=None), 2),
            "assessment_code": assessment_code,
            "assessment_label": assessment_label,
            "summary": summary,
            "tone": tone,
            "confidence": "high" if sustained_bad else "medium" if raw_bad or turbulence else "high",
        })
    return sorted(out, key=lambda item: (item["start"], item["target_class"]))


def empty_period(name, state, label, summary):
    return {
        "period": name,
        "start": None,
        "end": None,
        "state": state,
        "assessment_code": state,
        "assessment_label": label,
        "summary": summary,
        "total_samples": 0,
        "wan": summarize([], "wan"),
        "lan": summarize([], "lan"),
        "wan_buckets": [],
    }


def build_period(name, samples, state=None, label=None, summary=None):
    if not samples:
        return empty_period(
            name,
            state or ("insufficient_pre_event_evidence" if name == "before" else "awaiting_recovery_evidence"),
            label or "Insufficient evidence",
            summary or "No samples were available for this window.",
        )
    return {
        "period": name,
        "start": iso(min(sample["t"] for sample in samples)),
        "end": iso(max(sample["t"] for sample in samples)),
        "state": state,
        "assessment_code": state,
        "assessment_label": label,
        "summary": summary,
        "total_samples": len(samples),
        "wan": summarize(samples, "wan"),
        "lan": summarize(samples, "lan"),
        "wan_buckets": classify_buckets(samples),
    }


def detect_events(samples, telemetry_latest_at=None):
    events = []
    secondary = []
    wan = [sample for sample in samples if sample["kind"] == "wan"]
    for target_class in sorted({sample["target_class"] for sample in wan}):
        rows = sorted([sample for sample in wan if sample["target_class"] == target_class], key=lambda item: (item["t"], item["host"]))
        current = None
        healthy_run = 0
        for sample in rows:
            if sample.get("raw_bad"):
                if current is None:
                    current = new_event(target_class, sample)
                current["last_anomalous_at"] = sample["t"]
                current["recovery_candidate_at"] = None
                current["recovery_started_at"] = None
                current["recovered_at"] = None
                healthy_run = 0
                current["affected_targets"].add(sample["host"])
                current["raw_bad_count"] += 1
                current["sustained_bad_count"] += 1 if sample.get("sustained_bad") else 0
                if sample.get("sustained_bad") and current.get("confirmed_at") is None:
                    current["confirmed_at"] = sample["t"]
                current["max_p95_ms"] = max(current["max_p95_ms"] or 0, sample["p95"])
                current["max_jitter_ms"] = max(current["max_jitter_ms"] or 0, sample["jitter"])
                current["max_loss_pct"] = max(current["max_loss_pct"] or 0, sample["loss"])
                continue

            if current is None:
                continue
            if current.get("confirmed_at") is None:
                secondary.append(secondary_context(current))
                current = None
                healthy_run = 0
                continue
            if current.get("recovery_candidate_at") is None:
                current["recovery_candidate_at"] = sample["t"]
                healthy_run = 1
            else:
                healthy_run += 1
            if healthy_run >= RECOVERY_HEALTHY_PERSISTENCE and current.get("recovery_started_at") is None:
                current["recovery_started_at"] = current["recovery_candidate_at"]
            if current.get("recovery_started_at") is not None:
                stable_for = sample["t"] - current["recovery_started_at"]
                if stable_for >= dt.timedelta(minutes=RECOVERY_STABLE_WINDOW_MINUTES):
                    current["recovered_at"] = sample["t"]
                    current["lifecycle_state"] = "complete"
                    events.append(current)
                    current = None
                    healthy_run = 0
        if current is not None:
            if current.get("confirmed_at") is None:
                secondary.append(secondary_context(current))
            else:
                current["lifecycle_state"] = "recovering" if current.get("recovery_started_at") else "active"
                events.append(current)
    for event in events:
        event["selection_reason"] = {
            "active": "latest_active_sustained_event",
            "recovering": "latest_recovering_event",
            "complete": "most_recent_completed_meaningful_event",
        }[event["lifecycle_state"]]
    events.sort(key=lambda item: (item.get("confirmed_at") or item["first_anomalous_at"], item["target_class"]))
    return events, secondary


def new_event(target_class, sample):
    return {
        "target_class": target_class,
        "first_anomalous_at": sample["t"],
        "confirmed_at": sample["t"] if sample.get("sustained_bad") else None,
        "last_anomalous_at": sample["t"],
        "recovery_candidate_at": None,
        "recovery_started_at": None,
        "recovered_at": None,
        "affected_targets": set(),
        "raw_bad_count": 0,
        "sustained_bad_count": 0,
        "max_p95_ms": None,
        "max_jitter_ms": None,
        "max_loss_pct": None,
    }


def secondary_context(event):
    code = "isolated_excursion" if event["raw_bad_count"] == 1 else "intermittent_degradation"
    return {
        "target_class": event["target_class"],
        "assessment_code": code,
        "assessment_label": "Isolated excursion" if code == "isolated_excursion" else "Intermittent degradation",
        "start": iso(event["first_anomalous_at"]),
        "end": iso(event["last_anomalous_at"]),
        "raw_bad_count": event["raw_bad_count"],
        "sustained_bad_count": 0,
    }


def select_event(events):
    for state in ("active", "recovering", "complete"):
        matches = [event for event in events if event.get("lifecycle_state") == state]
        if matches:
            return sorted(matches, key=lambda item: item.get("confirmed_at") or item["first_anomalous_at"])[-1]
    return None


def severity_for_event(event):
    sustained = event.get("sustained_bad_count", 0)
    raw = event.get("raw_bad_count", 0)
    max_p95 = event.get("max_p95_ms") or 0
    if sustained >= 8 or max_p95 >= WAN_BAD["p95"] * 1.75:
        return "high"
    if sustained >= 2 or raw >= TURBULENCE_MIN_RAW_BAD:
        return "medium"
    return "low"


def public_event(event, telemetry_latest_at):
    affected = sorted(event["affected_targets"])
    return {
        "id": event_id(event["target_class"], event["first_anomalous_at"]),
        "target_class": event["target_class"],
        "lifecycle_state": event["lifecycle_state"],
        "first_anomalous_at": iso(event.get("first_anomalous_at")),
        "confirmed_at": iso(event.get("confirmed_at")),
        "last_anomalous_at": iso(event.get("last_anomalous_at")),
        "recovery_candidate_at": iso(event.get("recovery_candidate_at")),
        "recovery_started_at": iso(event.get("recovery_started_at")),
        "recovered_at": iso(event.get("recovered_at")),
        "affected_targets": affected,
        "severity": severity_for_event(event),
        "confidence": "high" if event.get("sustained_bad_count", 0) >= WAN_BAD_PERSISTENCE else "medium",
        "selection_reason": event.get("selection_reason"),
        "raw_bad_count": event.get("raw_bad_count", 0),
        "sustained_bad_count": event.get("sustained_bad_count", 0),
        "max_p95_ms": rounded(event.get("max_p95_ms")),
        "max_jitter_ms": rounded(event.get("max_jitter_ms")),
        "max_loss_pct": rounded(event.get("max_loss_pct"), 2),
        "evidence_latest_at": iso(event.get("recovered_at") or event.get("last_anomalous_at") or telemetry_latest_at),
    }


def samples_between(samples, start, end, *, before_end=False):
    if start is None and end is None:
        return []
    out = []
    for sample in samples:
        if start is not None and sample["t"] < start:
            continue
        if end is not None:
            if before_end and sample["t"] >= end:
                continue
            if not before_end and sample["t"] > end:
                continue
        out.append(sample)
    return out


def supporting_metrics(samples):
    wan = summarize(samples, "wan")
    lan = summarize(samples, "lan")
    return {
        "total_samples": len(samples),
        "wan_samples": wan.get("sample_count", 0),
        "lan_samples": lan.get("sample_count", 0),
        "raw_bad_count": wan.get("raw_bad_count", 0),
        "sustained_bad_count": wan.get("sustained_bad_count", 0),
        "turbulence_bucket_count": wan.get("turbulence_bucket_count", 0),
        "max_p95_ms": wan.get("max_p95_ms"),
        "max_jitter_ms": wan.get("jitter_95th_ms"),
        "max_loss_pct": wan.get("max_loss_pct"),
        "lan_elevated_count": lan.get("elevated_p95_count", 0),
    }


def window_supporting_metrics(samples, target_class=None):
    metrics = supporting_metrics(samples)
    target_samples = [sample for sample in samples if sample.get("target_class") == target_class] if target_class else []
    buckets = classify_buckets([sample for sample in samples if sample.get("kind") == "wan"])
    target_buckets = [bucket for bucket in buckets if not target_class or bucket.get("target_class") == target_class]
    metrics.update({
        "duration_minutes": duration_minutes(
            iso(min((sample["t"] for sample in samples), default=None)),
            iso(max((sample["t"] for sample in samples), default=None)),
        ),
        "affected_bucket_count": len([bucket for bucket in target_buckets if bucket.get("sustained_bad_count", 0) > 0]),
        "stable_bucket_count": len([bucket for bucket in target_buckets if bucket.get("assessment_code") == "stable"]),
        "isolated_excursion_bucket_count": len([bucket for bucket in target_buckets if bucket.get("assessment_code") == "isolated_excursion"]),
        "turbulence_bucket_count": len([bucket for bucket in target_buckets if bucket.get("turbulence")]),
        "selected_target": representative_summary(target_samples),
    })
    return metrics


def window_payload(name, samples, code, label, summary, tone, confidence, target_class=None):
    return {
        "name": name,
        "available": bool(samples),
        "start": iso(min((sample["t"] for sample in samples), default=None)),
        "end": iso(max((sample["t"] for sample in samples), default=None)),
        "assessment_code": code,
        "assessment_label": label,
        "summary": summary,
        "tone": tone,
        "confidence": confidence,
        "supporting_metrics": window_supporting_metrics(samples, target_class=target_class),
    }


def assessment_for_window(name, samples, selected_event):
    if not samples:
        if name == "baseline":
            return "insufficient_pre_event_evidence", "Insufficient evidence", "No pre-event telemetry was available before the selected incident.", "muted", "low"
        if name == "recovery":
            return "awaiting_recovery_evidence", "Insufficient evidence", "Recovery has not yet been confirmed.", "watch", "low"
        return "insufficient_evidence", "Insufficient evidence", "No telemetry was available for this window.", "muted", "low"
    wan = summarize(samples, "wan")
    if name == "baseline":
        if wan.get("sustained_bad_count"):
            return "baseline_unstable", "Baseline already unstable", "Available pre-event WAN evidence was already degraded.", "watch", "medium"
        return "stable_baseline", "Stable baseline", f"{target_label(selected_event['target_class'])} latency was stable before the event.", "ok", "high"
    if name == "degradation":
        return "sustained_degradation", "Sustained degradation", f"{target_label(selected_event['target_class'])} degradation persisted across multiple samples.", "risk", selected_event.get("confidence", "high")
    if selected_event.get("lifecycle_state") == "complete":
        return "recovered", "Recovered", f"{target_label(selected_event['target_class'])} performance returned to baseline and remained stable.", "ok", "high"
    return "recovery_in_progress", "Recovery in progress", "Recovery has not yet been confirmed.", "watch", "medium"


def build_windows(samples, selected_event):
    if not selected_event:
        return {
            "baseline": window_payload("baseline", [], "insufficient_pre_event_evidence", "Insufficient evidence", "No incident baseline is available because no sustained incident was selected.", "muted", "low"),
            "degradation": window_payload("degradation", samples, "no_sustained_incident", "No sustained incident", "No sustained network incident is present in the available evidence.", "ok", "high"),
            "recovery": window_payload("recovery", [], "awaiting_recovery_evidence", "Insufficient evidence", "No recovery window is available because no sustained incident was selected.", "muted", "low"),
        }
    first = parse_ts(selected_event["first_anomalous_at"])
    last = parse_ts(selected_event["last_anomalous_at"])
    recovery_candidate = parse_ts(selected_event.get("recovery_candidate_at"))
    recovered = parse_ts(selected_event.get("recovered_at"))
    baseline_samples = samples_between(samples, None, first, before_end=True)
    if selected_event["lifecycle_state"] == "active" and recovery_candidate is None:
        degradation_end = max((sample["t"] for sample in samples), default=last)
    else:
        degradation_end = last
    degradation_samples = samples_between(samples, first, degradation_end)
    recovery_end = recovered or max((sample["t"] for sample in samples), default=None)
    recovery_samples = samples_between(samples, recovery_candidate, recovery_end) if recovery_candidate else []
    windows = {}
    for name, rows in (("baseline", baseline_samples), ("degradation", degradation_samples), ("recovery", recovery_samples)):
        code, label, summary, tone, confidence = assessment_for_window(name, rows, selected_event)
        windows[name] = window_payload(name, rows, code, label, summary, tone, confidence, selected_event.get("target_class"))
    return windows


def timeline_rows(windows, selected_event):
    rows = []
    for key, phase in (("baseline", "Baseline"), ("degradation", "Degradation"), ("recovery", "Recovery")):
        window = windows[key]
        metrics = window["supporting_metrics"]
        representative = phase_summary_metrics(window)
        rows.append({
            "phase": phase,
            "start": window.get("start"),
            "end": window.get("end"),
            "assessment_code": window["assessment_code"],
            "assessment_label": window["assessment_label"],
            "summary": window["summary"],
            "tone": window["tone"],
            "confidence": window["confidence"],
            "affected_probes": target_label(selected_event["target_class"]) if selected_event else "None",
            "evidence": f"{metrics['wan_samples']} WAN samples, {metrics['raw_bad_count']} raw bad, {metrics['sustained_bad_count']} sustained bad",
            "supporting_metrics": metrics,
            "phase_summary": representative,
        })
    return rows


def legacy_periods(samples, windows):
    periods = {}
    for legacy, canonical in (("before", "baseline"), ("during", "degradation"), ("after", "recovery")):
        window = windows[canonical]
        rows = samples_between(samples, parse_ts(window.get("start")), parse_ts(window.get("end"))) if window.get("available") else []
        periods[legacy] = build_period(legacy, rows, window["assessment_code"], window["assessment_label"], window["summary"])
    return periods


def observation_references(observations_projection):
    if not isinstance(observations_projection, dict):
        return []
    refs = []
    for observation in observations_projection.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        ref = {
            "id": observation.get("id"),
            "type": observation.get("type"),
            "scope": observation.get("scope") if isinstance(observation.get("scope"), dict) else {},
            "interval": observation.get("interval") if isinstance(observation.get("interval"), dict) else {},
        }
        if isinstance(observation.get("state"), dict):
            ref["state"] = observation["state"]
        refs.append(ref)
    return refs


def artifact_state(selected_event, stale_reason=None):
    if stale_reason:
        return {"is_current": False, "is_stale": True, "is_historical": False, "label": "Stale investigation", "stale_reason": stale_reason}
    if not selected_event:
        return {"is_current": True, "is_stale": False, "is_historical": False, "label": "No sustained incident", "stale_reason": None}
    state = selected_event["lifecycle_state"]
    label = {"active": "Active investigation", "recovering": "Recovery in progress", "complete": "Completed investigation"}[state]
    return {"is_current": True, "is_stale": False, "is_historical": False, "label": label, "stale_reason": None}


def freshness(generated_at, telemetry_latest_at, evidence_latest_at):
    return {
        "telemetry_latest_at": iso(telemetry_latest_at),
        "evidence_latest_at": iso(evidence_latest_at),
        "generated_at": iso(generated_at),
        "telemetry_age_seconds": int(max((generated_at - telemetry_latest_at).total_seconds(), 0)) if telemetry_latest_at else None,
        "evidence_lag_seconds": int(max((telemetry_latest_at - evidence_latest_at).total_seconds(), 0)) if telemetry_latest_at and evidence_latest_at else None,
    }


def seconds_between(start, end):
    if start is None or end is None:
        return None
    return int(max((end - start).total_seconds(), 0))


def recovery_progress(selected_event, samples, telemetry_latest_at):
    if not selected_event:
        return {
            "available": False,
            "healthy_samples_since_last_anomaly": 0,
            "recovery_candidate_at": None,
            "healthy_observation_seconds": 0,
            "required_stable_seconds": RECOVERY_STABLE_WINDOW_MINUTES * 60,
            "remaining_stable_seconds": None,
            "healthy_persistence_satisfied": False,
        }
    target_class = selected_event.get("target_class")
    last_anomaly = parse_ts(selected_event.get("last_anomalous_at"))
    candidate = parse_ts(selected_event.get("recovery_candidate_at"))
    healthy_samples = [
        sample for sample in samples
        if sample.get("kind") == "wan"
        and sample.get("target_class") == target_class
        and last_anomaly is not None
        and sample["t"] > last_anomaly
        and not sample.get("raw_bad")
    ]
    observed_seconds = seconds_between(candidate, telemetry_latest_at) if candidate else 0
    required_seconds = RECOVERY_STABLE_WINDOW_MINUTES * 60
    return {
        "available": candidate is not None,
        "healthy_samples_since_last_anomaly": len(healthy_samples),
        "recovery_candidate_at": iso(candidate),
        "healthy_observation_seconds": observed_seconds,
        "required_stable_seconds": required_seconds,
        "remaining_stable_seconds": max(required_seconds - observed_seconds, 0) if observed_seconds is not None else None,
        "healthy_persistence_satisfied": selected_event.get("recovery_started_at") is not None,
    }


def phase_summary_metrics(window):
    metrics = window.get("supporting_metrics") if isinstance(window, dict) else {}
    selected = metrics.get("selected_target") if isinstance(metrics.get("selected_target"), dict) else {}
    return {
        "sample_count": selected.get("sample_count") or metrics.get("wan_samples", 0),
        "duration_minutes": metrics.get("duration_minutes"),
        "typical_p95_ms": selected.get("typical_p95_ms"),
        "p75_p95_ms": selected.get("p75_p95_ms"),
        "p90_p95_ms": selected.get("p90_p95_ms"),
        "max_p95_ms": selected.get("max_p95_ms") or metrics.get("max_p95_ms"),
        "max_loss_pct": selected.get("max_loss_pct") if selected.get("max_loss_pct") is not None else metrics.get("max_loss_pct"),
        "raw_bad_count": metrics.get("raw_bad_count", 0),
        "sustained_bad_count": metrics.get("sustained_bad_count", 0),
        "sustained_bad_bucket_count": metrics.get("affected_bucket_count", 0),
        "stable_bucket_count": metrics.get("stable_bucket_count", 0),
        "isolated_excursion_bucket_count": metrics.get("isolated_excursion_bucket_count", 0),
        "turbulence_bucket_count": metrics.get("turbulence_bucket_count", 0),
    }


def operational_state(selected_event, recovery):
    if not selected_event:
        return {
            "state": "no_sustained_incident",
            "label": "No sustained incident is selected.",
            "recommendation": "Continue normal observation unless a new sustained event appears.",
        }
    state = selected_event.get("lifecycle_state")
    if state == "active":
        return {
            "state": "active",
            "label": "Degradation is continuing.",
            "recommendation": "Confirm affected scope and monitor for recovery before closing the investigation.",
        }
    if state == "recovering":
        remaining = recovery.get("remaining_stable_seconds") if isinstance(recovery, dict) else None
        return {
            "state": "recovering",
            "label": "Signals are healthy again, but recovery is not confirmed yet.",
            "recommendation": f"Continue observation until the stable window completes{f' ({int(remaining // 60)} min remaining)' if isinstance(remaining, int) else ''}.",
        }
    return {
        "state": "complete",
        "label": "Recovery was confirmed after the required stable observation period.",
        "recommendation": "Use the recorded evidence for review or escalation if the pattern recurs.",
    }


def scope_impact(selected_event, windows, periods):
    during = periods.get("during") if isinstance(periods, dict) else {}
    wan = during.get("wan") if isinstance(during, dict) else {}
    lan = during.get("lan") if isinstance(during, dict) else {}
    groups = wan.get("target_groups") if isinstance(wan, dict) else {}
    selected_class = selected_event.get("target_class") if selected_event else None
    affected_group = groups.get(selected_class, {}) if isinstance(groups, dict) and selected_class else {}
    unaffected = []
    for target_class in ("internet_probe", "resolver_probe"):
        if target_class == selected_class:
            continue
        group = groups.get(target_class, {}) if isinstance(groups, dict) else {}
        if group.get("sample_count") is not None:
            unaffected.append({
                "target_class": target_class,
                "sample_count": group.get("sample_count", 0),
                "raw_bad_count": group.get("raw_bad_count", 0),
                "sustained_bad_count": group.get("sustained_bad_count", 0),
            })
    if isinstance(lan, dict) and lan.get("sample_count") is not None:
        unaffected.append({
            "target_class": "gateway_probe",
            "sample_count": lan.get("sample_count", 0),
            "elevated_count": lan.get("elevated_p95_count", 0),
            "max_p95_ms": lan.get("max_p95_ms"),
        })
    if selected_event and unaffected:
        conclusion = f"{target_label(selected_class)} degraded while comparison groups stayed below sustained-degradation thresholds or showed weaker evidence."
    elif selected_event:
        conclusion = f"{target_label(selected_class)} is the selected affected scope; comparison evidence is limited."
    else:
        conclusion = "No confirmed sustained affected scope is selected."
    degradation = phase_summary_metrics((windows or {}).get("degradation", {}))
    return {
        "affected_probe_class": selected_class,
        "affected_probe_label": target_label(selected_class) if selected_class else None,
        "affected_endpoints": selected_event.get("affected_targets", []) if selected_event else [],
        "first_anomaly": selected_event.get("first_anomalous_at") if selected_event else None,
        "last_anomaly": selected_event.get("last_anomalous_at") if selected_event else None,
        "anomalous_samples": selected_event.get("raw_bad_count", 0) if selected_event else 0,
        "sustained_bad_samples": selected_event.get("sustained_bad_count", 0) if selected_event else 0,
        "affected_evidence_buckets": degradation.get("sustained_bad_bucket_count", 0),
        "representative_latency_ms": degradation.get("typical_p95_ms"),
        "maximum_excursion_ms": selected_event.get("max_p95_ms") if selected_event else None,
        "packet_loss_pct": selected_event.get("max_loss_pct") if selected_event else None,
        "current_recovery_state": selected_event.get("lifecycle_state") if selected_event else "none",
        "affected_group": affected_group,
        "unaffected_comparison_groups": unaffected,
        "scope_conclusion": conclusion,
    }


def bucket_rollup(periods):
    buckets = []
    for period_name in ("before", "during", "after"):
        period = periods.get(period_name) if isinstance(periods, dict) else {}
        for bucket in period.get("wan_buckets", []) if isinstance(period, dict) else []:
            item = dict(bucket)
            item["period"] = period_name
            buckets.append(item)
    sustained = [bucket for bucket in buckets if bucket.get("assessment_code") == "sustained_degradation" or bucket.get("sustained_bad_count", 0) > 0]
    recovery = [bucket for bucket in buckets if bucket.get("period") == "after" and bucket.get("assessment_code") == "stable"]
    excursions = [bucket for bucket in buckets if bucket.get("assessment_code") in {"isolated_excursion", "intermittent_degradation"}]
    stable = [bucket for bucket in buckets if bucket.get("assessment_code") == "stable"]
    visible = sustained + recovery[-2:] + excursions
    visible.sort(key=lambda item: (item.get("start") or "", item.get("target_class") or ""))
    worst = max(sustained, key=lambda item: (item.get("sustained_bad_count", 0), item.get("max_p95_ms") or 0), default=None)
    return {
        "total_buckets": len(buckets),
        "stable_buckets": len(stable),
        "isolated_excursion_buckets": len(excursions),
        "sustained_degradation_buckets": len(sustained),
        "recovery_buckets": len(recovery),
        "affected_time_range": {
            "start": min((bucket.get("start") for bucket in sustained if bucket.get("start")), default=None),
            "end": max((bucket.get("end") for bucket in sustained if bucket.get("end")), default=None),
        },
        "worst_sustained_bucket": worst,
        "current_recovery_bucket": recovery[-1] if recovery else None,
        "default_buckets": visible[:40],
        "all_buckets": buckets,
    }


def episode_summary(selected_event, secondary_context, observation_refs):
    episodes = [ref for ref in observation_refs if isinstance(ref, dict) and ref.get("type") == "episode"]
    return {
        "total_observations_consolidated": len(episodes),
        "sustained_episodes": len([ref for ref in episodes if (ref.get("state") or {}).get("status") == "sustained_degradation"]),
        "isolated_excursions": len([item for item in secondary_context if item.get("assessment_code") == "isolated_excursion"]),
        "event_start": selected_event.get("first_anomalous_at") if selected_event else None,
        "last_anomaly": selected_event.get("last_anomalous_at") if selected_event else None,
        "recovery_candidate": selected_event.get("recovery_candidate_at") if selected_event else None,
        "recovery_confirmation": selected_event.get("recovered_at") if selected_event else None,
        "summary": (
            f"{len(episodes)} episode observation reference(s) are consolidated here; raw references remain in forensic detail."
            if episodes else "No episode observation references are available for this artifact."
        ),
    }


def evidence_argument(selected_event, windows, periods, scope, recovery):
    degradation = phase_summary_metrics((windows or {}).get("degradation", {}))
    baseline = phase_summary_metrics((windows or {}).get("baseline", {}))
    supporting = []
    limiting = []
    against_broader = []
    verification = []
    if selected_event:
        supporting.append(f"{target_label(selected_event.get('target_class'))} showed sustained degradation across {degradation.get('sustained_bad_bucket_count')} evidence bucket(s).")
        supporting.append(f"{selected_event.get('sustained_bad_count', 0)} sample(s) met sustained-bad criteria after persistence was established.")
        if selected_event.get("recovery_candidate_at"):
            supporting.append("Healthy samples began after the last anomaly, indicating recovery may be underway.")
        if baseline.get("max_p95_ms") and degradation.get("max_p95_ms") and baseline.get("max_p95_ms") > degradation.get("max_p95_ms"):
            limiting.append("The baseline contained a higher isolated maximum than the degradation window, so representative metrics must be read separately from maximum excursions.")
        if recovery and recovery.get("available") and recovery.get("remaining_stable_seconds"):
            limiting.append("Recovery has not yet completed the required stable observation window.")
        if not scope.get("unaffected_comparison_groups"):
            limiting.append("Comparison group evidence is limited for this selected event.")
        for group in scope.get("unaffected_comparison_groups", []):
            if group.get("target_class") == "gateway_probe" and not group.get("elevated_count"):
                against_broader.append("Gateway evidence did not show sustained local degradation during the selected window.")
            elif group.get("sustained_bad_count") == 0:
                against_broader.append(f"{target_label(group.get('target_class'))} remained below sustained-degradation thresholds.")
        verification.extend([
            "Compare affected probe latency from another local client if the issue recurs.",
            "Check the affected endpoint class against the unaffected comparison group before escalating.",
            "Continue observation until recovery is confirmed or a renewed anomaly appears.",
        ])
    else:
        supporting.append("No sustained event met the deterministic selection criteria.")
        limiting.append("Without a selected sustained event, fault-domain interpretation remains limited.")
        verification.append("Continue collecting telemetry and revisit if sustained bad samples appear.")
    return {
        "supporting_evidence": supporting[:6],
        "limiting_evidence": limiting[:6],
        "evidence_against_broader_impact": list(dict.fromkeys(against_broader))[:6],
        "verification_steps": verification[:6],
    }


def operator_brief(selected_event, windows, periods, recovery, attribution):
    scope = scope_impact(selected_event, windows, periods)
    ops = operational_state(selected_event, recovery)
    attribution_label = (attribution or {}).get("attribution_label") if isinstance(attribution, dict) else None
    likely_domain = attribution_label or "Unknown from available evidence"
    confidence = selected_event.get("confidence") if selected_event else "high"
    if selected_event:
        headline = f"{target_label(selected_event.get('target_class'))} degradation is {ops['state']}."
        summary = f"The selected event affected {target_label(selected_event.get('target_class')).lower()} with sustained bad evidence. {scope['scope_conclusion']}"
    else:
        headline = "No sustained network incident is selected."
        summary = "Prime Observer did not find a confirmed sustained event in the current deterministic evidence package."
    argument = evidence_argument(selected_event, windows, periods, scope, recovery)
    return {
        "headline": headline,
        "summary": summary,
        "affected_scope": scope.get("scope_conclusion"),
        "unaffected_scope": "; ".join(
            f"{target_label(item.get('target_class'))}: {item.get('sample_count', 0)} samples"
            for item in scope.get("unaffected_comparison_groups", [])
        ) or "No unaffected comparison group is available.",
        "likely_fault_domain": likely_domain,
        "confidence": confidence,
        "operational_state": ops,
        "recommended_actions": [
            {
                "action": "Verify the affected scope before changing configuration.",
                "reason": "The deterministic evidence separates affected and comparison probe groups.",
                "expected_observation": "Affected probes remain worse than comparison probes if the scope conclusion holds.",
                "assessment_change": "If comparison probes degrade too, broaden the fault-domain assessment.",
            },
            {
                "action": ops["recommendation"],
                "reason": "Recovery state determines whether the operator should monitor, confirm, or close out.",
                "expected_observation": "Stable healthy samples accumulate until the recovery window completes.",
                "assessment_change": "A renewed anomaly returns the event to active degradation.",
            },
        ],
        "why_these_actions": [
            "They test the leading deterministic scope conclusion without destructive changes.",
            "They preserve the distinction between observed facts and likely interpretation.",
        ],
        "supporting_evidence": argument["supporting_evidence"],
        "limiting_evidence": argument["limiting_evidence"],
        "conditions_that_change_assessment": [
            "Affected endpoints change materially.",
            "Comparison probe groups begin sustained degradation.",
            "New gateway elevation appears during the same interval.",
            "Recovery fails or a new anomaly appears before confirmation.",
        ],
        "monitoring_guidance": "Watch the selected target class and comparison groups through the recovery window.",
        "freshness_guidance": "Use the latest telemetry and evidence timestamps to judge whether this assessment reflects the current event state.",
    }


def event_list(events):
    out = []
    for event in events:
        selected = public_event(event, event.get("recovered_at") or event.get("last_anomalous_at"))
        out.append({
            "id": selected["id"],
            "type": "sustained_degradation",
            "title": "Sustained degradation",
            "start": selected["first_anomalous_at"],
            "end": selected["recovered_at"] or selected["last_anomalous_at"],
            "period": "degradation",
            "lifecycle_state": selected["lifecycle_state"],
            "details": selected,
        })
    return out


def compact_samples(samples, limit=240):
    selected = samples if len(samples) <= limit else samples[::max(1, len(samples) // limit)][:limit]
    return [{
        "ts": iso(sample["t"]),
        "source_file": "transform_latest.rows_out",
        "phase": sample.get("phase"),
        "host": sample.get("host"),
        "target_label": sample.get("target_label"),
        "target_class": sample.get("target_class"),
        "kind": sample.get("kind"),
        "p95_ms": rounded(sample.get("p95")),
        "jitter_ms": rounded(sample.get("jitter")),
        "loss_pct": rounded(sample.get("loss"), 2),
        "max_ms": rounded(sample.get("max_ms")),
    } for sample in selected]


def duration_minutes(start, end):
    start_ts = parse_ts(start)
    end_ts = parse_ts(end)
    if start_ts is None or end_ts is None:
        return None
    return rounded((end_ts - start_ts).total_seconds() / 60.0, 2)


def build_automatic_investigation(
    *,
    rows_out,
    generated_at,
    wan_series_marked=None,
    attribution=None,
    dashboard_health=None,
    health_dimensions=None,
    observations_projection=None,
    selected_event_id=None,
    historical=False,
    snapshot_written_at=None,
):
    samples = merge_marked_wan(rows_out, wan_series_marked)
    telemetry_latest_at = max((sample["t"] for sample in samples), default=generated_at)
    events, secondary_context = detect_events(samples, telemetry_latest_at)
    selected_raw = select_event(events)
    if selected_event_id is not None:
        selected_raw = next(
            (
                event
                for event in events
                if event_id(event["target_class"], event["first_anomalous_at"]) == selected_event_id
            ),
            None,
        )
    selected = public_event(selected_raw, telemetry_latest_at) if selected_raw else None
    windows = build_windows(samples, selected)
    evidence_latest_at = parse_ts(selected["evidence_latest_at"]) if selected else telemetry_latest_at
    periods = legacy_periods(samples, windows)
    timeline = timeline_rows(windows, selected)
    recovery = recovery_progress(selected, samples, telemetry_latest_at)
    scope = scope_impact(selected, windows, periods)
    buckets = bucket_rollup(periods)
    observations = observation_references(observations_projection)
    episodes = episode_summary(selected, secondary_context, observations)
    evidence = evidence_argument(selected, windows, periods, scope, recovery)
    brief = operator_brief(selected, windows, periods, recovery, attribution)
    event_start = periods["during"].get("start")
    event_end = periods["during"].get("end")
    payload = {
        "artifact_type": "completed_investigation_snapshot" if historical and selected else "current_investigation",
        "schema_version": 2,
        "mode": "automatic",
        "generated_at": iso(generated_at),
        "generator": dict(INVESTIGATION_GENERATOR),
        "immutable": bool(historical and selected),
        "id": selected["id"].replace("event-", "investigation-") if selected else "investigation-no-sustained-incident",
        "title": (
            "Completed event investigation"
            if historical and selected
            else "Automatic current-event investigation"
            if selected
            else "No sustained network incident"
        ),
        "status": "available" if samples else "no_samples",
        "artifact_state": (
            {
                "is_current": False,
                "is_stale": False,
                "is_historical": True,
                "label": "Historical investigation",
                "stale_reason": None,
            }
            if historical and selected
            else artifact_state(selected)
        ),
        "freshness": freshness(generated_at, telemetry_latest_at, evidence_latest_at),
        "selected_event": selected,
        "operator_brief": brief,
        "impact_assessment": (health_dimensions or {}).get("user_impact") if isinstance(health_dimensions, dict) else None,
        "dependency_state": (health_dimensions or {}).get("dependency_groups") if isinstance(health_dimensions, dict) else [],
        "health_dimensions": health_dimensions if isinstance(health_dimensions, dict) else None,
        "deterministic_operator_interpretation": (health_dimensions or {}).get("deterministic_operator_interpretation") if isinstance(health_dimensions, dict) else None,
        "scope_impact": scope,
        "recovery_progress": recovery,
        "episode_summary": episodes,
        "evidence_argument": evidence,
        "evidence_buckets": buckets,
        "secondary_context": secondary_context,
        "windows": windows,
        "timeline": timeline,
        "periods": periods,
        "requested_window": {"start": event_start, "end": event_end, "duration_minutes": duration_minutes(event_start, event_end)},
        "context_window": {
            "start": iso(min((sample["t"] for sample in samples), default=None)),
            "end": iso(max((sample["t"] for sample in samples), default=None)),
            "pad_minutes": None,
        },
        "event_window": {
            "start": event_start,
            "end": event_end,
            "duration_minutes": duration_minutes(event_start, event_end),
            "context_start": iso(min((sample["t"] for sample in samples), default=None)),
            "context_end": iso(max((sample["t"] for sample in samples), default=None)),
        },
        "thresholds": {
            "wan_bad_p95_ms": WAN_BAD["p95"],
            "wan_bad_jitter_ms": WAN_BAD["jitter"],
            "wan_bad_loss_pct": WAN_BAD["loss"],
            "wan_bad_persistence": WAN_BAD_PERSISTENCE,
            "turbulence_min_raw_bad": TURBULENCE_MIN_RAW_BAD,
            "bucket_minutes": HEAT_BIN_MINUTES,
            "recovery_healthy_persistence": RECOVERY_HEALTHY_PERSISTENCE,
            "recovery_stable_window_minutes": RECOVERY_STABLE_WINDOW_MINUTES,
        },
        "target_groups": target_group_summaries(samples),
        "observation_references": observations,
        "events": event_list(events),
        "navigation": {"first_event": None, "last_event": None, "events": {}},
        "event_neighborhoods": [],
        "timeline_samples": compact_samples(samples),
        "dns_context": {"available": False, "status": "unavailable", "reason": "Automatic investigation does not refresh optional DNS context."},
        "sources": {"telemetry_files": [], "observations": "viz/observations.json"},
        "provenance": {
            "producer": "bin/investigation_model.py",
            "transform_attribution_generated_at": (attribution or {}).get("generated_at") if isinstance(attribution, dict) else None,
            "dashboard_health_generated_at": (dashboard_health or {}).get("generated_at") if isinstance(dashboard_health, dict) else None,
        },
        "notes": [
            "Prime Observer automatic investigation output is deterministic telemetry evidence, not external interpretation.",
            "Automatic mode uses baseline, degradation, and recovery windows derived from event lifecycle boundaries.",
        ],
    }
    if historical and selected:
        payload["snapshot_written_at"] = iso(snapshot_written_at or generated_at)
    if not selected:
        payload["message"] = "No sustained network incident is present in the available evidence."
    payload["provenance"]["event_semantic_hash"] = event_semantic_hash(payload)
    payload["provenance"]["semantic_hash"] = payload["provenance"]["event_semantic_hash"]
    return payload


def catalog_entry(snapshot, snapshot_path):
    selected = snapshot.get("selected_event") if isinstance(snapshot.get("selected_event"), dict) else {}
    if selected.get("lifecycle_state") != "complete" or not selected.get("id"):
        return None
    return {
        "event_id": selected["id"],
        "lifecycle": selected["lifecycle_state"],
        "first_anomalous_at": selected.get("first_anomalous_at"),
        "recovered_at": selected.get("recovered_at"),
        "severity": selected.get("severity"),
        "confidence": selected.get("confidence"),
        "target_class": selected.get("target_class"),
        "affected_targets": selected.get("affected_targets") or [],
        "duration": duration_minutes(selected.get("first_anomalous_at"), selected.get("recovered_at")),
        "snapshot_path": snapshot_path,
    }


def invalid_snapshot_entry(path, *, error_type, error_message, detected_at=None):
    return {
        "snapshot_path": f"investigations/{path.name}",
        "event_id": path.stem or None,
        "error_type": error_type,
        "error_message": error_message,
        "detected_at": iso(detected_at) if detected_at is not None else None,
    }


def load_snapshot_for_catalog(path):
    try:
        raw = path.read_text()
    except OSError as exc:
        return None, "unreadable", str(exc)
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, "malformed_json", str(exc)
    if not isinstance(snapshot, dict):
        return None, "structurally_invalid", "Snapshot JSON root is not an object."
    selected = snapshot.get("selected_event") if isinstance(snapshot.get("selected_event"), dict) else None
    if not selected or selected.get("lifecycle_state") != "complete" or not selected.get("id"):
        return None, "structurally_invalid", "Snapshot does not contain a completed selected_event with an id."
    artifact_type = snapshot.get("artifact_type")
    if artifact_type not in {None, "completed_investigation_snapshot"}:
        return None, "structurally_invalid", f"Unsupported artifact_type: {artifact_type}."
    return snapshot, None, None


def build_investigation_catalog(investigations_dir=INVESTIGATIONS_DIR, generated_at=None):
    events = []
    invalid_snapshots = []
    if investigations_dir.exists():
        for path in investigations_dir.glob("*.json"):
            snapshot, error_type, error_message = load_snapshot_for_catalog(path)
            if error_type:
                invalid_snapshots.append(invalid_snapshot_entry(
                    path,
                    error_type=error_type,
                    error_message=error_message,
                    detected_at=generated_at,
                ))
                continue
            entry = catalog_entry(snapshot, f"investigations/{path.name}")
            if entry:
                events.append(entry)
    events.sort(
        key=lambda item: (
            parse_ts(item.get("recovered_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            parse_ts(item.get("first_anomalous_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            item.get("event_id") or "",
        ),
        reverse=True,
    )
    invalid_snapshots.sort(key=lambda item: item.get("snapshot_path") or "")
    return {
        "artifact_type": "investigation_catalog",
        "schema_version": 1,
        "generated_at": iso(generated_at) if generated_at is not None else None,
        "generator": dict(INVESTIGATION_GENERATOR),
        "events": events,
        "invalid_snapshots": invalid_snapshots,
    }


def serialize_json(payload):
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def existing_snapshot_state(path):
    if not path.exists():
        return {"state": "missing"}
    snapshot, error_type, error_message = load_snapshot_for_catalog(path)
    if error_type:
        return {"state": "invalid", "error_type": error_type, "error_message": error_message}
    return {"state": "valid", "snapshot": snapshot}


def write_json_once(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = existing_snapshot_state(path)
    if existing["state"] == "valid":
        return {"written": False, "state": "unchanged"}
    if existing["state"] == "invalid":
        return {
            "written": False,
            "state": "invalid_existing",
            "error_type": existing["error_type"],
            "error_message": existing["error_message"],
        }

    body = serialize_json(payload).encode("utf-8")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp_path, path)
    except FileExistsError:
        return {"written": False, "state": "race_lost"}
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return {"written": True, "state": "written"}


def write_completed_investigation_history(
    *,
    rows_out,
    generated_at,
    wan_series_marked=None,
    attribution=None,
    dashboard_health=None,
    health_dimensions=None,
    observations_projection=None,
    investigations_dir=INVESTIGATIONS_DIR,
    catalog_path=INVESTIGATION_CATALOG_OUT,
    current_investigation=None,
):
    current = current_investigation or build_automatic_investigation(
        rows_out=rows_out,
        generated_at=generated_at,
        wan_series_marked=wan_series_marked,
        attribution=attribution,
        dashboard_health=dashboard_health,
        health_dimensions=health_dimensions,
        observations_projection=observations_projection,
    )
    completed_events = [
        event
        for event in current.get("events") or []
        if event.get("lifecycle_state") == "complete" and event.get("id")
    ]
    written = []
    invalid = []
    for completed_event in completed_events:
        completed_event_id = completed_event["id"]
        snapshot_path = investigations_dir / f"{completed_event_id}.json"
        existing = existing_snapshot_state(snapshot_path)
        if existing["state"] == "valid":
            continue
        if existing["state"] == "invalid":
            invalid.append(invalid_snapshot_entry(
                snapshot_path,
                error_type=existing["error_type"],
                error_message=existing["error_message"],
                detected_at=generated_at,
            ))
            continue
        recovered_at = parse_ts((completed_event.get("details") or {}).get("recovered_at"))
        snapshot_rows = [
            row for row in rows_out
            if recovered_at is None or (parse_ts(row.get("ts")) or recovered_at) <= recovered_at
        ]
        snapshot_marked = [
            item for item in (wan_series_marked or [])
            if recovered_at is None or (parse_ts(item.get("t")) or recovered_at) <= recovered_at
        ]
        snapshot = build_automatic_investigation(
            rows_out=snapshot_rows,
            generated_at=generated_at,
            wan_series_marked=snapshot_marked,
            attribution=attribution,
            dashboard_health=dashboard_health,
            health_dimensions=health_dimensions,
            observations_projection=observations_projection,
            selected_event_id=completed_event_id,
            historical=True,
            snapshot_written_at=generated_at,
        )
        write_result = write_json_once(snapshot_path, snapshot)
        if write_result["written"]:
            written.append(completed_event_id)
        elif write_result.get("state") == "invalid_existing":
            invalid.append(invalid_snapshot_entry(
                snapshot_path,
                error_type=write_result.get("error_type"),
                error_message=write_result.get("error_message"),
                detected_at=generated_at,
            ))

    catalog = build_investigation_catalog(investigations_dir, generated_at)
    write_json_atomic(catalog_path, catalog)
    return {
        "snapshots_written": written,
        "invalid_snapshots": invalid or catalog.get("invalid_snapshots", []),
        "snapshot_count": len(catalog["events"]),
        "catalog": catalog,
    }


def event_semantic_payload(payload):
    def semantic_metrics(metrics):
        if not isinstance(metrics, dict):
            return None
        return {
            "wan_samples": metrics.get("wan_samples"),
            "raw_bad_count": metrics.get("raw_bad_count"),
            "sustained_bad_count": metrics.get("sustained_bad_count"),
            "turbulence_bucket_count": metrics.get("turbulence_bucket_count"),
            "max_p95_ms": metrics.get("max_p95_ms"),
            "max_jitter_ms": metrics.get("max_jitter_ms"),
            "max_loss_pct": metrics.get("max_loss_pct"),
        }
    selected = payload.get("selected_event") if isinstance(payload.get("selected_event"), dict) else {}
    selected_keys = (
        "id",
        "target_class",
        "lifecycle_state",
        "first_anomalous_at",
        "confirmed_at",
        "last_anomalous_at",
        "recovery_candidate_at",
        "recovery_started_at",
        "recovered_at",
        "affected_targets",
        "severity",
        "confidence",
        "selection_reason",
        "raw_bad_count",
        "sustained_bad_count",
        "max_p95_ms",
        "max_jitter_ms",
        "max_loss_pct",
    )
    artifact = payload.get("artifact_state") if isinstance(payload.get("artifact_state"), dict) else {}
    windows = payload.get("windows") if isinstance(payload.get("windows"), dict) else {}
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    return {
        "schema_version": payload.get("schema_version"),
        "mode": payload.get("mode"),
        "id": payload.get("id"),
        "status": payload.get("status"),
        "artifact_state": {
            "is_current": artifact.get("is_current"),
            "is_stale": artifact.get("is_stale"),
            "is_historical": artifact.get("is_historical"),
            "label": artifact.get("label"),
            "stale_reason": artifact.get("stale_reason"),
        },
        "selected_event": {key: selected.get(key) for key in selected_keys},
        "secondary_context": payload.get("secondary_context"),
        "health_dimensions": semantic_health_dimensions(payload.get("health_dimensions")),
        "windows": {
            name: {
                "available": window.get("available"),
                "assessment_code": window.get("assessment_code"),
                "assessment_label": window.get("assessment_label"),
                "summary": window.get("summary"),
                "tone": window.get("tone"),
                "confidence": window.get("confidence"),
                "supporting_metrics": semantic_metrics(window.get("supporting_metrics")) if name in {"baseline", "degradation"} else None,
            }
            for name, window in windows.items()
            if isinstance(window, dict)
        },
        "timeline": [
            {
                "phase": row.get("phase"),
                "assessment_code": row.get("assessment_code"),
                "assessment_label": row.get("assessment_label"),
                "summary": row.get("summary"),
                "tone": row.get("tone"),
                "confidence": row.get("confidence"),
                "affected_probes": row.get("affected_probes"),
                "supporting_metrics": semantic_metrics(row.get("supporting_metrics")) if row.get("phase") in {"Baseline", "Degradation"} else None,
            }
            for row in timeline
            if isinstance(row, dict)
        ],
        "message": payload.get("message"),
    }


def event_semantic_hash(payload):
    encoded = json.dumps(event_semantic_payload(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_hash(payload):
    return event_semantic_hash(payload)


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def existing_event_semantic_hash(existing_payload):
    return ((existing_payload.get("provenance") or {}).get("event_semantic_hash")
        or (existing_payload.get("provenance") or {}).get("semantic_hash"))


def artifact_rewrite_needed(existing_payload, new_payload):
    if not isinstance(existing_payload, dict):
        return True
    if existing_event_semantic_hash(existing_payload) != existing_event_semantic_hash(new_payload):
        return True
    old_freshness = existing_payload.get("freshness") if isinstance(existing_payload.get("freshness"), dict) else {}
    new_freshness = new_payload.get("freshness") if isinstance(new_payload.get("freshness"), dict) else {}
    if old_freshness.get("telemetry_latest_at") != new_freshness.get("telemetry_latest_at"):
        return True
    if existing_payload.get("artifact_state") != new_payload.get("artifact_state"):
        return True
    for key in ("artifact_type", "generator", "immutable"):
        if existing_payload.get(key) != new_payload.get(key):
            return True
    return False


def assistant_semantic_changed(existing_payload, new_payload):
    if not isinstance(existing_payload, dict):
        return True
    return existing_event_semantic_hash(existing_payload) != existing_event_semantic_hash(new_payload)


def mark_stale_against_telemetry(payload, telemetry_latest_at):
    current = parse_ts(((payload.get("freshness") or {}).get("telemetry_latest_at")))
    if current is None or telemetry_latest_at is None or telemetry_latest_at <= current:
        return payload
    updated = json.loads(json.dumps(payload))
    updated["artifact_state"] = {
        "is_current": False,
        "is_stale": True,
        "is_historical": bool((payload.get("artifact_state") or {}).get("is_historical")),
        "label": "Stale investigation",
        "stale_reason": "Available telemetry is newer than the investigation freshness timestamp.",
    }
    return updated


def write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        f.write(serialize_json(payload))
    tmp.replace(path)


def write_if_changed(path, payload):
    existing = load_json(path)
    if not artifact_rewrite_needed(existing, payload):
        return {"artifact_written": False, "assistant_semantic_changed": False}
    semantic_changed = assistant_semantic_changed(existing, payload)
    write_json_atomic(path, payload)
    return {"artifact_written": True, "assistant_semantic_changed": semantic_changed}
