#!/usr/bin/env python3
import datetime as dt
from collections import Counter
from typing import Any

from health_model import WAN_BAD, bucket_start
from target_metadata import target_metadata


INTERVAL_SUMMARY_SCHEMA_VERSION = 1
INTERVAL_SUMMARY_MODEL_VERSION = "prime_observer.interval_summary.v1"


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


def parse_float(value, fallback=None):
    try:
        return float(str(value).strip())
    except Exception:
        return fallback


def percentile(values, pct):
    vals = sorted(value for value in values if value is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * (pct / 100.0)
    lower = int(pos)
    upper = min(lower + 1, len(vals) - 1)
    weight = pos - lower
    return vals[lower] + ((vals[upper] - vals[lower]) * weight)


def rounded(value, digits=1):
    if value is None:
        return None
    return round(value, digits)


def normalize_row(row):
    timestamp = parse_ts(row.get("ts"))
    host = str(row.get("host") or "").strip()
    p95 = parse_float(row.get("p95_ms"), None)
    if timestamp is None or not host or p95 is None:
        return None
    meta = target_metadata(host)
    sent = parse_float(row.get("sent"), None)
    received = parse_float(row.get("received"), None)
    timeout = bool(sent and received is not None and received <= 0)
    return {
        "t": timestamp,
        "host": host,
        "target_class": row.get("target_class") or meta.get("target_class") or "unknown_probe",
        "target_label": row.get("target_label") or meta.get("target_label") or host,
        "member_id": meta.get("member_id"),
        "role": meta.get("role"),
        "p95": p95,
        "jitter": parse_float(row.get("jitter_ms"), 0.0) or 0.0,
        "loss": parse_float(row.get("loss_pct"), 0.0) or 0.0,
        "timeout": timeout,
    }


def rows_in_interval(rows, start, end):
    samples = []
    for row in rows:
        sample = normalize_row(row)
        if sample and start <= sample["t"] < end:
            samples.append(sample)
    return sorted(samples, key=lambda item: item["t"])


def group_metrics(samples, target_class):
    rows = [sample for sample in samples if sample.get("target_class") == target_class]
    p95 = [sample.get("p95") for sample in rows]
    jitter = [sample.get("jitter") for sample in rows]
    loss = [sample.get("loss") for sample in rows]
    raw_bad = [
        sample for sample in rows
        if sample.get("p95", 0.0) > WAN_BAD["p95"]
        or sample.get("jitter", 0.0) > WAN_BAD["jitter"]
        or sample.get("loss", 0.0) > WAN_BAD["loss"]
    ]
    if not rows:
        state = "unknown"
    elif any(sample.get("timeout") for sample in rows) or any((sample.get("loss") or 0.0) > WAN_BAD["loss"] for sample in rows):
        state = "failing"
    elif raw_bad:
        state = "elevated"
    else:
        state = "healthy"
    return {
        "state": state,
        "sample_count": len(rows),
        "median_latency_ms": rounded(percentile(p95, 50), 1),
        "p95_latency_ms": rounded(percentile(p95, 95), 1),
        "max_jitter_ms": rounded(max(jitter), 1) if jitter else None,
        "loss_rate_pct": rounded(sum(loss) / len(loss), 3) if loss else None,
        "timeout_count": len([sample for sample in rows if sample.get("timeout")]),
        "raw_bad_samples": len(raw_bad),
        "hosts": dict(Counter(sample.get("host") for sample in rows)),
    }


def application_summary(application_experience, start, end):
    if not isinstance(application_experience, dict):
        return {
            "state": "unknown",
            "dns_success": None,
            "https_success": None,
            "timeout_count": 0,
            "evidence": ["Application evidence is unavailable."],
        }
    dns = [item for item in application_experience.get("dns_transactions", []) if isinstance(item, dict)]
    https = application_experience.get("https_transaction") if isinstance(application_experience.get("https_transaction"), dict) else {}
    dns_success = bool(dns) and all(bool(item.get("success")) and not item.get("timeout") for item in dns)
    https_success = bool(https) and bool(https.get("success")) and not https.get("timeout")
    timeout_count = len([item for item in dns if item.get("timeout")]) + (1 if https.get("timeout") else 0)
    state = "working" if dns_success and https_success else "failing" if dns or https else "unknown"
    return {
        "state": state,
        "dns_success": dns_success if dns else None,
        "https_success": https_success if https else None,
        "timeout_count": timeout_count,
        "evidence": list(application_experience.get("evidence") or []),
    }


def interval_overlaps(a_start, a_end, b_start, b_end):
    return bool(a_start and a_end and b_start and b_end and a_start < b_end and a_end > b_start)


def incident_overlap(incidents, start, end):
    overlaps = []
    for incident in incidents or []:
        incident_start = parse_ts(incident.get("start") or incident.get("first_anomalous_at"))
        incident_end = parse_ts(incident.get("end") or incident.get("last_anomalous_at") or incident.get("recovered_at"))
        if not interval_overlaps(start, end, incident_start, incident_end):
            continue
        clipped_start = max(start, incident_start)
        clipped_end = min(end, incident_end)
        relation = "contains_interval" if incident_start <= start and incident_end >= end else "partial_overlap"
        if start <= incident_start and end >= incident_end:
            relation = "incident_inside_interval"
        overlaps.append({
            "id": incident.get("id") or f"interval-overlap-{len(overlaps) + 1}",
            "target_class": incident.get("target_class") or (incident.get("metrics") or {}).get("target_class"),
            "start": iso(incident_start),
            "end": iso(incident_end),
            "relation": relation,
            "overlap_start": iso(clipped_start),
            "overlap_end": iso(clipped_end),
            "status": incident.get("status") or incident.get("lifecycle_state"),
            "label": incident.get("label") or incident.get("selection_reason"),
        })
    return {"count": len(overlaps), "items": overlaps}


def adaptive_state(health_dimensions):
    members = (((health_dimensions or {}).get("adaptive_baseline") or {}).get("resolver_members") or [])
    states = [member.get("baseline_state") for member in members if isinstance(member, dict) and member.get("baseline_state")]
    if "failing" in states:
        return "failing"
    if "degraded_from_baseline" in states or "anomalous" in states:
        return "anomalous"
    if "elevated_but_stable" in states:
        return "elevated_but_stable"
    if "within_target" in states:
        return "within_target"
    return "unknown"


def baseline_comparison(health_dimensions):
    members = (((health_dimensions or {}).get("adaptive_baseline") or {}).get("resolver_members") or [])
    return {
        "adaptive_baseline_state": adaptive_state(health_dimensions),
        "resolver_members": [
            {
                "member_id": member.get("member_id"),
                "role": member.get("role"),
                "baseline_state": member.get("baseline_state"),
                "baseline_source": member.get("baseline_source"),
                "incident_eligible": member.get("incident_eligible"),
                "suppression_reason": member.get("incident_suppression_reason"),
            }
            for member in members if isinstance(member, dict)
        ],
    }


def likely_issue_for(gateway, internet, resolver, app, adaptive):
    if app.get("state") == "failing":
        return "Application transaction failure"
    if gateway.get("state") in {"elevated", "failing"}:
        return "Local gateway path"
    if internet.get("state") in {"elevated", "failing"} and resolver.get("state") in {"elevated", "failing"}:
        return "Broad upstream path"
    if resolver.get("state") in {"elevated", "failing"}:
        if adaptive == "elevated_but_stable":
            return "Established degraded resolver baseline"
        return "Resolver provider path"
    if internet.get("state") in {"elevated", "failing"}:
        return "Internet path"
    return "No active issue detected"


def overall_condition_for(gateway, internet, resolver, app, overlap, adaptive):
    if app.get("state") == "failing" or any(group.get("state") == "failing" for group in (gateway, internet, resolver)):
        return "failing"
    if overlap.get("count"):
        return "incident_overlap"
    if adaptive == "elevated_but_stable" and resolver.get("state") == "elevated":
        return "elevated_but_stable"
    if any(group.get("state") == "elevated" for group in (gateway, internet, resolver)):
        return "elevated"
    if all(group.get("state") in {"healthy", "unknown"} for group in (gateway, internet, resolver)) and app.get("state") in {"working", "unknown"}:
        return "healthy"
    return "unknown"


def services_for(gateway, internet, resolver, app):
    affected = []
    healthy = []
    for label, group in (("Gateway", gateway), ("Internet probes", internet), ("Resolver probes", resolver)):
        if group.get("state") in {"elevated", "failing"}:
            affected.append(label)
        elif group.get("state") == "healthy":
            healthy.append(label)
    if app.get("state") == "failing":
        affected.append("Application checks")
    elif app.get("state") == "working":
        healthy.append("Application checks")
    return affected, healthy


def summary_text(start, end, condition, likely_issue, affected, healthy, app, adaptive):
    start_text = start.strftime("%I:%M %p").lstrip("0")
    end_text = end.strftime("%I:%M %p").lstrip("0")
    if condition == "healthy":
        return f"Between {start_text} and {end_text}, Prime Observer did not detect active network degradation. Application checks remained {app.get('state', 'unknown')}."
    if adaptive == "elevated_but_stable" and "Resolver probes" in affected:
        return f"Between {start_text} and {end_text}, Prime Observer observed elevated latency on the resolver path. DNS and HTTPS checks continued succeeding, and the interval most closely represents an established degraded baseline rather than an active outage."
    return f"Between {start_text} and {end_text}, Prime Observer observed {likely_issue.lower()}. Affected services: {', '.join(affected) if affected else 'none detected'}. Healthy services: {', '.join(healthy) if healthy else 'none confirmed'}."


def build_interval_summary(*, rows, start, end, generated_at, incidents=None, health_dimensions=None, application_experience=None, source_path=None):
    start_ts = parse_ts(start)
    end_ts = parse_ts(end)
    if start_ts is None or end_ts is None or end_ts <= start_ts:
        raise ValueError("interval summary requires valid start and end timestamps")
    samples = rows_in_interval(rows, start_ts, end_ts)
    gateway = group_metrics(samples, "gateway_probe")
    internet = group_metrics(samples, "internet_probe")
    resolver = group_metrics(samples, "resolver_probe")
    app = application_summary(application_experience, start_ts, end_ts)
    overlap = incident_overlap(incidents or [], start_ts, end_ts)
    baseline = baseline_comparison(health_dimensions or {})
    adaptive = baseline.get("adaptive_baseline_state")
    condition = overall_condition_for(gateway, internet, resolver, app, overlap, adaptive)
    likely_issue = likely_issue_for(gateway, internet, resolver, app, adaptive)
    affected, healthy = services_for(gateway, internet, resolver, app)
    coverage = {
        "sample_count": len(samples),
        "wan_samples": len([sample for sample in samples if sample.get("target_class") in {"internet_probe", "resolver_probe"}]),
        "gateway_samples": len([sample for sample in samples if sample.get("target_class") == "gateway_probe"]),
        "has_samples": bool(samples),
        "source_path": source_path,
    }
    metrics = {
        "gateway": gateway,
        "internet": internet,
        "resolver": resolver,
        "application": app,
        "adaptive_baseline_state": adaptive,
        "timeout_count": gateway.get("timeout_count", 0) + internet.get("timeout_count", 0) + resolver.get("timeout_count", 0) + app.get("timeout_count", 0),
        "dns_success": app.get("dns_success"),
        "https_success": app.get("https_success"),
    }
    return {
        "schema_version": INTERVAL_SUMMARY_SCHEMA_VERSION,
        "model_version": INTERVAL_SUMMARY_MODEL_VERSION,
        "generated_at": iso(generated_at),
        "start": iso(start_ts),
        "end": iso(end_ts),
        "duration": {"seconds": int((end_ts - start_ts).total_seconds()), "label": f"{int((end_ts - start_ts).total_seconds() // 60)} minutes"},
        "current_or_historical": "current" if end_ts >= generated_at - dt.timedelta(minutes=30) else "historical",
        "coverage": coverage,
        "overall_condition": condition,
        "user_impact": "none_detected" if app.get("state") == "working" and condition in {"healthy", "elevated", "elevated_but_stable"} else "possible",
        "application_summary": app,
        "likely_issue": likely_issue,
        "affected_services": affected,
        "healthy_services": healthy,
        "incident_overlap": overlap,
        "baseline_comparison": baseline,
        "confidence": "high" if samples else "low",
        "summary": summary_text(start_ts, end_ts, condition, likely_issue, affected, healthy, app, adaptive),
        "evidence_refs": [
            {"path": source_path or "viz/latest.csv", "reason": "Telemetry rows within requested interval."},
            {"path": "viz/dashboard_health.json", "reason": "Adaptive baseline and application context."},
        ],
        "metrics": metrics,
    }


def latest_bucket_interval(rows, *, generated_at):
    latest = None
    for row in rows:
        sample = normalize_row(row)
        if sample is None:
            continue
        latest = sample["t"] if latest is None or sample["t"] > latest else latest
    end_anchor = latest or generated_at
    start = dt.datetime.fromtimestamp(bucket_start(end_anchor), tz=dt.timezone.utc)
    return start, start + dt.timedelta(minutes=15)
