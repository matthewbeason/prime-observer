import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "fetch_aps_power_context.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fetch_aps_power_context", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchApsPowerContextTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz_dir = self.base / "viz"
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz_dir
        self.module.OUT = self.viz_dir / "aps_power_context.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_payload_reports_current_outages(self):
        config = {
            "updateLayer": "https://example.com/MapServer/3",
            "pspsLayerTitle": "APSOutageMap - PSPS Events",
        }
        webmap = {
            "Data": {
                "operationalLayers": [
                    {"title": "Outages", "url": "https://example.com/MapServer/0"},
                    {"title": "APSOutageMap - PSPS Events", "url": "https://example.com/MapServer/8"},
                ]
            }
        }

        def fake_fetch_json(url, timeout=8, query=None):
            if url == self.module.CONFIG_URL:
                return config
            if url == self.module.WEBMAP_URL:
                return webmap
            if url == "https://example.com/MapServer/0/query":
                return {
                    "features": [
                        {
                            "attributes": {
                                "APSArea": "Metro Phoenix",
                                "City": "Phoenix",
                                "Boundary": "West Highland Ave to West Coolidge St",
                                "customers": 13,
                                "etr": 1783361700000,
                                "outagetype": "Unplanned Outage",
                                "MediaLink": None,
                            }
                        },
                        {
                            "attributes": {
                                "APSArea": "Southeastern Arizona",
                                "City": "Coolidge",
                                "Boundary": "Harding Ave to Coolidge Ave",
                                "customers": 7,
                                "etr": 1783365000000,
                                "outagetype": "Unplanned Outage",
                                "MediaLink": "https://example.com/coolidge",
                            }
                        },
                    ]
                }
            if url == "https://example.com/MapServer/8/query":
                return {"features": []}
            if url == "https://example.com/MapServer/3/query":
                return {
                    "features": [
                        {
                            "attributes": {
                                "TIMESTAMP": 1783379106000,
                            }
                        }
                    ]
                }
            raise AssertionError(url)

        payload = self.module.build_payload(config_fetcher=fake_fetch_json)

        self.assertEqual(payload["provider"], "aps")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "events_reported")
        self.assertEqual(payload["scope"]["label"], "APS service territory")
        self.assertEqual(payload["signals_checked"], ["Current outages", "PSPS events", "Update properties"])
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["event_type"], "unplanned_outage")
        self.assertEqual(payload["items"][0]["customer_count"], 13)
        self.assertEqual(payload["items"][0]["source_reference"], "https://outagemap.aps.com/outageviewer/")
        self.assertIn("2 APS power event(s) affecting 20 customers.", payload["summary"])

    def test_build_payload_returns_normal_when_no_events(self):
        config = {
            "updateLayer": "https://example.com/MapServer/3",
            "pspsLayerTitle": "APSOutageMap - PSPS Events",
        }
        webmap = {
            "Data": {
                "operationalLayers": [
                    {"title": "Outages", "url": "https://example.com/MapServer/0"},
                    {"title": "APSOutageMap - PSPS Events", "url": "https://example.com/MapServer/8"},
                ]
            }
        }

        def fake_fetch_json(url, timeout=8, query=None):
            if url == self.module.CONFIG_URL:
                return config
            if url == self.module.WEBMAP_URL:
                return webmap
            if url.endswith("/query"):
                return {"features": []}
            raise AssertionError(url)

        payload = self.module.build_payload(config_fetcher=fake_fetch_json)

        self.assertEqual(payload["status"], "normal")
        self.assertEqual(payload["summary"], "No APS outages or PSPS events reported.")
        self.assertEqual(payload["items"], [])

    def test_main_writes_unavailable_artifact_on_failure(self):
        with mock.patch.object(self.module, "build_payload", side_effect=urllib.error.URLError("down")):
            rc = self.module.main()

        payload = json.loads(self.module.OUT.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["summary"], "Unable to retrieve current APS power context.")
        self.assertEqual(payload["provider"], "aps")

    def test_build_payload_rejects_malformed_provider_configuration(self):
        def fake_fetch_json(url, timeout=8, query=None):
            if url == self.module.CONFIG_URL:
                return {"updateLayer": ""}
            if url == self.module.WEBMAP_URL:
                return {"Data": {"operationalLayers": []}}
            raise AssertionError(url)

        with self.assertRaises(ValueError):
            self.module.build_payload(config_fetcher=fake_fetch_json)

    def test_json_generation_is_atomic_and_parseable(self):
        payload = self.module.unavailable_payload()
        self.module.write_json_atomic(payload)

        written = json.loads(self.module.OUT.read_text())
        self.assertEqual(written["provider"], "aps")
        self.assertEqual(written["status"], "unavailable")
        self.assertEqual(written["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
