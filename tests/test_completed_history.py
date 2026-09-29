import datetime as dt
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "completed_history.py"


def load_module():
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location("completed_history", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class CompletedHistoryTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def snapshot(
        self,
        event_id,
        *,
        target_class="resolver_probe",
        start="2026-09-03T01:00:00Z",
        last="2026-09-03T01:10:00Z",
        recovered="2026-09-03T01:20:00Z",
        affected=None,
        snapshot_written_at="2026-09-03T01:21:00Z",
        generated_at="2026-09-03T01:20:30Z",
    ):
        payload = {
            "artifact_type": "completed_investigation_snapshot",
            "generated_at": generated_at,
            "selected_event": {
                "id": event_id,
                "lifecycle_state": "complete",
                "target_class": target_class,
                "first_anomalous_at": start,
                "last_anomalous_at": last,
                "recovered_at": recovered,
                "affected_targets": affected or ["one"],
            },
            "incident_record": {"incident_id": event_id, "started_at": start},
        }
        if snapshot_written_at is not None:
            payload["snapshot_written_at"] = snapshot_written_at
        return payload

    def project(self, snapshots):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, payload in snapshots:
                (root / name).write_text(json.dumps(payload))
            return self.module.build_completed_history_projection(root)

    def test_reconstructed_starts_and_affected_members_share_completed_identity(self):
        result = self.project([
            ("full.json", self.snapshot("event-full", start="2026-09-03T01:00:00Z", affected=["one", "two"])),
            ("truncated.json", self.snapshot("event-truncated", start="2026-09-03T01:05:00Z", affected=["two"])),
        ])
        catalog = result["catalog"]

        self.assertEqual(catalog["canonical_event_count"], 1)
        self.assertEqual(catalog["duplicate_alias_count"], 1)
        self.assertEqual(catalog["canonical_events"][0]["reconstruction"], {
            "count": 2,
            "earliest_start": "2026-09-03T01:00:00Z",
            "latest_start": "2026-09-03T01:05:00Z",
        })

    def test_target_class_and_exact_recovery_are_identity_components(self):
        result = self.project([
            ("resolver.json", self.snapshot("resolver", target_class="resolver_probe")),
            ("internet.json", self.snapshot("internet", target_class="internet_probe")),
            ("later.json", self.snapshot("later", recovered="2026-09-03T01:20:01Z")),
        ])

        self.assertEqual(result["catalog"]["canonical_event_count"], 3)

    def test_utc_equivalence_and_subseconds_are_preserved(self):
        first = self.module.canonical_completed_event_id("resolver_probe", "2026-09-03T01:20:00.123456Z")
        equivalent = self.module.canonical_completed_event_id("resolver_probe", "2026-09-02T18:20:00.123456-07:00")
        distinct = self.module.canonical_completed_event_id("resolver_probe", "2026-09-03T01:20:00.123457Z")

        self.assertEqual(first, equivalent)
        self.assertNotEqual(first, distinct)
        self.assertIn("123456", first)

    def test_disjoint_intervals_with_same_key_fail_closed(self):
        result = self.project([
            ("early.json", self.snapshot("early", start="2026-09-03T00:00:00Z", last="2026-09-03T00:10:00Z")),
            ("late.json", self.snapshot("late", start="2026-09-03T01:00:00Z", last="2026-09-03T01:10:00Z")),
        ])
        catalog = result["catalog"]

        self.assertEqual(catalog["canonical_events"], [])
        self.assertEqual(catalog["legacy_aliases"], [])
        self.assertEqual(catalog["identity_conflicts"][0]["error_type"], "identity_conflict")
        self.assertEqual(len(catalog["identity_conflicts"][0]["records"]), 2)

    def test_invalid_or_missing_completion_identity_is_preserved_separately(self):
        missing = self.snapshot("missing")
        missing["selected_event"].pop("recovered_at")
        invalid = self.snapshot("invalid")
        invalid["selected_event"]["recovered_at"] = "not-a-time"
        result = self.project([("missing.json", missing), ("invalid.json", invalid)])
        catalog = result["catalog"]

        self.assertEqual(catalog["canonical_event_count"], 0)
        self.assertEqual(catalog["counts"]["identity_incomplete"], 2)
        self.assertEqual({item["event_id"] for item in catalog["identity_incomplete_snapshots"]}, {"missing", "invalid"})

    def test_representative_uses_written_then_generated_then_path(self):
        without_written = self.snapshot(
            "generated-first",
            snapshot_written_at=None,
            generated_at="2026-09-03T01:20:30Z",
        )
        with_written = self.snapshot(
            "written-later",
            snapshot_written_at="2026-09-03T01:21:00Z",
            generated_at="2026-09-03T01:19:00Z",
        )
        result = self.project([("z.json", without_written), ("a.json", with_written)])
        event = result["catalog"]["canonical_events"][0]
        self.assertEqual(event["legacy_event_id"], "generated-first")

        left = self.snapshot("path-a", snapshot_written_at=None, generated_at=None)
        right = self.snapshot("path-b", snapshot_written_at=None, generated_at=None)
        fallback = self.project([("b.json", right), ("a.json", left)])
        self.assertEqual(fallback["catalog"]["canonical_events"][0]["legacy_event_id"], "path-a")


if __name__ == "__main__":
    unittest.main()
