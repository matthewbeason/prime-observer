from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from health_model import WAN_BAD, WAN_BAD_PERSISTENCE, lan_elevation
from target_metadata import target_metadata


HEALTH_DIMENSIONS_SCHEMA_VERSION = 1
HEALTH_DIMENSIONS_MODEL_VERSION = "prime_observer.health_dimensions.v1"
DIAGNOSTIC_EVIDENCE_MODEL_VERSION = "prime_observer.diagnostic_evidence.v1"

TECHNICAL_STATES = {"healthy", "elevated", "degraded", "severe", "unknown"}
USER_IMPACT_STATES = {"not_observed", "unlikely", "possible", "likely", "confirmed", "unknown"}
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
    risk = dimensions.get("operational_risk", {})
    attribution = dimensions.get("attribution", {})
    dependencies = dimensions.get("dependency_groups", [])
    dep = dependencies[0] if dependencies else {}
    drivers = list(technical.get("drivers") or []) + list(attribution.get("evidence_for") or [])
    limiting = list(dict.fromkeys((technical.get("missing_evidence") or []) + (impact.get("missing_evidence") or []) + (attribution.get("unresolved_evidence") or [])))
    return {
        "headline": f"{technical.get('state', 'unknown').title()} technical condition with {risk.get('state', 'unknown')} operational risk.",
        "condition_statement": f"Technical condition is {technical.get('state', 'unknown')} based on deterministic telemetry.",
        "impact_statement": f"User impact is {impact.get('state', 'unknown')} from available symptom, active-path, and fallback evidence.",
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

    member_states = [member.get("technical_condition", {}).get("state") for member in members]
    technical_state = aggregate_technical_condition([gateway.get("state"), internet.get("state"), resolver.get("state"), *member_states])
    technical = {
        "state": technical_state,
        "confidence": detection_confidence(marked, diagnostics, technical_state),
        "drivers": list(dict.fromkeys(
            [driver for member in members for driver in member.get("technical_condition", {}).get("drivers", [])]
            + (["gateway degraded"] if gateway.get("state") in {"degraded", "severe"} else [])
            + (["internet probes degraded"] if internet.get("state") in {"degraded", "severe"} else [])
            + (["resolver probes degraded"] if resolver.get("state") in {"degraded", "severe"} else [])
        )),
        "missing_evidence": [],
        "target_groups": {"gateway_probe": gateway, "internet_probe": internet, "resolver_probe": resolver},
    }
    if not samples:
        technical["state"] = "unknown"
        technical["confidence"] = "low"
        technical["missing_evidence"] = ["telemetry"]

    impact = user_impact_assessment(
        technical_state=technical["state"],
        gateway=gateway,
        internet=internet,
        dependency=dependency,
        diagnostics=diagnostics,
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
        "user_impact": impact,
        "operational_risk": risk,
        "detection_confidence": technical.get("confidence", "low"),
        "attribution_confidence": attribution.get("confidence", "low"),
        "attribution": attribution,
        "dependency_groups": [dependency] if dependency.get("state") != "insufficient_evidence" or members else [],
        "diagnostic_evidence": {
            "status": (diagnostic_evidence or {}).get("status", "missing"),
            "items_considered": len(diagnostics),
            "current_items": len([item for item in diagnostics if item.get("is_current")]),
            "stale_items": len([item for item in diagnostics if item.get("freshness") == "stale"]),
        },
        "unresolved_evidence": unresolved,
    }
    dimensions["deterministic_operator_interpretation"] = deterministic_operator_interpretation(dimensions)
    return dimensions


def semantic_health_dimensions(dimensions: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(dimensions, dict):
        return {}
    return {
        "model_version": dimensions.get("model_version"),
        "technical_condition": (dimensions.get("technical_condition") or {}).get("state"),
        "user_impact": (dimensions.get("user_impact") or {}).get("state"),
        "operational_risk": (dimensions.get("operational_risk") or {}).get("state"),
        "detection_confidence": dimensions.get("detection_confidence"),
        "attribution_domain": (dimensions.get("attribution") or {}).get("domain"),
        "attribution_confidence": dimensions.get("attribution_confidence"),
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
