from __future__ import annotations

import datetime as dt
from typing import Any

from .model import Observation


EPISODE_OBSERVATION_MODEL_VERSION = "prime_observer.episode.v1"
OBSERVATION_PROJECTION_MODEL_VERSION = "prime_observer.observation.v1"


def _telemetry_references(
    *,
    start: str,
    end: str,
    telemetry_export_path: str,
    telemetry_source_path: str | None,
) -> list[dict[str, Any]]:
    evidence_references = [
        {"kind": "artifact", "path": telemetry_export_path},
        {"kind": "telemetry_window", "start": start, "end": end},
    ]
    if telemetry_source_path:
        evidence_references.append({"kind": "telemetry_source", "path": telemetry_source_path})
    return evidence_references


def _target_label(target_class: str) -> str:
    return {
        "internet_probe": "Internet probe",
        "resolver_probe": "Resolver probe",
        "gateway_probe": "Gateway probe",
    }.get(target_class, target_class.replace("_", " ").capitalize())


def _host_summary(host_counts: dict[str, int]) -> str:
    hosts = sorted(host_counts)
    if not hosts:
        return "unknown hosts"
    return ", ".join(hosts)


def _build_sustained_episode_observation(
    run: list[dict[str, Any]],
    incident: dict[str, Any],
    *,
    incident_index: int,
    generated_at: dt.datetime,
    telemetry_source_path: str | None,
    telemetry_export_path: str,
    attribution_export_path: str,
) -> Observation:
    start = incident["start"]
    end = incident["end"]
    metrics = incident.get("metrics", {}) or {}
    target_class = metrics.get("target_class") or run[0].get("target_class") or "unknown_probe"
    target_label = _target_label(target_class)
    phase = run[0].get("phase") or "FIBER"
    host_counts = dict(metrics.get("target_hosts") or {})
    sustained_samples = metrics.get("wan_sustained_bad_samples", 0) or 0
    raw_bad_samples = metrics.get("wan_raw_bad_samples", 0) or 0
    wan_samples = metrics.get("wan_samples", len(run)) or len(run)
    lan_samples = metrics.get("lan_samples", 0) or 0
    lan_elevated = metrics.get("lan_elevated_samples", 0) or 0
    lan_rate = metrics.get("lan_elevated_rate_pct", 0.0) or 0.0

    evidence_references = _telemetry_references(
        start=start,
        end=end,
        telemetry_export_path=telemetry_export_path,
        telemetry_source_path=telemetry_source_path,
    )
    evidence_references.append({
        "kind": "artifact",
        "path": attribution_export_path,
        "source_export": "incidents",
        "index": incident_index,
    })

    return Observation.create(
        observation_type="episode",
        scope={
            "system": "prime_observer",
            "subject": "network",
            "view": "episode",
            "phase": phase,
            "target_class": target_class,
        },
        interval={"start": start, "end": end},
        state={
            "status": "sustained_degradation",
            "label": "Sustained degradation",
        },
        supporting_facts=[
            f"{target_label} interval observed on {phase} across {wan_samples} WAN sample(s) for host(s): {_host_summary(host_counts)}.",
            f"{sustained_samples} sustained bad WAN sample(s) and {raw_bad_samples} raw bad WAN sample(s) were present in the interval.",
            f"Local gateway overlap: {lan_elevated}/{lan_samples} elevated sample(s) ({lan_rate:.1f}%).",
        ],
        evidence_references=evidence_references,
        explanation=(
            f"{target_label} sustained degradation observed from {start} to {end} "
            f"with {sustained_samples} sustained bad WAN sample(s)."
        ),
        provenance={
            "producer": "bin/transform_latest.py",
            "source_export": "episode_observations",
            "source_logic": "find_sustained_wan_incidents",
            "evidence_artifacts": [telemetry_export_path, attribution_export_path],
        },
        model_version=EPISODE_OBSERVATION_MODEL_VERSION,
        generated_at=generated_at,
    )


def _build_turbulence_episode_observation(
    bucket: dict[str, Any],
    *,
    bucket_index: int,
    generated_at: dt.datetime,
    telemetry_source_path: str | None,
    telemetry_export_path: str,
) -> Observation:
    start = bucket["t"].isoformat()
    end = bucket["t2"].isoformat()
    target_class = bucket.get("target_class") or "unknown_probe"
    target_label = _target_label(target_class)
    phase = bucket.get("phase") or "FIBER"

    evidence_references = _telemetry_references(
        start=start,
        end=end,
        telemetry_export_path=telemetry_export_path,
        telemetry_source_path=telemetry_source_path,
    )
    evidence_references.append({
        "kind": "turbulence_bucket",
        "phase": phase,
        "target_class": target_class,
        "index": bucket_index,
    })

    return Observation.create(
        observation_type="episode",
        scope={
            "system": "prime_observer",
            "subject": "network",
            "view": "episode",
            "phase": phase,
            "target_class": target_class,
        },
        interval={"start": start, "end": end},
        state={
            "status": "turbulence",
            "label": "Turbulence",
        },
        supporting_facts=[
            f"{target_label} turbulence observed on {phase} in a single {bucket.get('total', 0)}-sample bucket.",
            (
                f"{bucket.get('raw_bad', 0)} raw bad WAN sample(s) were present with "
                f"{bucket.get('bad', 0)} sustained bad sample(s)."
            ),
            f"Maximum consecutive raw bad run in the bucket was {bucket.get('max_raw_run', 0)} sample(s).",
        ],
        evidence_references=evidence_references,
        explanation=(
            f"{target_label} turbulence observed from {start} to {end} with "
            f"{bucket.get('raw_bad', 0)} raw bad WAN sample(s) and no sustained run."
        ),
        provenance={
            "producer": "bin/transform_latest.py",
            "source_export": "episode_observations",
            "source_logic": "classify_buckets",
            "evidence_artifacts": [telemetry_export_path],
        },
        model_version=EPISODE_OBSERVATION_MODEL_VERSION,
        generated_at=generated_at,
    )


def build_episode_observations(
    *,
    incident_runs: list[list[dict[str, Any]]],
    incidents: list[dict[str, Any]],
    turbulence_buckets: list[dict[str, Any]],
    generated_at: dt.datetime,
    telemetry_source_path: str | None = None,
    telemetry_export_path: str = "viz/latest.csv",
    attribution_export_path: str = "viz/network_attribution.json",
) -> list[Observation]:
    observations = [
        _build_sustained_episode_observation(
            run,
            incident,
            incident_index=index,
            generated_at=generated_at,
            telemetry_source_path=telemetry_source_path,
            telemetry_export_path=telemetry_export_path,
            attribution_export_path=attribution_export_path,
        )
        for index, (run, incident) in enumerate(zip(incident_runs, incidents))
    ]
    observations.extend(
        _build_turbulence_episode_observation(
            bucket,
            bucket_index=index,
            generated_at=generated_at,
            telemetry_source_path=telemetry_source_path,
            telemetry_export_path=telemetry_export_path,
        )
        for index, bucket in enumerate(turbulence_buckets)
    )
    return observations
