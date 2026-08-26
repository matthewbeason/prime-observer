import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "external_context_history.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "external_context_history.json"


def load_module():
    spec = importlib.util.spec_from_file_location("external_context_history", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExternalContextHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.fixtures = json.loads(FIXTURE_PATH.read_text())

    def test_repeated_fetch_is_one_logical_event(self):
        first = self.module.canonical_events_from_snapshot(self.fixtures["cloudflare_first"])
        repeated = self.module.canonical_events_from_snapshot(self.fixtures["cloudflare_first"])
        merged = self.module.merge_event_observations(first, repeated)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["identity_basis"], "provider_id")

    def test_provider_update_keeps_identity_and_updates_lifecycle(self):
        first = self.module.canonical_events_from_snapshot(self.fixtures["cloudflare_first"])
        updated = self.module.canonical_events_from_snapshot(self.fixtures["cloudflare_updated"])
        merged = self.module.merge_event_observations(first, updated)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["observed_first"], "2026-08-26T12:05:00Z")
        self.assertEqual(merged[0]["observed_last"], "2026-08-26T12:30:00Z")
        self.assertEqual(merged[0]["event_end"], "2026-08-26T12:20:00Z")
        self.assertEqual(merged[0]["resolution"], "provider_ended")

    def test_distinct_provider_ids_remain_distinct(self):
        first = self.module.canonical_events_from_snapshot(self.fixtures["cloudflare_first"])
        distinct_payload = json.loads(json.dumps(self.fixtures["cloudflare_first"]))
        distinct_payload["items"][0]["provider_event_id"] = "radar-outage-2"
        distinct = self.module.canonical_events_from_snapshot(distinct_payload)
        self.assertEqual(len(self.module.merge_event_observations(first, distinct)), 2)

    def test_real_start_and_end_overlap_interval(self):
        event = self.module.canonical_events_from_snapshot(self.fixtures["cloudflare_updated"])[0]
        aligned = self.module.align_event_to_interval(
            event, "2026-08-26T12:10:00Z", "2026-08-26T12:25:00Z"
        )
        self.assertEqual(aligned["relationship"], "overlaps")
        self.assertEqual(aligned["alignment_basis"], "provider_event_range")

    def test_start_without_end_is_explicitly_uncertain_before_interval(self):
        event = self.module.canonical_events_from_snapshot(self.fixtures["cloudflare_first"])[0]
        aligned = self.module.align_event_to_interval(
            event, "2026-08-26T12:10:00Z", "2026-08-26T12:25:00Z"
        )
        self.assertEqual(aligned["relationship"], "uncertain")
        self.assertEqual(aligned["temporal_status"], "start_only")
        self.assertTrue(any("duration" in item.lower() for item in aligned["limitations"]))

    def test_start_only_event_observed_inside_interval_overlaps_by_observation(self):
        event = self.module.canonical_events_from_snapshot(self.fixtures["aps_with_start"])[0]
        aligned = self.module.align_event_to_interval(
            event, "2026-08-26T12:05:00Z", "2026-08-26T12:15:00Z"
        )
        self.assertEqual(aligned["relationship"], "overlaps")
        self.assertEqual(aligned["alignment_basis"], "prime_observation_within_interval")
        self.assertEqual(aligned["temporal_status"], "start_only")

    def test_snapshot_only_context_is_not_historically_aligned(self):
        event = self.module.canonical_events_from_snapshot(self.fixtures["aps_snapshot_only"])[0]
        aligned = self.module.align_event_to_interval(
            event, "2026-08-26T12:00:00Z", "2026-08-26T12:15:00Z"
        )
        self.assertEqual(aligned["relationship"], "snapshot_only")
        self.assertEqual(aligned["alignment_basis"], "prime_observation_only")

    def test_collection_time_is_never_event_occurrence_time(self):
        payload = json.loads(json.dumps(self.fixtures["cloudflare_first"]))
        payload["items"][0]["started"] = None
        event = self.module.canonical_events_from_snapshot(payload)[0]
        self.assertIsNone(event["event_start"])
        self.assertEqual(event["prime_collection_time"], payload["generated_at"])

    def test_aps_restoration_estimate_is_not_event_end(self):
        event = self.module.canonical_events_from_snapshot(self.fixtures["aps_with_start"])[0]
        self.assertIsNone(event["event_end"])
        self.assertEqual(
            event["supporting_detail"]["estimated_restoration_time"],
            "2026-08-26T14:00:00Z",
        )

    def test_disappearance_does_not_invent_resolution_time(self):
        event = self.module.canonical_events_from_snapshot(self.fixtures["aps_with_start"])
        marked = self.module.mark_absent_from_complete_snapshot(
            event,
            provider="aps",
            observed_keys=[],
            collection_time="2026-08-26T12:30:00Z",
        )
        self.assertEqual(marked[0]["resolution"], "absent_from_later_complete_snapshot")
        self.assertIsNone(marked[0]["event_end"])

    def test_missing_provider_data_degrades_safely(self):
        self.assertEqual(self.module.canonical_events_from_snapshot(None), [])
        self.assertEqual(
            self.module.canonical_events_from_snapshot(
                {"provider": "aps", "status": "unavailable", "generated_at": "2026-08-26T12:00:00Z"}
            ),
            [],
        )

    def test_contract_has_no_health_attribution_impact_or_causality_fields(self):
        event = self.module.canonical_events_from_snapshot(self.fixtures["aps_with_start"])[0]
        for field in ("health", "attribution", "incident_eligible", "user_impact", "causality_score"):
            self.assertNotIn(field, event)
        for relative in (
            "bin/health_model.py",
            "bin/health_dimensions.py",
            "bin/semantic_health.py",
            "bin/observation_domain/attribution.py",
        ):
            self.assertNotIn("external_context_history", (ROOT / relative).read_text())

    def test_browser_does_not_own_event_identity_or_overlap(self):
        for path in (ROOT / "viz" / "index.html", ROOT / "viz" / "investigate.html"):
            body = path.read_text()
            self.assertNotIn("stable_event_key", body)
            self.assertNotIn("alignment_basis", body)
            self.assertNotIn("alignEvent", body)


if __name__ == "__main__":
    unittest.main()
