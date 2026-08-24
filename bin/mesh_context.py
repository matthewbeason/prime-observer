#!/usr/bin/env python3
"""Build Prime Observer's current, read-only Mesh Signal evidence projection."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import re
import shlex
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MODEL_VERSION = "prime_observer.mesh_context.v1"
SOURCE_ENV = "MESH_SIGNAL_ARTIFACT_PATH"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / ".env.mesh"
INTENDED_POLL_SECONDS = 5 * 60
FRESH_THROUGH_SECONDS = 12 * 60
CURRENT_USE_THROUGH_SECONDS = 30 * 60
MAX_CLIENT_DETAILS = 256
FAMILY_NAMES = ("router", "satellites", "clients", "capabilities")
PRESERVED_FAMILIES = ("router", "satellites", "clients")
SOURCE_STATUSES = {"complete", "partial", "failed"}
IDENTITY_EXPOSURE_MODES = {"minimal", "local", "full"}
ID_PATTERN = re.compile(r"^(?:node|client)_[0-9a-f]{24}$")
IDENTITY_PATTERN = re.compile(r"^identity_[0-9a-f]{24}$")
ERROR_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
WARNING_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+$")
MAC_PATTERN = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")
IPV4_PATTERN = re.compile(
    r"(?<![0-9])(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}(?![0-9])"
)
IPV6_PATTERN = re.compile(
    r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{0,4}(?![0-9a-f:])"
    r"|(?<![0-9a-f:])[0-9a-f]{1,4}::[0-9a-f:]*[0-9a-f](?![0-9a-f:])"
)
FORBIDDEN_KEYS = {
    "authorization", "cookie", "ip", "mac", "password", "ssid", "token", "wan_ip",
}


class MeshContextError(ValueError):
    """A bounded source rejection safe to expose in a generated artifact."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_utc(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _walk(value: Any, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _walk(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _walk(nested, f"{path}[{index}]")


def _allowed_local_ip_path(path: str, exposure_mode: str | None) -> bool:
    return (
        exposure_mode in {"local", "full"}
        and re.fullmatch(r"\$\.clients\[\d+\]\.local_ip_addresses\[\d+\]", path) is not None
    )


def _allowed_mac_path(path: str, exposure_mode: str | None) -> bool:
    return (
        exposure_mode == "full"
        and re.fullmatch(
            r"\$\.(?:router\.mac_address|satellites\[\d+\]\.mac_address|clients\[\d+\]\.mac_address)",
            path,
        ) is not None
    )


def _validate_privacy(
    payload: dict[str, Any], *, exposure_mode: str | None = None
) -> None:
    for path, value in _walk(payload):
        key = path.rsplit(".", 1)[-1].lower()
        if key in FORBIDDEN_KEYS:
            raise MeshContextError("privacy_rejected")
        if not isinstance(value, str) or key == "firmware_version":
            continue
        has_mac = MAC_PATTERN.search(value) is not None
        has_ip = IPV4_PATTERN.search(value) is not None or IPV6_PATTERN.search(value) is not None
        if has_mac:
            if not _allowed_mac_path(path, exposure_mode):
                raise MeshContextError("privacy_rejected")
            continue
        if has_ip and not _allowed_local_ip_path(path, exposure_mode):
            raise MeshContextError("privacy_rejected")


def _canonical_local_address(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return None
    if address.is_loopback or not (address.is_private or address.is_link_local):
        return None
    return address.compressed


def discover_probe_local_addresses() -> set[str]:
    """Read local interface addresses without performing a network request."""
    try:
        result = subprocess.run(
            ["/sbin/ifconfig"], check=True, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    addresses = set()
    for candidate in re.findall(r"\binet6?\s+([^\s%]+)", result.stdout):
        if canonical := _canonical_local_address(candidate):
            addresses.add(canonical)
    return addresses


def _require_id(value: Any, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise MeshContextError("schema_rejected")
    return value


def _validate_family(name: str, family: Any, schema_version: str) -> None:
    if not isinstance(family, dict) or family.get("status") not in SOURCE_STATUSES:
        raise MeshContextError("schema_rejected")
    attempted = family.get("attempted_sources")
    successful = family.get("successful_sources")
    errors = family.get("errors")
    if not _is_int(attempted) or attempted < 0 or not _is_int(successful) or not 0 <= successful <= attempted:
        raise MeshContextError("schema_rejected")
    if not isinstance(errors, list) or any(
        not isinstance(item, str) or not ERROR_PATTERN.fullmatch(item) for item in errors
    ):
        raise MeshContextError("schema_rejected")
    if schema_version in {"0.2", "0.3"}:
        for field in ("normalized_item_count", "omitted_item_count"):
            if not _is_int(family.get(field)) or family[field] < 0:
                raise MeshContextError("schema_rejected")
        observed = family.get("source_observed_counts")
        if not isinstance(observed, dict) or any(
            not isinstance(key, str) or not _is_int(value) or value < 0
            for key, value in observed.items()
        ):
            raise MeshContextError("schema_rejected")


def _validate_source(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise MeshContextError("schema_rejected")
    schema_version = payload.get("schema_version")
    if schema_version not in {"0.1", "0.2", "0.3"}:
        raise MeshContextError("unsupported_schema")
    exposure_mode = None
    if schema_version == "0.3":
        privacy = payload.get("privacy")
        if not isinstance(privacy, dict) or set(privacy) != {"exposure_mode"}:
            raise MeshContextError("schema_rejected")
        exposure_mode = privacy.get("exposure_mode")
        if exposure_mode not in IDENTITY_EXPOSURE_MODES:
            raise MeshContextError("schema_rejected")
    _validate_privacy(payload, exposure_mode=exposure_mode)
    required = {"collected_at", "collection", "source", "router", "satellites", "clients", "warnings"}
    if not required.issubset(payload):
        raise MeshContextError("schema_rejected")
    started = parse_ts(payload.get("collected_at"))
    if started is None:
        raise MeshContextError("schema_rejected")
    collection = payload.get("collection")
    if not isinstance(collection, dict) or collection.get("status") not in SOURCE_STATUSES:
        raise MeshContextError("schema_rejected")
    if not _is_int(collection.get("duration_ms")) or collection["duration_ms"] < 0:
        raise MeshContextError("schema_rejected")
    completed_value = collection.get("completed_at") if schema_version in {"0.2", "0.3"} else payload.get("collected_at")
    completed = parse_ts(completed_value)
    if completed is None:
        raise MeshContextError("schema_rejected")
    if schema_version in {"0.2", "0.3"}:
        elapsed_ms = round((completed - started).total_seconds() * 1000)
        if elapsed_ms < 0 or abs(elapsed_ms - collection["duration_ms"]) > 1000:
            raise MeshContextError("schema_rejected")
    families = collection.get("families")
    if not isinstance(families, dict) or set(families) != set(FAMILY_NAMES):
        raise MeshContextError("schema_rejected")
    for name in FAMILY_NAMES:
        _validate_family(name, families[name], schema_version)
    if collection["status"] == "complete" and any(
        family["status"] != "complete" for family in families.values()
    ):
        raise MeshContextError("schema_rejected")
    source = payload.get("source")
    if not isinstance(source, dict) or source.get("type") != "netgear_orbi" or not isinstance(source.get("collector_version"), str):
        raise MeshContextError("schema_rejected")
    router = payload.get("router")
    satellites = payload.get("satellites")
    clients = payload.get("clients")
    if not isinstance(router, dict) or not isinstance(satellites, list) or not isinstance(clients, list):
        raise MeshContextError("schema_rejected")
    router_id = _require_id(router.get("node_id"), nullable=True)
    if schema_version in {"0.2", "0.3"} and router.get("collection_reachability") not in {"confirmed", "unknown"}:
        raise MeshContextError("schema_rejected")
    if schema_version == "0.3":
        if "friendly_name" in router and exposure_mode == "minimal":
            raise MeshContextError("schema_rejected")
        if "mac_address" in router and exposure_mode != "full":
            raise MeshContextError("schema_rejected")
        if "friendly_name" in router and not isinstance(router["friendly_name"], str):
            raise MeshContextError("schema_rejected")
        if "mac_address" in router and (
            not isinstance(router["mac_address"], str)
            or not MAC_PATTERN.fullmatch(router["mac_address"])
        ):
            raise MeshContextError("schema_rejected")
    satellite_ids = []
    for item in satellites:
        if not isinstance(item, dict):
            raise MeshContextError("schema_rejected")
        satellite_ids.append(_require_id(item.get("node_id")))
        if schema_version == "0.3" and "mac_address" in item and exposure_mode != "full":
            raise MeshContextError("schema_rejected")
        if schema_version == "0.3" and "mac_address" in item and (
            not isinstance(item["mac_address"], str)
            or not MAC_PATTERN.fullmatch(item["mac_address"])
        ):
            raise MeshContextError("schema_rejected")
    if satellite_ids != sorted(set(satellite_ids)):
        raise MeshContextError("schema_rejected")
    known_nodes = set(satellite_ids)
    if router_id:
        known_nodes.add(router_id)
    client_ids = []
    for item in clients:
        if not isinstance(item, dict):
            raise MeshContextError("schema_rejected")
        client_ids.append(_require_id(item.get("client_id")))
        association = item.get("associated_node_id")
        if schema_version in {"0.2", "0.3"}:
            resolution = item.get("association_resolution")
            if resolution not in {"resolved", "unresolved", "missing"}:
                raise MeshContextError("schema_rejected")
            if resolution == "resolved" and association not in known_nodes:
                raise MeshContextError("schema_rejected")
            if resolution != "resolved" and association is not None:
                raise MeshContextError("schema_rejected")
        elif association is not None:
            _require_id(association)
        if schema_version == "0.3":
            local_fields = {"local_ip_addresses", "associated_node_name"} & set(item)
            if local_fields and exposure_mode == "minimal":
                raise MeshContextError("schema_rejected")
            if "mac_address" in item and exposure_mode != "full":
                raise MeshContextError("schema_rejected")
            if "associated_node_name" in item and not isinstance(
                item["associated_node_name"], str
            ):
                raise MeshContextError("schema_rejected")
            if "mac_address" in item and (
                not isinstance(item["mac_address"], str)
                or not MAC_PATTERN.fullmatch(item["mac_address"])
            ):
                raise MeshContextError("schema_rejected")
            addresses = item.get("local_ip_addresses")
            if addresses is not None:
                if not isinstance(addresses, list) or not addresses:
                    raise MeshContextError("schema_rejected")
                canonical = [_canonical_local_address(value) for value in addresses]
                if any(value is None for value in canonical):
                    raise MeshContextError("schema_rejected")
                expected = sorted(
                    set(canonical),
                    key=lambda value: (ipaddress.ip_address(value).version, value),
                )
                if addresses != expected:
                    raise MeshContextError("schema_rejected")
    if client_ids != sorted(set(client_ids)):
        raise MeshContextError("schema_rejected")
    if schema_version in {"0.2", "0.3"}:
        if families["satellites"].get("normalized_item_count") != len(satellites):
            raise MeshContextError("schema_rejected")
        if families["clients"].get("normalized_item_count") != len(clients):
            raise MeshContextError("schema_rejected")
        expected_router_count = 1 if router.get("collection_reachability") == "confirmed" else 0
        if families["router"].get("normalized_item_count") != expected_router_count:
            raise MeshContextError("schema_rejected")
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, dict):
            raise MeshContextError("schema_rejected")
        if families["capabilities"].get("normalized_item_count") != len(capabilities):
            raise MeshContextError("schema_rejected")
    epoch = payload.get("identity_epoch")
    if schema_version in {"0.2", "0.3"} and (
        not isinstance(epoch, str) or not IDENTITY_PATTERN.fullmatch(epoch)
    ):
        raise MeshContextError("schema_rejected")
    warnings = payload.get("warnings")
    if not isinstance(warnings, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("code"), str)
        or not WARNING_PATTERN.fullmatch(item["code"]) for item in warnings
    ):
        raise MeshContextError("schema_rejected")
    if schema_version in {"0.2", "0.3"}:
        for warning in warnings:
            scope = warning.get("scope")
            affected = scope.get("affected_count") if isinstance(scope, dict) else None
            if (
                not isinstance(scope, dict)
                or scope.get("family") not in set(FAMILY_NAMES) | {"collection"}
                or scope.get("entity_type") not in {
                    "router", "satellite", "client", "capability", "transport", "artifact"
                }
                or (affected is not None and (not _is_int(affected) or affected < 1))
            ):
                raise MeshContextError("schema_rejected")
    return schema_version


def freshness(observed_at: dt.datetime | None, generated_at: dt.datetime) -> dict[str, Any]:
    if observed_at is None:
        return {
            "state": "unknown",
            "age_seconds": None,
            "is_fresh": False,
            "usable_for_current_semantics": False,
        }
    age = max(0, round((generated_at - observed_at).total_seconds()))
    if age <= FRESH_THROUGH_SECONDS:
        state = "fresh"
    elif age <= CURRENT_USE_THROUGH_SECONDS:
        state = "stale"
    else:
        state = "expired"
    return {
        "state": state,
        "age_seconds": age,
        "is_fresh": state == "fresh",
        "usable_for_current_semantics": age <= CURRENT_USE_THROUGH_SECONDS,
    }


def _family_projection(family: dict[str, Any], schema_version: str) -> dict[str, Any]:
    status = family["status"]
    return {
        "valid": True,
        "status": status,
        "usable_as_current": status == "complete",
        "successful_sources": family["successful_sources"],
        "attempted_sources": family["attempted_sources"],
        "errors": sorted(set(family.get("errors") or [])),
        "normalized_item_count": family.get("normalized_item_count") if schema_version in {"0.2", "0.3"} and status != "failed" else None,
        "source_observed_counts": dict(sorted((family.get("source_observed_counts") or {}).items())) if schema_version in {"0.2", "0.3"} else {},
        "omitted_item_count": family.get("omitted_item_count") if schema_version in {"0.2", "0.3"} else None,
    }


def _router_projection(router: dict[str, Any], schema_version: str) -> dict[str, Any]:
    reachability = router.get("collection_reachability")
    if schema_version == "0.1":
        reachability = "confirmed" if router.get("online") is True else "unknown"
    return {
        "node_id": router.get("node_id"),
        "collection_reachability": reachability if reachability in {"confirmed", "unknown"} else "unknown",
        "internet_state": router.get("internet_state") if router.get("internet_state") in {"up", "down", "unknown"} else "unknown",
        "wan_link_state": router.get("wan_link_state") if router.get("wan_link_state") in {"up", "down", "unknown"} else "unknown",
        "uptime_seconds": router.get("uptime_seconds") if _is_int(router.get("uptime_seconds")) else None,
        "memory_utilization_percent": router.get("memory_utilization_percent") if _is_int(router.get("memory_utilization_percent")) else None,
        "model": router.get("model") if isinstance(router.get("model"), str) else None,
        "firmware_version": router.get("firmware_version") if isinstance(router.get("firmware_version"), str) else None,
        "friendly_name": router.get("friendly_name") if schema_version == "0.3" and isinstance(router.get("friendly_name"), str) else None,
    }


def _satellite_projection(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": item.get("node_id"),
        "friendly_name": item.get("friendly_name") if isinstance(item.get("friendly_name"), str) else None,
        "state": item.get("state") if item.get("state") in {"online", "offline", "unknown"} else "unknown",
        "model": item.get("model") if isinstance(item.get("model"), str) else None,
        "firmware_version": item.get("firmware_version") if isinstance(item.get("firmware_version"), str) else None,
        "backhaul_type": item.get("backhaul_type") if item.get("backhaul_type") in {"wired", "2_4_ghz", "5_ghz", "unknown"} else "unknown",
        "quality_raw_relative": item.get("quality_raw") if _is_int(item.get("quality_raw")) else None,
    }


def _client_projection(item: dict[str, Any], known_nodes: set[str], schema_version: str) -> dict[str, Any]:
    association = item.get("associated_node_id")
    if schema_version in {"0.2", "0.3"}:
        resolution = item.get("association_resolution")
    elif association in known_nodes:
        resolution = "resolved"
    elif association is None:
        resolution = "missing"
    else:
        association = None
        resolution = "unresolved"
    return {
        "client_id": item.get("client_id"),
        "friendly_name": item.get("friendly_name") if isinstance(item.get("friendly_name"), str) else None,
        "medium": item.get("medium") if item.get("medium") in {"wired", "wireless", "unknown"} else "unknown",
        "band": item.get("band") if item.get("band") in {"2_4_ghz", "5_ghz"} else None,
        "network_role": item.get("network_role") if item.get("network_role") in {"main", "iot"} else None,
        "associated_node_id": association,
        "association_resolution": resolution,
        "signal_quality_raw_relative": item.get("signal_quality_raw") if _is_int(item.get("signal_quality_raw")) else None,
        "link_rate_mbps_apparent": item.get("link_rate_mbps_apparent") if _is_int(item.get("link_rate_mbps_apparent")) else None,
    }


def _probe_host_projection(
    source_clients: list[dict[str, Any]],
    projected_clients: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    *,
    exposure_mode: str | None,
    probe_local_addresses: set[str],
    collection_status: str,
    observed_at: str | None,
    current_freshness: dict[str, Any],
) -> dict[str, Any]:
    base = {
        "mapping_state": "identity_unavailable",
        "match_method": None,
        "lineage": "latest_attempt",
        "collection_status": collection_status,
        "observed_at": observed_at,
        "freshness": deepcopy(current_freshness),
        "attachment": None,
    }
    if collection_status == "failed":
        base["mapping_state"] = "client_family_unavailable"
        return base
    if exposure_mode not in {"local", "full"}:
        base["mapping_state"] = "identity_withheld"
        return base
    if not probe_local_addresses:
        return base

    matches: list[int] = []
    for index, item in enumerate(source_clients):
        addresses = {
            address
            for value in item.get("local_ip_addresses") or []
            if (address := _canonical_local_address(value))
        }
        if addresses & probe_local_addresses:
            matches.append(index)
    if not matches:
        base["mapping_state"] = "not_found"
        base["match_method"] = "local_address_intersection"
        return base
    if len(matches) != 1:
        base["mapping_state"] = "ambiguous"
        base["match_method"] = "local_address_intersection"
        return base

    source_client = source_clients[matches[0]]
    client = projected_clients[matches[0]]
    node = next(
        (item for item in nodes if item.get("node_id") == client.get("associated_node_id")),
        None,
    )
    node_name = source_client.get("associated_node_name")
    if not isinstance(node_name, str):
        node_name = node.get("friendly_name") if isinstance(node, dict) else None
    attachment = {
        "node_role": node.get("role") if isinstance(node, dict) else None,
        "node_name_local": node_name if isinstance(node_name, str) else None,
        "medium": client.get("medium"),
        "band": client.get("band"),
        "network_role": client.get("network_role"),
        "association_resolution": client.get("association_resolution"),
        "signal_quality_raw_relative": client.get("signal_quality_raw_relative"),
        "link_rate_mbps_apparent": client.get("link_rate_mbps_apparent"),
    }
    base.update({
        "mapping_state": "matched",
        "match_method": "local_address_intersection",
        "attachment": attachment,
    })
    return base


def _increment(group: dict[str, int], key: Any) -> None:
    label = "unknown" if key is None else str(key)
    group[label] = group.get(label, 0) + 1


def _client_summary(clients: list[dict[str, Any]], available: bool) -> dict[str, Any]:
    if not available:
        return {
            "available": False, "total": None, "by_medium": {}, "by_band": {},
            "by_network_role": {}, "by_associated_node": {},
            "resolved_associations": None, "unresolved_associations": None,
            "missing_associations": None, "not_resolved_associations": None,
        }
    by_medium: dict[str, int] = {}
    by_band: dict[str, int] = {}
    by_role: dict[str, int] = {}
    by_node: dict[str, int] = {}
    for item in clients:
        _increment(by_medium, item.get("medium"))
        _increment(by_band, item.get("band"))
        _increment(by_role, item.get("network_role"))
        _increment(by_node, item.get("associated_node_id"))
    resolved = sum(item.get("association_resolution") == "resolved" for item in clients)
    unresolved = sum(item.get("association_resolution") == "unresolved" for item in clients)
    missing = sum(item.get("association_resolution") == "missing" for item in clients)
    return {
        "available": True,
        "total": len(clients),
        "by_medium": dict(sorted(by_medium.items())),
        "by_band": dict(sorted(by_band.items())),
        "by_network_role": dict(sorted(by_role.items())),
        "by_associated_node": dict(sorted(by_node.items())),
        "resolved_associations": resolved,
        "unresolved_associations": unresolved,
        "missing_associations": missing,
        "not_resolved_associations": len(clients) - resolved,
    }


def _warnings(payload: dict[str, Any], schema_version: str) -> list[dict[str, Any]]:
    result = []
    for item in payload.get("warnings", [])[:100]:
        scope = item.get("scope") if schema_version in {"0.2", "0.3"} and isinstance(item.get("scope"), dict) else {
            "family": "collection", "entity_type": "artifact", "affected_count": None,
        }
        result.append({
            "code": item["code"],
            "severity": item.get("severity") if item.get("severity") in {"info", "warning", "error"} else "warning",
            "message": str(item.get("message") or "Source warning.")[:300],
            "scope": {
                "family": scope.get("family"),
                "entity_type": scope.get("entity_type"),
                "affected_count": scope.get("affected_count") if _is_int(scope.get("affected_count")) else None,
            },
        })
    return result


def _last_good_entry(data: dict[str, Any], observed_at: dt.datetime, generated_at: dt.datetime) -> dict[str, Any]:
    return {
        "observed_at": iso_utc(observed_at),
        "freshness": freshness(observed_at, generated_at),
        "is_current": False,
        "data": deepcopy(data),
    }


def _refresh_last_good_ages(last_good: dict[str, Any], generated_at: dt.datetime) -> dict[str, Any]:
    result = deepcopy(last_good)
    families = result.get("families") if isinstance(result.get("families"), dict) else {}
    result["families"] = families
    for name in PRESERVED_FAMILIES:
        entry = families.get(name)
        if not isinstance(entry, dict):
            families[name] = None
            continue
        observed = parse_ts(entry.get("observed_at"))
        if observed is None or not isinstance(entry.get("data"), dict):
            families[name] = None
            continue
        entry["freshness"] = freshness(observed, generated_at)
        entry["is_current"] = False
    return result


def _empty_last_good(identity_epoch: str | None = None) -> dict[str, Any]:
    return {"identity_epoch": identity_epoch, "families": {name: None for name in PRESERVED_FAMILIES}}


def _existing_last_good(existing: Any, generated_at: dt.datetime) -> dict[str, Any]:
    if not isinstance(existing, dict) or existing.get("schema_version") != SCHEMA_VERSION or existing.get("model_version") != MODEL_VERSION:
        return _empty_last_good()
    last_good = existing.get("last_good")
    if not isinstance(last_good, dict):
        return _empty_last_good()
    try:
        _validate_privacy(existing)
    except MeshContextError:
        return _empty_last_good()
    return _refresh_last_good_ages(last_good, generated_at)


def _status_for(source_status: str, freshness_state: str) -> str:
    if freshness_state == "expired":
        return "unavailable"
    if freshness_state == "stale":
        return "stale"
    return {"complete": "ok", "partial": "partial", "failed": "failed"}[source_status]


def _summary(status: str, node_count: int | None, client_count: int | None) -> str:
    if status == "ok":
        return f"Mesh Signal collection is complete: {node_count or 0} nodes and {client_count or 0} attached clients."
    if status == "partial":
        return "Mesh Signal collection is partial; only complete families are current."
    if status == "failed":
        return "Latest Mesh Signal collection failed; last complete family data may be available."
    if status == "stale":
        return "Mesh Signal data is stale; current facts are age-labeled."
    return "Mesh Signal data is unavailable for current semantic use."


def _family_evidence(
    name: str,
    latest_attempt: dict[str, Any],
    last_good: dict[str, Any],
) -> dict[str, Any]:
    family = latest_attempt.get("families", {}).get(name)
    if isinstance(family, dict) and family.get("status") != "failed":
        if name == "router":
            data = {"router": latest_attempt.get("router")}
        elif name == "satellites":
            data = {
                "nodes": [
                    item for item in latest_attempt.get("nodes", [])
                    if item.get("role") == "satellite"
                ]
            }
        else:
            data = {
                "summary": latest_attempt.get("client_summary"),
                "probe_host": latest_attempt.get("probe_host"),
            }
        return {
            "lineage": "latest_attempt",
            "collection_status": family.get("status"),
            "observed_at": latest_attempt.get("collection_completed_at"),
            "freshness": deepcopy(latest_attempt.get("freshness")),
            "data": data,
        }
    retained = last_good.get("families", {}).get(name)
    if isinstance(retained, dict):
        data = deepcopy(retained.get("data"))
        if name == "clients" and isinstance(data, dict):
            data = {"summary": data.get("summary"), "probe_host": data.get("probe_host")}
        return {
            "lineage": "last_good",
            "collection_status": "complete",
            "observed_at": retained.get("observed_at"),
            "freshness": deepcopy(retained.get("freshness")),
            "data": data,
        }
    return {
        "lineage": "unavailable",
        "collection_status": family.get("status") if isinstance(family, dict) else None,
        "observed_at": None,
        "freshness": {
            "state": "unknown",
            "age_seconds": None,
            "is_fresh": False,
            "usable_for_current_semantics": False,
        },
        "data": None,
    }


def _lan_evidence(
    latest_attempt: dict[str, Any],
    last_good: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    latest_freshness = latest_attempt.get("freshness") or {}
    freshness_state = latest_freshness.get("state")
    source_status = latest_attempt.get("source_status")
    if freshness_state == "expired":
        state, label = "expired", "Mesh evidence expired"
    elif freshness_state == "stale":
        state, label = "stale", "Mesh evidence stale"
    elif source_status == "failed":
        state, label = "latest_failed", "Latest Mesh collection failed"
    elif source_status == "partial":
        state, label = "partial", "Mesh evidence partial"
    elif status == "ok":
        state, label = "current", "Mesh evidence current"
    else:
        state, label = "unavailable", "Mesh evidence unavailable"

    router_evidence = _family_evidence("router", latest_attempt, last_good)
    satellite_evidence = _family_evidence("satellites", latest_attempt, last_good)
    client_evidence = _family_evidence("clients", latest_attempt, last_good)
    router_data = router_evidence.get("data") or {}
    router = router_data.get("router") if isinstance(router_data, dict) else None
    satellite_data = satellite_evidence.get("data") or {}
    satellites = satellite_data.get("nodes") if isinstance(satellite_data, dict) else None
    satellites = satellites if isinstance(satellites, list) else None
    client_data = client_evidence.get("data") or {}
    client_summary = client_data.get("summary") if isinstance(client_data, dict) else None
    client_summary = client_summary if isinstance(client_summary, dict) else None
    probe_host = client_data.get("probe_host") if isinstance(client_data, dict) else None
    probe_host = deepcopy(probe_host) if isinstance(probe_host, dict) else {
        "mapping_state": "client_family_unavailable",
        "match_method": None,
        "lineage": client_evidence["lineage"],
        "collection_status": client_evidence["collection_status"],
        "observed_at": client_evidence["observed_at"],
        "freshness": deepcopy(client_evidence["freshness"]),
        "attachment": None,
    }
    probe_host["lineage"] = client_evidence["lineage"]
    probe_host["collection_status"] = client_evidence["collection_status"]
    probe_host["observed_at"] = client_evidence["observed_at"]
    probe_host["freshness"] = deepcopy(client_evidence["freshness"])

    satellite_states = {"online": 0, "offline": 0, "unknown": 0}
    if satellites is not None:
        for item in satellites:
            item_state = item.get("state")
            satellite_states[item_state if item_state in satellite_states else "unknown"] += 1
    router_reported = (
        router.get("collection_reachability") == "confirmed"
        if isinstance(router, dict) else None
    )
    node_total = (
        (1 if router_reported else 0) + len(satellites)
        if router_reported is not None and satellites is not None else None
    )

    if state == "unavailable":
        summary = "Mesh evidence is unavailable; LAN telemetry remains available."
    elif state == "latest_failed":
        summary = "The latest collection failed; any displayed inventory is explicitly last known good."
    elif state in {"stale", "expired"}:
        summary = "Mesh inventory is age-labeled and must not be treated as current."
    elif state == "partial":
        summary = "Only successfully collected Mesh families are current; failed families use labeled prior evidence when available."
    else:
        summary = "Current Mesh inventory can qualify LAN evidence without changing LAN health or attribution."

    return {
        "state": state,
        "temporal_scope": "current_snapshot_only",
        "label": label,
        "summary": summary,
        "source_age_seconds": latest_freshness.get("age_seconds"),
        "router": {
            "lineage": router_evidence["lineage"],
            "observed_at": router_evidence["observed_at"],
            "freshness": router_evidence["freshness"],
            "reported": router_reported,
            "internet_state": router.get("internet_state") if isinstance(router, dict) else None,
            "wan_link_state": router.get("wan_link_state") if isinstance(router, dict) else None,
        },
        "satellites": {
            "lineage": satellite_evidence["lineage"],
            "collection_status": satellite_evidence["collection_status"],
            "observed_at": satellite_evidence["observed_at"],
            "freshness": satellite_evidence["freshness"],
            "total": len(satellites) if satellites is not None else None,
            "by_state": satellite_states if satellites is not None else {},
        },
        "nodes": {"total_reported": node_total},
        "clients": {
            "lineage": client_evidence["lineage"],
            "collection_status": client_evidence["collection_status"],
            "observed_at": client_evidence["observed_at"],
            "freshness": client_evidence["freshness"],
            "total": client_summary.get("total") if client_summary else None,
            "by_medium": deepcopy(client_summary.get("by_medium", {})) if client_summary else {},
            "by_band": deepcopy(client_summary.get("by_band", {})) if client_summary else {},
            "resolved_associations": client_summary.get("resolved_associations") if client_summary else None,
            "not_resolved_associations": client_summary.get("not_resolved_associations") if client_summary else None,
        },
        "probe_host": probe_host,
        "limitations": [
            "Current Mesh evidence is not point-in-time history for older LAN samples.",
            "Probe-host attachment is a current snapshot and is not point-in-time evidence for older LAN samples.",
            "Signal values are raw relative vendor metrics and link rates are apparent, not measured throughput.",
        ],
    }


def build_projection(
    source_payload: dict[str, Any] | None,
    *,
    source_state: str,
    generated_at: dt.datetime,
    existing_projection: dict[str, Any] | None = None,
    source_reference: str = "configured_mesh_signal_artifact",
    probe_local_addresses: set[str] | None = None,
) -> dict[str, Any]:
    last_good = _existing_last_good(existing_projection, generated_at)
    limitations: list[str] = []
    latest_attempt: dict[str, Any] = {
        "state": source_state,
        "source_status": None,
        "source_schema_version": None,
        "identity_epoch": None,
        "collection_started_at": None,
        "collection_completed_at": None,
        "collection_duration_ms": None,
        "freshness": freshness(None, generated_at),
        "families": {},
        "router": None,
        "nodes": [],
        "client_summary": _client_summary([], False),
        "clients": [],
        "probe_host": None,
        "warnings": [],
    }
    status = "unavailable"
    identity_continuity = "unknown"
    source_metadata = {"type": None, "collector_version": None, "router_model": None, "firmware_version": None}

    if source_payload is not None and source_state == "valid":
        schema_version = _validate_source(source_payload)
        collection = source_payload["collection"]
        families = collection["families"]
        source_status = collection["status"]
        started = parse_ts(source_payload["collected_at"])
        completed = parse_ts(collection.get("completed_at")) if schema_version in {"0.2", "0.3"} else None
        if completed is None and started is not None:
            completed = started + dt.timedelta(milliseconds=collection["duration_ms"])
        current_freshness = freshness(completed, generated_at)
        current_epoch = source_payload.get("identity_epoch") if schema_version in {"0.2", "0.3"} else None
        previous_epoch = last_good.get("identity_epoch")
        has_preserved_data = any(
            isinstance(last_good.get("families", {}).get(name), dict)
            for name in PRESERVED_FAMILIES
        )
        if schema_version == "0.1":
            identity_continuity = "legacy_unverifiable"
            limitations.append("Source schema 0.1 cannot signal identity-key replacement.")
            if has_preserved_data:
                limitations.append("Legacy identity cannot safely reuse prior last-good family data.")
            last_good = _empty_last_good()
        elif previous_epoch and previous_epoch != current_epoch:
            last_good = _empty_last_good(current_epoch)
            identity_continuity = "reset"
            limitations.append("Mesh identity continuity reset; earlier last-good family data was discarded.")
        elif previous_epoch is None and has_preserved_data:
            last_good = _empty_last_good(current_epoch)
            identity_continuity = "reset"
            limitations.append("Identity-aware source data replaced unverifiable last-good identity data.")
        else:
            identity_continuity = "continuous"
            last_good["identity_epoch"] = current_epoch

        family_projection = {
            name: _family_projection(families[name], schema_version) for name in FAMILY_NAMES
        }
        router = _router_projection(source_payload["router"], schema_version)
        satellites = [_satellite_projection(item) for item in source_payload["satellites"]]
        known_nodes = {item["node_id"] for item in satellites}
        if router.get("node_id"):
            known_nodes.add(router["node_id"])
        all_clients = [
            _client_projection(item, known_nodes, schema_version) for item in source_payload["clients"]
        ]
        all_clients = sorted(all_clients, key=lambda item: item["client_id"])
        clients_available = family_projection["clients"]["status"] != "failed"
        client_summary = _client_summary(all_clients, clients_available)
        clients = all_clients
        if len(all_clients) > MAX_CLIENT_DETAILS:
            clients = all_clients[:MAX_CLIENT_DETAILS]
            limitations.append("Client detail was bounded to the projection limit.")
        router_current = family_projection["router"]["status"] != "failed"
        satellites_current = family_projection["satellites"]["status"] != "failed"
        nodes = []
        if router_current:
            nodes.append({"role": "router", **router})
        if satellites_current:
            nodes.extend({"role": "satellite", **item} for item in satellites)
        exposure_mode = (
            source_payload.get("privacy", {}).get("exposure_mode")
            if schema_version == "0.3" else None
        )
        probe_host = _probe_host_projection(
            source_payload["clients"],
            all_clients,
            nodes,
            exposure_mode=exposure_mode,
            probe_local_addresses=probe_local_addresses or set(),
            collection_status=family_projection["clients"]["status"],
            observed_at=iso_utc(completed) if completed else None,
            current_freshness=current_freshness,
        )
        node_count = len(nodes) if router_current or satellites_current else None
        latest_attempt = {
            "state": source_status,
            "source_status": source_status,
            "source_schema_version": schema_version,
            "identity_epoch": current_epoch,
            "collection_started_at": iso_utc(started) if started else None,
            "collection_completed_at": iso_utc(completed) if completed else None,
            "collection_duration_ms": collection["duration_ms"],
            "freshness": current_freshness,
            "families": family_projection,
            "router": router if router_current else None,
            "nodes": nodes,
            "client_summary": client_summary,
            "clients": clients if clients_available else [],
            "probe_host": probe_host,
            "warnings": _warnings(source_payload, schema_version),
        }
        observed = completed or generated_at
        if families["router"]["status"] == "complete":
            last_good["families"]["router"] = _last_good_entry({"router": router}, observed, generated_at)
        if families["satellites"]["status"] == "complete":
            last_good["families"]["satellites"] = _last_good_entry({"nodes": satellites}, observed, generated_at)
        if families["clients"]["status"] == "complete":
            last_good["families"]["clients"] = _last_good_entry(
                {"summary": client_summary, "clients": clients, "probe_host": probe_host},
                observed,
                generated_at,
            )
        last_good = _refresh_last_good_ages(last_good, generated_at)
        status = _status_for(source_status, current_freshness["state"])
        source = source_payload["source"]
        source_metadata = {
            "type": source.get("type"),
            "collector_version": source.get("collector_version"),
            "router_model": source.get("router_model"),
            "firmware_version": source.get("firmware_version"),
        }
        summary = _summary(status, node_count, client_summary.get("total"))
    else:
        reason_messages = {
            "not_configured": "Mesh Signal source is not configured.",
            "missing": "Configured Mesh Signal artifact is missing.",
            "malformed": "Configured Mesh Signal artifact is unreadable or malformed.",
            "unsupported_schema": "Mesh Signal artifact schema is unsupported.",
            "privacy_rejected": "Mesh Signal artifact failed privacy validation.",
            "schema_rejected": "Mesh Signal artifact failed schema validation.",
        }
        summary = reason_messages.get(source_state, "Mesh Signal data is unavailable.")
        limitations.append(summary)

    lan_evidence = _lan_evidence(latest_attempt, last_good, status)
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "artifact_type": "mesh_context",
        "generated_at": iso_utc(generated_at),
        "status": status,
        "summary": summary,
        "freshness": latest_attempt["freshness"],
        "identity_continuity": identity_continuity,
        "source": source_metadata,
        "latest_attempt": latest_attempt,
        "last_good": last_good,
        "lan_evidence": lan_evidence,
        "warnings": latest_attempt["warnings"],
        "freshness_policy": {
            "intended_poll_seconds": INTENDED_POLL_SECONDS,
            "fresh_through_seconds": FRESH_THROUGH_SECONDS,
            "current_use_through_seconds": CURRENT_USE_THROUGH_SECONDS,
        },
        "privacy": {
            "classification": "local_only",
            "friendly_names_local_only": True,
            "source_exposure_mode": (
                source_payload.get("privacy", {}).get("exposure_mode")
                if isinstance(source_payload, dict) and source_payload.get("schema_version") == "0.3"
                else None
            ),
            "probe_identity_values_persisted": False,
            "safe_for_external_assistant": False,
        },
        "limitations": limitations,
        "provenance": {
            "producer": "bin/mesh_context.py",
            "source_reference": source_reference,
            "network_requests_performed": False,
        },
    }


def load_existing(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_local_config(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if tokens and tokens[0] == "export":
            tokens = tokens[1:]
        if not tokens:
            continue
        if len(tokens) >= 3 and tokens[1] == "=":
            key, value = tokens[0], " ".join(tokens[2:])
        elif "=" in tokens[0]:
            key, value = tokens[0].split("=", 1)
        else:
            continue
        if key.strip() == SOURCE_ENV:
            values[SOURCE_ENV] = value.strip()
    return values


def configured_source_path(
    *,
    environ: dict[str, str],
    config_path: Path,
    project_root: Path,
) -> tuple[Path | None, str]:
    raw_path = str(environ.get(SOURCE_ENV, "")).strip()
    reference = "process_environment"
    if not raw_path:
        raw_path = read_local_config(config_path).get(SOURCE_ENV, "").strip()
        reference = "repository_local_config"
    if not raw_path:
        return None, "not_configured"
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate, reference


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refresh_mesh_context(
    output_path: Path,
    *,
    source_path: Path | None = None,
    generated_at: dt.datetime | None = None,
    environ: dict[str, str] | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    project_root: Path = PROJECT_ROOT,
    probe_local_addresses: set[str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or utc_now()
    existing = load_existing(output_path)
    env = os.environ if environ is None else environ
    configured = source_path
    source_reference = "explicit_argument" if source_path is not None else "not_configured"
    if configured is None:
        configured, source_reference = configured_source_path(
            environ=env,
            config_path=config_path,
            project_root=project_root,
        )
    source_payload = None
    state = "not_configured"
    if configured is not None:
        try:
            source_payload = json.loads(configured.read_text(encoding="utf-8"))
            state = "valid"
            _validate_source(source_payload)
        except FileNotFoundError:
            state = "missing"
            source_payload = None
        except (OSError, json.JSONDecodeError):
            state = "malformed"
            source_payload = None
        except MeshContextError as error:
            state = error.code
            source_payload = None
    projection = build_projection(
        source_payload,
        source_state=state,
        generated_at=generated,
        existing_projection=existing,
        source_reference=source_reference,
        probe_local_addresses=(
            discover_probe_local_addresses()
            if probe_local_addresses is None and state == "valid"
            else (probe_local_addresses or set())
        ),
    )
    write_json_atomic(output_path, projection)
    return projection


if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    refresh_mesh_context(base / "viz" / "mesh_context.json")
