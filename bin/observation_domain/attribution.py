from __future__ import annotations

import datetime as dt
from typing import Any

from .model import Observation, build_projection


ATTRIBUTION_OBSERVATION_MODEL_VERSION = "prime_observer.attribution.v1"


def _artifact_references(
    attribution_payload: dict[str, Any],
    *,
    source_name: str,
    telemetry_source_path: str | None,
    attribution_export_path: str,
    telemetry_export_path: str,
    lookback_minutes: int | None = None,
) -> list[dict[str, Any]]:
    evidence_references = [
        {"kind": "artifact", "path": attribution_export_path},
        {"kind": "artifact", "path": telemetry_export_path},
        {"kind": "attribution_source", "name": source_name},
    ]
    if lookback_minutes is not None:
        evidence_references[-1]["lookback_minutes"] = lookback_minutes

    observation_window = attribution_payload.get("observation_window", {}) or {}
    if observation_window:
        evidence_references.append({
            "kind": "telemetry_window",
            "hours": observation_window.get("hours"),
            "start": observation_window.get("start"),
            "end": observation_window.get("end"),
        })
    if telemetry_source_path:
        evidence_references.append({"kind": "telemetry_source", "path": telemetry_source_path})
    return evidence_references


def _build_current_attribution_observation(
    attribution_payload: dict[str, Any],
    *,
    generated_at: dt.datetime,
    telemetry_source_path: str | None,
    attribution_export_path: str,
    telemetry_export_path: str,
) -> Observation:
    current = attribution_payload.get("current_attribution", {}) or {}
    attribution_evidence = attribution_payload.get("attribution_evidence", {}) or {}
    lookback_minutes = int(attribution_evidence.get("lookback_minutes", 15) or 15)
    interval = {
        "start": (generated_at - dt.timedelta(minutes=lookback_minutes)).isoformat(),
        "end": generated_at.isoformat(),
    }
    scope = {
        "system": "prime_observer",
        "subject": "network",
        "view": "current_attribution",
    }
    state = {
        "status": current.get("status") or attribution_payload.get("attribution_status"),
        "label": current.get("label") or attribution_payload.get("attribution_label"),
    }
    evidence_references = _artifact_references(
        attribution_payload,
        source_name="current_attribution",
        telemetry_source_path=telemetry_source_path,
        attribution_export_path=attribution_export_path,
        telemetry_export_path=telemetry_export_path,
        lookback_minutes=lookback_minutes,
    )

    return Observation.create(
        observation_type="attribution",
        scope=scope,
        interval=interval,
        state=state,
        supporting_facts=list(attribution_evidence.get("target_group_facts") or []),
        evidence_references=evidence_references,
        confidence=current.get("confidence"),
        explanation=attribution_evidence.get("summary"),
        provenance={
            "producer": "bin/transform_latest.py",
            "source_export": "current_attribution",
            "evidence_artifacts": [attribution_export_path, telemetry_export_path],
        },
        model_version=ATTRIBUTION_OBSERVATION_MODEL_VERSION,
        generated_at=generated_at,
    )


def _build_window_attribution_observation(
    attribution_payload: dict[str, Any],
    *,
    generated_at: dt.datetime,
    telemetry_source_path: str | None,
    attribution_export_path: str,
    telemetry_export_path: str,
) -> Observation:
    window = attribution_payload.get("window_attribution", {}) or {}
    observation_window = attribution_payload.get("observation_window", {}) or {}
    interval = {
        "start": observation_window.get("start") or generated_at.isoformat(),
        "end": observation_window.get("end") or generated_at.isoformat(),
    }
    scope = {
        "system": "prime_observer",
        "subject": "network",
        "view": "window_attribution",
    }
    evidence = list(window.get("evidence") or [])
    evidence_references = _artifact_references(
        attribution_payload,
        source_name="window_attribution",
        telemetry_source_path=telemetry_source_path,
        attribution_export_path=attribution_export_path,
        telemetry_export_path=telemetry_export_path,
    )

    return Observation.create(
        observation_type="attribution",
        scope=scope,
        interval=interval,
        state={
            "status": window.get("status"),
            "label": window.get("label"),
        },
        supporting_facts=evidence,
        evidence_references=evidence_references,
        confidence=window.get("confidence"),
        explanation=" ".join(evidence) if evidence else None,
        provenance={
            "producer": "bin/transform_latest.py",
            "source_export": "window_attribution",
            "evidence_artifacts": [attribution_export_path, telemetry_export_path],
        },
        model_version=ATTRIBUTION_OBSERVATION_MODEL_VERSION,
        generated_at=generated_at,
    )


def build_attribution_observations(
    attribution_payload: dict[str, Any],
    *,
    generated_at: dt.datetime,
    telemetry_source_path: str | None = None,
    attribution_export_path: str = "viz/network_attribution.json",
    telemetry_export_path: str = "viz/latest.csv",
) -> list[Observation]:
    return [
        _build_current_attribution_observation(
            attribution_payload,
            generated_at=generated_at,
            telemetry_source_path=telemetry_source_path,
            attribution_export_path=attribution_export_path,
            telemetry_export_path=telemetry_export_path,
        ),
        _build_window_attribution_observation(
            attribution_payload,
            generated_at=generated_at,
            telemetry_source_path=telemetry_source_path,
            attribution_export_path=attribution_export_path,
            telemetry_export_path=telemetry_export_path,
        ),
    ]


def build_attribution_observation(
    attribution_payload: dict[str, Any],
    *,
    generated_at: dt.datetime,
    telemetry_source_path: str | None = None,
    attribution_export_path: str = "viz/network_attribution.json",
    telemetry_export_path: str = "viz/latest.csv",
) -> Observation:
    return build_attribution_observations(
        attribution_payload,
        generated_at=generated_at,
        telemetry_source_path=telemetry_source_path,
        attribution_export_path=attribution_export_path,
        telemetry_export_path=telemetry_export_path,
    )[0]


def build_attribution_projection(
    attribution_payload: dict[str, Any],
    *,
    generated_at: dt.datetime,
    telemetry_source_path: str | None = None,
) -> dict[str, Any]:
    observations = build_attribution_observations(
        attribution_payload,
        generated_at=generated_at,
        telemetry_source_path=telemetry_source_path,
    )
    return build_projection(
        observations,
        model_version=ATTRIBUTION_OBSERVATION_MODEL_VERSION,
        generated_at=generated_at,
    )
