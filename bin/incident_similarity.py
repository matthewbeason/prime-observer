#!/usr/bin/env python3
"""Deterministic incident similarity model for Prime Observer."""

from pathlib import Path
import datetime as dt
import json


SCHEMA_VERSION = 1
MODEL_VERSION = "prime_observer.incident_similarity.v1"
MIN_MATCH_SCORE = 35.0
STRONG_MATCH_SCORE = 70.0

WEIGHTS = {
    "affected_services": 16,
    "target_class": 12,
    "resolver_members": 8,
    "gateway": 8,
    "adaptive_baseline_state": 8,
    "likely_issue": 10,
    "technical_condition": 8,
    "user_impact": 8,
    "application_experience": 6,
    "dependency_state": 6,
    "recovery_behavior": 5,
    "duration_bucket": 4,
    "external_context": 3,
    "guardrail_profile": 4,
}


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


def safe_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def normalize_text(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("/", "_")


def duration_minutes(payload):
    record = payload.get("incident_record") if isinstance(payload.get("incident_record"), dict) else {}
    selected = payload.get("selected_event") if isinstance(payload.get("selected_event"), dict) else {}
    for value in (record.get("duration_minutes"), selected.get("duration_minutes")):
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    start = parse_ts(record.get("started_at") or selected.get("first_anomalous_at") or selected.get("start"))
    end = parse_ts(selected.get("recovered_at") or selected.get("last_anomalous_at") or selected.get("end"))
    if start and end and end > start:
        return round((end - start).total_seconds() / 60.0, 1)
    return None


def duration_bucket(minutes):
    if minutes is None:
        return None
    if minutes < 30:
        return "short"
    if minutes < 120:
        return "medium"
    if minutes < 480:
        return "long"
    return "very_long"


def adaptive_members(health):
    adaptive = health.get("adaptive_baseline") if isinstance(health.get("adaptive_baseline"), dict) else {}
    members = adaptive.get("resolver_members") if isinstance(adaptive.get("resolver_members"), list) else []
    return [member for member in members if isinstance(member, dict)]


def adaptive_state(members):
    states = [member.get("baseline_state") for member in members if member.get("baseline_state")]
    if "failing" in states:
        return "failing"
    if "degraded_from_baseline" in states or "anomalous" in states:
        return "anomalous"
    if "elevated_but_stable" in states:
        return "elevated_but_stable"
    if "within_target" in states:
        return "within_target"
    return None


def recovery_behavior(payload):
    phases = payload.get("incident_phases") if isinstance(payload.get("incident_phases"), dict) else {}
    after = phases.get("after") if isinstance(phases.get("after"), dict) else {}
    selected = payload.get("selected_event") if isinstance(payload.get("selected_event"), dict) else {}
    if after.get("status"):
        return after.get("status")
    if selected.get("recovered_at"):
        return "recovered"
    return selected.get("lifecycle_state") or selected.get("status")


def impact_state(health, record):
    for key in ("observed_user_impact", "estimated_user_impact", "user_impact"):
        item = health.get(key) if isinstance(health.get(key), dict) else {}
        if item.get("state"):
            return item.get("state")
    return record.get("user_facing_impact")


def dependency_state(payload, health):
    dependency = payload.get("dependency_state") if isinstance(payload.get("dependency_state"), dict) else {}
    if dependency.get("state"):
        return dependency.get("state")
    groups = health.get("dependency_groups") if isinstance(health.get("dependency_groups"), list) else []
    if groups and isinstance(groups[0], dict):
        return groups[0].get("state")
    return None


def external_context(payload):
    for key in ("internet_conditions_context", "power_infrastructure_context"):
        item = payload.get(key) if isinstance(payload.get(key), dict) else {}
        if item.get("available"):
            return item.get("status") or "available"
    return "unavailable"


def guardrail_profile(members):
    values = []
    for member in members:
        for key in ("guardrail_status", "guardrail_breach", "suppression_reason", "incident_suppression_reason"):
            if member.get(key):
                values.append(str(member.get(key)))
    return sorted(set(values))


def first_supported(*values):
    for value in values:
        if value is not None and value != "" and value != []:
            return value
    return None


def incident_features(payload):
    record = payload.get("incident_record") if isinstance(payload.get("incident_record"), dict) else {}
    phases = payload.get("incident_phases") if isinstance(payload.get("incident_phases"), dict) else {}
    during = phases.get("during") if isinstance(phases.get("during"), dict) else {}
    selected = payload.get("selected_event") if isinstance(payload.get("selected_event"), dict) else {}
    health = payload.get("health_dimensions") if isinstance(payload.get("health_dimensions"), dict) else {}
    application = health.get("application_experience") if isinstance(health.get("application_experience"), dict) else {}
    members = adaptive_members(health)
    minutes = duration_minutes(payload)
    affected = safe_list(first_supported(record.get("affected_services"), during.get("affected_services"), selected.get("affected_targets")))
    target_class = selected.get("target_class") or record.get("target_class")
    likely_issue = record.get("likely_issue") or during.get("likely_issue")
    technical = health.get("technical_condition") if isinstance(health.get("technical_condition"), dict) else {}
    return {
        "incident_id": selected.get("id") or record.get("incident_id") or payload.get("id"),
        "affected_services": sorted({normalize_text(item) for item in affected}),
        "target_class": normalize_text(target_class) or None,
        "resolver_members": sorted({str(member.get("member_id") or member.get("role")) for member in members if member.get("member_id") or member.get("role")}),
        "gateway": any("gateway" in normalize_text(item) or normalize_text(item) == "gateway_probe" for item in affected) or normalize_text(target_class) == "gateway_probe",
        "adaptive_baseline_state": adaptive_state(members),
        "likely_issue": normalize_text(likely_issue) or None,
        "technical_condition": technical.get("state"),
        "user_impact": impact_state(health, record),
        "application_experience": application.get("state") or application.get("status"),
        "dependency_state": dependency_state(payload, health),
        "recovery_behavior": recovery_behavior(payload),
        "duration_bucket": duration_bucket(minutes),
        "external_context": external_context(payload),
        "guardrail_profile": guardrail_profile(members),
        "duration_minutes": minutes,
        "previous_recovery": recovery_behavior(payload),
        "previous_user_impact": impact_state(health, record),
        "previous_operator_feedback": (health.get("observed_user_impact") or {}).get("source") or (health.get("operator_impact_feedback") or {}).get("impact") if isinstance(health, dict) else None,
    }


def compare_sets(current, previous):
    left = set(current or [])
    right = set(previous or [])
    if not left or not right:
        return None
    return len(left & right) / len(left | right)


def compare_values(current, previous):
    if current is None or current == "" or previous is None or previous == "":
        return None
    return 1.0 if current == previous else 0.0


def dimension_score(name, current, previous):
    if name in {"affected_services", "resolver_members", "guardrail_profile"}:
        return compare_sets(current.get(name), previous.get(name))
    if name == "gateway":
        return 1.0 if bool(current.get(name)) == bool(previous.get(name)) else 0.0
    return compare_values(current.get(name), previous.get(name))


def dimension_label(name):
    return name.replace("_", " ").title()


def score_match(current, previous):
    breakdown = []
    matching = []
    different = []
    total = float(sum(WEIGHTS.values()))
    contribution = 0.0
    available = 0
    for name, weight in WEIGHTS.items():
        fraction = dimension_score(name, current, previous)
        matched = bool(fraction and fraction >= 0.5)
        value = round(weight * (fraction or 0.0), 3)
        contribution += value
        if fraction is not None:
            available += 1
            if matched:
                matching.append(dimension_label(name))
            else:
                different.append(dimension_label(name))
        breakdown.append({
            "dimension": name,
            "weight": weight,
            "current": current.get(name),
            "previous": previous.get(name),
            "matched": matched,
            "contribution": value,
        })
    score = round((contribution / total) * 100.0, 1)
    confidence = "high" if available >= 10 and score >= STRONG_MATCH_SCORE else "medium" if available >= 8 and score >= MIN_MATCH_SCORE else "low"
    return score, breakdown, matching, different, confidence


def has_core_cause_match(breakdown):
    core = {"affected_services", "target_class", "likely_issue", "gateway", "adaptive_baseline_state"}
    return any(item.get("dimension") in core and item.get("matched") for item in breakdown)


def pattern_label(features):
    affected = set(features.get("affected_services") or [])
    target = features.get("target_class")
    likely = features.get("likely_issue") or ""
    app = features.get("application_experience")
    adaptive = features.get("adaptive_baseline_state")
    if app in {"failing", "failed"} or "dns_service" in likely or "application_transaction_failure" in likely:
        return "dns_service_failure"
    if features.get("gateway") or target == "gateway_probe" or "gateway" in likely:
        return "gateway_instability"
    if adaptive in {"elevated_but_stable", "anomalous", "degraded_from_baseline"}:
        return "adaptive_baseline_event"
    if target == "resolver_probe" or "resolver_probes" in affected or "resolver" in likely:
        return "resolver_path_degradation"
    if target == "internet_probe" or "internet_probes" in affected:
        return "internet_path_instability"
    if len(affected) > 1 or "broad" in likely:
        return "broad_upstream_instability" if "internet_probes" in affected and "resolver_probes" in affected else "mixed_service_degradation"
    return "unknown_pattern"


def pattern_title(pattern):
    return pattern.replace("_", "-")


def match_summary(current_features, matches):
    if not matches:
        return "No historically similar completed incidents meet the deterministic similarity threshold."
    pattern = matches[0]["pattern"].replace("_", "-")
    count = len(matches)
    noun = "incident" if count == 1 else "incidents"
    recovery = [item.get("previous_recovery") for item in matches if item.get("previous_recovery")]
    difference = matches[0].get("different_dimensions") or []
    parts = [f"This incident most closely matches {count} previous {pattern} {noun}."]
    if recovery:
        parts.append(f"Previous events most often ended as {recovery[0]}.")
    if difference:
        parts.append(f"Current incident differs on {', '.join(difference[:2]).lower()}.")
    return " ".join(parts)


def evidence_refs(path, match_id):
    refs = [{"path": "viz/investigation.json", "reason": "Current investigation features."}]
    if path:
        refs.append({"path": path, "reason": f"Completed incident snapshot {match_id}."})
    return refs


def load_completed_snapshots(investigations_dir):
    snapshots = []
    path = Path(investigations_dir)
    if not path.exists():
        return snapshots
    for item in sorted(path.glob("*.json")):
        try:
            payload = json.loads(item.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            snapshots.append((f"investigations/{item.name}", payload))
    return snapshots


def build_incident_similarity(*, current_investigation, completed_snapshots, generated_at):
    current_features = incident_features(current_investigation or {})
    current_id = current_features.get("incident_id")
    selected = (current_investigation or {}).get("selected_event") if isinstance((current_investigation or {}).get("selected_event"), dict) else {}
    if not current_id or selected.get("lifecycle_state") == "none":
        return {
            "schema_version": SCHEMA_VERSION,
            "model_version": MODEL_VERSION,
            "generated_at": iso(generated_at),
            "current_incident": {"incident_id": current_id, "pattern": "unknown_pattern", "summary": "No current incident is available for similarity comparison."},
            "matches": [],
        }
    current_pattern = pattern_label(current_features)
    matches = []
    for path, snapshot in completed_snapshots or []:
        previous_features = incident_features(snapshot or {})
        previous_id = previous_features.get("incident_id")
        if not previous_id or previous_id == current_id:
            continue
        score, breakdown, matching, different, confidence = score_match(current_features, previous_features)
        if score < MIN_MATCH_SCORE or not has_core_cause_match(breakdown):
            continue
        pattern = pattern_label(previous_features)
        matches.append({
            "incident_id": previous_id,
            "score": score,
            "pattern": pattern,
            "summary": f"Previous {pattern_title(pattern)} with {', '.join(matching[:3]).lower() or 'limited matching evidence'}.",
            "similarity_breakdown": breakdown,
            "matching_dimensions": matching,
            "different_dimensions": different,
            "previous_duration": {"minutes": previous_features.get("duration_minutes"), "bucket": previous_features.get("duration_bucket")},
            "previous_recovery": previous_features.get("previous_recovery"),
            "previous_user_impact": previous_features.get("previous_user_impact"),
            "previous_operator_feedback": previous_features.get("previous_operator_feedback"),
            "evidence_refs": evidence_refs(path, previous_id),
            "confidence": confidence,
        })
    matches.sort(key=lambda item: (-item["score"], item["incident_id"]))
    matches = matches[:5]
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "generated_at": iso(generated_at),
        "current_incident": {
            "incident_id": current_id,
            "pattern": current_pattern,
            "summary": match_summary(current_features, matches),
            "strong_match_count": len([item for item in matches if item.get("score", 0) >= STRONG_MATCH_SCORE]),
        },
        "matches": matches,
    }
