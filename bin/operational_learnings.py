#!/usr/bin/env python3
"""Deterministic operational learning model for Prime Observer."""

import datetime as dt
import json
from pathlib import Path

from incident_similarity import incident_features, load_completed_snapshots, pattern_label


SCHEMA_VERSION = 1
MODEL_VERSION = "prime_observer.operational_learnings.v1"
LEARNING_VERSION = "operational_learning.phase_1"
MIN_SUPPORTING_OBSERVATIONS = 2


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


def normalize(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("/", "_")


def display(value):
    return str(value or "unknown").replace("_", " ")


def confidence_for(support_count, conflict_count=0):
    if conflict_count:
        return "low" if support_count >= MIN_SUPPORTING_OBSERVATIONS else "retired"
    if support_count >= 4:
        return "high"
    if support_count >= MIN_SUPPORTING_OBSERVATIONS:
        return "medium"
    return "low"


def stability_for(support_count, conflict_count=0):
    if conflict_count and support_count < MIN_SUPPORTING_OBSERVATIONS:
        return "retired"
    if conflict_count:
        return "reduced_confidence"
    if support_count >= 4:
        return "stable"
    return "emerging"


def event_times(snapshot):
    record = snapshot.get("incident_record") if isinstance(snapshot.get("incident_record"), dict) else {}
    selected = snapshot.get("selected_event") if isinstance(snapshot.get("selected_event"), dict) else {}
    candidates = [
        record.get("started_at"),
        record.get("first_seen"),
        selected.get("first_anomalous_at"),
        selected.get("start"),
    ]
    end_candidates = [
        selected.get("recovered_at"),
        selected.get("last_anomalous_at"),
        selected.get("end"),
        record.get("latest_affected_at"),
    ]
    start = next((parse_ts(item) for item in candidates if parse_ts(item)), None)
    end = next((parse_ts(item) for item in end_candidates if parse_ts(item)), None) or start
    return start, end


def no_user_failure(features):
    impact = normalize(features.get("previous_user_impact"))
    app = normalize(features.get("application_experience"))
    return impact in {"none_observed", "none_reported", "unlikely", "low", "none_expected"} and app not in {"failing", "failed"}


def recovered_without_intervention(features):
    recovery = normalize(features.get("previous_recovery"))
    feedback = normalize(features.get("previous_operator_feedback"))
    return recovery in {"recovered", "complete", "completed", "resolved"} and "intervention" not in feedback


def snapshot_record(path, snapshot):
    features = incident_features(snapshot or {})
    incident_id = features.get("incident_id")
    if not incident_id:
        return None
    start, end = event_times(snapshot or {})
    pattern = pattern_label(features)
    return {
        "path": path,
        "snapshot": snapshot or {},
        "features": features,
        "incident_id": incident_id,
        "pattern": pattern,
        "start": start,
        "end": end,
        "target_class": features.get("target_class"),
        "likely_issue": features.get("likely_issue"),
        "adaptive_state": features.get("adaptive_baseline_state"),
        "no_user_failure": no_user_failure(features),
        "recovered_without_intervention": recovered_without_intervention(features),
        "external_context": normalize(features.get("external_context")),
    }


def evidence_ref(path, reason):
    return {"path": path, "reason": reason}


def insight_payload(*, insight_id, category, title, summary, support, generated_at, supporting_baselines=None, conflicts=None):
    supporting_baselines = supporting_baselines or []
    conflicts = conflicts or []
    support_count = len(support) + len(supporting_baselines)
    conflict_count = len(conflicts)
    stability = stability_for(support_count, conflict_count)
    confidence = "retired" if stability == "retired" else confidence_for(support_count, conflict_count)
    times = [item.get("start") for item in support if item.get("start")] + [item.get("end") for item in support if item.get("end")]
    return {
        "id": insight_id,
        "category": category,
        "title": title,
        "summary": summary,
        "confidence": confidence,
        "supporting_incidents": [item["incident_id"] for item in support],
        "supporting_intervals": [],
        "supporting_baselines": supporting_baselines,
        "first_seen": iso(min(times)) if times else iso(generated_at),
        "last_seen": iso(max(times)) if times else iso(generated_at),
        "times_observed": support_count,
        "stability": stability,
        "evidence_refs": [evidence_ref(item["path"], "Completed incident supporting this operational learning.") for item in support] + [evidence_ref(item, "Durable baseline supporting this operational learning.") for item in supporting_baselines] + [evidence_ref(item["path"], "Newer completed incident conflicts with this operational learning.") for item in conflicts],
    }


def baseline_records(baseline_history):
    targets = baseline_history.get("targets") if isinstance(baseline_history, dict) and isinstance(baseline_history.get("targets"), dict) else {}
    if not isinstance(targets, dict):
        targets = {}
    records = []
    for key, target in sorted(targets.items()):
        if not isinstance(target, dict):
            continue
        identity = target.get("identity") if isinstance(target.get("identity"), dict) else {}
        records.append({"key": key, "target": target, "identity": identity})
    return records


def build_operational_learnings(*, completed_snapshots, baseline_history=None, generated_at=None):
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc)
    records = []
    for path, snapshot in completed_snapshots or []:
        record = snapshot_record(path, snapshot)
        if record:
            records.append(record)
    insights = []

    resolver_no_failure = [item for item in records if item["target_class"] == "resolver_probe" and item["no_user_failure"]]
    resolver_conflicts = [item for item in records if item["target_class"] == "resolver_probe" and not item["no_user_failure"]]
    resolver_baselines = [f"viz/baseline_history.json#{item['key']}" for item in baseline_records(baseline_history) if item["identity"].get("target_class") == "resolver_probe" and item["target"].get("accepted_state") in {"elevated_but_stable", "changed"}]
    resolver_support_count = len(resolver_no_failure) + len(resolver_baselines)
    if resolver_support_count >= MIN_SUPPORTING_OBSERVATIONS or (resolver_support_count and resolver_conflicts):
        insights.append(insight_payload(
            insight_id="resolver-elevated-without-user-impact",
            category="resolver_behavior",
            title="Resolver latency can stay elevated without user-facing failure",
            summary=f"Resolver-path evidence has appeared {len(resolver_no_failure) + len(resolver_baselines)} times without observed user-facing failures.",
            support=resolver_no_failure,
            supporting_baselines=resolver_baselines,
            conflicts=resolver_conflicts,
            generated_at=generated_at,
        ))

    resolver_recovered = [item for item in records if item["target_class"] == "resolver_probe" and item["recovered_without_intervention"]]
    resolver_recovery_conflicts = [item for item in records if item["target_class"] == "resolver_probe" and not item["recovered_without_intervention"]]
    if len(resolver_recovered) >= MIN_SUPPORTING_OBSERVATIONS:
        insights.append(insight_payload(
            insight_id="resolver-path-recovers-without-intervention",
            category="recovery_behavior",
            title="Resolver-path incidents have recovered without intervention",
            summary=f"Resolver-path completed incidents recovered without recorded intervention {len(resolver_recovered)} times.",
            support=resolver_recovered,
            conflicts=resolver_recovery_conflicts,
            generated_at=generated_at,
        ))

    by_pattern = {}
    for item in records:
        if item["pattern"] and item["pattern"] != "unknown_pattern":
            by_pattern.setdefault(item["pattern"], []).append(item)
    for pattern, items in sorted(by_pattern.items()):
        if len(items) < MIN_SUPPORTING_OBSERVATIONS:
            continue
        insights.append(insight_payload(
            insight_id=f"pattern-{pattern}",
            category="baseline_learning" if pattern == "adaptive_baseline_event" else "isp_behavior" if "internet" in pattern or "upstream" in pattern else "resolver_behavior" if "resolver" in pattern else "gateway_behavior" if "gateway" in pattern else "application_behavior",
            title=f"{display(pattern).title()} has repeated",
            summary=f"Completed incident history contains {len(items)} {display(pattern)} observations.",
            support=items,
            generated_at=generated_at,
        ))

    external = [item for item in records if item["external_context"] not in {"", "unavailable", "unknown", "none"}]
    if len(external) >= MIN_SUPPORTING_OBSERVATIONS:
        insights.append(insight_payload(
            insight_id="external-context-coincides-with-incidents",
            category="environmental_context",
            title="External context has coincided with incidents",
            summary=f"Provider or environmental context was available during {len(external)} completed incidents.",
            support=external,
            generated_at=generated_at,
        ))

    insights.sort(key=lambda item: (item["stability"] == "retired", item["category"], item["id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "generated_at": iso(generated_at),
        "learning_version": LEARNING_VERSION,
        "insights": insights,
    }


def load_json(path):
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Build Prime Observer operational learnings artifact.")
    parser.add_argument("--investigations-dir", default="viz/investigations")
    parser.add_argument("--baseline-history", default="viz/baseline_history.json")
    parser.add_argument("--out", default="viz/operational_learnings.json")
    args = parser.parse_args(argv)
    payload = build_operational_learnings(
        completed_snapshots=load_completed_snapshots(args.investigations_dir),
        baseline_history=load_json(args.baseline_history),
        generated_at=dt.datetime.now(dt.timezone.utc),
    )
    out = Path(args.out)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(out)


if __name__ == "__main__":
    main()
