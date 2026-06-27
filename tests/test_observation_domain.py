import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from observation_domain import (  # noqa: E402
    ATTRIBUTION_OBSERVATION_MODEL_VERSION,
    EPISODE_OBSERVATION_MODEL_VERSION,
    LIFECYCLE_FINALIZED,
    LIFECYCLE_ROLLING,
    OBSERVATION_MATERIALIZATION_POLICY_VERSION,
    OBSERVATION_POLICY,
    Observation,
    ObservationCandidate,
    OBSERVATION_PROJECTION_MODEL_VERSION,
    build_attribution_observation,
    build_attribution_observations,
    build_attribution_projection,
    build_episode_observations,
    build_projection,
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

    def sample_incident_run(self):
        return [
            {
                "t": dt.datetime(2026, 6, 26, 11, 45, tzinfo=dt.timezone.utc),
                "phase": "FIBER",
                "target_class": "internet_probe",
                "host": "1.1.1.1",
                "raw_bad": True,
                "is_bad": False,
            },
            {
                "t": dt.datetime(2026, 6, 26, 11, 46, tzinfo=dt.timezone.utc),
                "phase": "FIBER",
                "target_class": "internet_probe",
                "host": "1.1.1.1",
                "raw_bad": True,
                "is_bad": True,
            },
        ]

    def sample_incident(self):
        return {
            "start": "2026-06-26T11:45:00+00:00",
            "end": "2026-06-26T11:46:00+00:00",
            "status": "likely_upstream",
            "label": "Likely upstream (ISP / path)",
            "confidence": "high",
            "evidence": ["internet_probe degradation", "local gateway stable"],
            "metrics": {
                "wan_samples": 2,
                "wan_raw_bad_samples": 2,
                "wan_sustained_bad_samples": 1,
                "lan_samples": 2,
                "lan_elevated_samples": 0,
                "lan_elevated_rate_pct": 0.0,
                "target_class": "internet_probe",
                "target_hosts": {"1.1.1.1": 2},
            },
        }

    def sample_turbulence_bucket(self):
        return {
            "phase": "FIBER",
            "target_class": "resolver_probe",
            "t": dt.datetime(2026, 6, 26, 11, 30, tzinfo=dt.timezone.utc),
            "t2": dt.datetime(2026, 6, 26, 11, 45, tzinfo=dt.timezone.utc),
            "total": 4,
            "bad": 0,
            "raw_bad": 4,
            "max_raw_run": 1,
            "is_turbulence": True,
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
        self.assertEqual(
            payload["provenance"]["materialization"]["policy_version"],
            OBSERVATION_MATERIALIZATION_POLICY_VERSION,
        )
        self.assertEqual(payload["provenance"]["materialization"]["lifecycle"], LIFECYCLE_ROLLING)

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

    def test_build_episode_observations_maps_sustained_and_turbulence_intervals(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)

        observations = build_episode_observations(
            incident_runs=[self.sample_incident_run()],
            incidents=[self.sample_incident()],
            turbulence_buckets=[self.sample_turbulence_bucket()],
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )
        payloads = [observation.to_dict() for observation in observations]
        sustained = next(item for item in payloads if item["state"]["status"] == "sustained_degradation")
        turbulence = next(item for item in payloads if item["state"]["status"] == "turbulence")

        self.assertEqual(sustained["type"], "episode")
        self.assertEqual(sustained["interval"]["start"], "2026-06-26T11:45:00+00:00")
        self.assertEqual(sustained["interval"]["end"], "2026-06-26T11:46:00+00:00")
        self.assertEqual(sustained["scope"]["target_class"], "internet_probe")
        self.assertEqual(sustained["evidence_references"][0]["path"], "viz/latest.csv")
        self.assertEqual(sustained["evidence_references"][-1]["path"], "viz/network_attribution.json")
        self.assertEqual(sustained["model_version"], EPISODE_OBSERVATION_MODEL_VERSION)
        self.assertEqual(sustained["provenance"]["materialization"]["lifecycle"], LIFECYCLE_FINALIZED)
        self.assertIn("sustained degradation observed", sustained["explanation"].lower())

        self.assertEqual(turbulence["type"], "episode")
        self.assertEqual(turbulence["interval"]["start"], "2026-06-26T11:30:00+00:00")
        self.assertEqual(turbulence["interval"]["end"], "2026-06-26T11:45:00+00:00")
        self.assertEqual(turbulence["scope"]["target_class"], "resolver_probe")
        self.assertEqual(turbulence["evidence_references"][-1]["kind"], "turbulence_bucket")
        self.assertEqual(turbulence["provenance"]["materialization"]["lifecycle"], LIFECYCLE_FINALIZED)
        self.assertIn("no sustained run", turbulence["explanation"].lower())

    def test_episode_observation_ids_are_deterministic(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        first = [
            item.to_dict()["id"]
            for item in build_episode_observations(
                incident_runs=[self.sample_incident_run()],
                incidents=[self.sample_incident()],
                turbulence_buckets=[self.sample_turbulence_bucket()],
                generated_at=generated_at,
                telemetry_source_path="data/bakeoff_20260626.csv",
            )
        ]
        second = [
            item.to_dict()["id"]
            for item in build_episode_observations(
                incident_runs=[self.sample_incident_run()],
                incidents=[self.sample_incident()],
                turbulence_buckets=[self.sample_turbulence_bucket()],
                generated_at=generated_at,
                telemetry_source_path="data/bakeoff_20260626.csv",
            )
        ]

        self.assertEqual(first, second)
        self.assertTrue(all(item.startswith("observation-episode-") for item in first))
        self.assertEqual(first, ["observation-episode-ef7a7f690d8cd6852d6e", "observation-episode-7c421a8d6d7926f2ef30"])

    def test_attribution_observation_ids_remain_unchanged(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        observations = build_attribution_observations(
            self.sample_attribution_payload(),
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )
        ids_by_view = {item.scope["view"]: item.id for item in observations}

        self.assertEqual(ids_by_view["current_attribution"], "observation-attribution-5a2b4ed3ecf2946ca35e")
        self.assertEqual(ids_by_view["window_attribution"], "observation-attribution-4b69af4b886cea590e9a")

    def test_policy_materializes_supported_conclusions(self):
        candidate = ObservationCandidate(
            observation_type="episode",
            conclusion_kind="sustained_episode",
            scope={"system": "prime_observer", "subject": "network", "view": "episode"},
            interval={"start": "2026-06-26T11:45:00+00:00", "end": "2026-06-26T11:46:00+00:00"},
            evidence_references=[{"kind": "artifact", "path": "viz/latest.csv"}],
        )

        decision = OBSERVATION_POLICY.decide(candidate)

        self.assertTrue(decision.should_materialize)
        self.assertEqual(decision.lifecycle, LIFECYCLE_FINALIZED)

    def test_policy_does_not_materialize_raw_bucket(self):
        candidate = ObservationCandidate(
            observation_type="episode",
            conclusion_kind="raw_bucket",
            scope={"system": "prime_observer", "subject": "network", "view": "bucket"},
            interval={"start": "2026-06-26T11:30:00+00:00", "end": "2026-06-26T11:45:00+00:00"},
            evidence_references=[{"kind": "artifact", "path": "viz/latest.csv"}],
        )

        decision = OBSERVATION_POLICY.decide(candidate)

        self.assertFalse(decision.should_materialize)
        self.assertEqual(decision.reason, "implementation_detail")

    def test_policy_does_not_materialize_target_group_summary(self):
        candidate = ObservationCandidate(
            observation_type="attribution",
            conclusion_kind="target_group_summary",
            scope={"system": "prime_observer", "subject": "network", "view": "summary"},
            interval={"start": "2026-06-25T12:00:00+00:00", "end": "2026-06-26T12:00:00+00:00"},
            evidence_references=[{"kind": "artifact", "path": "viz/network_attribution.json"}],
        )

        decision = OBSERVATION_POLICY.decide(candidate)

        self.assertFalse(decision.should_materialize)
        self.assertEqual(decision.reason, "implementation_detail")

    def test_policy_decisions_are_deterministic(self):
        candidate = ObservationCandidate(
            observation_type="attribution",
            conclusion_kind="current_attribution",
            scope={"system": "prime_observer", "subject": "network", "view": "current_attribution"},
            interval={"start": "2026-06-26T11:45:00+00:00", "end": "2026-06-26T12:00:00+00:00"},
            evidence_references=[{"kind": "artifact", "path": "viz/network_attribution.json"}],
        )

        first = OBSERVATION_POLICY.decide(candidate)
        second = OBSERVATION_POLICY.decide(candidate)

        self.assertEqual(first, second)
        self.assertEqual(first.lifecycle, LIFECYCLE_ROLLING)

    def test_projection_can_include_attribution_and_episode_observations(self):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        attribution_observations = build_attribution_observations(
            self.sample_attribution_payload(),
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )
        episode_observations = build_episode_observations(
            incident_runs=[self.sample_incident_run()],
            incidents=[self.sample_incident()],
            turbulence_buckets=[self.sample_turbulence_bucket()],
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )

        projection = build_projection(
            attribution_observations + episode_observations,
            model_version=OBSERVATION_PROJECTION_MODEL_VERSION,
            generated_at=generated_at,
        )

        self.assertEqual(projection["model_version"], OBSERVATION_PROJECTION_MODEL_VERSION)
        self.assertEqual(len(projection["observations"]), 4)
        self.assertEqual([item["type"] for item in projection["observations"][:2]], ["attribution", "attribution"])
        self.assertEqual({item["type"] for item in projection["observations"]}, {"attribution", "episode"})


if __name__ == "__main__":
    unittest.main()
