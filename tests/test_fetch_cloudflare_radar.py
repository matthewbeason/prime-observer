import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "fetch_cloudflare_radar.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fetch_cloudflare_radar", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchCloudflareRadarTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz_dir = self.base / "viz"
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz_dir
        self.module.OUT = self.viz_dir / "internet_conditions.json"
        self.module.ENV_FILE = self.base / ".env.cloudflare"

    def tearDown(self):
        self.tmp.cleanup()

    def config(self):
        return {
            "CLOUDFLARE_API_TOKEN": "test-token",
            "CLOUDFLARE_RADAR_DATE_RANGE": "7d",
            "CLOUDFLARE_RADAR_TIMEOUT_SECONDS": 1,
            "CLOUDFLARE_RADAR_LIMIT": 10,
            "PRIME_OBSERVER_INTERNET_ASN": "",
            "PRIME_OBSERVER_INTERNET_PROVIDER_LABEL": "",
        }

    def test_build_payload_normalizes_current_disruptions(self):
        now = self.module.parse_ts("2026-06-29T18:00:00Z")

        def fake_outages_fetch(api_token, date_range, timeout, limit):
            self.assertEqual(api_token, "test-token")
            self.assertEqual(date_range, "7d")
            self.assertEqual(timeout, 1)
            self.assertEqual(limit, 10)
            return {
                "success": True,
                "result": {
                    "annotations": [
                        {
                            "startDate": "2026-06-29T17:30:00Z",
                            "scope": "Arizona",
                            "description": "Regional packet loss event",
                            "linkedUrl": "https://radar.cloudflare.com/outage/az",
                        },
                        {
                            "startDate": "2026-06-29T10:00:00Z",
                            "endDate": "2026-06-29T11:00:00Z",
                            "locationsDetails": [{"code": "US", "name": "United States"}],
                            "outage": {"outageType": "REGIONAL", "outageCause": "POWER_ISSUE"},
                            "linkedUrl": "",
                        },
                        {
                            "startDate": "2026-06-27T10:00:00Z",
                            "endDate": "2026-06-27T11:00:00Z",
                            "scope": "Old event",
                            "description": "Should be ignored",
                            "linkedUrl": "https://example.com/old",
                        },
                    ]
                },
            }

        def fake_traffic_fetch(api_token, date_range, timeout, limit):
            self.assertEqual(api_token, "test-token")
            self.assertEqual(date_range, "7d")
            self.assertEqual(timeout, 1)
            self.assertEqual(limit, 10)
            return {
                "success": True,
                "result": {
                    "trafficAnomalies": [
                        {
                            "startDate": "2026-06-29T16:30:00Z",
                            "endDate": "2026-06-29T17:00:00Z",
                            "locationDetails": {"code": "US", "name": "United States"},
                            "status": "UNVERIFIED",
                            "type": "LOCATION",
                            "uuid": "traffic-1",
                        }
                    ]
                },
            }

        payload = self.module.build_payload(
            self.config(),
            now=now,
            outages_fetcher=fake_outages_fetch,
            traffic_fetcher=fake_traffic_fetch,
        )

        self.assertEqual(payload["provider"], "cloudflare_radar")
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["status"], "disruption")
        self.assertEqual(payload["scope"]["country"], "US")
        self.assertIsNone(payload["scope"]["region"])
        self.assertEqual(payload["scope"]["label"], "United States context")
        self.assertEqual(payload["signals_checked"], ["Outages", "Traffic anomalies"])
        self.assertEqual(payload["query_mode"], "country")
        self.assertEqual(payload["query_target_label"], "United States")
        self.assertEqual(payload["query_target_id"], "US")
        self.assertEqual(payload["provider_display_name"], "US Radar")
        self.assertFalse(payload["fallback_used"])
        self.assertEqual(payload["summary"], "United States Internet outage reported in Arizona and 2 more location(s).")
        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(payload["items"][0]["region"], "Arizona")
        self.assertEqual(payload["items"][0]["signal"], "outage")
        self.assertEqual(payload["items"][0]["description"], "Regional packet loss event")
        self.assertEqual(payload["items"][1]["region"], "United States")
        self.assertEqual(payload["items"][1]["description"], "regional power issue")
        self.assertEqual(payload["items"][2]["signal"], "traffic_anomaly")
        self.assertEqual(payload["items"][2]["description"], "Elevated traffic anomaly detected in United States")

    def test_build_payload_returns_normal_when_no_recent_annotations(self):
        now = self.module.parse_ts("2026-06-29T18:00:00Z")

        payload = self.module.build_payload(
            self.config(),
            now=now,
            outages_fetcher=lambda *_: {
                "success": True,
                "result": {
                    "annotations": [
                        {
                            "startDate": "2026-06-20T10:00:00Z",
                            "endDate": "2026-06-20T11:00:00Z",
                            "scope": "Old event",
                        }
                    ]
                },
            },
            traffic_fetcher=lambda *_: {
                "success": True,
                "result": {
                    "trafficAnomalies": [
                        {
                            "startDate": "2026-06-20T10:00:00Z",
                            "endDate": "2026-06-20T11:00:00Z",
                            "locationDetails": {"code": "US", "name": "United States"},
                            "status": "UNVERIFIED",
                        }
                    ]
                },
            },
        )

        self.assertEqual(payload["status"], "normal")
        self.assertEqual(payload["summary"], "No United States Internet outages or traffic anomalies detected.")
        self.assertEqual(payload["scope"]["label"], "United States context")
        self.assertEqual(payload["signals_checked"], ["Outages", "Traffic anomalies"])
        self.assertEqual(payload["query_mode"], "country")
        self.assertEqual(payload["provider_display_name"], "US Radar")
        self.assertEqual(payload["items"], [])

    def test_build_payload_limits_items_to_first_three_meaningful_entries(self):
        now = self.module.parse_ts("2026-06-29T18:00:00Z")

        payload = self.module.build_payload(
            self.config(),
            now=now,
            outages_fetcher=lambda *_: {
                "success": True,
                "result": {
                    "annotations": [
                        {
                            "startDate": "2026-06-29T17:55:00Z",
                            "scope": "Arizona",
                            "description": "Arizona outage",
                        },
                        {
                            "startDate": "2026-06-29T17:50:00Z",
                            "scope": "United States",
                            "description": "National outage",
                        },
                    ]
                },
            },
            traffic_fetcher=lambda *_: {
                "success": True,
                "result": {
                    "trafficAnomalies": [
                        {
                            "startDate": "2026-06-29T17:45:00Z",
                            "locationDetails": {"code": "US", "name": "United States"},
                            "status": "VERIFIED",
                        },
                        {
                            "startDate": "2026-06-29T17:40:00Z",
                            "locationDetails": {"code": "US", "name": "United States"},
                            "status": "UNVERIFIED",
                        },
                    ]
                },
            },
        )

        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(
            [item["description"] for item in payload["items"]],
            [
                "Arizona outage",
                "National outage",
                "Verified traffic anomaly detected in United States",
            ],
        )

    def test_build_payload_uses_configured_asn_mode_for_traffic_anomalies(self):
        now = self.module.parse_ts("2026-06-29T18:00:00Z")
        config = self.config()
        config["PRIME_OBSERVER_INTERNET_ASN"] = "22773"
        config["PRIME_OBSERVER_INTERNET_PROVIDER_LABEL"] = "Cox"

        payload = self.module.build_payload(
            config,
            now=now,
            outages_fetcher=lambda *_: self.fail("outage fetch should not run for ASN mode"),
            traffic_fetcher=lambda *_: self.fail("country anomaly fetch should not run for ASN mode"),
            asn_traffic_fetcher=lambda api_token, date_range, timeout, limit, asn: {
                "success": True,
                "result": {
                    "trafficAnomalies": [
                        {
                            "startDate": "2026-06-29T17:45:00Z",
                            "endDate": None,
                            "asnDetails": {"asn": "22773", "name": "Cox Communications", "locations": {"code": "US", "name": "United States"}},
                            "status": "VERIFIED",
                            "type": "AS",
                            "uuid": "traffic-asn-1",
                        }
                    ]
                },
            },
        )

        self.assertEqual(payload["status"], "disruption")
        self.assertEqual(payload["query_mode"], "asn")
        self.assertEqual(payload["query_target_label"], "Cox")
        self.assertEqual(payload["query_target_id"], "AS22773")
        self.assertEqual(payload["provider_display_name"], "Cox")
        self.assertFalse(payload["fallback_used"])
        self.assertEqual(payload["signals_checked"], ["Traffic anomalies"])
        self.assertEqual(payload["scope"]["label"], "Cox network context")
        self.assertEqual(payload["summary"], "Cloudflare Radar traffic anomaly detected for Cox.")
        self.assertEqual(payload["items"][0]["region"], "Cox Communications")
        self.assertEqual(payload["items"][0]["description"], "Verified traffic anomaly detected for Cox Communications")

    def test_build_payload_falls_back_to_country_when_asn_query_fails(self):
        now = self.module.parse_ts("2026-06-29T18:00:00Z")
        config = self.config()
        config["PRIME_OBSERVER_INTERNET_ASN"] = "22773"
        config["PRIME_OBSERVER_INTERNET_PROVIDER_LABEL"] = "Cox"

        payload = self.module.build_payload(
            config,
            now=now,
            outages_fetcher=lambda *_: {
                "success": True,
                "result": {"annotations": []},
            },
            traffic_fetcher=lambda *_: {
                "success": True,
                "result": {"trafficAnomalies": []},
            },
            asn_traffic_fetcher=lambda *_: (_ for _ in ()).throw(urllib.error.URLError("asn down")),
        )

        self.assertEqual(payload["status"], "normal")
        self.assertEqual(payload["query_mode"], "asn")
        self.assertEqual(payload["query_target_label"], "Cox")
        self.assertEqual(payload["query_target_id"], "AS22773")
        self.assertEqual(payload["provider_display_name"], "US Radar")
        self.assertTrue(payload["fallback_used"])
        self.assertEqual(payload["scope"]["label"], "United States context")
        self.assertEqual(payload["signals_checked"], ["Outages", "Traffic anomalies"])
        self.assertEqual(payload["summary"], "No United States Internet outages or traffic anomalies detected.")

    def test_missing_token_writes_unavailable_summary(self):
        config = self.config()
        config["CLOUDFLARE_API_TOKEN"] = ""

        with mock.patch.object(self.module, "load_config", return_value=config):
            rc = self.module.main()

        payload = json.loads(self.module.OUT.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["summary"], "Unable to retrieve current Internet conditions.")
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["scope"]["label"], "United States context")
        self.assertEqual(payload["signals_checked"], ["Outages", "Traffic anomalies"])
        self.assertEqual(payload["query_mode"], "country")
        self.assertEqual(payload["provider_display_name"], "US Radar")
        self.assertEqual(payload["items"], [])

    def test_load_config_uses_process_environment_token(self):
        with mock.patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_TOKEN": "env-token",
                "CLOUDFLARE_RADAR_DATE_RANGE": "30d",
                "CLOUDFLARE_RADAR_TIMEOUT_SECONDS": "12",
                "CLOUDFLARE_RADAR_LIMIT": "7",
                "PRIME_OBSERVER_INTERNET_ASN": "AS22773",
                "PRIME_OBSERVER_INTERNET_PROVIDER_LABEL": "Cox",
            },
            clear=True,
        ):
            config = self.module.load_config()

        self.assertEqual(config["CLOUDFLARE_API_TOKEN"], "env-token")
        self.assertEqual(config["CLOUDFLARE_RADAR_DATE_RANGE"], "30d")
        self.assertEqual(config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"], 12.0)
        self.assertEqual(config["CLOUDFLARE_RADAR_LIMIT"], 7)
        self.assertEqual(config["PRIME_OBSERVER_INTERNET_ASN"], "22773")
        self.assertEqual(config["PRIME_OBSERVER_INTERNET_PROVIDER_LABEL"], "Cox")

    def test_load_config_uses_env_cloudflare_token_when_process_env_absent(self):
        self.module.ENV_FILE.write_text(
            "\n".join(
                [
                    "# Local Cloudflare Radar token",
                    "CLOUDFLARE_API_TOKEN=dotenv-token",
                    "CLOUDFLARE_RADAR_DATE_RANGE=14d",
                    "CLOUDFLARE_RADAR_TIMEOUT_SECONDS=9",
                    "CLOUDFLARE_RADAR_LIMIT=6",
                    "PRIME_OBSERVER_INTERNET_ASN=22773",
                    "PRIME_OBSERVER_INTERNET_PROVIDER_LABEL=Cox",
                    "",
                ]
            )
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            config = self.module.load_config()

        self.assertEqual(config["CLOUDFLARE_API_TOKEN"], "dotenv-token")
        self.assertEqual(config["CLOUDFLARE_RADAR_DATE_RANGE"], "14d")
        self.assertEqual(config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"], 9.0)
        self.assertEqual(config["CLOUDFLARE_RADAR_LIMIT"], 6)
        self.assertEqual(config["PRIME_OBSERVER_INTERNET_ASN"], "22773")
        self.assertEqual(config["PRIME_OBSERVER_INTERNET_PROVIDER_LABEL"], "Cox")

    def test_process_environment_overrides_env_cloudflare(self):
        self.module.ENV_FILE.write_text(
            "\n".join(
                [
                "CLOUDFLARE_API_TOKEN=dotenv-token",
                "CLOUDFLARE_RADAR_DATE_RANGE=14d",
                "CLOUDFLARE_RADAR_TIMEOUT_SECONDS=9",
                "CLOUDFLARE_RADAR_LIMIT=6",
                "PRIME_OBSERVER_INTERNET_ASN=22773",
                "PRIME_OBSERVER_INTERNET_PROVIDER_LABEL=Dotenv Cox",
                "",
            ]
        )
        )

        with mock.patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_TOKEN": "env-token",
                "CLOUDFLARE_RADAR_DATE_RANGE": "30d",
                "CLOUDFLARE_RADAR_TIMEOUT_SECONDS": "12",
                "CLOUDFLARE_RADAR_LIMIT": "7",
                "PRIME_OBSERVER_INTERNET_ASN": "AS7018",
                "PRIME_OBSERVER_INTERNET_PROVIDER_LABEL": "AT&T",
            },
            clear=True,
        ):
            config = self.module.load_config()

        self.assertEqual(config["CLOUDFLARE_API_TOKEN"], "env-token")
        self.assertEqual(config["CLOUDFLARE_RADAR_DATE_RANGE"], "30d")
        self.assertEqual(config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"], 12.0)
        self.assertEqual(config["CLOUDFLARE_RADAR_LIMIT"], 7)
        self.assertEqual(config["PRIME_OBSERVER_INTERNET_ASN"], "7018")
        self.assertEqual(config["PRIME_OBSERVER_INTERNET_PROVIDER_LABEL"], "AT&T")

    def test_api_failure_writes_unavailable_summary(self):
        with mock.patch.object(self.module, "load_config", return_value=self.config()):
            with mock.patch.object(self.module, "build_payload", side_effect=urllib.error.URLError("down")):
                rc = self.module.main()

        payload = json.loads(self.module.OUT.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["summary"], "Unable to retrieve current Internet conditions.")
        self.assertEqual(payload["scope"]["label"], "United States context")
        self.assertEqual(payload["query_mode"], "country")

    def test_json_generation_is_atomic_and_parseable(self):
        payload = self.module.unavailable_payload()
        self.module.write_json_atomic(payload)

        written = json.loads(self.module.OUT.read_text())
        self.assertEqual(written["provider"], "cloudflare_radar")
        self.assertEqual(written["status"], "unavailable")
        self.assertEqual(written["schema_version"], 2)

    def test_dotenv_example_exists_and_uses_placeholder_only(self):
        env_example = ROOT / ".env.example"
        self.assertTrue(env_example.exists())

        body = env_example.read_text()
        self.assertIn("CLOUDFLARE_API_TOKEN=replace-with-token", body)
        self.assertIn("PRIME_OBSERVER_INTERNET_ASN=22773", body)
        self.assertIn("PRIME_OBSERVER_INTERNET_PROVIDER_LABEL=Cox", body)
        self.assertNotRegex(body, r"CLOUDFLARE_API_TOKEN=(?!replace-with-token)[^\s#]+")

    def test_gitignore_excludes_env_cloudflare(self):
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertIn("\n.env.cloudflare\n", f"\n{gitignore}\n")

    def test_committed_files_do_not_contain_real_cloudflare_token_values(self):
        tracked_files = subprocess.check_output(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
        ).splitlines()

        for relative_path in tracked_files:
            path = ROOT / relative_path
            if not path.is_file():
                continue
            if path.suffix in {".png", ".pyc"}:
                continue

            for line in path.read_text(errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped.startswith("CLOUDFLARE_API_TOKEN="):
                    continue
                self.assertRegex(stripped, r"^CLOUDFLARE_API_TOKEN=(replace-with-token)?$")


if __name__ == "__main__":
    unittest.main()
