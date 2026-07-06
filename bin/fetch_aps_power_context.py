#!/usr/bin/env python3
from pathlib import Path
import datetime as dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE = Path("/Users/mbeason/prime-observer")
VIZ_DIR = BASE / "viz"
OUT = VIZ_DIR / "aps_power_context.json"

OUTAGE_VIEWER_URL = "https://outagemap.aps.com/outageviewer/"
CONFIG_URL = f"{OUTAGE_VIEWER_URL}mockData/config.json"
WEBMAP_URL = f"{OUTAGE_VIEWER_URL}mockData/webmap.json"
USER_AGENT = "PrimeObserver/0.9.0"
DEFAULT_TIMEOUT_SECONDS = 8
MAX_ITEMS = 5

OUTAGE_LAYER_TITLE = "Outages"
PSPS_LAYER_TITLE = "APSOutageMap - PSPS Events"
SIGNALS_CHECKED = ["Current outages", "PSPS events", "Update properties"]
SCOPE = {
    "state": "AZ",
    "service_area": "APS service territory",
    "label": "APS service territory",
}


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(ts):
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_epoch_millis(value):
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return dt.datetime.fromtimestamp(millis / 1000.0, tz=dt.timezone.utc)


def normalize_event_type(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return "power_event"
    return raw.replace(" ", "_")


def summarize_area(city, area, boundary):
    bits = []
    city = str(city or "").strip()
    area = str(area or "").strip()
    boundary = str(boundary or "").strip()
    if city:
        bits.append(city)
    if area and area != city:
        bits.append(area)
    label = " • ".join(bits)
    if boundary:
        return f"{label}: {boundary}" if label else boundary
    return label or "APS service territory"


def provider_reference(item):
    media_link = str(item.get("MediaLink") or "").strip()
    if media_link:
        return media_link
    return OUTAGE_VIEWER_URL


def base_payload(status, summary):
    return {
        "schema_version": 1,
        "generated_at": iso_utc(utc_now()),
        "provider": "aps",
        "status": status,
        "summary": summary,
        "scope": dict(SCOPE),
        "signals_checked": list(SIGNALS_CHECKED),
        "items": [],
    }


def unavailable_payload(reason=None):
    payload = base_payload("unavailable", "Unable to retrieve current APS power context.")
    if reason:
        payload["reason"] = reason
    return payload


def write_json_atomic(payload):
    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with tmp.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(OUT)


def fetch_json(url, timeout=DEFAULT_TIMEOUT_SECONDS, query=None):
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def layer_url_by_title(webmap_payload, title):
    layers = (
        webmap_payload.get("Data", {}).get("operationalLayers")
        if isinstance(webmap_payload, dict)
        else None
    )
    if not isinstance(layers, list):
        return None
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if str(layer.get("title") or "").strip() == title:
            url = str(layer.get("url") or "").strip()
            if url:
                return url
    return None


def query_features(layer_url, timeout, out_fields, *, fetcher=fetch_json):
    payload = fetcher(
        f"{layer_url}/query",
        timeout=timeout,
        query={
            "where": "1=1",
            "outFields": ",".join(out_fields),
            "returnGeometry": "false",
            "f": "json",
        },
    )
    features = payload.get("features")
    return features if isinstance(features, list) else []


def update_properties(update_layer_url, timeout, *, fetcher=fetch_json):
    rows = query_features(
        update_layer_url,
        timeout,
        ["TIMESTAMP", "APS_Banner", "APS_Message", "APP_MODE", "APP_MESSAGE"],
        fetcher=fetcher,
    )
    if not rows:
        return {}
    attrs = rows[0].get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def item_from_feature(feature, fallback_event_type):
    attrs = feature.get("attributes") if isinstance(feature, dict) else {}
    if not isinstance(attrs, dict):
        return None

    customers = attrs.get("customers")
    try:
        customers = int(customers) if customers is not None else None
    except (TypeError, ValueError):
        customers = None

    item = {
        "event_type": normalize_event_type(attrs.get("outagetype") or fallback_event_type),
        "affected_area": summarize_area(
            attrs.get("City"),
            attrs.get("APSArea"),
            attrs.get("Boundary"),
        ),
        "customer_count": customers,
        "estimated_restoration_time": iso_utc(parse_epoch_millis(attrs.get("etr"))) if attrs.get("etr") else None,
        "source_reference": provider_reference(attrs),
    }

    if item["event_type"] == "planned_outage" and "psps" in fallback_event_type.lower():
        item["event_type"] = "psps_event"

    return item


def build_summary(items, provider_update_at):
    if not items:
        return "No APS outages or PSPS events reported."

    total_customers = sum(
        item["customer_count"]
        for item in items
        if isinstance(item.get("customer_count"), int)
    )
    event_count = len(items)
    summary = f"{event_count} APS power event(s)"
    if total_customers:
        summary += f" affecting {total_customers} customers"
    summary += "."
    if provider_update_at:
        summary += f" Source updated {provider_update_at}."
    return summary


def build_payload(timeout=DEFAULT_TIMEOUT_SECONDS, *, config_fetcher=fetch_json):
    config = config_fetcher(CONFIG_URL, timeout=timeout)
    webmap = config_fetcher(WEBMAP_URL, timeout=timeout)

    if not isinstance(config, dict):
        raise ValueError("APS config payload was invalid")
    if not isinstance(webmap, dict):
        raise ValueError("APS web map payload was invalid")

    outage_layer_url = layer_url_by_title(webmap, OUTAGE_LAYER_TITLE)
    psps_layer_url = layer_url_by_title(webmap, config.get("pspsLayerTitle") or PSPS_LAYER_TITLE)
    update_layer_url = str(config.get("updateLayer") or "").strip()

    if not outage_layer_url or not psps_layer_url or not update_layer_url:
        raise ValueError("APS provider configuration was incomplete")

    outage_features = query_features(
        outage_layer_url,
        timeout,
        ["APSArea", "City", "Boundary", "customers", "etr", "outagetype", "MediaLink"],
        fetcher=config_fetcher,
    )
    psps_features = query_features(
        psps_layer_url,
        timeout,
        ["APSArea", "City", "Boundary", "customers", "etr", "outagetype", "MediaLink", "Cause"],
        fetcher=config_fetcher,
    )
    update_properties_row = update_properties(update_layer_url, timeout, fetcher=config_fetcher)
    provider_update = parse_epoch_millis(update_properties_row.get("TIMESTAMP"))
    provider_update_at = iso_utc(provider_update) if provider_update is not None else None

    items = []
    for feature in outage_features:
        item = item_from_feature(feature, "unplanned_outage")
        if item is not None:
            items.append(item)
    for feature in psps_features:
        item = item_from_feature(feature, "psps_event")
        if item is not None:
            items.append(item)

    items.sort(
        key=lambda item: (
            item.get("event_type") or "",
            -(item.get("customer_count") or 0),
            item.get("affected_area") or "",
        )
    )
    items = items[:MAX_ITEMS]

    payload = base_payload(
        "events_reported" if items else "normal",
        build_summary(items, provider_update_at),
    )
    payload["items"] = items
    return payload


def main():
    try:
        payload = build_payload()
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"APS power context fetch failed: {exc}", file=sys.stderr)
        payload = unavailable_payload(str(exc))

    write_json_atomic(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
