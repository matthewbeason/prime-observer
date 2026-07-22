#!/usr/bin/env python3
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json

BASE = Path(__file__).resolve().parents[1]
VIZ_DIR = BASE / "viz"
INVESTIGATION = VIZ_DIR / "investigation.json"
OUT = VIZ_DIR / "operator_assistant_input.json"
STATE_OUT = VIZ_DIR / "operator_assistant_generation_state.json"


def parse_ts(value):
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
    return parsed


def iso_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def rounded(value, digits=1):
    if value is None:
        return None
    return round(value, digits)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def load_json_object(path):
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def pending_generation_state(input_hash, requested_by, reason, requested_at=None):
    requested_at = requested_at or iso_now()
    return {
        "schema_version": 2,
        "status": "pending",
        "provider": "openrouter",
        "input_hash": input_hash,
        "requested_at": requested_at,
        "updated_at": requested_at,
        "requested_by": requested_by,
        "reason": reason,
        "attempt_count": 0,
    }


def write_input_and_pending_state(out_path, state_path, payload, requested_by):
    existing = load_json_object(out_path)
    existing_hash = existing.get("input_hash") if existing else None
    semantic_changed = existing_hash != payload.get("input_hash")
    write_json(out_path, payload)
    if semantic_changed:
        write_json(
            state_path,
            pending_generation_state(
                payload.get("input_hash"),
                requested_by,
                "semantic evidence changed" if existing else "assistant input missing",
            ),
        )
    return semantic_changed


def load_json(path):
    source_file = str(path.relative_to(BASE)) if path.is_absolute() and path.is_relative_to(BASE) else str(path)
    if not path.exists():
        return None, source_file, ["Investigation artifact was not found."]

    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, source_file, ["Investigation artifact was unreadable."]

    if not isinstance(payload, dict):
        return None, source_file, ["Investigation artifact was invalid."]

    return payload, source_file, []


def bounded_items(items, limit):
    if not isinstance(items, list):
        return []
    return [item for item in items[:limit] if isinstance(item, dict)]


def safe_dict(value):
    return value if isinstance(value, dict) else {}


def normalized_input_payload(input_payload):
    payload = safe_dict(input_payload)
    investigation = safe_dict(payload.get("investigation"))
    attribution = safe_dict(payload.get("attribution"))
    environmental = safe_dict(payload.get("environmental_context"))
    health_dimensions = safe_dict(payload.get("health_dimensions"))

    def normalized_context(value, *, include_provider=False):
        context = safe_dict(value)
        normalized = {
            "available": bool(context.get("available")),
            "status": context.get("status"),
            "summary": context.get("summary"),
        }
        if include_provider:
            normalized["provider_display_name"] = context.get("provider_display_name")
            normalized["fallback_used"] = bool(context.get("fallback_used"))
        return normalized

    return {
        "schema_version": payload.get("schema_version"),
        "semantic_schema_version": payload.get("semantic_schema_version"),
        "investigation": {
            "id": investigation.get("id"),
            "mode": investigation.get("mode"),
            "window_start": investigation.get("window_start"),
            "window_end": investigation.get("window_end"),
            "duration_minutes": investigation.get("duration_minutes"),
            "source_status": investigation.get("source_status"),
            "artifact_label": investigation.get("artifact_label"),
        },
        "selected_event": safe_dict(payload.get("selected_event")),
        "artifact_state": safe_dict(payload.get("artifact_state")),
        "freshness": {
            "telemetry_latest_at": safe_dict(payload.get("freshness")).get("telemetry_latest_at"),
            "evidence_latest_at": safe_dict(payload.get("freshness")).get("evidence_latest_at"),
            "evidence_lag_seconds": safe_dict(payload.get("freshness")).get("evidence_lag_seconds"),
        },
        "operator_brief": safe_dict(payload.get("operator_brief")),
        "health_dimensions": {
            "technical_condition": safe_dict(health_dimensions.get("technical_condition")).get("state"),
            "user_impact": safe_dict(health_dimensions.get("user_impact")).get("state"),
            "operational_risk": safe_dict(health_dimensions.get("operational_risk")).get("state"),
            "detection_confidence": health_dimensions.get("detection_confidence"),
            "attribution_domain": safe_dict(health_dimensions.get("attribution")).get("domain"),
            "attribution_confidence": health_dimensions.get("attribution_confidence"),
            "dependency_groups": [
                {
                    "state": safe_dict(group).get("state"),
                    "redundancy_status": safe_dict(group).get("redundancy_status"),
                    "active_member": safe_dict(group).get("active_member"),
                    "fallback_status": safe_dict(group).get("fallback_status"),
                }
                for group in health_dimensions.get("dependency_groups", [])
                if isinstance(group, dict)
            ],
            "unresolved_evidence": health_dimensions.get("unresolved_evidence") or [],
        },
        "scope_impact": safe_dict(payload.get("scope_impact")),
        "recovery_progress": safe_dict(payload.get("recovery_progress")),
        "episode_summary": safe_dict(payload.get("episode_summary")),
        "evidence_argument": safe_dict(payload.get("evidence_argument")),
        "phase_summaries": safe_dict(payload.get("phase_summaries")),
        "evidence_buckets": safe_dict(payload.get("evidence_buckets")),
        "attribution": {
            "current": {
                "status": safe_dict(attribution.get("current")).get("status"),
                "label": safe_dict(attribution.get("current")).get("label"),
                "start": safe_dict(attribution.get("current")).get("start"),
                "end": safe_dict(attribution.get("current")).get("end"),
                "observation_id": safe_dict(attribution.get("current")).get("observation_id"),
            },
            "window": {
                "status": safe_dict(attribution.get("window")).get("status"),
                "label": safe_dict(attribution.get("window")).get("label"),
                "start": safe_dict(attribution.get("window")).get("start"),
                "end": safe_dict(attribution.get("window")).get("end"),
                "observation_id": safe_dict(attribution.get("window")).get("observation_id"),
            },
        },
        "episode": {
            "target_class": safe_dict(payload.get("episode")).get("target_class"),
            "status": safe_dict(payload.get("episode")).get("status"),
            "label": safe_dict(payload.get("episode")).get("label"),
            "start": safe_dict(payload.get("episode")).get("start"),
            "end": safe_dict(payload.get("episode")).get("end"),
            "observation_id": safe_dict(payload.get("episode")).get("observation_id"),
        },
        "evidence": {
            "internet": safe_dict(payload.get("evidence")).get("internet"),
            "resolver": safe_dict(payload.get("evidence")).get("resolver"),
            "lan": safe_dict(payload.get("evidence")).get("lan"),
        },
        "environmental_context": {
            "dns": normalized_context(environmental.get("dns")),
            "internet_conditions": normalized_context(
                environmental.get("internet_conditions"),
                include_provider=True,
            ),
            "power": normalized_context(environmental.get("power")),
        },
    }


def input_hash_for_payload(input_payload):
    def canonicalize(value):
        if isinstance(value, dict):
            return {key: canonicalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonicalize(item) for item in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    encoded = json.dumps(
        canonicalize(normalized_input_payload(input_payload)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def extract_observation_refs(investigation):
    refs = investigation.get("observation_references")
    if not isinstance(refs, list):
        return []
    return [item for item in refs if isinstance(item, dict)]


def attribution_entry(observation_refs, view_name):
    for item in observation_refs:
        scope = safe_dict(item.get("scope"))
        if item.get("type") != "attribution" or scope.get("view") != view_name:
            continue
        state = safe_dict(item.get("state"))
        interval = safe_dict(item.get("interval"))
        return {
            "status": state.get("status"),
            "label": state.get("label"),
            "start": interval.get("start"),
            "end": interval.get("end"),
            "observation_id": item.get("id"),
        }
    return {
        "status": None,
        "label": None,
        "start": None,
        "end": None,
        "observation_id": None,
    }


def select_episode(observation_refs):
    episodes = []
    for item in observation_refs:
        if item.get("type") != "episode":
            continue
        interval = safe_dict(item.get("interval"))
        state = safe_dict(item.get("state"))
        scope = safe_dict(item.get("scope"))
        episodes.append({
            "target_class": scope.get("target_class"),
            "status": state.get("status"),
            "label": state.get("label"),
            "start": interval.get("start"),
            "end": interval.get("end"),
            "observation_id": item.get("id"),
        })
    episodes.sort(key=lambda item: (item.get("start") or "", item.get("end") or "", item.get("observation_id") or ""))
    return episodes[0] if episodes else {
        "target_class": None,
        "status": None,
        "label": None,
        "start": None,
        "end": None,
        "observation_id": None,
    }


def evidence_summary(periods, period_name, target_class):
    period = safe_dict(periods.get(period_name))
    if target_class == "gateway_probe":
        section = safe_dict(safe_dict(period.get("lan")).get("target_groups")).get(target_class)
        lan = safe_dict(period.get("lan"))
        return {
            "elevated_count": lan.get("elevated_p95_count"),
            "sample_count": safe_dict(section).get("sample_count"),
            "max_p95_ms": lan.get("max_p95_ms"),
            "max_loss_pct": lan.get("max_loss_pct"),
        }

    section = safe_dict(safe_dict(period.get("wan")).get("target_groups")).get(target_class)
    buckets = [
        item for item in bounded_items(period.get("wan_buckets"), 32)
        if item.get("target_class") == target_class
    ]
    return {
        "raw_bad_count": safe_dict(section).get("raw_bad_count"),
        "sustained_bad_count": safe_dict(section).get("sustained_bad_count"),
        "sample_count": safe_dict(section).get("sample_count"),
        "max_p95_ms": max((item.get("max_p95_ms") for item in buckets if item.get("max_p95_ms") is not None), default=None),
        "max_loss_pct": max((item.get("max_loss_pct") for item in buckets if item.get("max_loss_pct") is not None), default=None),
    }


def window_evidence_summary(windows, window_name, target_class=None):
    window = safe_dict(windows.get(window_name))
    metrics = safe_dict(window.get("supporting_metrics"))
    return {
        "assessment_code": window.get("assessment_code"),
        "assessment_label": window.get("assessment_label"),
        "summary": window.get("summary"),
        "confidence": window.get("confidence"),
        "target_class": target_class,
        "raw_bad_count": metrics.get("raw_bad_count"),
        "sustained_bad_count": metrics.get("sustained_bad_count"),
        "sample_count": metrics.get("wan_samples"),
        "max_p95_ms": metrics.get("max_p95_ms"),
        "max_loss_pct": metrics.get("max_loss_pct"),
    }


def compact_dns_context(context):
    if not isinstance(context, dict):
        return {"available": False}
    summary = safe_dict(context.get("summary"))
    total = summary.get("total_queries")
    blocked = summary.get("blocked_queries")
    rate = summary.get("block_rate_pct")
    text = None
    if total is not None and blocked is not None:
        text = f"NextDNS summary window {context.get('window')} captured {total} queries with {blocked} blocked"
        if rate is not None:
            text += f" ({rate}% block rate)"
        text += "."
    return {
        "available": bool(context.get("available")),
        "status": context.get("status"),
        "summary": text,
        "generated_at": context.get("generated_at"),
        "minutes_from_event_midpoint": context.get("minutes_from_event_midpoint"),
        "source_file": context.get("source_file"),
    }


def compact_environment_context(context, *, provider_display_name=False):
    if not isinstance(context, dict):
        return {"available": False}
    payload = {
        "available": bool(context.get("available")),
        "status": context.get("status"),
        "summary": context.get("summary"),
        "generated_at": context.get("generated_at"),
        "minutes_from_event_midpoint": context.get("minutes_from_event_midpoint"),
        "source_file": context.get("source_file"),
    }
    if provider_display_name:
        payload["provider_display_name"] = context.get("provider_display_name") or context.get("provider")
        payload["fallback_used"] = bool(context.get("fallback_used"))
    return payload


def build_observations(package):
    items = []
    current = safe_dict(safe_dict(package.get("attribution")).get("current"))
    window = safe_dict(safe_dict(package.get("attribution")).get("window"))
    episode = safe_dict(package.get("episode"))
    evidence = safe_dict(package.get("evidence"))
    artifact_state = safe_dict(package.get("artifact_state"))
    selected_event = safe_dict(package.get("selected_event"))

    if artifact_state.get("label"):
        items.append(f"Investigation state: {artifact_state['label']}.")
    if selected_event.get("id"):
        items.append(
            f"Selected event: {selected_event.get('target_class')} {selected_event.get('lifecycle_state')} from {selected_event.get('first_anomalous_at')} to {selected_event.get('last_anomalous_at')}."
        )

    if current.get("label"):
        items.append(f"Current attribution: {current['label']}.")
    if window.get("label"):
        items.append(f"Window attribution: {window['label']}.")
    if episode.get("label") and episode.get("target_class"):
        items.append(
            f"Episode observation: {episode['label']} on {episode['target_class']} from {episode.get('start')} to {episode.get('end')}."
        )

    for label, key in (("internet probes", "internet"), ("resolver probes", "resolver")):
        summary = safe_dict(evidence.get(key))
        if summary.get("sample_count") is None:
            continue
        items.append(
            f"During window {label}: raw bad {summary.get('raw_bad_count')}, sustained bad {summary.get('sustained_bad_count')}, max p95 {summary.get('max_p95_ms')} ms."
        )

    lan = safe_dict(evidence.get("lan"))
    if lan.get("sample_count") is not None:
        items.append(
            f"During window LAN gateway: elevated count {lan.get('elevated_count')}, max p95 {lan.get('max_p95_ms')} ms, max loss {lan.get('max_loss_pct')}%."
        )

    return items[:8]


def build_limitations(investigation, package, base_limitations):
    limitations = list(base_limitations)
    notes = investigation.get("notes")
    if isinstance(notes, list):
        for note in notes[:2]:
            if isinstance(note, str):
                limitations.append(note)

    attribution = safe_dict(package.get("attribution"))
    current = safe_dict(attribution.get("current"))
    window = safe_dict(attribution.get("window"))
    if current.get("status") and window.get("status") and current.get("status") != window.get("status"):
        limitations.append("Current attribution and window attribution disagree and should be preserved as separate scopes.")

    periods = safe_dict(investigation.get("periods"))
    after = safe_dict(periods.get("after"))
    if investigation.get("schema_version") == 1 and after.get("total_samples") == 0:
        limitations.append("No after-window telemetry samples were available in this investigation package.")

    if not current.get("status"):
        limitations.append("Current attribution observation was not available in the investigation package.")
    if not window.get("status"):
        limitations.append("Window attribution observation was not available in the investigation package.")

    episode = safe_dict(package.get("episode"))
    if not episode.get("status"):
        limitations.append("No overlapping episode observation was available in the investigation package.")

    env = safe_dict(package.get("environmental_context"))
    for key, label in (
        ("dns", "DNS context"),
        ("internet_conditions", "Internet Conditions context"),
        ("power", "Power context"),
    ):
        context = safe_dict(env.get(key))
        if not context.get("available"):
            limitations.append(f"{label} was unavailable or missing for this package.")

    deduped = []
    seen = set()
    for item in limitations:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped[:10]


def build_package(investigation, source_file):
    requested_window = safe_dict(investigation.get("requested_window"))
    periods = safe_dict(investigation.get("periods"))
    windows = safe_dict(investigation.get("windows"))
    observation_refs = extract_observation_refs(investigation)
    current_attribution = attribution_entry(observation_refs, "current_attribution")
    window_attribution = attribution_entry(observation_refs, "window_attribution")
    selected_event = safe_dict(investigation.get("selected_event"))
    artifact_state = safe_dict(investigation.get("artifact_state"))
    freshness = safe_dict(investigation.get("freshness"))
    health_dimensions = safe_dict(investigation.get("health_dimensions"))
    target_class = selected_event.get("target_class")
    use_schema2 = investigation.get("schema_version") == 2 and bool(windows)

    package = {
        "schema_version": 2,
        "semantic_schema_version": "operator_assistant_input.v2",
        "generated_at": iso_now(),
        "investigation": {
            "id": investigation.get("id"),
            "mode": investigation.get("mode"),
            "window_start": requested_window.get("start"),
            "window_end": requested_window.get("end"),
            "duration_minutes": requested_window.get("duration_minutes"),
            "source_generated_at": investigation.get("generated_at"),
            "source_status": investigation.get("status"),
            "artifact_label": artifact_state.get("label"),
        },
        "selected_event": selected_event,
        "artifact_state": artifact_state,
        "freshness": {
            "telemetry_latest_at": freshness.get("telemetry_latest_at"),
            "evidence_latest_at": freshness.get("evidence_latest_at"),
            "evidence_lag_seconds": freshness.get("evidence_lag_seconds"),
        },
        "timeline": bounded_items(investigation.get("timeline"), 8),
        "phase_summaries": {
            str((row or {}).get("phase", "")).lower(): safe_dict((row or {}).get("phase_summary"))
            for row in bounded_items(investigation.get("timeline"), 8)
        },
        "operator_brief": safe_dict(investigation.get("operator_brief")),
        "health_dimensions": health_dimensions,
        "deterministic_operator_interpretation": safe_dict(investigation.get("deterministic_operator_interpretation")),
        "dependency_groups": investigation.get("dependency_state") if isinstance(investigation.get("dependency_state"), list) else [],
        "impact_assessment": safe_dict(investigation.get("impact_assessment")),
        "scope_impact": safe_dict(investigation.get("scope_impact")),
        "recovery_progress": safe_dict(investigation.get("recovery_progress")),
        "episode_summary": safe_dict(investigation.get("episode_summary")),
        "evidence_argument": safe_dict(investigation.get("evidence_argument")),
        "evidence_buckets": {
            key: value
            for key, value in safe_dict(investigation.get("evidence_buckets")).items()
            if key != "all_buckets"
        },
        "attribution": {
            "current": current_attribution,
            "window": window_attribution,
        },
        "episode": selected_event if selected_event else select_episode(observation_refs),
        "evidence": {
            "internet": window_evidence_summary(windows, "degradation", "internet_probe") if use_schema2 and target_class == "internet_probe" else evidence_summary(periods, "during", "internet_probe"),
            "resolver": window_evidence_summary(windows, "degradation", "resolver_probe") if use_schema2 and target_class == "resolver_probe" else evidence_summary(periods, "during", "resolver_probe"),
            "lan": evidence_summary(periods, "during", "gateway_probe"),
            "baseline": window_evidence_summary(windows, "baseline", target_class) if use_schema2 else None,
            "recovery": window_evidence_summary(windows, "recovery", target_class) if use_schema2 else None,
        },
        "environmental_context": {
            "dns": compact_dns_context(investigation.get("dns_context")),
            "internet_conditions": compact_environment_context(
                investigation.get("internet_conditions_context"),
                provider_display_name=True,
            ),
            "power": compact_environment_context(investigation.get("power_infrastructure_context")),
        },
        "provenance": {
            "producer": "bin/build_operator_assistant_input.py",
            "source_file": source_file,
            "source_producer": safe_dict(investigation.get("provenance")).get("producer"),
            "observation_reference_count": len(observation_refs),
            "source_semantic_hash": safe_dict(investigation.get("provenance")).get("event_semantic_hash"),
        },
        "claim_boundaries": {
            "observed_fact": "Use only directly supplied deterministic evidence for facts.",
            "inference": "Likely meaning may be inferred when phrased as an engineering assessment, not proof.",
            "unknown": "State unknowns when evidence is insufficient or contradictory.",
            "impact": "Do not convert technical severity into user impact without symptom, active-path, fallback, or application evidence.",
            "telemetry": "Operator assertions and diagnostics may refine confidence or impact, but must not silently override telemetry.",
        },
        "prohibited_claims": [
            "Do not claim definite ISP, DNS provider, local network, routing, or power fault unless the evidence explicitly supports that certainty.",
            "Do not claim unavailable tests were performed.",
            "Do not invent measurements, services, devices, users, or external outages.",
            "Do not recommend destructive or risky action without explicit justification.",
        ],
        "recommended_safe_diagnostic_categories": [
            "compare affected and unaffected probe groups",
            "verify from another local client",
            "continue recovery observation",
            "escalate with bounded evidence when sustained degradation persists",
        ],
    }
    package["observations"] = build_observations(package)
    package["limitations"] = build_limitations(investigation, package, [])
    package["input_hash"] = input_hash_for_payload(package)
    return package


def unavailable_package(source_file, limitations):
    package = {
        "schema_version": 2,
        "semantic_schema_version": "operator_assistant_input.v2",
        "generated_at": iso_now(),
        "investigation": {
            "id": None,
            "window_start": None,
            "window_end": None,
            "duration_minutes": None,
            "source_generated_at": None,
            "source_status": "unavailable",
        },
        "observations": [],
        "attribution": {
            "current": {"status": None, "label": None, "start": None, "end": None, "observation_id": None},
            "window": {"status": None, "label": None, "start": None, "end": None, "observation_id": None},
        },
        "episode": {"target_class": None, "status": None, "label": None, "start": None, "end": None, "observation_id": None},
        "evidence": {
            "internet": {"raw_bad_count": None, "sustained_bad_count": None, "sample_count": None, "max_p95_ms": None, "max_loss_pct": None},
            "resolver": {"raw_bad_count": None, "sustained_bad_count": None, "sample_count": None, "max_p95_ms": None, "max_loss_pct": None},
            "lan": {"elevated_count": None, "sample_count": None, "max_p95_ms": None, "max_loss_pct": None},
        },
        "environmental_context": {
            "dns": {"available": False},
            "internet_conditions": {"available": False},
            "power": {"available": False},
        },
        "limitations": limitations[:10],
        "operator_brief": {},
        "health_dimensions": {},
        "deterministic_operator_interpretation": {},
        "dependency_groups": [],
        "impact_assessment": {},
        "scope_impact": {},
        "recovery_progress": {},
        "episode_summary": {},
        "evidence_argument": {},
        "phase_summaries": {},
        "evidence_buckets": {},
        "claim_boundaries": {},
        "prohibited_claims": [],
        "recommended_safe_diagnostic_categories": [],
        "provenance": {
            "producer": "bin/build_operator_assistant_input.py",
            "source_file": source_file,
            "source_producer": None,
            "observation_reference_count": 0,
        },
    }
    package["input_hash"] = input_hash_for_payload(package)
    return package


def build_from_path(path):
    investigation, source_file, errors = load_json(path)
    if investigation is None:
        return unavailable_package(source_file, errors)
    return build_package(investigation, source_file)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a compact deterministic evidence package for a future operator assistant from viz/investigation.json."
    )
    parser.add_argument("--investigation", type=Path, default=INVESTIGATION, help="Source investigation JSON path")
    parser.add_argument("--out", type=Path, default=OUT, help="Output evidence package JSON path")
    parser.add_argument("--state-out", type=Path, default=STATE_OUT, help="Output generation-state JSON path")
    args = parser.parse_args(argv)

    payload = build_from_path(args.investigation)
    semantic_changed = write_input_and_pending_state(
        args.out,
        args.state_out,
        payload,
        "bin/build_operator_assistant_input.py",
    )
    print(f"{args.out} ({'pending generation' if semantic_changed else 'semantic input unchanged'})")


if __name__ == "__main__":
    main()
