from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from typing import Any


PROJECTION_SCHEMA_VERSION = 1


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _isoformat(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


def generate_observation_id(
    observation_type: str,
    scope: dict[str, Any],
    interval: dict[str, Any],
    state: dict[str, Any],
    model_version: str,
    evidence_references: list[dict[str, Any]],
) -> str:
    stable_identity = {
        "type": observation_type,
        "scope": scope,
        "interval": interval,
        "state": state,
        "model_version": model_version,
        "evidence_references": evidence_references,
    }
    digest = hashlib.sha256(_canonical_json(stable_identity).encode("utf-8")).hexdigest()[:20]
    return f"observation-{observation_type}-{digest}"


@dataclass(frozen=True)
class Observation:
    id: str
    type: str
    scope: dict[str, Any]
    interval: dict[str, Any]
    state: dict[str, Any]
    supporting_facts: list[str]
    evidence_references: list[dict[str, Any]]
    provenance: dict[str, Any]
    model_version: str
    generated_at: str
    confidence: str | None = None
    uncertainties: list[str] | None = None
    explanation: str | None = None

    @classmethod
    def create(
        cls,
        *,
        observation_type: str,
        scope: dict[str, Any],
        interval: dict[str, Any],
        state: dict[str, Any],
        supporting_facts: list[str],
        evidence_references: list[dict[str, Any]],
        provenance: dict[str, Any],
        model_version: str,
        generated_at: dt.datetime,
        confidence: str | None = None,
        uncertainties: list[str] | None = None,
        explanation: str | None = None,
    ) -> "Observation":
        observation_id = generate_observation_id(
            observation_type=observation_type,
            scope=scope,
            interval=interval,
            state=state,
            model_version=model_version,
            evidence_references=evidence_references,
        )
        return cls(
            id=observation_id,
            type=observation_type,
            scope=scope,
            interval=interval,
            state=state,
            supporting_facts=list(supporting_facts),
            evidence_references=list(evidence_references),
            provenance=dict(provenance),
            model_version=model_version,
            generated_at=_isoformat(generated_at),
            confidence=confidence,
            uncertainties=list(uncertainties) if uncertainties else None,
            explanation=explanation,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "type": self.type,
            "scope": self.scope,
            "interval": self.interval,
            "state": self.state,
            "supporting_facts": self.supporting_facts,
            "evidence_references": self.evidence_references,
            "provenance": self.provenance,
            "model_version": self.model_version,
            "generated_at": self.generated_at,
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.uncertainties is not None:
            payload["uncertainties"] = self.uncertainties
        if self.explanation is not None:
            payload["explanation"] = self.explanation
        return payload


def build_projection(
    observations: list[Observation],
    *,
    model_version: str,
    generated_at: dt.datetime,
    schema_version: int = PROJECTION_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "generated_at": _isoformat(generated_at),
        "model_version": model_version,
        "observations": [item.to_dict() for item in observations],
    }
