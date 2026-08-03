from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from health_model import ATTRIBUTION_CUT_MINUTES, WAN_BAD, WAN_BAD_PERSISTENCE, lan_elevation
from target_metadata import target_metadata


HEALTH_DIMENSIONS_SCHEMA_VERSION = 1
HEALTH_DIMENSIONS_MODEL_VERSION = "prime_observer.health_dimensions.v1"
ADAPTIVE_BASELINE_MODEL_VERSION = "prime_observer.adaptive_baseline.v1.phase_a"
DIAGNOSTIC_EVIDENCE_MODEL_VERSION = "prime_observer.diagnostic_evidence.v1"
APPLICATION_EXPERIENCE_MODEL_VERSION = "prime_observer.application_experience.v1"
OPERATOR_IMPACT_FEEDBACK_MODEL_VERSION = "prime_observer.operator_impact_feedback.v1"

TECHNICAL_STATES = {"healthy", "elevated", "degraded", "severe", "unknown"}
USER_IMPACT_STATES = {"not_observed", "unlikely", "possible", "likely", "confirmed", "unknown"}
ESTIMATED_USER_IMPACT_STATES = {"none_expected", "low", "possible", "likely", "severe", "unknown"}
OBSERVED_USER_IMPACT_STATES = {"none_reported", "reported_minor", "reported_major", "confirmed_service_failure", "unknown"}
OPERATIONAL_RISK_STATES = {"low", "guarded", "elevated", "high", "critical", "unknown"}
CONFIDENCE_STATES = {"low", "medium", "high"}
ATTRIBUTION_DOMAINS = {
    "local_gateway",
    "local_lan_or_wifi",
    "broad_isp_path",
    "upstream_transit_route",
    "resolver_provider_path",
    "resolver_endpoint_or_pop",
    "broad_internet_condition",
    "power_environmental",
    "mixed",
    "unknown",
}
DEPENDENCY_STATES = {
    "both_healthy",
    "primary_degraded_secondary_healthy",
    "primary_healthy_secondary_degraded",
    "both_degraded",
    "active_healthy_peer_degraded",
    "active_degraded_fallback_healthy",
    "active_path_unknown",
    "no_usable_fallback",
    "insufficient_evidence",
}

DIRECT_DNS_HEALTHY_MS = 80.0
FRESHNESS_SECONDS = 60 * 60
APPLICATION_EXPERIENCE_FRESHNESS_SECONDS = 35 * 60
OPERATOR_IMPACT_FEEDBACK_FRESHNESS_SECONDS = 24 * 60 * 60
OPERATOR_IMPACT_FEEDBACK_STATES = {
    "none_observed",
    "minor_slowness",
    "intermittent_failures",
    "major_disruption",
    "full_outage",
    "unknown",
}
MAX_OPERATOR_FEEDBACK_NOTE_CHARS = 500
ADAPTIVE_BASELINE_MIN_SAMPLES = 12
ADAPTIVE_BASELINE_MIN_WINDOW_MINUTES = 60
ADAPTIVE_BASELINE_RECENT_SAMPLES = 3
ADAPTIVE_BASELINE_STABLE_SPREAD_PCT = 35.0
ADAPTIVE_BASELINE_WORSENING_DELTA_PCT = 35.0
ADAPTIVE_BASELINE_SEVERE_P95_MS = 500.0


def parse_ts(value: Any) -> dt.datetime | None:
    raw = str(value or "").strip()
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


def parse_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_diagnostic_item(item: dict[str, Any], *, generated_at: dt.datetime | None = None) -> dict[str, Any]:
    observed = parse_ts(item.get("observed_at") or item.get("checked_at") or item.get("generated_at"))
    freshness = str(item.get("freshness") or "unknown").strip().lower() or "unknown"
    if observed and generated_at and freshness == "unknown":
        age = max(0.0, (generated_at - observed).total_seconds())
        freshness = "fresh" if age <= FRESHNESS_SECONDS else "stale"
    elif freshness not in {"fresh", "stale", "missing", "unknown"}:
        freshness = "unknown"
    return {
        **item,
        "type": str(item.get("type") or "unknown"),
        "freshness": freshness,
        "provenance": str(item.get("provenance") or "unknown"),
        "confidence": str(item.get("confidence") or "low").lower(),
        "observed_at": iso(observed),
        "is_current": freshness == "fresh",
    }


def load_diagnostic_evidence(path: Path, *, generated_at: dt.datetime | None = None) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "model_version": DIAGNOSTIC_EVIDENCE_MODEL_VERSION,
            "status": "missing",
            "items": [],
            "limitations": ["Diagnostic evidence artifact is absent."],
        }
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema_version": 1,
            "model_version": DIAGNOSTIC_EVIDENCE_MODEL_VERSION,
            "status": "malformed",
            "items": [],
            "limitations": [f"Diagnostic evidence artifact is unreadable: {exc}"],
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": 1,
            "model_version": DIAGNOSTIC_EVIDENCE_MODEL_VERSION,
            "status": "malformed",
            "items": [],
            "limitations": ["Diagnostic evidence artifact root is not an object."],
        }
    items = [
        normalize_diagnostic_item(item, generated_at=generated_at)
        for item in payload.get("items", [])
        if isinstance(item, dict)
    ]
    return {
        "schema_version": payload.get("schema_version", 1),
        "model_version": payload.get("model_version") or DIAGNOSTIC_EVIDENCE_MODEL_VERSION,
        "status": payload.get("status") or "ok",
        "generated_at": payload.get("generated_at"),
        "items": items,
        "limitations": [],
    }


def normalize_application_experience(payload: dict[str, Any], *, generated_at: dt.datetime | None = None) -> dict[str, Any]:
    checked_at = parse_ts(payload.get("generated_at"))
    freshness_raw = payload.get("freshness")
    freshness_payload = freshness_raw if isinstance(freshness_raw, dict) else {}
    stale_after = parse_float(freshness_payload.get("stale_after_seconds"), APPLICATION_EXPERIENCE_FRESHNESS_SECONDS)
    freshness = "unknown"
    if checked_at and generated_at:
        age = max(0.0, (generated_at - checked_at).total_seconds())
        freshness = "fresh" if age <= (stale_after or APPLICATION_EXPERIENCE_FRESHNESS_SECONDS) else "stale"
    dns_transactions = [item for item in payload.get("dns_transactions", []) if isinstance(item, dict)]
    https_raw = payload.get("https_transaction")
    https_transaction = https_raw if isinstance(https_raw, dict) else {}
    failures = [item for item in dns_transactions if not item.get("success")]
    if https_transaction and not https_transaction.get("success"):
        failures.append(https_transaction)
    system_dns = next((item for item in dns_transactions if item.get("role") == "system"), None)
    direct_dns = [item for item in dns_transactions if item.get("role") in {"primary", "secondary"}]
    evidence = []
    limitations = []
    if freshness != "fresh":
        limitations.append("Application experience evidence is unavailable or stale.")
    elif system_dns and system_dns.get("success"):
        evidence.append("System DNS queries are succeeding normally.")
    elif system_dns:
        evidence.append("System DNS query failed or timed out.")
    for item in direct_dns:
        role = item.get("role") or "direct"
        if item.get("timeout"):
            evidence.append(f"Direct {role} resolver queries are timing out.")
        elif item.get("success"):
            evidence.append(f"Direct {role} resolver queries are succeeding.")
    if https_transaction.get("success"):
        evidence.append("HTTPS session establishment remains normal.")
    elif https_transaction:
        evidence.append("HTTPS transaction failed.")
    return {
        "schema_version": payload.get("schema_version", 1),
        "model_version": payload.get("model_version") or APPLICATION_EXPERIENCE_MODEL_VERSION,
        "status": payload.get("status") or payload.get("overall_status") or "unknown",
        "generated_at": iso(checked_at),
        "freshness": freshness,
        "is_current": freshness == "fresh",
        "dns_transactions": dns_transactions,
        "https_transaction": https_transaction,
        "failure_counts": payload.get("failure_counts") if isinstance(payload.get("failure_counts"), dict) else {"total": len(failures)},
        "latency_summaries": payload.get("latency_summaries") if isinstance(payload.get("latency_summaries"), dict) else {},
        "evidence": evidence,
        "limitations": limitations + [item for item in payload.get("limitations", []) if isinstance(item, str)],
    }


def load_application_experience(path: Path, *, generated_at: dt.datetime | None = None) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "model_version": APPLICATION_EXPERIENCE_MODEL_VERSION,
            "status": "missing",
            "freshness": "missing",
            "is_current": False,
            "dns_transactions": [],
            "https_transaction": {},
            "failure_counts": {"total": 0},
            "latency_summaries": {},
            "evidence": ["Application evidence is unavailable or stale."],
            "limitations": ["Application experience artifact is absent."],
        }
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema_version": 1,
            "model_version": APPLICATION_EXPERIENCE_MODEL_VERSION,
            "status": "malformed",
            "freshness": "unknown",
            "is_current": False,
            "dns_transactions": [],
            "https_transaction": {},
            "failure_counts": {"total": 0},
            "latency_summaries": {},
            "evidence": ["Application evidence is unavailable or stale."],
            "limitations": [f"Application experience artifact is unreadable: {exc}"],
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": 1,
            "model_version": APPLICATION_EXPERIENCE_MODEL_VERSION,
            "status": "malformed",
            "freshness": "unknown",
            "is_current": False,
            "dns_transactions": [],
            "https_transaction": {},
            "failure_counts": {"total": 0},
            "latency_summaries": {},
            "evidence": ["Application evidence is unavailable or stale."],
            "limitations": ["Application experience artifact root is not an object."],
        }
    return normalize_application_experience(payload, generated_at=generated_at)


def sanitize_operator_note(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_OPERATOR_FEEDBACK_NOTE_CHARS]


def operator_feedback_unavailable(status: str, limitation: str, *, incident_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model_version": OPERATOR_IMPACT_FEEDBACK_MODEL_VERSION,
        "status": status,
        "incident_id": incident_id,
        "observed_at": None,
        "impact_state": "unknown",
        "note": "",
        "source": "operator",
        "freshness": "missing" if status == "missing" else "unknown",
        "is_current": False,
        "association": "none",
        "limitations": [limitation],
    }


def normalize_operator_impact_feedback(
    payload: dict[str, Any],
    *,
    current_incident_id: str | None,
    generated_at: dt.datetime | None = None,
) -> dict[str, Any]:
    observed_at = parse_ts(payload.get("observed_at"))
    incident_id = str(payload.get("incident_id") or "").strip()
    impact_state = str(payload.get("impact_state") or "unknown").strip().lower()
    limitations = []
    if impact_state not in OPERATOR_IMPACT_FEEDBACK_STATES:
        impact_state = "unknown"
        limitations.append("Operator impact feedback state is invalid.")
    if payload.get("cleared"):
        impact_state = "unknown"
        limitations.append("Operator impact feedback was cleared.")
    freshness = "unknown"
    if observed_at and generated_at:
        age = max(0.0, (generated_at - observed_at).total_seconds())
        freshness = "fresh" if age <= OPERATOR_IMPACT_FEEDBACK_FRESHNESS_SECONDS else "stale"
    association = "current_incident" if current_incident_id and incident_id == current_incident_id else "mismatched_incident"
    if not current_incident_id:
        association = "no_current_incident"
    if not incident_id:
        association = "missing_incident"
    is_current = freshness == "fresh" and association == "current_incident" and impact_state != "unknown" and not payload.get("cleared")
    if association != "current_incident":
        limitations.append("Operator impact feedback does not match the current incident.")
    if freshness != "fresh":
        limitations.append("Operator impact feedback is unavailable or stale.")
    return {
        "schema_version": payload.get("schema_version", 1),
        "model_version": payload.get("model_version") or OPERATOR_IMPACT_FEEDBACK_MODEL_VERSION,
        "status": "ok" if is_current else "unavailable",
        "incident_id": incident_id or None,
        "current_incident_id": current_incident_id,
        "observed_at": iso(observed_at),
        "impact_state": impact_state,
        "note": sanitize_operator_note(payload.get("note")),
        "source": "operator",
        "freshness": freshness,
        "is_current": is_current,
        "association": association,
        "limitations": list(dict.fromkeys(limitations)),
    }


def load_operator_impact_feedback(
    path: Path,
    *,
    current_incident_id: str | None,
    generated_at: dt.datetime | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return operator_feedback_unavailable("missing", "Operator impact feedback artifact is absent.", incident_id=current_incident_id)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return operator_feedback_unavailable("malformed", f"Operator impact feedback artifact is unreadable: {exc}", incident_id=current_incident_id)
    if not isinstance(payload, dict):
        return operator_feedback_unavailable("malformed", "Operator impact feedback artifact root is not an object.", incident_id=current_incident_id)
    return normalize_operator_impact_feedback(payload, current_incident_id=current_incident_id, generated_at=generated_at)


def normalize_sample(row: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = parse_ts(row.get("ts"))
    host = str(row.get("host") or "").strip()
    p95 = parse_float(row.get("p95_ms"), None)
    if timestamp is None or not host or p95 is None:
        return None
    meta = target_metadata(host)
    return {
        "t": timestamp,
        "host": host,
        "target_label": row.get("target_label") or meta.get("target_label") or host,
        "target_class": row.get("target_class") or meta.get("target_class") or "unknown_probe",
        "dependency_group_id": meta.get("dependency_group_id"),
        "dependency_type": meta.get("dependency_type"),
        "member_id": meta.get("member_id"),
        "role": meta.get("role"),
        "provider": meta.get("provider"),
        "endpoint": meta.get("endpoint") or host,
        "phase": str(row.get("phase_label") or "FIBER").strip().upper(),
        "p95": p95,
        "jitter": parse_float(row.get("jitter_ms"), 0.0) or 0.0,
        "loss": parse_float(row.get("loss_pct"), 0.0) or 0.0,
        "baseline_p95": parse_float(row.get("baseline_p95"), None),
        "baseline_delta_pct": parse_float(row.get("baseline_delta_pct"), None),
    }


def mark_persistence(samples: list[dict[str, Any]], *, key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    streaks: dict[tuple[Any, ...], int] = {}
    out = []
    for sample in sorted(samples, key=lambda item: item["t"]):
        key = tuple(sample.get(field) for field in key_fields)
        raw_bad = (
            sample.get("p95", 0.0) > WAN_BAD["p95"]
            or sample.get("jitter", 0.0) > WAN_BAD["jitter"]
            or sample.get("loss", 0.0) > WAN_BAD["loss"]
        )
        streaks[key] = streaks.get(key, 0) + 1 if raw_bad else 0
        item = dict(sample)
        item["raw_bad"] = raw_bad
        item["is_bad"] = streaks[key] >= WAN_BAD_PERSISTENCE
        out.append(item)
    return out


def max_or_none(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return max(clean) if clean else None


def percentile(values: list[float | None], pct: float) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * (pct / 100.0)
    lower = int(pos)
    upper = min(lower + 1, len(clean) - 1)
    weight = pos - lower
    return clean[lower] + ((clean[upper] - clean[lower]) * weight)


def rounded(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def direct_dns_success_for_member(application_experience: dict[str, Any] | None, diagnostics: list[dict[str, Any]], member: dict[str, Any]) -> bool:
    app = application_transaction_summary(application_experience)
    role = str(member.get("role") or "").lower()
    endpoint = str(member.get("endpoint") or member.get("host") or "").lower()
    if app.get("current"):
        for item in (application_experience or {}).get("dns_transactions", []):
            if not isinstance(item, dict) or item.get("role") == "system":
                continue
            item_role = str(item.get("role") or "").lower()
            item_endpoint = str(item.get("resolver_endpoint") or "").lower()
            if (role and item_role == role) or (endpoint and item_endpoint == endpoint):
                return bool(item.get("success")) and not bool(item.get("timeout"))
    for item in diagnostics:
        if not item.get("is_current") or item.get("type") != "direct_dns_query_measurement":
            continue
        if not diagnostic_matches(item, member):
            continue
        status = diagnostic_status(item)
        if status in {"timeout", "failed", "failure", "error"}:
            return False
        latencies = item.get("latency_ms") if isinstance(item.get("latency_ms"), list) else []
        if latencies:
            return True
    return False


def member_dns_failed(application_experience: dict[str, Any] | None, member: dict[str, Any]) -> bool:
    app = application_transaction_summary(application_experience)
    if not app.get("current"):
        return False
    role = str(member.get("role") or "").lower()
    endpoint = str(member.get("endpoint") or member.get("host") or "").lower()
    for item in (application_experience or {}).get("dns_transactions", []):
        if not isinstance(item, dict) or item.get("role") == "system":
            continue
        item_role = str(item.get("role") or "").lower()
        item_endpoint = str(item.get("resolver_endpoint") or "").lower()
        if (role and item_role == role) or (endpoint and item_endpoint == endpoint):
            return not bool(item.get("success"))
    return False


def adaptive_baseline_guardrails(
    *,
    member: dict[str, Any],
    members: list[dict[str, Any]],
    gateway: dict[str, Any],
    internet: dict[str, Any],
    dependency: dict[str, Any],
    observed: dict[str, Any],
    application_experience: dict[str, Any] | None,
    diagnostics: list[dict[str, Any]],
    worsening: bool,
) -> list[str]:
    app = application_transaction_summary(application_experience)
    condition = member.get("technical_condition") or {}
    samples = member.get("samples") or []
    breaches = []
    if any((sample.get("loss") or 0.0) > WAN_BAD["loss"] for sample in samples):
        breaches.append("packet_loss_above_threshold")
    if app.get("current") and app.get("total_timeouts", 0) > 0:
        breaches.append("timeout")
    if member_dns_failed(application_experience, member):
        breaches.append("dns_failure")
    if app.get("current") and (app.get("system_dns_failed") or app.get("https_failed") or app.get("broad_failure_count", 0) > 0):
        breaches.append("application_failure")
    if gateway.get("state") in {"degraded", "severe"}:
        breaches.append("gateway_degradation")
    degraded_members = [item for item in members if condition_rank((item.get("technical_condition") or {}).get("state")) >= 2]
    if len(degraded_members) >= 2:
        breaches.append("both_resolver_members_degraded")
    if dependency.get("active_member") == member.get("member_id") and condition_rank(condition.get("state")) >= 2 and dependency.get("fallback_status") != "healthy":
        breaches.append("active_resolver_degraded_without_proven_healthy_fallback")
    if internet.get("state") in {"degraded", "severe"} and condition_rank(condition.get("state")) >= 2:
        breaches.append("broad_correlated_resolver_and_internet_degradation")
    if worsening:
        breaches.append("rapid_worsening")
    if (condition.get("max_p95_ms") or 0.0) >= ADAPTIVE_BASELINE_SEVERE_P95_MS:
        breaches.append("severe_excursion")
    if observed.get("state") in {"reported_major", "confirmed_service_failure"}:
        breaches.append("reported_major_or_confirmed_impact")
    for item in diagnostics:
        if not item.get("is_current"):
            continue
        if diagnostic_matches(item, member) and diagnostic_status(item) in {"timeout", "failed", "failure", "error"}:
            breaches.append("dns_failure")
    return list(dict.fromkeys(breaches))


def adaptive_resolver_member_baseline(
    *,
    member: dict[str, Any],
    members: list[dict[str, Any]],
    gateway: dict[str, Any],
    internet: dict[str, Any],
    dependency: dict[str, Any],
    observed: dict[str, Any],
    application_experience: dict[str, Any] | None,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    samples = sorted(member.get("samples") or [], key=lambda item: item.get("t"))
    p95_values = [sample.get("p95") for sample in samples if sample.get("p95") is not None]
    start = samples[0].get("t") if samples else None
    end = samples[-1].get("t") if samples else None
    window_minutes = int((end - start).total_seconds() // 60) if start and end else 0
    learned = {
        "min_p95_ms": rounded(min(p95_values), 1) if p95_values else None,
        "p25_p95_ms": rounded(percentile(p95_values, 25), 1),
        "median_p95_ms": rounded(percentile(p95_values, 50), 1),
        "p75_p95_ms": rounded(percentile(p95_values, 75), 1),
        "p90_p95_ms": rounded(percentile(p95_values, 90), 1),
        "max_p95_ms": rounded(max(p95_values), 1) if p95_values else None,
    }
    baseline_samples = samples[:-ADAPTIVE_BASELINE_RECENT_SAMPLES] if len(samples) > ADAPTIVE_BASELINE_RECENT_SAMPLES else samples
    recent_samples = samples[-ADAPTIVE_BASELINE_RECENT_SAMPLES:] if samples else []
    baseline_p95 = [sample.get("p95") for sample in baseline_samples if sample.get("p95") is not None]
    recent_p95 = [sample.get("p95") for sample in recent_samples if sample.get("p95") is not None]
    baseline_median = percentile(baseline_p95, 50)
    recent_median = percentile(recent_p95, 50)
    delta_pct = ((recent_median - baseline_median) / baseline_median) * 100.0 if baseline_median and recent_median is not None else None
    recent_spread_pct = None
    if recent_p95:
        recent_median_for_spread = percentile(recent_p95, 50) or 1.0
        recent_spread_pct = ((percentile(recent_p95, 75) or 0.0) - (percentile(recent_p95, 25) or 0.0)) / recent_median_for_spread * 100.0
    spread_pct = recent_spread_pct
    enough_samples = len(samples) >= ADAPTIVE_BASELINE_MIN_SAMPLES and window_minutes >= ADAPTIVE_BASELINE_MIN_WINDOW_MINUTES
    absolute_breached = any(
        (sample.get("p95") or 0.0) > WAN_BAD["p95"]
        or (sample.get("jitter") or 0.0) > WAN_BAD["jitter"]
        or (sample.get("loss") or 0.0) > WAN_BAD["loss"]
        for sample in samples
    )
    elevated = bool(learned["median_p95_ms"] is not None and learned["median_p95_ms"] > WAN_BAD["p95"])
    stable = bool(spread_pct is not None and spread_pct <= ADAPTIVE_BASELINE_STABLE_SPREAD_PCT)
    worsening = bool(delta_pct is not None and delta_pct >= ADAPTIVE_BASELINE_WORSENING_DELTA_PCT)
    improving = bool(delta_pct is not None and delta_pct <= -ADAPTIVE_BASELINE_WORSENING_DELTA_PCT)
    direct_dns_ok = direct_dns_success_for_member(application_experience, diagnostics, member)
    app = application_transaction_summary(application_experience)
    app_ok = bool(app.get("current") and app.get("healthy_system_dns") and app.get("healthy_https") and app.get("total_failures", 0) == 0 and app.get("total_timeouts", 0) == 0)
    no_reported_impact = observed.get("state") in {"none_reported", "unknown"}
    guardrails = adaptive_baseline_guardrails(
        member=member,
        members=members,
        gateway=gateway,
        internet=internet,
        dependency=dependency,
        observed=observed,
        application_experience=application_experience,
        diagnostics=diagnostics,
        worsening=worsening,
    )
    if not samples:
        state = "unknown"
        eligible = False
        reason = "insufficient_evidence"
        confidence = "low"
    elif guardrails:
        if guardrails == ["rapid_worsening"]:
            state = "degraded_from_baseline"
        else:
            state = "failing" if any(item in guardrails for item in {"packet_loss_above_threshold", "timeout", "dns_failure", "application_failure", "gateway_degradation", "both_resolver_members_degraded"}) else "anomalous"
        eligible = True
        reason = None
        confidence = "high" if enough_samples else "medium"
    elif not enough_samples:
        state = "unknown"
        eligible = bool(absolute_breached)
        reason = "insufficient_baseline_evidence"
        confidence = "low"
    elif worsening:
        state = "degraded_from_baseline"
        eligible = True
        reason = None
        confidence = "high"
    elif improving and direct_dns_ok and app_ok and no_reported_impact:
        state = "recovering"
        eligible = True
        reason = None
        confidence = "medium"
    elif elevated and stable and direct_dns_ok and app_ok and no_reported_impact:
        state = "elevated_but_stable"
        eligible = False
        reason = "established_degraded_baseline"
        confidence = "high"
    elif not absolute_breached:
        state = "within_target"
        eligible = False
        reason = "within_target"
        confidence = "high" if enough_samples else "medium"
    else:
        state = "anomalous"
        eligible = True
        reason = None
        confidence = "medium"
    return {
        "baseline_state": state,
        "baseline_version": f"{member.get('member_id') or member.get('host') or 'resolver'}:{iso(start)}:{iso(end)}",
        "baseline_model_version": ADAPTIVE_BASELINE_MODEL_VERSION,
        "learned_range": learned,
        "baseline_window": {"start": iso(start), "end": iso(end), "duration_minutes": window_minutes},
        "baseline_sample_count": len(samples),
        "deviation_from_baseline": {
            "baseline_median_p95_ms": rounded(baseline_median, 1),
            "recent_median_p95_ms": rounded(recent_median, 1),
            "delta_pct": rounded(delta_pct, 1),
            "spread_pct": rounded(spread_pct, 1),
            "spread_basis": "recent_p95_iqr",
            "worsening_trend": worsening,
            "improving_trend": improving,
        },
        "absolute_threshold_state": "breached" if absolute_breached else "within_target",
        "guardrail_breaches": guardrails,
        "incident_eligible": eligible,
        "incident_suppression_reason": reason,
        "confidence": confidence,
        "evidence_window": {
            "start": iso(start),
            "end": iso(end),
            "sample_count": len(samples),
            "required_sample_count": ADAPTIVE_BASELINE_MIN_SAMPLES,
            "required_duration_minutes": ADAPTIVE_BASELINE_MIN_WINDOW_MINUTES,
            "direct_dns_success": direct_dns_ok,
            "system_dns_success": bool(app.get("healthy_system_dns")),
            "https_success": bool(app.get("healthy_https")),
            "no_reported_user_impact": no_reported_impact,
            "unrelated_target_groups_broadly_degraded": internet.get("state") in {"degraded", "severe"},
        },
    }


def suppressed_adaptive_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for member in members:
        adaptive = member.get("adaptive_baseline") if isinstance(member.get("adaptive_baseline"), dict) else None
        if not adaptive:
            continue
        evidence = adaptive.get("evidence_window") if isinstance(adaptive.get("evidence_window"), dict) else {}
        deviation = adaptive.get("deviation_from_baseline") if isinstance(adaptive.get("deviation_from_baseline"), dict) else {}
        if (
            adaptive.get("baseline_state") == "elevated_but_stable"
            and adaptive.get("incident_suppression_reason") == "established_degraded_baseline"
            and not adaptive.get("guardrail_breaches")
            and adaptive.get("incident_eligible") is False
            and evidence.get("direct_dns_success") is True
            and evidence.get("system_dns_success") is True
            and evidence.get("https_success") is True
            and evidence.get("no_reported_user_impact") is True
            and not deviation.get("worsening_trend")
        ):
            out.append(member)
    return out


def apply_adaptive_current_condition(current_condition: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    suppressed = suppressed_adaptive_members(members)
    if not suppressed:
        return current_condition
    roles = ", ".join(str(member.get("role") or member.get("member_id") or "resolver") for member in suppressed)
    adjusted = dict(current_condition)
    adjusted["state"] = "elevated"
    adjusted["confidence"] = "high"
    adjusted["adaptive_baseline_state"] = "elevated_but_stable"
    adjusted["incident_eligible"] = False
    adjusted["incident_suppression_reason"] = "established_degraded_baseline"
    adjusted["drivers"] = list(dict.fromkeys([
        f"{roles.title()} resolver latency remains above original target but stable",
        "DNS and web checks continue succeeding",
        "No active incident is detected",
        *adjusted.get("drivers", []),
    ]))
    return adjusted


def diagnostic_matches(item: dict[str, Any], member: dict[str, Any]) -> bool:
    tokens = {
        str(member.get("host") or ""),
        str(member.get("endpoint") or ""),
        str(member.get("member_id") or ""),
        str(member.get("role") or ""),
    }
    candidates = {
        str(item.get("target") or ""),
        str(item.get("endpoint") or ""),
        str(item.get("member_id") or ""),
        str(item.get("active_member_id") or ""),
        str((item.get("target_association") or {}).get("member_id") if isinstance(item.get("target_association"), dict) else item.get("target_association") or ""),
    }
    return bool({token.lower() for token in tokens if token} & {token.lower() for token in candidates if token})


def member_technical_condition(member: dict[str, Any], diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    diagnostics = diagnostics or []
    samples = member.get("samples", [])
    if not samples:
        return {
            "state": "unknown",
            "confidence": "low",
            "drivers": [],
            "missing_evidence": ["telemetry"],
            "sample_count": 0,
        }
    raw_bad = [sample for sample in samples if sample.get("raw_bad")]
    sustained = [sample for sample in samples if sample.get("is_bad")]
    max_p95 = max_or_none([sample.get("p95") for sample in samples])
    max_jitter = max_or_none([sample.get("jitter") for sample in samples])
    max_loss = max_or_none([sample.get("loss") for sample in samples])
    max_baseline_delta = max_or_none([sample.get("baseline_delta_pct") for sample in samples])
    current_dns = [item for item in diagnostics if item.get("is_current") and item.get("type") == "direct_dns_query_measurement" and diagnostic_matches(item, member)]
    dns_normal = False
    for item in current_dns:
        latencies = item.get("latency_ms") if isinstance(item.get("latency_ms"), list) else []
        if latencies and max(float(value) for value in latencies) <= DIRECT_DNS_HEALTHY_MS:
            dns_normal = True

    drivers = []
    if max_p95 is not None and max_p95 > WAN_BAD["p95"]:
        drivers.append(f"p95 exceeded {WAN_BAD['p95']} ms")
    if max_jitter is not None and max_jitter > WAN_BAD["jitter"]:
        drivers.append(f"jitter exceeded {WAN_BAD['jitter']} ms")
    if max_loss is not None and max_loss > WAN_BAD["loss"]:
        drivers.append(f"loss exceeded {WAN_BAD['loss']}%")
    if max_baseline_delta is not None and max_baseline_delta >= 100:
        drivers.append("p95 was at least 100% above baseline")
    if dns_normal:
        drivers.append("direct DNS query latency was normal despite probe latency")

    if sustained and dns_normal:
        state = "elevated"
        confidence = "medium"
    elif sustained:
        state = "severe" if (max_p95 or 0.0) >= 240 or (max_loss or 0.0) > WAN_BAD["loss"] else "degraded"
        confidence = "high" if len(samples) >= WAN_BAD_PERSISTENCE else "medium"
    elif raw_bad:
        state = "elevated"
        confidence = "medium"
    else:
        state = "healthy"
        confidence = "high" if len(samples) >= WAN_BAD_PERSISTENCE else "medium"

    return {
        "state": state,
        "confidence": confidence,
        "drivers": drivers,
        "missing_evidence": [],
        "sample_count": len(samples),
        "raw_bad_samples": len(raw_bad),
        "sustained_bad_samples": len(sustained),
        "max_p95_ms": round(max_p95, 1) if max_p95 is not None else None,
        "max_jitter_ms": round(max_jitter, 1) if max_jitter is not None else None,
        "max_loss_pct": round(max_loss, 2) if max_loss is not None else None,
    }


def condition_rank(state: str | None) -> int:
    return {"unknown": -1, "healthy": 0, "elevated": 1, "degraded": 2, "severe": 3}.get(state or "unknown", -1)


def active_member_from_diagnostics(diagnostics: list[dict[str, Any]], members: list[dict[str, Any]]) -> tuple[str | None, str]:
    for item in diagnostics:
        if not item.get("is_current") or item.get("type") != "active_dependency_path":
            continue
        active = item.get("active_member_id") or item.get("member_id")
        if not active:
            continue
        active_lower = str(active).lower()
        for member in members:
            if active_lower in {
                str(member.get("member_id") or "").lower(),
                str(member.get("role") or "").lower(),
                str(member.get("endpoint") or "").lower(),
                str(member.get("host") or "").lower(),
            }:
                return member.get("member_id"), item.get("confidence") or "medium"
    return None, "low"


def dependency_group_state(members: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    if not members:
        return {
            "state": "insufficient_evidence",
            "redundancy_status": "unknown",
            "active_member": None,
            "active_member_confidence": "low",
            "fallback_status": "unknown",
            "missing_evidence": ["dependency_metadata"],
            "members": [],
        }
    primary = next((member for member in members if member.get("role") == "primary"), None)
    secondary = next((member for member in members if member.get("role") == "secondary"), None)
    active_member, active_confidence = active_member_from_diagnostics(diagnostics, members)
    degraded = {member.get("member_id") for member in members if condition_rank(member.get("technical_condition", {}).get("state")) >= 2}
    healthy = {member.get("member_id") for member in members if member.get("technical_condition", {}).get("state") == "healthy"}
    usable = bool(healthy)

    missing = []
    if active_member is None:
        missing.append("active_dependency_path")
    if primary and secondary:
        primary_bad = primary.get("member_id") in degraded
        secondary_bad = secondary.get("member_id") in degraded
        if primary_bad and secondary_bad:
            state = "both_degraded"
            redundancy = "unavailable"
            fallback = "unavailable"
        elif primary_bad and not secondary_bad:
            state = "primary_degraded_secondary_healthy"
            redundancy = "reduced"
            fallback = "healthy" if secondary.get("member_id") in healthy else "unknown"
        elif secondary_bad and not primary_bad:
            state = "primary_healthy_secondary_degraded"
            redundancy = "reduced"
            fallback = "healthy" if primary.get("member_id") in healthy else "unknown"
        else:
            state = "both_healthy"
            redundancy = "healthy"
            fallback = "healthy"
    else:
        state = "insufficient_evidence"
        redundancy = "unknown"
        fallback = "unknown"

    if active_member:
        active = next((member for member in members if member.get("member_id") == active_member), None)
        peers = [member for member in members if member.get("member_id") != active_member]
        active_bad = condition_rank((active or {}).get("technical_condition", {}).get("state")) >= 2
        peer_healthy = any(member.get("technical_condition", {}).get("state") == "healthy" for member in peers)
        peer_bad = any(condition_rank(member.get("technical_condition", {}).get("state")) >= 2 for member in peers)
        if active_bad and peer_healthy:
            state = "active_degraded_fallback_healthy"
            redundancy = "reduced"
            fallback = "healthy"
        elif not active_bad and peer_bad:
            state = "active_healthy_peer_degraded"
            redundancy = "reduced"
            fallback = "healthy"
    elif primary and primary.get("member_id") in degraded and secondary and secondary.get("member_id") not in degraded:
        state = "active_path_unknown"
        redundancy = "reduced"

    if degraded and not usable:
        state = "no_usable_fallback" if len(degraded) == len(members) else state
        redundancy = "unavailable"
        fallback = "unavailable"

    return {
        "state": state,
        "redundancy_status": redundancy,
        "active_member": active_member,
        "active_member_confidence": active_confidence,
        "fallback_status": fallback,
        "missing_evidence": missing,
        "members": [
            {
                "member_id": member.get("member_id"),
                "role": member.get("role"),
                "endpoint": member.get("endpoint"),
                "provider": member.get("provider"),
                "technical_condition": member.get("technical_condition"),
                **({"adaptive_baseline": member.get("adaptive_baseline")} if isinstance(member.get("adaptive_baseline"), dict) else {}),
            }
            for member in members
        ],
    }


def group_condition(samples: list[dict[str, Any]], *, target_class: str) -> dict[str, Any]:
    rows = [sample for sample in samples if sample.get("target_class") == target_class]
    if not rows:
        return {"state": "unknown", "sample_count": 0, "sustained_bad_samples": 0, "raw_bad_samples": 0}
    raw_bad = [sample for sample in rows if sample.get("raw_bad")]
    sustained = [sample for sample in rows if sample.get("is_bad")]
    max_p95 = max_or_none([sample.get("p95") for sample in rows])
    if sustained:
        state = "severe" if (max_p95 or 0.0) >= 240 else "degraded"
    elif raw_bad:
        state = "elevated"
    else:
        state = "healthy"
    return {
        "state": state,
        "sample_count": len(rows),
        "sustained_bad_samples": len(sustained),
        "raw_bad_samples": len(raw_bad),
        "max_p95_ms": round(max_p95, 1) if max_p95 is not None else None,
    }


def gateway_condition(lan_samples: list[dict[str, Any]]) -> dict[str, Any]:
    lan = lan_elevation(lan_samples)
    max_p95 = max_or_none([sample.get("p95") for sample in lan_samples])
    if not lan_samples:
        state = "unknown"
    elif lan["lan_bad"]:
        state = "severe" if (max_p95 or 0.0) >= 160 else "degraded"
    elif lan["elevated"]:
        state = "elevated"
    else:
        state = "healthy"
    return {
        "state": state,
        "sample_count": len(lan_samples),
        "elevated_samples": len(lan["elevated"]),
        "max_p95_ms": round(max_p95, 1) if max_p95 is not None else None,
    }


def aggregate_technical_condition(conditions: list[str | None]) -> str:
    clean = [condition for condition in conditions if condition]
    if not clean:
        return "unknown"
    return max(clean, key=condition_rank)


def technical_condition_from_parts(
    *,
    gateway: dict[str, Any],
    internet: dict[str, Any],
    resolver: dict[str, Any],
    members: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    marked_samples: list[dict[str, Any]],
    missing_evidence: list[str] | None = None,
) -> dict[str, Any]:
    member_states = [member.get("technical_condition", {}).get("state") for member in members]
    state = aggregate_technical_condition([gateway.get("state"), internet.get("state"), resolver.get("state"), *member_states])
    technical = {
        "state": state,
        "confidence": detection_confidence(marked_samples, diagnostics, state),
        "drivers": list(dict.fromkeys(
            [driver for member in members for driver in member.get("technical_condition", {}).get("drivers", [])]
            + (["gateway degraded"] if gateway.get("state") in {"degraded", "severe"} else [])
            + (["internet probes degraded"] if internet.get("state") in {"degraded", "severe"} else [])
            + (["resolver probes degraded"] if resolver.get("state") in {"degraded", "severe"} else [])
        )),
        "missing_evidence": missing_evidence or [],
        "target_groups": {"gateway_probe": gateway, "internet_probe": internet, "resolver_probe": resolver},
    }
    return technical


def diagnostic_types(diagnostics: list[dict[str, Any]], *, current_only: bool = True) -> set[str]:
    return {str(item.get("type")) for item in diagnostics if (item.get("is_current") or not current_only) and item.get("type")}


def detection_confidence(samples: list[dict[str, Any]], diagnostics: list[dict[str, Any]], technical_state: str) -> str:
    if not samples:
        return "low"
    sustained = len([sample for sample in samples if sample.get("is_bad")])
    if technical_state in {"severe", "degraded"} and sustained >= WAN_BAD_PERSISTENCE:
        if diagnostic_types(diagnostics) & {"direct_dns_query_measurement", "resolver_route_diagnostic", "traceroute_summary"}:
            return "high"
        return "high" if len(samples) >= 4 else "medium"
    if technical_state == "elevated":
        return "medium"
    return "high" if len(samples) >= 4 else "medium"


def user_impact_assessment(
    *,
    technical_state: str,
    gateway: dict[str, Any],
    internet: dict[str, Any],
    dependency: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    current = [item for item in diagnostics if item.get("is_current")]
    reports = [item for item in current if item.get("type") in {"user_report", "application_symptom", "operator_observation"}]
    if any(str(item.get("state") or item.get("status") or "").lower() in {"symptoms_confirmed", "confirmed", "affected"} for item in reports):
        return {"state": "confirmed", "confidence": "medium", "drivers": ["fresh symptom evidence"], "missing_evidence": []}
    no_symptoms = any(str(item.get("state") or "").lower() in {"no_symptoms_reported", "not_observed"} for item in reports)
    missing = [] if reports else ["user_symptoms"]
    dep_state = dependency.get("state")
    active_known = bool(dependency.get("active_member"))
    if gateway.get("state") in {"degraded", "severe"} or internet.get("state") in {"degraded", "severe"}:
        return {"state": "possible", "confidence": "medium", "drivers": ["broad path or gateway degradation"], "missing_evidence": missing}
    if dep_state == "active_degraded_fallback_healthy":
        return {"state": "possible", "confidence": "medium", "drivers": ["active dependency path is degraded"], "missing_evidence": missing}
    if dep_state in {"both_degraded", "no_usable_fallback"}:
        return {"state": "likely", "confidence": "medium", "drivers": ["no usable resolver fallback"], "missing_evidence": missing}
    if dep_state == "active_healthy_peer_degraded" and no_symptoms:
        return {"state": "not_observed", "confidence": "medium", "drivers": ["active dependency path is healthy", "no symptoms reported"], "missing_evidence": []}
    if dep_state == "active_healthy_peer_degraded":
        return {"state": "unlikely", "confidence": "low", "drivers": ["active dependency path is healthy"], "missing_evidence": missing}
    if technical_state in {"severe", "degraded"} and not active_known:
        return {"state": "unknown", "confidence": "low", "drivers": ["technical degradation exists but active path is unknown"], "missing_evidence": list(dict.fromkeys(missing + ["active_dependency_path"]))}
    if no_symptoms:
        return {"state": "not_observed", "confidence": "medium", "drivers": ["no symptoms reported"], "missing_evidence": []}
    if technical_state in {"healthy", "elevated"}:
        return {"state": "unlikely", "confidence": "medium", "drivers": ["available telemetry does not show user-facing breadth"], "missing_evidence": missing}
    return {"state": "unknown", "confidence": "low", "drivers": [], "missing_evidence": missing}


def diagnostic_status(item: dict[str, Any]) -> str:
    return str(item.get("state") or item.get("status") or item.get("result") or "").strip().lower()


def observed_state_from_operator_feedback(feedback: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(feedback, dict) or not feedback.get("is_current"):
        return None
    impact_state = feedback.get("impact_state")
    note = str(feedback.get("note") or "").lower()
    if impact_state == "none_observed":
        return {"state": "none_reported", "confidence": "high", "drivers": ["fresh operator feedback reports no observed impact"], "missing_evidence": []}
    if impact_state == "minor_slowness":
        return {"state": "reported_minor", "confidence": "high", "drivers": ["fresh operator feedback reports minor slowness"], "missing_evidence": []}
    if impact_state == "intermittent_failures":
        major_terms = {"major", "outage", "unusable", "failed", "failure", "cannot connect"}
        if any(term in note for term in major_terms):
            return {"state": "reported_major", "confidence": "high", "drivers": ["fresh operator feedback reports intermittent failures with major impact wording"], "missing_evidence": []}
        return {"state": "reported_minor", "confidence": "high", "drivers": ["fresh operator feedback reports intermittent failures"], "missing_evidence": []}
    if impact_state == "major_disruption":
        return {"state": "reported_major", "confidence": "high", "drivers": ["fresh operator feedback reports major disruption"], "missing_evidence": []}
    if impact_state == "full_outage":
        return {"state": "confirmed_service_failure", "confidence": "high", "drivers": ["fresh operator feedback reports full outage"], "missing_evidence": []}
    return None


def observed_user_impact_assessment(*, diagnostics: list[dict[str, Any]], operator_feedback: dict[str, Any] | None = None) -> dict[str, Any]:
    feedback_result = observed_state_from_operator_feedback(operator_feedback or {})
    if feedback_result:
        feedback_result["operator_feedback"] = operator_feedback
        return feedback_result
    current = [item for item in diagnostics if item.get("is_current")]
    reports = [item for item in current if item.get("type") in {"user_report", "application_symptom", "operator_observation"}]
    drivers = []
    for item in reports:
        status = diagnostic_status(item)
        severity = str(item.get("severity") or item.get("impact") or "").strip().lower()
        if status in {"confirmed_service_failure", "service_failure", "outage", "failed"}:
            return {"state": "confirmed_service_failure", "confidence": "high", "drivers": ["fresh confirmed service failure evidence"], "missing_evidence": []}
        if status in {"symptoms_confirmed", "confirmed", "affected"} or severity in {"major", "severe"}:
            drivers.append("fresh major symptom evidence")
            return {"state": "reported_major", "confidence": "medium", "drivers": drivers, "missing_evidence": []}
        if status in {"minor", "reported_minor", "degraded_experience"} or severity == "minor":
            drivers.append("fresh minor symptom evidence")
            return {"state": "reported_minor", "confidence": "medium", "drivers": drivers, "missing_evidence": []}
        if status in {"no_symptoms_reported", "not_observed", "none_reported"}:
            drivers.append("fresh report states no symptoms")
            return {"state": "none_reported", "confidence": "medium", "drivers": drivers, "missing_evidence": []}
    return {"state": "unknown", "confidence": "low", "drivers": [], "missing_evidence": ["user_symptoms"]}


def has_repeated_timeout_evidence(diagnostics: list[dict[str, Any]]) -> bool:
    timeout_items = []
    for item in diagnostics:
        if not item.get("is_current"):
            continue
        status = diagnostic_status(item)
        if "timeout" in status or "timeout" in str(item.get("type") or "").lower():
            timeout_items.append(item)
            continue
        values = item.get("timeouts") or item.get("timeout_count") or item.get("failed_transactions")
        count = parse_float(values, 0.0) or 0.0
        if count > 0:
            timeout_items.append(item)
    return len(timeout_items) >= 1


def application_transaction_summary(application_experience: dict[str, Any] | None) -> dict[str, Any]:
    app = application_experience if isinstance(application_experience, dict) else {}
    if not app.get("is_current"):
        return {
            "current": False,
            "healthy_system_dns": False,
            "healthy_https": False,
            "direct_dns_timeouts": 0,
            "system_dns_failed": False,
            "https_failed": False,
            "https_failure_category": None,
            "broad_failure_count": 0,
            "total_failures": 0.0,
            "total_timeouts": 0.0,
            "high_latency_only": False,
            "drivers": ["Application evidence is unavailable or stale."],
        }
    dns_transactions = [item for item in app.get("dns_transactions", []) if isinstance(item, dict)]
    https_raw = app.get("https_transaction")
    https_transaction = https_raw if isinstance(https_raw, dict) else {}
    system_dns = next((item for item in dns_transactions if item.get("role") == "system"), {})
    direct_dns = [item for item in dns_transactions if item.get("role") in {"primary", "secondary"}]
    direct_timeouts = len([item for item in direct_dns if item.get("timeout")])
    direct_failures = len([item for item in direct_dns if not item.get("success")])
    system_failed = bool(system_dns and not system_dns.get("success"))
    https_failed = bool(https_transaction and not https_transaction.get("success"))
    healthy_system_dns = bool(system_dns and system_dns.get("success"))
    healthy_https = bool(https_transaction and https_transaction.get("success"))
    broad_failure_count = direct_failures + (1 if system_failed else 0) + (1 if https_failed else 0)
    failure_counts_raw = app.get("failure_counts")
    failure_counts = failure_counts_raw if isinstance(failure_counts_raw, dict) else {}
    total_failures = parse_float(failure_counts.get("total"), broad_failure_count) or 0.0
    total_timeouts = parse_float(failure_counts.get("timeouts"), direct_timeouts) or 0.0
    slow_dns = [item for item in dns_transactions if item.get("success") and (parse_float(item.get("latency_ms"), 0.0) or 0.0) > 200.0]
    slow_https = bool(healthy_https and (parse_float(https_transaction.get("total_duration_ms"), 0.0) or 0.0) > 1200.0)
    drivers = list(app.get("evidence") or [])
    return {
        "current": True,
        "healthy_system_dns": healthy_system_dns,
        "healthy_https": healthy_https,
        "direct_dns_timeouts": direct_timeouts,
        "system_dns_failed": system_failed,
        "https_failed": https_failed,
        "https_failure_category": https_transaction.get("failure_category"),
        "broad_failure_count": broad_failure_count,
        "total_failures": total_failures,
        "total_timeouts": total_timeouts,
        "high_latency_only": bool((slow_dns or slow_https) and broad_failure_count == 0),
        "drivers": drivers,
    }


def estimated_user_impact_assessment(
    *,
    technical_state: str,
    gateway: dict[str, Any],
    internet: dict[str, Any],
    resolver: dict[str, Any],
    dependency: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    observed: dict[str, Any],
    application_experience: dict[str, Any] | None = None,
) -> dict[str, Any]:
    drivers = []
    missing = []
    current_types = diagnostic_types(diagnostics)
    observed_state = observed.get("state")

    if observed_state == "confirmed_service_failure":
        return {"state": "severe", "confidence": "high", "drivers": ["confirmed service failure"], "missing_evidence": []}
    if observed_state == "reported_major":
        return {"state": "likely", "confidence": "medium", "drivers": ["major user symptoms were reported"], "missing_evidence": []}
    if observed_state == "reported_minor":
        drivers.append("minor user symptoms were reported")

    repeated_timeouts = has_repeated_timeout_evidence(diagnostics)
    app_summary = application_transaction_summary(application_experience)
    if app_summary["current"] and app_summary["direct_dns_timeouts"]:
        repeated_timeouts = True
    lossy_samples = [sample for sample in samples if (sample.get("loss") or 0.0) > WAN_BAD["loss"]]
    gateway_bad = gateway.get("state") in {"degraded", "severe"}
    internet_bad = internet.get("state") in {"degraded", "severe"}
    resolver_bad = resolver.get("state") in {"degraded", "severe"}
    dep_state = dependency.get("state")

    if repeated_timeouts:
        drivers.append("fresh timeout or failed-transaction evidence")
    if app_summary["current"]:
        drivers.extend(app_summary["drivers"])
    if lossy_samples:
        drivers.append("packet loss exceeded degradation threshold")
    if gateway_bad:
        drivers.append("gateway degradation affects the local path")
    if internet_bad and resolver_bad:
        drivers.append("degradation spans unrelated internet and resolver targets")
    elif internet_bad:
        drivers.append("general internet probes degraded")
    elif resolver_bad:
        drivers.append("resolver probes degraded")

    if dep_state == "active_healthy_peer_degraded":
        drivers.append("active resolver path is healthy while peer is degraded")
    elif dep_state == "active_degraded_fallback_healthy":
        drivers.append("active resolver path is degraded but monitored fallback is healthy")
    elif dep_state == "active_path_unknown":
        missing.append("active_dependency_path")
    elif dep_state in {"both_degraded", "no_usable_fallback"}:
        drivers.append("both monitored resolver paths are degraded or no usable fallback is known")

    if "direct_dns_query_measurement" in current_types:
        drivers.append("direct DNS query evidence is available")

    if app_summary["current"] and app_summary["broad_failure_count"] >= 3:
        state = "severe"
        confidence = "high"
        drivers.append("Application checks corroborate likely user impact.")
    elif app_summary["current"] and (app_summary["system_dns_failed"] or app_summary["https_failed"]):
        state = "likely"
        confidence = "high"
        drivers.append("Application checks corroborate likely user impact.")
    elif gateway_bad and (repeated_timeouts or lossy_samples):
        state = "severe"
        confidence = "high"
    elif repeated_timeouts or (internet_bad and resolver_bad and lossy_samples):
        state = "likely"
        confidence = "high"
    elif gateway_bad or internet_bad:
        state = "likely" if lossy_samples else "possible"
        confidence = "medium"
    elif dep_state in {"both_degraded", "no_usable_fallback"}:
        state = "possible" if not (repeated_timeouts or lossy_samples) else "likely"
        confidence = "medium"
    elif dep_state == "active_degraded_fallback_healthy":
        state = "possible"
        confidence = "medium"
    elif dep_state == "active_healthy_peer_degraded":
        state = "low"
        confidence = "medium"
    elif resolver_bad and dep_state == "active_path_unknown":
        state = "low"
        confidence = "low"
    elif technical_state in {"healthy", "elevated"}:
        state = "none_expected"
        confidence = "medium"
    elif technical_state == "unknown":
        state = "unknown"
        confidence = "low"
    else:
        state = "possible"
        confidence = "low"

    if observed_state == "none_reported":
        drivers.append("no symptoms were reported, but absence of reports is not proof of no impact")
    elif observed_state == "unknown":
        missing.append("user_symptoms")

    app_zero_failures = app_summary["total_failures"] == 0 and app_summary["total_timeouts"] == 0 and app_summary["broad_failure_count"] == 0
    if app_summary["current"] and app_summary["healthy_system_dns"] and app_summary["healthy_https"] and state in {"possible", "likely"} and not (app_summary["system_dns_failed"] or app_summary["https_failed"]):
        if repeated_timeouts and app_summary["direct_dns_timeouts"]:
            state = "possible"
        elif not gateway_bad and not internet_bad:
            state = "low"
        confidence = "medium"
    if app_summary["current"] and app_zero_failures and observed_state in {"unknown", "none_reported"} and state == "likely" and not repeated_timeouts:
        state = "possible"
        confidence = "medium"
        drivers.append("Current application checks did not reproduce user-facing failure.")
    if app_summary["current"] and app_summary["high_latency_only"] and state == "none_expected":
        state = "low"

    return {
        "state": state,
        "confidence": confidence,
        "drivers": list(dict.fromkeys(drivers)),
        "missing_evidence": list(dict.fromkeys(missing)),
    }


def operational_risk_assessment(*, technical_state: str, dependency: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    sustained = len([sample for sample in samples if sample.get("is_bad")])
    dep_state = dependency.get("state")
    redundancy = dependency.get("redundancy_status")
    drivers = []
    if redundancy == "reduced":
        drivers.append("dependency redundancy is reduced")
    if redundancy == "unavailable":
        drivers.append("no usable fallback is available")
    if dep_state == "active_degraded_fallback_healthy":
        drivers.append("active dependency path is degraded")
    if sustained >= WAN_BAD_PERSISTENCE:
        drivers.append("degradation persisted across samples")

    if redundancy == "unavailable":
        state = "high"
    elif dep_state == "active_degraded_fallback_healthy":
        state = "high"
    elif technical_state in {"severe", "degraded"} and redundancy == "reduced":
        state = "elevated"
    elif technical_state == "elevated":
        state = "guarded"
    elif technical_state == "unknown":
        state = "unknown"
    else:
        state = "low"
    return {"state": state, "confidence": "medium" if state != "unknown" else "low", "drivers": drivers, "missing_evidence": dependency.get("missing_evidence", [])}


def attribution_assessment(
    *,
    technical_state: str,
    gateway: dict[str, Any],
    internet: dict[str, Any],
    resolver: dict[str, Any],
    dependency: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    impact: dict[str, Any],
) -> dict[str, Any]:
    current_types = diagnostic_types(diagnostics)
    stale_diag = any(item.get("freshness") == "stale" for item in diagnostics)
    evidence_for = []
    evidence_against = []
    unresolved = []

    gateway_bad = gateway.get("state") in {"degraded", "severe"}
    internet_bad = internet.get("state") in {"degraded", "severe"}
    resolver_bad = resolver.get("state") in {"degraded", "severe"}

    if impact.get("state") == "confirmed" and dependency.get("state") == "active_healthy_peer_degraded":
        return {
            "domain": "mixed",
            "confidence": "medium",
            "evidence_for": ["symptoms conflict with healthy active resolver fallback"],
            "evidence_against": [],
            "unresolved_evidence": ["symptom_telemetry_mismatch"],
        }
    if gateway_bad:
        return {
            "domain": "local_gateway",
            "confidence": "medium",
            "evidence_for": ["gateway and WAN degradation overlap"],
            "evidence_against": [],
            "unresolved_evidence": ["local_lan_or_gateway_boundary"],
        }
    if internet_bad and resolver_bad:
        return {
            "domain": "broad_isp_path",
            "confidence": "medium",
            "evidence_for": ["internet and resolver probe groups degraded together"],
            "evidence_against": [],
            "unresolved_evidence": ["transit_or_isp_boundary"],
        }
    if resolver_bad and internet.get("state") == "healthy" and gateway.get("state") == "healthy":
        evidence_for.append("resolver probes degraded while internet probes and gateway were healthy")
        if "direct_dns_query_measurement" in current_types:
            evidence_for.append("direct DNS query measurement corroborated resolver path behavior")
        if "resolver_route_diagnostic" in current_types:
            evidence_for.append("fresh resolver route diagnostic corroborated resolver path behavior")
        if stale_diag:
            unresolved.append("stale_diagnostic_evidence")
        if not dependency.get("active_member"):
            unresolved.append("active_dependency_path")
        confidence = "high" if {"direct_dns_query_measurement", "resolver_route_diagnostic"}.issubset(current_types) else "medium"
        return {
            "domain": "resolver_provider_path",
            "confidence": confidence,
            "evidence_for": evidence_for,
            "evidence_against": ["general internet probes were healthy", "gateway was healthy"],
            "unresolved_evidence": list(dict.fromkeys(unresolved)),
        }
    if technical_state == "elevated":
        return {
            "domain": "unknown",
            "confidence": "low",
            "evidence_for": [],
            "evidence_against": ["no persistent degradation domain was established"],
            "unresolved_evidence": ["persistence"],
        }
    return {
        "domain": "unknown",
        "confidence": "low",
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "unresolved_evidence": unresolved,
    }


def deterministic_operator_interpretation(dimensions: dict[str, Any]) -> dict[str, Any]:
    technical = dimensions.get("technical_condition", {})
    impact = dimensions.get("user_impact", {})
    estimated = dimensions.get("estimated_user_impact", {})
    observed = dimensions.get("observed_user_impact", {})
    risk = dimensions.get("operational_risk", {})
    attribution = dimensions.get("attribution", {})
    application = dimensions.get("application_experience", {})
    dependencies = dimensions.get("dependency_groups", [])
    dep = dependencies[0] if dependencies else {}
    drivers = list(technical.get("drivers") or []) + list(attribution.get("evidence_for") or []) + list(application.get("evidence") or [])
    limiting = list(dict.fromkeys((technical.get("missing_evidence") or []) + (impact.get("missing_evidence") or []) + (attribution.get("unresolved_evidence") or [])))
    return {
        "headline": f"{technical.get('state', 'unknown').title()} technical condition with {risk.get('state', 'unknown')} operational risk.",
        "condition_statement": f"Technical condition is {technical.get('state', 'unknown')} based on deterministic telemetry.",
        "impact_statement": f"Legacy user impact is {impact.get('state', 'unknown')}; estimated user impact is {estimated.get('state', 'unknown')} and observed user impact is {observed.get('state', 'unknown')}.",
        "impact_reasoning": "A severe technical condition can coexist with low estimated impact when degradation is isolated to an inactive or redundant resolver path and no failures or symptoms are observed.",
        "application_experience_statement": "; ".join((application.get("evidence") or ["Application evidence is unavailable or stale."])[:3]),
        "risk_statement": f"Operational risk is {risk.get('state', 'unknown')} with redundancy {dep.get('redundancy_status', 'unknown')}.",
        "attribution_statement": f"Refined attribution domain is {attribution.get('domain', 'unknown')} with {attribution.get('confidence', 'low')} confidence.",
        "evidence_drivers": drivers[:8],
        "limiting_evidence": limiting[:8],
        "recommended_deterministic_checks": [
            "Confirm active dependency member if unknown.",
            "Compare resolver, internet, and gateway probe groups before changing configuration.",
            "Use direct DNS query timing to separate resolver service latency from ICMP probe latency.",
        ],
    }


def evaluate_health_dimensions(
    rows: list[dict[str, Any]],
    *,
    generated_at: dt.datetime,
    diagnostic_evidence: dict[str, Any] | None = None,
    application_experience: dict[str, Any] | None = None,
    operator_impact_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diagnostics = [
        normalize_diagnostic_item(item, generated_at=generated_at)
        for item in (diagnostic_evidence or {}).get("items", [])
        if isinstance(item, dict)
    ]
    samples = [sample for sample in (normalize_sample(row) for row in rows) if sample]
    lan_samples = [sample for sample in samples if sample.get("target_class") == "gateway_probe"]
    wan_samples = [sample for sample in samples if sample.get("target_class") in {"internet_probe", "resolver_probe"}]
    marked_by_group = mark_persistence(wan_samples, key_fields=("phase", "target_class"))
    marked_by_member = mark_persistence(wan_samples, key_fields=("phase", "host"))
    by_host_marked = {(sample["host"], sample["t"]): sample for sample in marked_by_member}
    marked = [{**sample, **{k: v for k, v in by_host_marked.get((sample["host"], sample["t"]), {}).items() if k in {"raw_bad", "is_bad"}}} for sample in marked_by_group]
    all_samples = lan_samples + marked

    gateway = gateway_condition(lan_samples)
    internet = group_condition(marked, target_class="internet_probe")
    resolver = group_condition(marked, target_class="resolver_probe")

    member_rows: dict[str, dict[str, Any]] = {}
    for sample in marked_by_member:
        if sample.get("target_class") != "resolver_probe" or not sample.get("member_id"):
            continue
        member = member_rows.setdefault(sample["member_id"], {
            "dependency_group_id": sample.get("dependency_group_id"),
            "dependency_type": sample.get("dependency_type"),
            "member_id": sample.get("member_id"),
            "role": sample.get("role"),
            "provider": sample.get("provider"),
            "endpoint": sample.get("endpoint"),
            "host": sample.get("host"),
            "samples": [],
        })
        member["samples"].append(sample)
    members = []
    for member in member_rows.values():
        item = dict(member)
        item["technical_condition"] = member_technical_condition(member, diagnostics)
        members.append(item)
    members.sort(key=lambda item: (item.get("dependency_group_id") or "", item.get("role") or "", item.get("member_id") or ""))
    if condition_rank(resolver.get("state")) >= 2 and members and not any(
        condition_rank(member.get("technical_condition", {}).get("state")) >= 2
        for member in members
    ):
        resolver["state"] = "elevated"
    dependency = dependency_group_state(members, diagnostics)
    dependency_group_id = next((member.get("dependency_group_id") for member in members if member.get("dependency_group_id")), None)
    dependency["dependency_group_id"] = dependency_group_id
    dependency["dependency_type"] = next((member.get("dependency_type") for member in members if member.get("dependency_type")), None)
    observed_impact = observed_user_impact_assessment(diagnostics=diagnostics, operator_feedback=operator_impact_feedback)
    for member in members:
        member["adaptive_baseline"] = adaptive_resolver_member_baseline(
            member=member,
            members=members,
            gateway=gateway,
            internet=internet,
            dependency=dependency,
            observed=observed_impact,
            application_experience=application_experience,
            diagnostics=diagnostics,
        )
    dependency = dependency_group_state(members, diagnostics)
    dependency["dependency_group_id"] = dependency_group_id
    dependency["dependency_type"] = next((member.get("dependency_type") for member in members if member.get("dependency_type")), None)

    technical = technical_condition_from_parts(
        gateway=gateway,
        internet=internet,
        resolver=resolver,
        members=members,
        diagnostics=diagnostics,
        marked_samples=marked,
    )
    if not samples:
        technical["state"] = "unknown"
        technical["confidence"] = "low"
        technical["missing_evidence"] = ["telemetry"]

    latest_sample_at = max((sample["t"] for sample in all_samples), default=None)
    current_end = latest_sample_at or generated_at
    current_start = current_end - dt.timedelta(minutes=ATTRIBUTION_CUT_MINUTES)
    current_lan_samples = [sample for sample in lan_samples if sample["t"] >= current_start]
    current_marked = [sample for sample in marked if sample["t"] >= current_start]
    current_marked_by_member = [sample for sample in marked_by_member if sample["t"] >= current_start]
    current_gateway = gateway_condition(current_lan_samples)
    current_internet = group_condition(current_marked, target_class="internet_probe")
    current_resolver = group_condition(current_marked, target_class="resolver_probe")
    current_member_rows: dict[str, dict[str, Any]] = {}
    for sample in current_marked_by_member:
        if sample.get("target_class") != "resolver_probe" or not sample.get("member_id"):
            continue
        member = current_member_rows.setdefault(sample["member_id"], {
            "dependency_group_id": sample.get("dependency_group_id"),
            "dependency_type": sample.get("dependency_type"),
            "member_id": sample.get("member_id"),
            "role": sample.get("role"),
            "provider": sample.get("provider"),
            "endpoint": sample.get("endpoint"),
            "host": sample.get("host"),
            "samples": [],
        })
        member["samples"].append(sample)
    current_members = []
    for member in current_member_rows.values():
        item = dict(member)
        item["technical_condition"] = member_technical_condition(member, diagnostics)
        current_members.append(item)
    current_members.sort(key=lambda item: (item.get("dependency_group_id") or "", item.get("role") or "", item.get("member_id") or ""))
    if condition_rank(current_resolver.get("state")) >= 2 and current_members and not any(
        condition_rank(member.get("technical_condition", {}).get("state")) >= 2
        for member in current_members
    ):
        current_resolver["state"] = "elevated"
    current_condition = technical_condition_from_parts(
        gateway=current_gateway,
        internet=current_internet,
        resolver=current_resolver,
        members=current_members,
        diagnostics=diagnostics,
        marked_samples=current_marked,
        missing_evidence=[] if current_marked or current_lan_samples else ["current_telemetry"],
    )
    if not current_marked and not current_lan_samples:
        current_condition["state"] = "unknown"
        current_condition["confidence"] = "low"
    current_condition = apply_adaptive_current_condition(current_condition, members)
    current_condition["window"] = {
        "minutes": ATTRIBUTION_CUT_MINUTES,
        "start": iso(current_start),
        "end": iso(current_end),
        "lan_samples": len(current_lan_samples),
        "wan_samples": len(current_marked),
    }

    rolling_condition = {
        **technical,
        "window": {
            "hours": 24,
            "start": iso(min((sample["t"] for sample in all_samples), default=None)),
            "end": iso(latest_sample_at),
            "lan_samples": len(lan_samples),
            "wan_samples": len(marked),
        },
    }

    impact = user_impact_assessment(
        technical_state=technical["state"],
        gateway=gateway,
        internet=internet,
        dependency=dependency,
        diagnostics=diagnostics,
    )
    estimated_impact = estimated_user_impact_assessment(
        technical_state=technical["state"],
        gateway=gateway,
        internet=internet,
        resolver=resolver,
        dependency=dependency,
        diagnostics=diagnostics,
        samples=marked + lan_samples,
        observed=observed_impact,
        application_experience=application_experience,
    )
    risk = operational_risk_assessment(technical_state=technical["state"], dependency=dependency, samples=marked)
    attribution = attribution_assessment(
        technical_state=technical["state"],
        gateway=gateway,
        internet=internet,
        resolver=resolver,
        dependency=dependency,
        diagnostics=diagnostics,
        impact=impact,
    )
    unresolved = list(dict.fromkeys(
        technical.get("missing_evidence", [])
        + impact.get("missing_evidence", [])
        + risk.get("missing_evidence", [])
        + attribution.get("unresolved_evidence", [])
        + ((diagnostic_evidence or {}).get("limitations") or [])
    ))
    dimensions = {
        "schema_version": HEALTH_DIMENSIONS_SCHEMA_VERSION,
        "model_version": HEALTH_DIMENSIONS_MODEL_VERSION,
        "generated_at": iso(generated_at),
        "technical_condition": technical,
        "current_condition": current_condition,
        "rolling_condition": rolling_condition,
        "user_impact": impact,
        "estimated_user_impact": estimated_impact,
        "observed_user_impact": observed_impact,
        "operational_risk": risk,
        "detection_confidence": technical.get("confidence", "low"),
        "attribution_confidence": attribution.get("confidence", "low"),
        "attribution": attribution,
        "dependency_groups": [dependency] if dependency.get("state") != "insufficient_evidence" or members else [],
        "adaptive_baseline": {
            "model_version": ADAPTIVE_BASELINE_MODEL_VERSION,
            "resolver_members": [
                {
                    "member_id": member.get("member_id"),
                    "role": member.get("role"),
                    "endpoint": member.get("endpoint"),
                    **(member.get("adaptive_baseline") or {}),
                }
                for member in members
                if isinstance(member.get("adaptive_baseline"), dict)
            ],
        },
        "diagnostic_evidence": {
            "status": (diagnostic_evidence or {}).get("status", "missing"),
            "items_considered": len(diagnostics),
            "current_items": len([item for item in diagnostics if item.get("is_current")]),
            "stale_items": len([item for item in diagnostics if item.get("freshness") == "stale"]),
        },
        "application_experience": application_experience if isinstance(application_experience, dict) else {
            "status": "missing",
            "freshness": "missing",
            "is_current": False,
            "evidence": ["Application evidence is unavailable or stale."],
            "limitations": ["Application experience artifact is absent."],
        },
        "operator_impact_feedback": operator_impact_feedback if isinstance(operator_impact_feedback, dict) else {
            "status": "missing",
            "freshness": "missing",
            "is_current": False,
            "impact_state": "unknown",
            "note": "",
            "limitations": ["Operator impact feedback artifact is absent."],
        },
        "unresolved_evidence": unresolved,
    }
    dimensions["deterministic_operator_interpretation"] = deterministic_operator_interpretation(dimensions)
    return dimensions


def semantic_health_dimensions(dimensions: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(dimensions, dict):
        return {}
    application_raw = dimensions.get("application_experience")
    application = application_raw if isinstance(application_raw, dict) else {}
    feedback_raw = dimensions.get("operator_impact_feedback")
    feedback = feedback_raw if isinstance(feedback_raw, dict) else {}
    return {
        "model_version": dimensions.get("model_version"),
        "technical_condition": (dimensions.get("technical_condition") or {}).get("state"),
        "current_condition": (dimensions.get("current_condition") or {}).get("state"),
        "rolling_condition": (dimensions.get("rolling_condition") or {}).get("state"),
        "user_impact": (dimensions.get("user_impact") or {}).get("state"),
        "estimated_user_impact": (dimensions.get("estimated_user_impact") or {}).get("state"),
        "observed_user_impact": (dimensions.get("observed_user_impact") or {}).get("state"),
        "operational_risk": (dimensions.get("operational_risk") or {}).get("state"),
        "detection_confidence": dimensions.get("detection_confidence"),
        "attribution_domain": (dimensions.get("attribution") or {}).get("domain"),
        "attribution_confidence": dimensions.get("attribution_confidence"),
        "application_experience": {
            "status": application.get("status"),
            "freshness": application.get("freshness"),
            "is_current": application.get("is_current"),
            "failure_counts": application.get("failure_counts") or {},
            "evidence": application.get("evidence") or [],
        },
        "operator_impact_feedback": {
            "status": feedback.get("status"),
            "is_current": feedback.get("is_current"),
            "association": feedback.get("association"),
            "incident_id": feedback.get("incident_id"),
            "impact_state": feedback.get("impact_state"),
            "note": feedback.get("note") or "",
        },
        "dependency_groups": [
            {
                "state": group.get("state"),
                "redundancy_status": group.get("redundancy_status"),
                "active_member": group.get("active_member"),
                "fallback_status": group.get("fallback_status"),
                "members": [
                    {
                        "member_id": member.get("member_id"),
                        "role": member.get("role"),
                        "technical_condition": (member.get("technical_condition") or {}).get("state"),
                    }
                    for member in group.get("members", [])
                    if isinstance(member, dict)
                ],
            }
            for group in dimensions.get("dependency_groups", [])
            if isinstance(group, dict)
        ],
        "unresolved_evidence": dimensions.get("unresolved_evidence") or [],
    }
