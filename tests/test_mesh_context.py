from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from mesh_context import refresh_mesh_context  # noqa: E402


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 24, 1, 0, 0, tzinfo=UTC)
ROUTER_ID = "node_111111111111111111111111"
SATELLITE_ID = "node_222222222222222222222222"
CLIENT_A = "client_aaaaaaaaaaaaaaaaaaaaaaaa"
CLIENT_B = "client_bbbbbbbbbbbbbbbbbbbbbbbb"


def family(status="complete", successful=1, attempted=1, count=0, observed=None):
    return {
        "status": status,
        "successful_sources": successful,
        "attempted_sources": attempted,
        "errors": [] if successful else ["TIMEOUT"],
        "normalized_item_count": count,
        "source_observed_counts": observed or {},
        "omitted_item_count": 0,
    }


def source_payload(
    *, completed_at=None, identity="identity_0123456789abcdef01234567",
    schema_version="0.2", exposure_mode="minimal",
):
    completed = completed_at or (NOW - dt.timedelta(minutes=1))
    started = completed - dt.timedelta(seconds=1)
    payload = {
        "schema_version": schema_version,
        "identity_epoch": identity,
        "collected_at": started.isoformat().replace("+00:00", "Z"),
        "collection": {
            "status": "complete",
            "duration_ms": 1000,
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
            "families": {
                "router": family(count=1),
                "satellites": family(count=1, observed={"all_records": 1}),
                "clients": family(count=2, observed={"ajax_records": 2}),
                "capabilities": family(count=0),
            },
        },
        "source": {
            "type": "netgear_orbi",
            "router_model": "RBR-test",
            "firmware_version": "V-test",
            "collector_version": "0.2.0",
        },
        "router": {
            "node_id": ROUTER_ID,
            "collection_reachability": "confirmed",
            "internet_state": "up",
            "wan_link_state": "up",
            "uptime_seconds": 1234,
            "cpu_raw": 100,
            "memory_utilization_percent": 50,
            "timezone": "local",
            "model": "RBR-test",
            "firmware_version": "V-test",
        },
        "satellites": [{
            "node_id": SATELLITE_ID,
            "friendly_name": "Satellite A",
            "state": "online",
            "model": "RBS-test",
            "firmware_version": "V-test",
            "backhaul_type": "5_ghz",
            "status_raw": 2,
            "quality_raw": 52,
        }],
        "clients": [
            {
                "client_id": CLIENT_A,
                "friendly_name": "Device A",
                "state": "connected",
                "status_raw": 1,
                "medium": "wireless",
                "band": "5_ghz",
                "network_role": "main",
                "associated_node_id": SATELLITE_ID,
                "association_resolution": "resolved",
                "signal_quality_raw": 48,
                "link_rate_mbps_apparent": 433,
                "access_state": "allowed",
            },
            {
                "client_id": CLIENT_B,
                "friendly_name": "Device B",
                "state": "connected",
                "status_raw": 1,
                "medium": "wired",
                "band": None,
                "network_role": None,
                "associated_node_id": ROUTER_ID,
                "association_resolution": "resolved",
                "signal_quality_raw": None,
                "link_rate_mbps_apparent": None,
                "access_state": "allowed",
            },
        ],
        "capabilities": {},
        "warnings": [{
            "code": "CLIENT_SIGNAL_SEMANTICS_UNVERIFIED",
            "severity": "info",
            "message": "Wireless client signal is a relative vendor metric.",
            "scope": {"family": "clients", "entity_type": "client", "affected_count": 1},
        }],
    }

    if schema_version == "0.3":
        payload["privacy"] = {"exposure_mode": exposure_mode}
    return payload


class MeshContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "mesh_signal.json"
        self.output = self.root / "mesh_context.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_source(self, payload):
        self.source.write_text(json.dumps(payload), encoding="utf-8")

    def refresh(self, payload=None, *, now=NOW):
        if payload is not None:
            self.write_source(payload)
        return refresh_mesh_context(self.output, source_path=self.source, generated_at=now)

    def test_complete_fresh_projection_is_minimized_and_aggregated(self):
        result = self.refresh(source_payload())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["freshness"], result["latest_attempt"]["freshness"])
        self.assertEqual(result["latest_attempt"]["freshness"]["state"], "fresh")
        self.assertTrue(result["latest_attempt"]["families"]["clients"]["valid"])
        self.assertEqual(result["latest_attempt"]["client_summary"]["total"], 2)
        self.assertEqual(result["latest_attempt"]["client_summary"]["by_medium"], {"wired": 1, "wireless": 1})
        self.assertEqual([item["role"] for item in result["latest_attempt"]["nodes"]], ["router", "satellite"])
        self.assertEqual(result["lan_evidence"]["state"], "current")
        self.assertEqual(result["lan_evidence"]["temporal_scope"], "current_snapshot_only")
        self.assertEqual(result["lan_evidence"]["clients"]["total"], 2)
        self.assertNotIn(CLIENT_A, json.dumps(result["lan_evidence"]))
        self.assertNotIn(ROUTER_ID, json.dumps(result["lan_evidence"]))
        self.assertNotIn(SATELLITE_ID, json.dumps(result["lan_evidence"]))
        self.assertNotIn("Device A", json.dumps(result["lan_evidence"]))
        self.assertNotIn("Satellite A", json.dumps(result["lan_evidence"]))
        serialized = json.dumps(result)
        self.assertNotIn("cpu_raw", serialized)
        self.assertNotIn("access_state", serialized)
        self.assertNotIn("status_raw", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertEqual(result["warnings"], result["latest_attempt"]["warnings"])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_schema_v03_local_maps_probe_host_without_persisting_identity_values(self):
        payload = source_payload(schema_version="0.3", exposure_mode="local")
        payload["source"]["collector_version"] = "0.3.0"
        payload["router"]["friendly_name"] = "Main Router"
        payload["clients"][0]["local_ip_addresses"] = ["192.168.1.50"]
        payload["clients"][0]["associated_node_name"] = "Office Satellite"
        self.write_source(payload)
        result = refresh_mesh_context(
            self.output,
            source_path=self.source,
            generated_at=NOW,
            probe_local_addresses={"192.168.1.50"},
        )
        probe = result["lan_evidence"]["probe_host"]
        self.assertEqual(result["latest_attempt"]["source_schema_version"], "0.3")
        self.assertEqual(result["privacy"]["source_exposure_mode"], "local")
        self.assertEqual(probe["mapping_state"], "matched")
        self.assertEqual(probe["match_method"], "local_address_intersection")
        self.assertEqual(probe["attachment"]["node_name_local"], "Office Satellite")
        self.assertEqual(probe["attachment"]["band"], "5_ghz")
        self.assertEqual(probe["attachment"]["signal_quality_raw_relative"], 48)
        self.assertEqual(probe["attachment"]["link_rate_mbps_apparent"], 433)
        serialized_probe = json.dumps(probe)
        self.assertNotIn("192.168.1.50", serialized_probe)
        self.assertNotIn(CLIENT_A, serialized_probe)
        self.assertNotIn("Device A", serialized_probe)

    def test_schema_v03_minimal_withholds_probe_identity_and_rejects_local_fields(self):
        minimal = source_payload(schema_version="0.3", exposure_mode="minimal")
        result = self.refresh(minimal)
        self.assertEqual(
            result["lan_evidence"]["probe_host"]["mapping_state"], "identity_withheld"
        )
        minimal["clients"][0]["local_ip_addresses"] = ["192.168.1.50"]
        rejected = self.refresh(minimal)
        self.assertEqual(rejected["latest_attempt"]["state"], "privacy_rejected")

    def test_schema_v03_full_accepts_but_does_not_project_raw_macs(self):
        payload = source_payload(schema_version="0.3", exposure_mode="full")
        payload["router"]["mac_address"] = "02:00:00:00:00:01"
        payload["satellites"][0]["mac_address"] = "02:00:00:00:00:02"
        payload["clients"][0]["mac_address"] = "02:00:00:00:00:03"
        payload["clients"][0]["local_ip_addresses"] = ["192.168.1.50"]
        payload["clients"][0]["associated_node_name"] = "Office Satellite"
        self.write_source(payload)
        result = refresh_mesh_context(
            self.output, source_path=self.source, generated_at=NOW,
            probe_local_addresses={"192.168.1.50"},
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["lan_evidence"]["probe_host"]["mapping_state"], "matched")
        self.assertNotIn("mac_address", json.dumps(result))
        self.assertNotIn("02:00:00:00:00:03", json.dumps(result))

    def test_probe_mapping_reports_partial_ambiguous_and_stale_without_scoring(self):
        partial = source_payload(schema_version="0.3", exposure_mode="local")
        partial["collection"]["status"] = "partial"
        partial["collection"]["families"]["clients"] = family(
            status="partial", successful=1, attempted=2, count=2
        )
        for client in partial["clients"]:
            client["local_ip_addresses"] = ["192.168.1.50"]
        self.write_source(partial)
        ambiguous = refresh_mesh_context(
            self.output, source_path=self.source, generated_at=NOW,
            probe_local_addresses={"192.168.1.50"},
        )
        probe = ambiguous["lan_evidence"]["probe_host"]
        self.assertEqual(ambiguous["status"], "partial")
        self.assertEqual(probe["mapping_state"], "ambiguous")
        self.assertEqual(probe["collection_status"], "partial")

        stale_payload = source_payload(
            completed_at=NOW - dt.timedelta(minutes=13),
            schema_version="0.3", exposure_mode="local",
        )
        stale_payload["clients"][0]["local_ip_addresses"] = ["192.168.1.50"]
        self.write_source(stale_payload)
        stale = refresh_mesh_context(
            self.output, source_path=self.source, generated_at=NOW,
            probe_local_addresses={"192.168.1.50"},
        )
        stale_probe = stale["lan_evidence"]["probe_host"]
        self.assertEqual(stale_probe["mapping_state"], "matched")
        self.assertEqual(stale_probe["freshness"]["state"], "stale")
        self.assertNotIn("health", json.dumps(stale_probe).lower())

    def test_probe_host_last_good_is_explicit_when_latest_client_family_fails(self):
        payload = source_payload(schema_version="0.3", exposure_mode="local")
        payload["clients"][0]["local_ip_addresses"] = ["192.168.1.50"]
        payload["clients"][0]["associated_node_name"] = "Office Satellite"
        self.write_source(payload)
        refresh_mesh_context(
            self.output, source_path=self.source, generated_at=NOW,
            probe_local_addresses={"192.168.1.50"},
        )
        failed = source_payload(
            completed_at=NOW - dt.timedelta(seconds=20),
            schema_version="0.3", exposure_mode="local",
        )
        failed["collection"]["status"] = "partial"
        failed["collection"]["families"]["clients"] = family(
            status="failed", successful=0, attempted=2, count=0
        )
        failed["clients"] = []
        self.write_source(failed)
        result = refresh_mesh_context(
            self.output, source_path=self.source, generated_at=NOW,
            probe_local_addresses={"192.168.1.50"},
        )
        probe = result["lan_evidence"]["probe_host"]
        self.assertEqual(probe["mapping_state"], "matched")
        self.assertEqual(probe["lineage"], "last_good")
        self.assertEqual(probe["attachment"]["node_name_local"], "Office Satellite")

    def test_partial_collection_preserves_family_level_last_good(self):
        first = source_payload()
        self.refresh(first)
        second = source_payload(completed_at=NOW - dt.timedelta(seconds=20))
        second["collection"]["status"] = "partial"
        second["collection"]["families"]["clients"] = family(
            status="failed", successful=0, attempted=2, count=0
        )
        second["clients"] = []
        result = self.refresh(second)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["latest_attempt"]["client_summary"]["total"])
        self.assertEqual(result["latest_attempt"]["clients"], [])
        self.assertEqual(result["last_good"]["families"]["clients"]["data"]["summary"]["total"], 2)
        self.assertFalse(result["last_good"]["families"]["clients"]["is_current"])
        self.assertEqual(result["last_good"]["families"]["router"]["observed_at"], second["collection"]["completed_at"])
        self.assertEqual(result["lan_evidence"]["state"], "partial")
        self.assertEqual(result["lan_evidence"]["clients"]["lineage"], "last_good")

    def test_failed_collection_does_not_claim_zero_clients_or_offline_nodes(self):
        payload = source_payload()
        payload["collection"]["status"] = "failed"
        for name, attempted in (("router", 7), ("satellites", 3), ("clients", 2), ("capabilities", 2)):
            payload["collection"]["families"][name] = family(
                status="failed", successful=0, attempted=attempted, count=0
            )
        payload["router"]["collection_reachability"] = "unknown"
        payload["satellites"] = []
        payload["clients"] = []
        result = self.refresh(payload)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["latest_attempt"]["client_summary"]["total"])
        self.assertEqual(result["latest_attempt"]["nodes"], [])
        self.assertNotIn("offline", result["summary"].lower())
        self.assertEqual(result["lan_evidence"]["state"], "latest_failed")
        self.assertIsNone(result["lan_evidence"]["clients"]["total"])

    def test_stale_and_expired_are_distinct(self):
        stale = self.refresh(source_payload(completed_at=NOW - dt.timedelta(minutes=13)))
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["lan_evidence"]["state"], "stale")
        self.assertTrue(stale["latest_attempt"]["freshness"]["usable_for_current_semantics"])
        expired_payload = source_payload(completed_at=NOW - dt.timedelta(minutes=31))
        expired = self.refresh(expired_payload)
        self.assertEqual(expired["status"], "unavailable")
        self.assertEqual(expired["latest_attempt"]["freshness"]["state"], "expired")
        self.assertFalse(expired["latest_attempt"]["freshness"]["usable_for_current_semantics"])
        self.assertEqual(expired["lan_evidence"]["state"], "expired")

    def test_identity_epoch_change_discards_incompatible_last_good(self):
        self.refresh(source_payload())
        changed = source_payload(identity="identity_ffffffffffffffffffffffff")
        changed["collection"]["status"] = "partial"
        changed["collection"]["families"]["clients"] = family(
            status="failed", successful=0, attempted=2, count=0
        )
        changed["clients"] = []
        result = self.refresh(changed)
        self.assertEqual(result["identity_continuity"], "reset")
        self.assertIsNone(result["last_good"]["families"]["clients"])
        self.assertIsNotNone(result["last_good"]["families"]["router"])

    def test_missing_malformed_unsupported_and_privacy_rejections_are_bounded(self):
        missing = refresh_mesh_context(self.output, source_path=self.source, generated_at=NOW)
        self.assertEqual(missing["latest_attempt"]["state"], "missing")
        self.source.write_text("not-json", encoding="utf-8")
        malformed = refresh_mesh_context(self.output, source_path=self.source, generated_at=NOW)
        self.assertEqual(malformed["latest_attempt"]["state"], "malformed")
        unsupported_payload = source_payload()
        unsupported_payload["schema_version"] = "9.9"
        unsupported = self.refresh(unsupported_payload)
        self.assertEqual(unsupported["latest_attempt"]["state"], "unsupported_schema")
        private_payload = source_payload()
        private_payload["clients"][0]["ip"] = "redacted"
        rejected = self.refresh(private_payload)
        self.assertEqual(rejected["latest_attempt"]["state"], "privacy_rejected")
        serialized = json.dumps(rejected)
        self.assertNotIn('"ip"', serialized)
        self.assertNotIn(CLIENT_A, serialized)

    def test_missing_configuration_is_normal_unavailable_state(self):
        result = refresh_mesh_context(
            self.output,
            generated_at=NOW,
            environ={},
            config_path=self.root / ".env.mesh",
            project_root=self.root,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["latest_attempt"]["state"], "not_configured")
        self.assertEqual(result["lan_evidence"]["state"], "unavailable")

    def test_local_config_resolves_relative_to_project_root_and_environment_wins(self):
        configured_source = self.root / "mesh-signal" / "mesh_signal.json"
        configured_source.parent.mkdir()
        configured_source.write_text(json.dumps(source_payload()), encoding="utf-8")
        config = self.root / ".env.mesh"
        config.write_text(
            "MESH_SIGNAL_ARTIFACT_PATH=mesh-signal/mesh_signal.json\n",
            encoding="utf-8",
        )
        configured = refresh_mesh_context(
            self.output,
            generated_at=NOW,
            environ={},
            config_path=config,
            project_root=self.root,
        )
        self.assertEqual(configured["status"], "ok")

        missing = refresh_mesh_context(
            self.output,
            generated_at=NOW,
            environ={"MESH_SIGNAL_ARTIFACT_PATH": "missing.json"},
            config_path=config,
            project_root=self.root,
        )
        self.assertEqual(missing["latest_attempt"]["state"], "missing")

    def test_inconsistent_v02_counts_are_schema_rejected(self):
        payload = source_payload()
        payload["collection"]["families"]["clients"]["normalized_item_count"] = 0
        result = self.refresh(payload)
        self.assertEqual(result["latest_attempt"]["state"], "schema_rejected")

    def test_schema_v01_is_supported_with_unverifiable_identity_continuity(self):
        payload = source_payload()
        payload["schema_version"] = "0.1"
        payload.pop("identity_epoch")
        payload["collection"].pop("completed_at")
        for item in payload["collection"]["families"].values():
            for field in ("normalized_item_count", "source_observed_counts", "omitted_item_count"):
                item.pop(field)
        payload["router"]["online"] = True
        payload["router"].pop("collection_reachability")
        for client in payload["clients"]:
            client.pop("association_resolution")
        for warning in payload["warnings"]:
            warning.pop("scope")
        result = self.refresh(payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["identity_continuity"], "legacy_unverifiable")
        self.assertTrue(result["limitations"])

    def test_aggregation_and_serialization_are_deterministic(self):
        payload = source_payload()
        first = self.refresh(payload)
        first_text = self.output.read_text(encoding="utf-8")
        second = self.refresh(deepcopy(payload))
        second_text = self.output.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(first_text, second_text)

    def test_client_details_are_bounded_without_truncating_aggregates(self):
        payload = source_payload()
        payload["clients"] = []
        for index in range(300):
            item = deepcopy(source_payload()["clients"][0])
            item["client_id"] = f"client_{index:024x}"
            item["friendly_name"] = f"Device {index}"
            payload["clients"].append(item)
        payload["collection"]["families"]["clients"]["normalized_item_count"] = 300
        payload["collection"]["families"]["clients"]["source_observed_counts"] = {
            "ajax_records": 300
        }
        result = self.refresh(payload)
        self.assertEqual(result["latest_attempt"]["client_summary"]["total"], 300)
        self.assertEqual(len(result["latest_attempt"]["clients"]), 256)
        self.assertIn("Client detail was bounded to the projection limit.", result["limitations"])

    def test_transform_generates_unavailable_projection_without_telemetry_or_network(self):
        spec = importlib.util.spec_from_file_location(
            "transform_latest_mesh_test", ROOT / "bin" / "transform_latest.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data_dir = self.root / "data"
        viz_dir = self.root / "viz"
        data_dir.mkdir()
        viz_dir.mkdir()
        module.DATA_DIR = data_dir
        module.VIZ_DIR = viz_dir
        def isolated_refresh(path, *, generated_at):
            return refresh_mesh_context(
                path,
                generated_at=generated_at,
                environ={},
                config_path=self.root / ".env.mesh",
                project_root=self.root,
            )

        with mock.patch.object(module, "refresh_mesh_context", side_effect=isolated_refresh):
            module.main()
        projection = json.loads((viz_dir / "mesh_context.json").read_text())
        self.assertEqual(projection["status"], "unavailable")
        self.assertFalse(projection["provenance"]["network_requests_performed"])


if __name__ == "__main__":
    unittest.main()
