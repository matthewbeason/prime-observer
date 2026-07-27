import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "record_operator_impact.py"


def load_module():
    spec = importlib.util.spec_from_file_location("record_operator_impact", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordOperatorImpactTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz = self.base / "viz"
        self.viz.mkdir()
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz
        self.module.INVESTIGATION = self.viz / "investigation.json"
        self.module.OUT = self.viz / "operator_impact_feedback.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_investigation(self, incident_id="event-test"):
        self.module.INVESTIGATION.write_text(json.dumps({"selected_event": {"id": incident_id}}))

    def test_list_values(self):
        with mock.patch("builtins.print") as printer:
            result = self.module.main(["--list-values"])

        self.assertEqual(result, 0)
        values = [call.args[0] for call in printer.call_args_list]
        self.assertIn("none_observed", values)
        self.assertIn("full_outage", values)

    def test_no_active_investigation_degrades_safely(self):
        result = self.module.main(["--impact", "none_observed"])

        self.assertEqual(result, 2)
        self.assertFalse(self.module.OUT.exists())

    def test_records_none_observed_for_current_incident(self):
        self.write_investigation("event-current")

        result = self.module.main(["--impact", "none_observed", "--note", " Everything normal.  "])

        self.assertEqual(result, 0)
        payload = json.loads(self.module.OUT.read_text())
        self.assertEqual(payload["incident_id"], "event-current")
        self.assertEqual(payload["impact_state"], "none_observed")
        self.assertEqual(payload["note"], "Everything normal.")
        self.assertEqual(payload["source"], "operator")

    def test_records_all_supported_impacts_with_explicit_incident(self):
        for state in sorted(self.module.ALLOWED_IMPACTS):
            with self.subTest(state=state):
                result = self.module.main(["--incident-id", "event-explicit", "--impact", state])
                self.assertEqual(result, 0)
                payload = json.loads(self.module.OUT.read_text())
                self.assertEqual(payload["impact_state"], state)

    def test_clearing_feedback_writes_atomic_clear_payload(self):
        self.write_investigation("event-current")

        result = self.module.main(["--clear"])

        self.assertEqual(result, 0)
        payload = json.loads(self.module.OUT.read_text())
        self.assertTrue(payload["cleared"])
        self.assertEqual(payload["impact_state"], "unknown")
        self.assertFalse(self.module.OUT.with_suffix(".json.tmp").exists())

    def test_note_is_bounded(self):
        self.write_investigation("event-current")
        long_note = "x" * 600

        self.module.main(["--impact", "minor_slowness", "--note", long_note])

        payload = json.loads(self.module.OUT.read_text())
        self.assertEqual(len(payload["note"]), self.module.MAX_NOTE_CHARS)


if __name__ == "__main__":
    unittest.main()
