from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OBSERVATION_MATERIALIZATION_POLICY_VERSION = "prime_observer.observation_materialization.v1"

LIFECYCLE_ROLLING = "rolling"
LIFECYCLE_FINALIZED = "finalized"
LIFECYCLE_SUPERSEDED = "superseded"


@dataclass(frozen=True)
class ObservationCandidate:
    observation_type: str
    conclusion_kind: str
    scope: dict[str, Any]
    interval: dict[str, Any]
    evidence_references: list[dict[str, Any]]
    deterministic: bool = True
    explainable: bool = True
    independently_useful: bool = True
    externally_meaningful: bool = True


@dataclass(frozen=True)
class MaterializationDecision:
    should_materialize: bool
    lifecycle: str | None
    reason: str


class ObservationPolicy:
    """Deterministic allowlist/denylist for durable Observation conclusions."""

    _ALLOWED_LIFECYCLES = {
        "current_attribution": LIFECYCLE_ROLLING,
        "window_attribution": LIFECYCLE_ROLLING,
        "sustained_episode": LIFECYCLE_FINALIZED,
        "turbulence_episode": LIFECYCLE_FINALIZED,
    }

    _REJECTED_KINDS = {
        "raw_bucket",
        "intermediate_counter",
        "target_group_summary",
        "classified_sample",
        "health_threshold_evaluation",
        "baseline_calculation",
        "temporary_aggregation",
    }

    def decide(self, candidate: ObservationCandidate) -> MaterializationDecision:
        if candidate.conclusion_kind in self._REJECTED_KINDS:
            return MaterializationDecision(False, None, "implementation_detail")

        if candidate.conclusion_kind not in self._ALLOWED_LIFECYCLES:
            return MaterializationDecision(False, None, "unsupported_conclusion_kind")

        if not candidate.deterministic:
            return MaterializationDecision(False, None, "non_deterministic")
        if not candidate.explainable:
            return MaterializationDecision(False, None, "not_explainable")
        if not candidate.independently_useful:
            return MaterializationDecision(False, None, "not_independently_useful")
        if not candidate.externally_meaningful:
            return MaterializationDecision(False, None, "not_externally_meaningful")
        if not self._has_stable_scope(candidate.scope):
            return MaterializationDecision(False, None, "unstable_scope")
        if not self._has_stable_timing(candidate.interval):
            return MaterializationDecision(False, None, "unstable_timing")
        if not candidate.evidence_references:
            return MaterializationDecision(False, None, "missing_evidence")

        return MaterializationDecision(
            True,
            self._ALLOWED_LIFECYCLES[candidate.conclusion_kind],
            "materialize",
        )

    @staticmethod
    def _has_stable_scope(scope: dict[str, Any]) -> bool:
        return bool(scope.get("system") and scope.get("subject") and scope.get("view"))

    @staticmethod
    def _has_stable_timing(interval: dict[str, Any]) -> bool:
        return bool(interval.get("start") and interval.get("end"))


OBSERVATION_POLICY = ObservationPolicy()


def materialization_metadata(
    candidate: ObservationCandidate,
    decision: MaterializationDecision,
) -> dict[str, Any]:
    return {
        "policy_version": OBSERVATION_MATERIALIZATION_POLICY_VERSION,
        "conclusion_kind": candidate.conclusion_kind,
        "lifecycle": decision.lifecycle,
        "may_change": decision.lifecycle == LIFECYCLE_ROLLING,
    }
