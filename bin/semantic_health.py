#!/usr/bin/env python3
"""Shared Python-owned WAN sample and interval classification semantics."""

from __future__ import annotations

from typing import Any
import datetime as dt

from health_model import WAN_BAD, WAN_BAD_PERSISTENCE
from target_metadata import target_metadata


SEMANTIC_MODEL_VERSION = "prime_observer.semantic_health.v1"
BASELINE_HISTORY_SCHEMA_VERSION = 1
BASELINE_HISTORY_MIN_SAMPLES = 24
RESOLVER_SEVERE_P95_MS = 500.0


def application_checks_healthy(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict) or not payload.get("is_current"):
        return False
    failures = payload.get("failure_counts") if isinstance(payload.get("failure_counts"), dict) else {}
    if failures.get("total", 0):
        return False
    dns = [item for item in payload.get("dns_transactions", []) if isinstance(item, dict)]
    https = payload.get("https_transaction") if isinstance(payload.get("https_transaction"), dict) else {}
    return bool(dns) and all(item.get("success") and not item.get("timeout") for item in dns) and bool(https.get("success")) and not https.get("timeout")


def baseline_target_key(sample: dict[str, Any]) -> str:
    meta = target_metadata(sample.get("host"))
    phase = sample.get("phase") or "FIBER"
    target_class = sample.get("target_class") or meta.get("target_class") or "unknown_probe"
    identity = meta.get("member_id") or sample.get("member_id") or sample.get("host") or "unknown"
    return f"{phase}|{target_class}|{identity}"


def parse_ts(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def adaptive_member_for_sample(sample: dict[str, Any], health_dimensions: dict[str, Any] | None) -> dict[str, Any] | None:
    members = (((health_dimensions or {}).get("adaptive_baseline") or {}).get("resolver_members") or [])
    meta = target_metadata(sample.get("host"))
    keys = {str(meta.get("member_id") or ""), str(meta.get("endpoint") or ""), str(sample.get("host") or "")}
    for member in members:
        if not isinstance(member, dict):
            continue
        member_keys = {str(member.get("member_id") or ""), str(member.get("endpoint") or "")}
        if not (keys & member_keys):
            continue
        window = member.get("evidence_window") if isinstance(member.get("evidence_window"), dict) else {}
        start = parse_ts(window.get("start"))
        end = parse_ts(window.get("end"))
        timestamp = sample.get("t")
        if start and timestamp and timestamp < start:
            continue
        if end and timestamp and timestamp > end:
            continue
        return member
    return None


def durable_baseline_for_sample(sample: dict[str, Any], baseline_history: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(baseline_history, dict) or baseline_history.get("schema_version") != BASELINE_HISTORY_SCHEMA_VERSION:
        return None
    targets = baseline_history.get("targets") if isinstance(baseline_history.get("targets"), dict) else {}
    baseline = targets.get(baseline_target_key(sample))
    if not isinstance(baseline, dict) or baseline.get("sample_count", 0) < BASELINE_HISTORY_MIN_SAMPLES:
        return None
    guardrail = baseline.get("guardrail_status") if isinstance(baseline.get("guardrail_status"), dict) else {}
    if guardrail.get("status") == "breached" or baseline.get("median") is None or baseline.get("p95") is None:
        return None
    return baseline


def absolute_excursion_reasons(sample: dict[str, Any]) -> list[str]:
    reasons = []
    if (sample.get("p95") or 0.0) > WAN_BAD["p95"]:
        reasons.append("p95_above_static_threshold")
    if (sample.get("jitter") or 0.0) > WAN_BAD["jitter"]:
        reasons.append("jitter_above_threshold")
    if (sample.get("loss") or 0.0) > WAN_BAD["loss"]:
        reasons.append("packet_loss_above_threshold")
    if sample.get("timeout"):
        reasons.append("timeout")
    return reasons


def evaluate_wan_sample(
    sample: dict[str, Any],
    *,
    baseline_history: dict[str, Any] | None = None,
    application_experience: dict[str, Any] | None = None,
    health_dimensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return explicit semantic layers for one WAN sample.

    `operator_bad` is the compatibility classification consumed by persistence
    and buckets. `absolute_threshold_excursion` remains separately factual.
    """
    absolute_reasons = absolute_excursion_reasons(sample)
    absolute_excursion = bool(absolute_reasons)
    target_class = sample.get("target_class")
    baseline = durable_baseline_for_sample(sample, baseline_history) if target_class == "resolver_probe" else None
    adaptive_member = adaptive_member_for_sample(sample, health_dimensions) if target_class == "resolver_probe" else None
    guardrails = []
    if (sample.get("loss") or 0.0) > WAN_BAD["loss"]:
        guardrails.append("packet_loss_above_threshold")
    if sample.get("timeout"):
        guardrails.append("timeout")
    if (sample.get("jitter") or 0.0) > WAN_BAD["jitter"]:
        guardrails.append("jitter_above_threshold")
    if target_class == "resolver_probe" and (sample.get("p95") or 0.0) >= RESOLVER_SEVERE_P95_MS:
        guardrails.append("severe_excursion")
    blocked_reasons = (baseline.get("blocked_update") or {}).get("reasons") if baseline else []
    if absolute_excursion and "rapid_worsening" in (blocked_reasons or []):
        guardrails.append("rapid_worsening")
    app_is_current = isinstance(application_experience, dict) and bool(application_experience.get("is_current"))
    app_healthy = application_checks_healthy(application_experience)
    if target_class == "resolver_probe" and absolute_excursion and app_is_current and not app_healthy:
        guardrails.append("application_failure")
    if adaptive_member and absolute_excursion:
        guardrails.extend(adaptive_member.get("guardrail_breaches") or [])

    if target_class != "resolver_probe":
        learned_state = "not_applicable"
        deviation = None
        operator_bad = absolute_excursion
        baseline_source = None
    elif baseline is None and adaptive_member and adaptive_member.get("baseline_state") == "elevated_but_stable" and adaptive_member.get("incident_eligible") is False and app_healthy and not guardrails:
        learned_state = "elevated_but_stable"
        deviation = None
        operator_bad = False
        baseline_source = adaptive_member.get("baseline_source") or "window"
    elif baseline is None:
        learned_state = "fallback_absolute_threshold"
        deviation = None
        operator_bad = absolute_excursion
        baseline_source = "static_fallback"
    else:
        learned_p95 = baseline.get("p95") or WAN_BAD["p95"]
        deviation = round((sample.get("p95") or 0.0) - learned_p95, 1)
        beyond_learned = (sample.get("p95") or 0.0) > learned_p95
        if beyond_learned:
            learned_state = "degraded_from_baseline"
        elif baseline.get("accepted_state") == "elevated_but_stable" and absolute_excursion:
            learned_state = "elevated_but_stable"
        else:
            learned_state = "within_target"
        operator_bad = bool(guardrails or beyond_learned or (absolute_excursion and not app_healthy))
        baseline_source = "durable"

    incident_eligible = bool(operator_bad)
    final_state = "degraded" if operator_bad else learned_state
    return {
        "model_version": SEMANTIC_MODEL_VERSION,
        "raw_measurement": {
            "p95_ms": sample.get("p95"),
            "jitter_ms": sample.get("jitter"),
            "loss_pct": sample.get("loss"),
            "timeout": bool(sample.get("timeout")),
        },
        "absolute_threshold_excursion": absolute_excursion,
        "absolute_excursion_reasons": absolute_reasons,
        "learned_comparison": {
            "state": learned_state,
            "baseline_source": baseline_source,
            "baseline_version": baseline.get("baseline_version") if baseline else None,
            "learned_p95_ms": baseline.get("p95") if baseline else None,
            "deviation_from_learned_p95_ms": deviation,
        },
        "guardrail_breaches": list(dict.fromkeys(guardrails)),
        "operator_bad": operator_bad,
        "incident_eligible": incident_eligible,
        "final_state": final_state,
    }


def mark_wan_semantics(
    series: list[dict[str, Any]],
    *,
    baseline_history: dict[str, Any] | None = None,
    application_experience: dict[str, Any] | None = None,
    health_dimensions: dict[str, Any] | None = None,
    min_streak: int = WAN_BAD_PERSISTENCE,
) -> list[dict[str, Any]]:
    streaks: dict[tuple[Any, ...], int] = {}
    marked = []
    for sample in series:
        semantic = evaluate_wan_sample(
            sample,
            baseline_history=baseline_history,
            application_experience=application_experience,
            health_dimensions=health_dimensions,
        )
        key = (sample.get("phase"), sample.get("target_class"), sample.get("host"))
        streaks[key] = streaks.get(key, 0) + 1 if semantic["operator_bad"] else 0
        persistent = streaks[key] >= min_streak
        item = dict(sample)
        item.update({
            "absolute_threshold_excursion": semantic["absolute_threshold_excursion"],
            "learned_normal_state": semantic["learned_comparison"]["state"],
            "semantic_guardrail_breaches": semantic["guardrail_breaches"],
            "operator_bad": semantic["operator_bad"],
            "incident_eligible": semantic["incident_eligible"],
            # Compatibility fields now explicitly mean operator-facing sample
            # abnormality and its persistence, not raw static-threshold facts.
            "raw_bad": semantic["operator_bad"],
            "is_bad": persistent,
            "semantic_health": {
                **semantic,
                "persistence": {
                    "streak": streaks[key],
                    "required": min_streak,
                    "qualified": persistent,
                },
            },
        })
        marked.append(item)
    return marked


def interval_guardrails(samples: list[dict[str, Any]], *, gateway_degraded: bool = False) -> list[str]:
    breaches = []
    for sample in samples:
        breaches.extend(sample.get("semantic_guardrail_breaches") or [])
    degraded_resolvers = {
        target_metadata(sample.get("host")).get("member_id") or sample.get("host")
        for sample in samples
        if sample.get("target_class") == "resolver_probe" and sample.get("is_bad")
    }
    resolver_bad = bool(degraded_resolvers)
    internet_bad = any(sample.get("target_class") == "internet_probe" and sample.get("is_bad") for sample in samples)
    if len(degraded_resolvers) >= 2:
        breaches.append("both_resolver_members_degraded")
    if resolver_bad and internet_bad:
        breaches.append("broad_correlated_resolver_and_internet_degradation")
    if gateway_degraded:
        breaches.append("gateway_degradation")
    return list(dict.fromkeys(breaches))
