import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "time_context.py"


def load_module():
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location("time_context", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class TimeContextTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.generated_at = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)
        self.start = dt.datetime(2026, 8, 5, 11, 45, tzinfo=dt.timezone.utc)
        self.end = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)

    def build(self, **kwargs):
        return self.module.build_time_context(
            start=kwargs.pop("start", self.start),
            end=kwargs.pop("end", self.end),
            generated_at=self.generated_at,
            **kwargs,
        )

    def snapshot(self, provider, items):
        return {
            "provider": provider,
            "status": "events_reported" if items else "normal",
            "generated_at": "2026-08-05T12:00:00+00:00",
            "items": items,
        }

    def test_no_selection_defaults_to_current_context(self):
        payload = self.build(mode="current", incidents=[])

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["model_version"], "prime_observer.time_context.v2")
        self.assertEqual(payload["mode"], "current")
        self.assertFalse(payload["overlaps_incident"])

    def test_selected_interval_records_incident_overlap(self):
        payload = self.build(
            mode="selected_interval",
            incidents=[{"id": "event-1", "start": "2026-08-05T11:50:00+00:00", "end": "2026-08-05T11:58:00+00:00"}],
        )

        self.assertTrue(payload["overlaps_incident"])
        self.assertEqual(payload["incident_id"], "event-1")

    def test_external_context_overlap_is_recorded(self):
        payload = self.build(
            external_contexts=[self.snapshot("cloudflare_radar", [{"signal": "outage", "provider_event_id": "cf-1", "started": "2026-08-05T11:55:00+00:00", "ended": "2026-08-05T12:10:00+00:00"}])],
        )

        self.assertTrue(payload["overlaps_external_context"])

    def test_external_context_non_overlap_is_ignored(self):
        payload = self.build(
            external_contexts=[self.snapshot("cloudflare_radar", [{"signal": "outage", "provider_event_id": "cf-1", "started": "2026-08-05T10:00:00+00:00", "ended": "2026-08-05T10:10:00+00:00"}])],
        )

        self.assertFalse(payload["overlaps_external_context"])

    def test_overlapping_external_event_sources_names_sources(self):
        payload = self.build(
            external_contexts=[
                self.snapshot("cloudflare_radar", [{"signal": "outage", "provider_event_id": "cf-1", "started": "2026-08-05T11:55:00+00:00", "ended": "2026-08-05T12:10:00+00:00"}]),
                self.snapshot("aps", []),
            ],
        )

        self.assertIn("overlapping_external_event_sources", payload)
        self.assertEqual(payload["overlapping_external_event_sources"], ["Cloudflare Radar"])

    def test_overlapping_external_event_sources_empty_when_no_overlap(self):
        payload = self.build(
            external_contexts=[
                self.snapshot("cloudflare_radar", [{"signal": "outage", "provider_event_id": "cf-1", "started": "2026-08-05T14:00:00+00:00", "ended": "2026-08-05T14:10:00+00:00"}]),
                self.snapshot("aps", []),
            ],
        )

        self.assertEqual(payload["overlapping_external_event_sources"], [])
        self.assertFalse(payload["overlaps_external_context"])

    def test_overlapping_external_event_sources_multiple_providers(self):
        payload = self.build(
            external_contexts=[
                self.snapshot("cloudflare_radar", [{"signal": "outage", "provider_event_id": "cf-1", "started": "2026-08-05T11:50:00+00:00", "ended": "2026-08-05T11:58:00+00:00"}]),
                self.snapshot("aps", [{"event_type": "outage", "provider_event_id": "aps-1", "event_start": "2026-08-05T11:52:00+00:00", "estimated_restoration_time": "2026-08-05T12:30:00+00:00"}]),
            ],
        )

        sources = payload["overlapping_external_event_sources"]
        self.assertIn("Cloudflare Radar", sources)
        self.assertIn("APS", sources)

    def test_unknown_provider_not_included_in_sources(self):
        payload = self.build(
            external_contexts=[self.snapshot("unknown_provider", [{"started": "2026-08-05T11:50:00+00:00", "ended": "2026-08-05T11:58:00+00:00"}])],
        )

        self.assertEqual(payload["overlapping_external_event_sources"], [])

    def test_invalid_interval_falls_back_safely(self):
        payload = self.build(start="bad", end="also-bad")

        self.assertEqual(payload["start"], self.generated_at.isoformat())
        self.assertEqual(payload["end"], self.generated_at.isoformat())


if __name__ == "__main__":
    unittest.main()
