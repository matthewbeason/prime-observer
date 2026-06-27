import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from observation_domain import build_attribution_projection  # noqa: E402


class AttributionObservationParityTest(unittest.TestCase):
    def projection_by_view(self, payload):
        generated_at = dt.datetime(2026, 6, 26, 12, 0, tzinfo=dt.timezone.utc)
        projection = build_attribution_projection(
            payload,
            generated_at=generated_at,
            telemetry_source_path="data/bakeoff_20260626.csv",
        )
        return {
            "generated_at": projection["generated_at"],
            "model_version": projection["model_version"],
            "views": {
                observation["scope"]["view"]: observation
                for observation in projection["observations"]
            },
        }

    def sample_payload(
        self,
        *,
        current_status,
        current_label,
        current_confidence,
        current_summary,
        current_facts,
        window_status,
        window_label,
        window_confidence,
        window_evidence,
    ):
        return {
            "attribution_status": current_status,
            "attribution_label": current_label,
            "attribution_confidence": current_confidence.title(),
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
                "lan_samples": 12,
                "wan_samples": 24,
                "internet_probe_samples": 12,
                "resolver_probe_samples": 12,
            },
            "generated_at": "2026-06-26T12:00:00+00:00",
        }

    def assert_current_parity(self, payload, observation):
        self.assertEqual(observation["type"], "attribution")
        self.assertEqual(observation["scope"]["system"], "prime_observer")
        self.assertEqual(observation["scope"]["subject"], "network")
        self.assertEqual(observation["scope"]["view"], "current_attribution")
        self.assertEqual(observation["state"]["status"], payload["current_attribution"]["status"])
        self.assertEqual(observation["state"]["label"], payload["current_attribution"]["label"])
        self.assertEqual(observation["confidence"], payload["current_attribution"]["confidence"])
        self.assertEqual(observation["explanation"], payload["attribution_evidence"]["summary"])
        self.assertEqual(
            observation["supporting_facts"],
            payload["attribution_evidence"]["target_group_facts"],
        )
        self.assertEqual(observation["interval"]["start"], "2026-06-26T11:45:00+00:00")
        self.assertEqual(observation["interval"]["end"], "2026-06-26T12:00:00+00:00")
        self.assertEqual(observation["provenance"]["source_export"], "current_attribution")
        self.assertEqual(observation["model_version"], "prime_observer.attribution.v1")
        self.assertTrue(observation["generated_at"])

        refs = observation["evidence_references"]
        self.assertEqual(refs[0], {"kind": "artifact", "path": "viz/network_attribution.json"})
        self.assertEqual(refs[1], {"kind": "artifact", "path": "viz/latest.csv"})
        self.assertEqual(refs[2]["name"], "current_attribution")
        self.assertEqual(refs[2]["lookback_minutes"], 15)
        self.assertEqual(refs[3]["kind"], "telemetry_window")
        self.assertEqual(refs[4], {"kind": "telemetry_source", "path": "data/bakeoff_20260626.csv"})

    def assert_window_parity(self, payload, observation):
        self.assertEqual(observation["type"], "attribution")
        self.assertEqual(observation["scope"]["system"], "prime_observer")
        self.assertEqual(observation["scope"]["subject"], "network")
        self.assertEqual(observation["scope"]["view"], "window_attribution")
        self.assertEqual(observation["state"]["status"], payload["window_attribution"]["status"])
        self.assertEqual(observation["state"]["label"], payload["window_attribution"]["label"])
        self.assertEqual(observation["confidence"], payload["window_attribution"]["confidence"])
        self.assertEqual(
            observation["supporting_facts"],
            payload["window_attribution"]["evidence"],
        )
        self.assertEqual(
            observation["explanation"],
            " ".join(payload["window_attribution"]["evidence"]),
        )
        self.assertEqual(observation["interval"]["start"], payload["observation_window"]["start"])
        self.assertEqual(observation["interval"]["end"], payload["observation_window"]["end"])
        self.assertEqual(observation["provenance"]["source_export"], "window_attribution")
        self.assertEqual(observation["model_version"], "prime_observer.attribution.v1")
        self.assertTrue(observation["generated_at"])

        refs = observation["evidence_references"]
        self.assertEqual(refs[0], {"kind": "artifact", "path": "viz/network_attribution.json"})
        self.assertEqual(refs[1], {"kind": "artifact", "path": "viz/latest.csv"})
        self.assertEqual(refs[2], {"kind": "attribution_source", "name": "window_attribution"})
        self.assertEqual(refs[3]["kind"], "telemetry_window")
        self.assertEqual(refs[4], {"kind": "telemetry_source", "path": "data/bakeoff_20260626.csv"})

    def test_projection_preserves_attribution_semantics_across_scenarios(self):
        scenarios = [
            {
                "name": "no issue detected",
                "payload": self.sample_payload(
                    current_status="no_issue_detected",
                    current_label="No network issue detected",
                    current_confidence="high",
                    current_summary="LAN and WAN both look stable in the last 15 minutes.",
                    current_facts=[
                        "Internet and resolver probes both remained below degradation thresholds.",
                        "LAN/gateway elevated samples: 0/5.",
                    ],
                    window_status="inconclusive",
                    window_label="Inconclusive",
                    window_confidence="low",
                    window_evidence=["No sustained WAN incidents were found in the 24-hour window."],
                ),
            },
            {
                "name": "likely upstream",
                "payload": self.sample_payload(
                    current_status="likely_upstream",
                    current_label="Likely upstream (ISP / path)",
                    current_confidence="high",
                    current_summary="WAN shows sustained degradation while LAN stays below local threshold (1/17 elevated).",
                    current_facts=[
                        "Resolver probes degraded while internet probes remained healthy.",
                        "LAN/gateway elevated samples: 1/17.",
                    ],
                    window_status="likely_upstream",
                    window_label="Likely upstream (ISP / path)",
                    window_confidence="medium",
                    window_evidence=[
                        "2 sustained WAN target-group incident(s)",
                        "2 incident(s) with stable local gateway",
                    ],
                ),
            },
            {
                "name": "likely local",
                "payload": self.sample_payload(
                    current_status="likely_local",
                    current_label="Likely local (LAN / Wi-Fi)",
                    current_confidence="high",
                    current_summary="Local gateway stayed elevated across the lookback while WAN degradation remained limited.",
                    current_facts=[
                        "LAN/gateway elevated samples: 5/5.",
                        "Internet probe degraded buckets: 1.",
                    ],
                    window_status="likely_local",
                    window_label="Likely local (LAN / Wi-Fi)",
                    window_confidence="medium",
                    window_evidence=[
                        "1 sustained WAN target-group incident(s)",
                        "1 incident(s) with persistent local gateway degradation",
                    ],
                ),
            },
            {
                "name": "mixed evidence",
                "payload": self.sample_payload(
                    current_status="mixed_evidence",
                    current_label="Mixed evidence",
                    current_confidence="medium",
                    current_summary="WAN and LAN both showed meaningful degradation in the lookback window.",
                    current_facts=[
                        "Internet probe degraded buckets: 1.",
                        "LAN/gateway elevated samples: 3/5.",
                    ],
                    window_status="inconclusive",
                    window_label="Inconclusive",
                    window_confidence="medium",
                    window_evidence=[
                        "3 sustained WAN target-group incident(s)",
                        "1 incident(s) with stable local gateway",
                        "2 incident(s) with mixed or insufficient local evidence",
                    ],
                ),
            },
            {
                "name": "inconclusive with missing lan evidence",
                "payload": self.sample_payload(
                    current_status="inconclusive",
                    current_label="Inconclusive",
                    current_confidence="low",
                    current_summary="WAN degraded recently, but local evidence is missing or too sparse to attribute confidently.",
                    current_facts=[
                        "LAN/gateway elevated samples: 0/0.",
                        "Local evidence was missing during the attribution window.",
                    ],
                    window_status="inconclusive",
                    window_label="Inconclusive",
                    window_confidence="low",
                    window_evidence=[
                        "1 sustained WAN target-group incident(s)",
                        "1 incident(s) with mixed or insufficient local evidence",
                    ],
                ),
            },
            {
                "name": "sparse evidence",
                "payload": self.sample_payload(
                    current_status="inconclusive",
                    current_label="Inconclusive",
                    current_confidence="low",
                    current_summary="Too few recent samples were available to distinguish local from upstream behavior.",
                    current_facts=[
                        "Internet probe samples were sparse in the lookback.",
                        "LAN/gateway elevated samples: 1/1.",
                    ],
                    window_status="inconclusive",
                    window_label="Inconclusive",
                    window_confidence="low",
                    window_evidence=[
                        "Sustained incidents could not be classified confidently from sparse local evidence.",
                    ],
                ),
            },
        ]

        for scenario in scenarios:
            with self.subTest(scenario=scenario["name"]):
                projection = self.projection_by_view(scenario["payload"])
                current = projection["views"]["current_attribution"]
                window = projection["views"]["window_attribution"]

                self.assertEqual(projection["generated_at"], scenario["payload"]["generated_at"])
                self.assertEqual(projection["model_version"], "prime_observer.attribution.v1")
                self.assert_current_parity(scenario["payload"], current)
                self.assert_window_parity(scenario["payload"], window)


if __name__ == "__main__":
    unittest.main()
