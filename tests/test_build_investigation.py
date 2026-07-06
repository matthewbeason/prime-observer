import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "build_investigation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_investigation", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildInvestigationTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data_dir = self.base / "data"
        self.viz_dir = self.base / "viz"
        self.data_dir.mkdir()
        self.viz_dir.mkdir()
        self.module.BASE = self.base
        self.module.DATA_DIR = self.data_dir
        self.module.VIZ_DIR = self.viz_dir
        self.module.OUT = self.viz_dir / "investigation.json"
        self.module.INDEX_OUT = self.viz_dir / "investigation_index.json"
        self.module.NEXTDNS_SUMMARY = self.viz_dir / "nextdns_summary.json"
        self.module.INTERNET_CONDITIONS = self.viz_dir / "internet_conditions.json"
        self.module.APS_POWER_CONTEXT = self.viz_dir / "aps_power_context.json"
        self.module.OBSERVATIONS = self.viz_dir / "observations.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_rows(self, rows):
        path = self.data_dir / "bakeoff_20260608.csv"
        fields = [
            "ts",
            "host",
            "phase_label",
            "sent",
            "received",
            "loss_pct",
            "avg_ms",
            "p50_ms",
            "p95_ms",
            "max_ms",
            "jitter_ms",
        ]
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def telemetry_row(self, ts, host, p95, jitter=5, loss=0):
        return {
            "ts": ts,
            "host": host,
            "phase_label": "wan",
            "sent": "10",
            "received": "10",
            "loss_pct": str(loss),
            "avg_ms": "20",
            "p50_ms": "20",
            "p95_ms": str(p95),
            "max_ms": str(p95 + 10),
            "jitter_ms": str(jitter),
        }

    def write_observations(self, observations, generated_at="2026-06-08T12:30:00+00:00"):
        payload = {
            "schema_version": 1,
            "generated_at": generated_at,
            "model_version": "prime_observer.observation.v1",
            "observations": observations,
        }
        self.module.OBSERVATIONS.write_text(json.dumps(payload))

    def write_internet_conditions(self, **overrides):
        payload = {
            "schema_version": 2,
            "generated_at": "2026-06-08T12:08:00Z",
            "provider": "cloudflare_radar",
            "status": "normal",
            "summary": "No United States Internet outages or traffic anomalies detected.",
            "scope": {
                "country": "US",
                "region": None,
                "label": "United States context",
            },
            "signals_checked": ["Outages", "Traffic anomalies"],
            "items": [],
        }
        payload.update(overrides)
        self.module.INTERNET_CONDITIONS.write_text(json.dumps(payload))

    def write_aps_power_context(self, **overrides):
        payload = {
            "schema_version": 1,
            "generated_at": "2026-06-08T12:08:00Z",
            "provider": "aps",
            "status": "normal",
            "summary": "No APS outages or PSPS events reported.",
            "scope": {
                "state": "AZ",
                "service_area": "APS service territory",
                "label": "APS service territory",
            },
            "signals_checked": ["Current outages", "PSPS events", "Update properties"],
            "items": [],
        }
        payload.update(overrides)
        self.module.APS_POWER_CONTEXT.write_text(json.dumps(payload))

    def test_investigation_metadata_is_additive(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
            self.telemetry_row("2026-06-08T12:05:00+00:00", "9.9.9.9", 180),
            self.telemetry_row("2026-06-08T12:06:00+00:00", "45.90.28.134", 25),
            self.telemetry_row("2026-06-08T12:10:00+00:00", "1.1.1.1", 190),
            self.telemetry_row("2026-06-08T12:15:00+00:00", "192.168.1.1", 8),
        ])

        payload = self.module.build_investigation(
            "2026-06-08T12:05:00+00:00",
            "2026-06-08T12:10:00+00:00",
            15,
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("periods", payload)
        self.assertIn("timeline_samples", payload)
        self.assertIn("target_groups", payload)
        self.assertIn("dns_context", payload)
        self.assertFalse(payload["dns_context"]["available"])
        self.assertNotIn("internet_conditions_context", payload)
        self.assertNotIn("power_infrastructure_context", payload)
        self.assertTrue(payload["id"].startswith("investigation-"))
        self.assertEqual(payload["status"], "available")
        self.assertGreaterEqual(len(payload["events"]), 2)
        self.assertIn("requested_window", payload)
        self.assertIn("context_window", payload)
        self.assertIn("observation_references", payload)
        self.assertIn("provenance", payload)
        self.assertEqual(payload["navigation"]["first_event"], payload["events"][0]["id"])
        self.assertEqual(payload["navigation"]["last_event"], payload["events"][-1]["id"])
        for event in payload["events"]:
            self.assertIn(event["id"], payload["navigation"]["events"])
        neighborhoods = {item["event_id"]: item for item in payload["event_neighborhoods"]}
        self.assertEqual(set(neighborhoods), {event["id"] for event in payload["events"]})
        self.assertTrue(any(
            "temporal_proximity" in nearby["signals"]
            for item in neighborhoods.values()
            for nearby in item["nearby_events"]
        ))
        by_host = {sample["host"]: sample for sample in payload["timeline_samples"]}
        self.assertEqual(by_host["1.1.1.1"]["target_class"], "internet_probe")
        self.assertEqual(by_host["1.1.1.1"]["target_label"], "Cloudflare")
        self.assertEqual(by_host["45.90.28.134"]["target_class"], "resolver_probe")
        self.assertEqual(by_host["45.90.28.134"]["target_label"], "NextDNS primary")
        self.assertEqual(by_host["192.168.1.1"]["target_class"], "gateway_probe")
        during_groups = payload["periods"]["during"]["wan"]["target_groups"]
        self.assertIn("internet_probe", during_groups)
        self.assertIn("resolver_probe", during_groups)
        self.assertTrue(any(
            bucket.get("target_class") == "internet_probe"
            for bucket in payload["periods"]["during"]["wan_buckets"]
        ))
        self.assertEqual(payload["sources"]["observations"], "viz/observations.json")
        self.assertFalse(payload["provenance"]["observation_projection"]["available"])

    def test_index_upserts_catalog_entry(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        out = self.viz_dir / "investigation.json"
        index = self.viz_dir / "investigation_index.json"
        first = self.module.update_index(index, payload, out)
        second = self.module.update_index(index, payload, out)
        written = json.loads(index.read_text())

        self.assertEqual(len(first["investigations"]), 1)
        self.assertEqual(len(second["investigations"]), 1)
        self.assertEqual(len(written["investigations"]), 1)
        entry = written["investigations"][0]
        self.assertEqual(entry["id"], payload["id"])
        self.assertEqual(entry["title"], payload["title"])
        self.assertEqual(entry["created_at"], payload["generated_at"])
        self.assertEqual(entry["event_count"], len(payload["events"]))
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["path"], "viz/investigation.json")

    def test_dns_context_is_copied_from_generated_summary(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        summary = {
            "schema_version": 1,
            "source": "nextdns",
            "status": "ok",
            "profile_id_suffix": "3456",
            "window": "-24h",
            "generated_at": "2026-06-08T12:00:00Z",
            "summary": {
                "total_queries": 100,
                "blocked_queries": 5,
                "block_rate_pct": 5.0,
                "encrypted_rate_pct": 60.0,
                "top_blocked_reason": "Blocklist",
                "top_blocked_reason_queries": 5,
                "top_entities": [
                    {
                        "entity_type": "domain",
                        "label": "entity_1",
                        "name_redacted": True,
                        "count": 20,
                        "share_of_total": 0.2,
                    }
                ],
            },
            "error": None,
        }
        self.module.NEXTDNS_SUMMARY.write_text(json.dumps(summary))

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        dns = payload["dns_context"]
        self.assertTrue(dns["available"])
        self.assertEqual(dns["status"], "ok")
        self.assertEqual(dns["profile_id_suffix"], "3456")
        self.assertEqual(dns["summary"]["total_queries"], 100)
        self.assertEqual(dns["summary"]["blocked_queries"], 5)
        self.assertEqual(dns["summary"]["block_rate_pct"], 5.0)
        self.assertEqual(dns["summary"]["encrypted_rate_pct"], 60.0)
        self.assertIn("not a historical DNS log", dns["note"])

    def test_internet_conditions_context_is_copied_from_generated_artifact(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        self.write_internet_conditions(
            generated_at="2026-06-08T12:04:00Z",
            status="disruption",
            summary="United States Internet outage reported in Arizona and 1 more location(s).",
            items=[
                {
                    "signal": "outage",
                    "region": "Arizona",
                    "started": "2026-06-08T11:50:00Z",
                    "description": "Regional packet loss event",
                    "reference": "https://radar.cloudflare.com/outage/az",
                    "ignored_field": "ignored",
                },
                {
                    "signal": "traffic_anomaly",
                    "region": "United States",
                    "started": "2026-06-08T11:40:00Z",
                    "description": "Elevated traffic anomaly detected in United States",
                    "reference": "",
                },
            ],
        )

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        context = payload["internet_conditions_context"]
        self.assertTrue(context["available"])
        self.assertEqual(context["provider"], "cloudflare_radar")
        self.assertEqual(context["status"], "disruption")
        self.assertEqual(context["summary"], "United States Internet outage reported in Arizona and 1 more location(s).")
        self.assertEqual(context["scope"]["label"], "United States context")
        self.assertEqual(context["signals_checked"], ["Outages", "Traffic anomalies"])
        self.assertEqual(context["items"][0]["region"], "Arizona")
        self.assertNotIn("ignored_field", context["items"][0])
        self.assertEqual(context["minutes_from_event_midpoint"], 4.0)
        self.assertIn("not historical proof or attribution", context["note"])

    def test_internet_conditions_context_preserves_unavailable_status_without_affecting_schema(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        self.write_internet_conditions(
            status="unavailable",
            summary="Unable to retrieve current Internet conditions.",
            items=[],
        )

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        context = payload["internet_conditions_context"]
        self.assertFalse(context["available"])
        self.assertEqual(context["status"], "unavailable")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "available")

    def test_internet_conditions_context_is_omitted_when_artifact_missing(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        self.assertNotIn("internet_conditions_context", payload)

    def test_internet_conditions_context_marks_malformed_artifact_unavailable(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        self.module.INTERNET_CONDITIONS.write_text("{not-json")

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        context = payload["internet_conditions_context"]
        self.assertFalse(context["available"])
        self.assertEqual(context["reason"], "Internet Conditions artifact was unreadable")
        self.assertEqual(context["source_file"], "viz/internet_conditions.json")

    def test_power_infrastructure_context_is_copied_from_generated_artifact(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        self.write_aps_power_context(
            generated_at="2026-06-08T12:04:00Z",
            status="events_reported",
            summary="2 APS power event(s) affecting 20 customers.",
            items=[
                {
                    "event_type": "unplanned_outage",
                    "affected_area": "Phoenix • Metro Phoenix: West Highland Ave to West Coolidge St",
                    "customer_count": 13,
                    "estimated_restoration_time": "2026-06-08T13:35:00Z",
                    "source_reference": "https://outagemap.aps.com/outageviewer/",
                    "ignored_field": "ignored",
                },
                {
                    "event_type": "psps_event",
                    "affected_area": "Northern Arizona",
                    "customer_count": 7,
                    "estimated_restoration_time": None,
                    "source_reference": "https://outagemap.aps.com/outageviewer/",
                },
            ],
        )

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        context = payload["power_infrastructure_context"]
        self.assertTrue(context["available"])
        self.assertEqual(context["provider"], "aps")
        self.assertEqual(context["status"], "events_reported")
        self.assertEqual(context["summary"], "2 APS power event(s) affecting 20 customers.")
        self.assertEqual(context["scope"]["label"], "APS service territory")
        self.assertEqual(context["signals_checked"], ["Current outages", "PSPS events", "Update properties"])
        self.assertEqual(context["items"][0]["event_type"], "unplanned_outage")
        self.assertEqual(context["items"][0]["customer_count"], 13)
        self.assertNotIn("ignored_field", context["items"][0])
        self.assertEqual(context["minutes_from_event_midpoint"], 4.0)
        self.assertIn("not historical proof or attribution", context["note"])

    def test_power_infrastructure_context_preserves_unavailable_status_without_affecting_schema(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        self.write_aps_power_context(
            status="unavailable",
            summary="Unable to retrieve current APS power context.",
            items=[],
        )

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        context = payload["power_infrastructure_context"]
        self.assertFalse(context["available"])
        self.assertEqual(context["status"], "unavailable")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "available")

    def test_power_infrastructure_context_is_omitted_when_artifact_missing(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        self.assertNotIn("power_infrastructure_context", payload)

    def test_power_infrastructure_context_marks_malformed_artifact_unavailable(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:00:00+00:00", "1.1.1.1", 20),
        ])
        self.module.APS_POWER_CONTEXT.write_text("{not-json")

        payload = self.module.build_investigation(
            "2026-06-08T12:00:00+00:00",
            "2026-06-08T12:00:00+00:00",
            0,
        )

        context = payload["power_infrastructure_context"]
        self.assertFalse(context["available"])
        self.assertEqual(context["reason"], "Power Infrastructure artifact was unreadable")
        self.assertEqual(context["source_file"], "viz/aps_power_context.json")

    def test_select_overlapping_observations_is_deterministic(self):
        observations = [
            {
                "id": "observation-episode-b",
                "type": "episode",
                "scope": {"system": "prime_observer", "subject": "network", "view": "episode", "target_class": "resolver_probe"},
                "interval": {"start": "2026-06-08T12:06:00+00:00", "end": "2026-06-08T12:08:00+00:00"},
            },
            {
                "id": "observation-attribution-a",
                "type": "attribution",
                "scope": {"system": "prime_observer", "subject": "network", "view": "window_attribution"},
                "interval": {"start": "2026-06-08T12:05:00+00:00", "end": "2026-06-08T12:10:00+00:00"},
            },
            {
                "id": "observation-episode-c",
                "type": "episode",
                "scope": {"system": "prime_observer", "subject": "network", "view": "episode", "target_class": "internet_probe"},
                "interval": {"start": "2026-06-08T12:20:00+00:00", "end": "2026-06-08T12:25:00+00:00"},
            },
        ]

        first = self.module.select_overlapping_observations(
            observations,
            self.module.utc_ts("2026-06-08T12:05:00+00:00"),
            self.module.utc_ts("2026-06-08T12:10:00+00:00"),
        )
        second = self.module.select_overlapping_observations(
            list(reversed(observations)),
            self.module.utc_ts("2026-06-08T12:05:00+00:00"),
            self.module.utc_ts("2026-06-08T12:10:00+00:00"),
        )

        self.assertEqual(first, second)
        self.assertEqual([item["id"] for item in first], ["observation-attribution-a", "observation-episode-b"])

    def test_investigation_adds_overlapping_observation_references(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:05:00+00:00", "1.1.1.1", 180),
            self.telemetry_row("2026-06-08T12:06:00+00:00", "1.1.1.1", 181),
            self.telemetry_row("2026-06-08T12:07:00+00:00", "45.90.28.134", 25),
        ])
        self.write_observations([
            {
                "id": "observation-attribution-window",
                "type": "attribution",
                "scope": {"system": "prime_observer", "subject": "network", "view": "window_attribution"},
                "interval": {"start": "2026-06-08T12:05:00+00:00", "end": "2026-06-08T12:10:00+00:00"},
                "state": {"status": "likely_upstream", "label": "Likely upstream (ISP / path)"},
                "generated_at": "2026-06-08T12:30:00+00:00",
                "model_version": "prime_observer.attribution.v1",
                "provenance": {
                    "materialization": {
                        "policy_version": "prime_observer.observation_materialization.v1",
                        "conclusion_kind": "window_attribution",
                        "lifecycle": "rolling",
                    }
                },
            },
            {
                "id": "observation-episode-existing",
                "type": "episode",
                "scope": {"system": "prime_observer", "subject": "network", "view": "episode", "target_class": "internet_probe"},
                "interval": {"start": "2026-06-08T12:00:00+00:00", "end": "2026-06-08T12:15:00+00:00"},
                "state": {"status": "sustained_degradation", "label": "Sustained degradation"},
                "generated_at": "2026-06-08T12:30:00+00:00",
                "model_version": "prime_observer.episode.v1",
                "provenance": {
                    "materialization": {
                        "policy_version": "prime_observer.observation_materialization.v1",
                        "conclusion_kind": "sustained_episode",
                        "lifecycle": "finalized",
                    }
                },
            },
            {
                "id": "observation-episode-outside",
                "type": "episode",
                "scope": {"system": "prime_observer", "subject": "network", "view": "episode", "target_class": "internet_probe"},
                "interval": {"start": "2026-06-08T13:00:00+00:00", "end": "2026-06-08T13:15:00+00:00"},
            },
        ])

        payload = self.module.build_investigation(
            "2026-06-08T12:05:00+00:00",
            "2026-06-08T12:10:00+00:00",
            15,
        )

        self.assertEqual(
            [item["id"] for item in payload["observation_references"]],
            ["observation-episode-existing", "observation-attribution-window"],
        )
        self.assertTrue(payload["provenance"]["observation_projection"]["available"])
        self.assertEqual(payload["provenance"]["observation_projection"]["selected_count"], 2)
        bucket_events = [
            event for event in payload["events"]
            if event["type"] == "wan_bucket_observation" and event["details"].get("target_class") == "internet_probe"
        ]
        self.assertTrue(bucket_events)
        self.assertEqual(
            [item["id"] for item in bucket_events[0]["details"]["observation_references"]],
            ["observation-episode-existing"],
        )

    def test_investigation_preserves_observation_ids_without_regenerating(self):
        self.write_rows([
            self.telemetry_row("2026-06-08T12:05:00+00:00", "45.90.28.134", 180),
            self.telemetry_row("2026-06-08T12:06:00+00:00", "45.90.28.134", 181),
        ])
        self.write_observations([
            {
                "id": "observation-episode-preserved",
                "type": "episode",
                "scope": {"system": "prime_observer", "subject": "network", "view": "episode", "target_class": "resolver_probe"},
                "interval": {"start": "2026-06-08T12:00:00+00:00", "end": "2026-06-08T12:15:00+00:00"},
            }
        ])

        payload = self.module.build_investigation(
            "2026-06-08T12:05:00+00:00",
            "2026-06-08T12:10:00+00:00",
            0,
        )

        self.assertEqual(
            [item["id"] for item in payload["observation_references"]],
            ["observation-episode-preserved"],
        )
        referenced = set()
        for event in payload["events"]:
            for item in event["details"].get("observation_references", []):
                referenced.add(item["id"])
        self.assertEqual(referenced, {"observation-episode-preserved"})


if __name__ == "__main__":
    unittest.main()
