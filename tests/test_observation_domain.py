import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from observation_domain import (  # noqa: E402
    ATTRIBUTION_OBSERVATION_MODEL_VERSION,
    Observation,
    build_attribution_observation,
    build_attribution_observations,
    build_attribution_projection,
    generate_observation_id,
)


class ObservationDomainTest(unittest.TestCase):
    def sample_attribution_payload(
        self,
        *,
        current_status="likely_upstream",
        current_label="Likely upstream (ISP / path)",
        current_confidence="high",
        current_summary="WAN shows sustained degradation while LAN stays below local threshold (1/17 elevated).",
        current_facts=None,
        window_status="likely_upstream",
        window_label="Likely upstream (ISP / path)",
        window_confidence="medium",
        window_evidence=None,
    ):
        if current_facts is None:
            current_facts = [
                "Resolver probes degraded while internet probes remained healthy.",
                "LAN/gateway elevated samples: 1/17.",
            ]
        if window_evidence is None:
            window_evidence = [
                "2 sustained WAN target-group incident(s)",
                "2 incident(s) with stable local gateway",
            ]

        return {
            "attribution_status": current_status,
            "attribution_label": current_label,
            "attribution_evidence": {
                "summary": current_summary,
                "target_group_facts": current_facts,
                "lookback_minutes": 15,
            },
            "current_attribution": {
                "status": current_status,
                "label": current_label,
                "confidence": current_confidence,
                "evidence": [current_summary],
            },
            "window_attribution": {
                "status": window_status,
                "label": window_label,
                "confidence": window_confidence,
                "evidence": window_evidence,
            },
            "observation_window": {
                "hours": 24,
                "start": "2026-06-25T12:00:00+00:00",
                "end": "2026-06-26T12:00:00+00:00",
            },
        }

    def test_observation_model_serialization_omits_optional_fields(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        observation = Observation.create(
            observation_type="attribution",
            scope={"system": "prime_observer"},
            interval={"start": "2026-06-26T11:45:00+00:00", "end": "2026-06-26T12:00:00+00:00"},
            state={"status": "likely_upstream"},
            supporting_facts=["Resolver probes degraded while internet probes remained healthy."],
            evidence_references=[{"kind": "artifact", "path": "viz/network_attribution.json"}],
            provenance={"producer": "bin/transform_latest.py"},
            model_version=ATTRIBUTION_OBSERVATION_MODEL_VERSION,
            generated_at=generated_at,
        )

        payload = observation.to_dict()

        self.assertIn("id", payload)
        self.assertEqual(payload["type"], "attribution")
        self.assertNotIn("confidence", payload)
        self.assertNotIn("uncertainties", payload)
        self.assertNotIn("explanation", payload)

    def test_observation_id_generation_is_deterministic(self):
        scope = {"system": "prime_observer", "subject": "network", "view": "current_attribution"}
        interval = {"start": "2026-06-26T11:45:00+00:00", "end": "2026-06-26T12:00:00+00:00"}
        state = {"status": "likely_upstream", "label": "Likely upstream (ISP / path)"}
        evidence_references = [
            {"kind": "artifact", "path": "viz/network_attribution.json"},
            {"kind": "telemetry_source", "path": "data/bakeoff_20260626.csv"},
        ]

        first = generate_observation_id(
            "attribution",
            scope,
            interval,
            state,
            ATTRIBUTION_OBSERVATION_MODEL_VERSION,
            evidence_references,
        )
        second = generate_observation_id(
            "attribution",
            scope,
            interval,
            state,
            ATTRIBUTION_OBSERVATION_MODEL_VERSION,
            evidence_references,
        )

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("observation-attribution-"))

    def test_build_attribution_observation_uses_current_export_shape(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        attribution_payload = self.sample_attribution_payload()

        observation = build_attribution_observation(
            attribution_payload,
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )
        payload = observation.to_dict()

        self.assertEqual(payload["type"], "attribution")
        self.assertEqual(payload["state"]["status"], "likely_upstream")
        self.assertEqual(payload["confidence"], "high")
        self.assertEqual(payload["explanation"], attribution_payload["attribution_evidence"]["summary"])
        self.assertEqual(payload["interval"]["start"], "2026-06-26T11:45:00+00:00")
        self.assertEqual(payload["interval"]["end"], "2026-06-26T12:00:00+00:00")
        self.assertEqual(payload["evidence_references"][-1]["path"], "data/bakeoff_20260626.csv")

    def test_build_attribution_observations_includes_current_and_window_views(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        attribution_payload = self.sample_attribution_payload(
            current_status="no_issue_detected",
            current_label="No network issue detected",
            current_summary="LAN and WAN both look stable in the last 15 minutes.",
            current_facts=[
                "Internet and resolver probes both remained below degradation thresholds.",
                "LAN/gateway elevated samples: 0/5.",
            ],
            window_status="inconclusive",
            window_label="Inconclusive",
            window_confidence="low",
            window_evidence=["No sustained WAN incidents were found in the 24-hour window."],
        )

        observations = build_attribution_observations(
            attribution_payload,
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )
        by_view = {observation.scope["view"]: observation.to_dict() for observation in observations}

        self.assertEqual(set(by_view), {"current_attribution", "window_attribution"})
        self.assertEqual(by_view["current_attribution"]["state"]["status"], "no_issue_detected")
        self.assertEqual(by_view["window_attribution"]["state"]["status"], "inconclusive")
        self.assertEqual(by_view["window_attribution"]["interval"]["start"], "2026-06-25T12:00:00+00:00")
        self.assertEqual(by_view["window_attribution"]["interval"]["end"], "2026-06-26T12:00:00+00:00")
        self.assertEqual(
            by_view["window_attribution"]["supporting_facts"],
            ["No sustained WAN incidents were found in the 24-hour window."],
        )

    def test_projection_includes_expected_metadata(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        attribution_payload = self.sample_attribution_payload(
            current_status="no_issue_detected",
            current_label="No network issue detected",
            current_summary="LAN and WAN both look stable in the last 15 minutes.",
            current_facts=[
                "Internet and resolver probes both remained below degradation thresholds.",
                "LAN/gateway elevated samples: 0/5.",
            ],
        )

        projection = build_attribution_projection(
            attribution_payload,
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )

        self.assertEqual(projection["schema_version"], 1)
        self.assertEqual(projection["generated_at"], "2026-06-26T12:00:00+00:00")
        self.assertEqual(projection["model_version"], ATTRIBUTION_OBSERVATION_MODEL_VERSION)
        self.assertEqual(len(projection["observations"]), 2)
        self.assertEqual(projection["observations"][0]["model_version"], ATTRIBUTION_OBSERVATION_MODEL_VERSION)


if __name__ == "__main__":
    unittest.main()
