import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "fetch_nextdns_summary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fetch_nextdns_summary", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchNextDnsSummaryTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz_dir = self.base / "viz"
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz_dir
        self.module.OUT = self.viz_dir / "nextdns_summary.json"
        self.module.ENV_FILE = self.base / ".env.nextdns"

    def tearDown(self):
        self.tmp.cleanup()

    def config(self):
        return {
            "NEXTDNS_PROFILE_ID": "abcdef123456",
            "NEXTDNS_API_KEY": "test-key",
            "NEXTDNS_WINDOW": "-24h",
            "NEXTDNS_TIMEOUT_SECONDS": 1,
            "NEXTDNS_EXPORT_DOMAIN_NAMES": True,
            "NEXTDNS_TOP_ENTITIES_LIMIT": 3,
        }

    def fake_fetch(self, profile_id, api_key, endpoint, window, timeout, extra_params=None):
        self.assertEqual(profile_id, "abcdef123456")
        self.assertEqual(api_key, "test-key")
        self.assertEqual(window, "-24h")
        self.assertEqual(timeout, 1)

        if endpoint == "status":
            return {
                "data": [
                    {"status": "default", "queries": 70},
                    {"status": "blocked", "queries": 25},
                    {"status": "allowed", "queries": 5},
                ]
            }
        if endpoint == "reasons":
            return {"data": [{"name": "Blocklist", "queries": 25}]}
        if endpoint == "encryption":
            return {
                "data": [
                    {"encrypted": True, "queries": 40},
                    {"encrypted": False, "queries": 60},
                ]
            }
        if endpoint == "domains":
            rows = [
                {"domain": "example.com", "queries": 30},
                {"domain": "blocked.test", "queries": 10},
                {"domain": "cdn.example", "queries": 5},
            ]
            if extra_params and extra_params.get("status") == "blocked":
                rows = [{"domain": "blocked.test", "queries": 10}]
            if extra_params and extra_params.get("status") == "default":
                rows = [{"domain": "example.com", "queries": 30}]
            return {"data": rows}
        return {"data": []}

    def test_successful_analytics_fetch_builds_public_safe_summary(self):
        with mock.patch.object(self.module, "fetch_json", side_effect=self.fake_fetch):
            payload = self.module.build_summary(self.config())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source"], "nextdns")
        self.assertEqual(payload["profile_id_suffix"], "3456")
        self.assertNotIn("NEXTDNS_API_KEY", json.dumps(payload))
        self.assertNotIn("test-key", json.dumps(payload))

        summary = payload["summary"]
        self.assertEqual(summary["queries"], 100)
        self.assertEqual(summary["blocked"], 25)
        self.assertEqual(summary["blocked_percent"], 25.0)
        self.assertEqual(summary["encrypted_percent"], 40.0)
        self.assertEqual(summary["total_queries"], 100)
        self.assertEqual(summary["blocked_queries"], 25)
        self.assertEqual(summary["block_rate_pct"], 25.0)
        self.assertEqual(summary["encrypted_rate_pct"], 40.0)
        self.assertEqual(summary["top_queries"][0]["domain"], "example.com")
        self.assertEqual(summary["top_blocked"][0]["domain"], "blocked.test")

    def test_domain_export_can_redact_names_without_dropping_counts(self):
        config = self.config()
        config["NEXTDNS_EXPORT_DOMAIN_NAMES"] = False
        with mock.patch.object(self.module, "fetch_json", side_effect=self.fake_fetch):
            payload = self.module.build_summary(config)

        top_query = payload["summary"]["top_queries"][0]
        self.assertNotIn("domain", top_query)
        self.assertTrue(top_query["name_redacted"])
        self.assertEqual(top_query["count"], 30)

    def test_missing_credentials_writes_unavailable_summary(self):
        config = self.config()
        config["NEXTDNS_PROFILE_ID"] = ""
        with mock.patch.object(self.module, "load_config", return_value=config):
            rc = self.module.main()

        payload = json.loads(self.module.OUT.read_text())
        self.assertEqual(rc, 2)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["error"]["kind"], "configuration")
        self.assertIsNone(payload["summary"])

    def test_api_failure_writes_unavailable_summary(self):
        with mock.patch.object(self.module, "load_config", return_value=self.config()):
            with mock.patch.object(self.module, "build_summary", side_effect=urllib.error.URLError("down")):
                rc = self.module.main()

        payload = json.loads(self.module.OUT.read_text())
        self.assertEqual(rc, 1)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["error"]["kind"], "network_error")
        self.assertIsNone(payload["summary"])

    def test_json_generation_is_atomic_and_parseable(self):
        payload = self.module.failure_payload(self.config(), "configuration", "missing")
        self.module.write_json_atomic(payload)

        written = json.loads(self.module.OUT.read_text())
        self.assertEqual(written["status"], "unavailable")
        self.assertEqual(written["source"], "nextdns")


if __name__ == "__main__":
    unittest.main()
