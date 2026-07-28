#!/usr/bin/env python3
from pathlib import Path
import datetime as dt
import json
import os
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE = Path(__file__).resolve().parents[1]
VIZ_DIR = BASE / "viz"
OUT = VIZ_DIR / "internet_conditions.json"
ENV_FILE = BASE / ".env.cloudflare"

API_BASE = "https://api.cloudflare.com/client/v4"
OUTAGES_API_PATH = "/radar/annotations/outages"
TRAFFIC_ANOMALIES_API_PATH = "/radar/traffic_anomalies"
BGP_ROUTE_LEAKS_API_PATH = "/radar/bgp/leaks/events"
USER_AGENT = "PrimeObserver/0.8.2"
DEFAULT_DATE_RANGE = "7d"
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_LIMIT = 10
RECENT_WINDOW_HOURS = 24
MAX_ITEMS = 3
COUNTRY_LOCATION = "US"
COUNTRY_SCOPE = {
    "country": "US",
    "region": None,
    "label": "United States context",
}
MODEL_VERSION = "internet_conditions_v2"
COUNTRY_SIGNALS_CHECKED = ["US outages", "US traffic anomalies"]
ASN_SIGNALS_CHECKED = [
    "AS traffic anomalies",
    "BGP route leaks involving configured AS",
    "US outages",
    "US traffic anomalies",
]
COUNTRY_PROVIDER_DISPLAY_NAME = "US Radar"
LIMITATIONS = [
    "Cloudflare Radar normal results do not prove a measured local ISP path is healthy.",
    "Internet Conditions is supporting Environmental Context only and does not affect local health, attribution, or impact scoring.",
]
FETCH_ERRORS = (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(ts):
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def parse_env_file(path):
    values = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
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
            key = tokens[0]
            value = " ".join(tokens[2:])
        elif "=" in tokens[0]:
            key, value = tokens[0].split("=", 1)
        else:
            continue

        key = key.strip()
        value = value.strip()
        if not key:
            continue

        values[key] = value

    return values


def config_value(key, file_values, default=""):
    value = os.environ.get(key)
    if value is None:
        value = file_values.get(key, default)
    return str(value).strip()


def normalize_asn(value):
    raw = str(value or "").strip().upper()
    if raw.startswith("AS"):
        raw = raw[2:]
    return raw if raw.isdigit() else ""


def load_config():
    file_values = parse_env_file(ENV_FILE)

    token = config_value("CLOUDFLARE_API_TOKEN", file_values)
    date_range = config_value("CLOUDFLARE_RADAR_DATE_RANGE", file_values, DEFAULT_DATE_RANGE)
    timeout_raw = config_value(
        "CLOUDFLARE_RADAR_TIMEOUT_SECONDS",
        file_values,
        str(DEFAULT_TIMEOUT_SECONDS),
    )
    limit_raw = config_value(
        "CLOUDFLARE_RADAR_LIMIT",
        file_values,
        str(DEFAULT_LIMIT),
    )

    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS

    try:
        limit = int(limit_raw)
    except ValueError:
        limit = DEFAULT_LIMIT

    return {
        "CLOUDFLARE_API_TOKEN": token,
        "CLOUDFLARE_RADAR_DATE_RANGE": date_range or DEFAULT_DATE_RANGE,
        "CLOUDFLARE_RADAR_TIMEOUT_SECONDS": timeout,
        "CLOUDFLARE_RADAR_LIMIT": max(1, min(limit, 50)),
        "PRIME_OBSERVER_INTERNET_ASN": normalize_asn(
            config_value("PRIME_OBSERVER_INTERNET_ASN", file_values)
        ),
        "PRIME_OBSERVER_INTERNET_PROVIDER_LABEL": config_value(
            "PRIME_OBSERVER_INTERNET_PROVIDER_LABEL",
            file_values,
        ),
    }


def requested_query_metadata(config):
    asn = config.get("PRIME_OBSERVER_INTERNET_ASN") or ""
    provider_label = config.get("PRIME_OBSERVER_INTERNET_PROVIDER_LABEL") or ""
    if asn:
        display_name = provider_label or "Configured network"
        return {
            "query_mode": "asn",
            "query_target_label": provider_label or f"AS{asn}",
            "query_target_id": f"AS{asn}",
            "provider_display_name": display_name,
            "fallback_used": False,
        }

    return {
        "query_mode": "country",
        "query_target_label": "United States",
        "query_target_id": "US",
        "provider_display_name": COUNTRY_PROVIDER_DISPLAY_NAME,
        "fallback_used": False,
    }


def configuration_notes(config):
    asn = str(config.get("PRIME_OBSERVER_INTERNET_ASN") or "").strip()
    provider_label = str(config.get("PRIME_OBSERVER_INTERNET_PROVIDER_LABEL") or "").strip()
    notes = []

    if not asn:
        notes.append("Reason: PRIME_OBSERVER_INTERNET_ASN not configured.")
        if provider_label:
            notes.append(
                "Note: PRIME_OBSERVER_INTERNET_PROVIDER_LABEL is set but will be ignored without PRIME_OBSERVER_INTERNET_ASN."
            )
        return notes

    if not provider_label:
        notes.append(
            "Note: PRIME_OBSERVER_INTERNET_PROVIDER_LABEL not configured. Using a generic operator label."
        )
    return notes


def print_configuration_diagnostics(config, query_meta):
    mode_label = "ASN" if query_meta["query_mode"] == "asn" else "US"
    print("Internet Conditions configuration")
    print(f"Mode: {mode_label}")
    for note in configuration_notes(config):
        print(note)
    if query_meta["query_mode"] == "asn":
        print(f"Provider: {query_meta['provider_display_name']}")
        print(f"ASN: {query_meta['query_target_id']}")


def print_result_diagnostics(payload):
    if payload.get("query_mode") == "asn" and payload.get("fallback_used"):
        print("Result: Falling back to US-scoped query.")


def asn_scope_label(query_meta):
    label = str(query_meta.get("provider_display_name") or "").strip()
    if not label:
        label = "Configured network"
    return {
        "country": None,
        "region": None,
        "label": f"{label} network context",
    }


def base_payload(status, summary, query_meta, scope, signals_checked):
    payload = {
        "schema_version": 2,
        "model_version": MODEL_VERSION,
        "generated_at": iso_utc(utc_now()),
        "provider": "cloudflare_radar",
        "status": status,
        "summary": summary,
        "scope": dict(scope),
        "signals_checked": list(signals_checked),
        "items": [],
        "checked_window": {},
        "signal_results": {},
        "degradation": {
            "partial": False,
            "unavailable_signals": [],
        },
        "limitations": list(LIMITATIONS),
    }
    payload.update(
        {
            "query_mode": query_meta["query_mode"],
            "query_target_label": query_meta["query_target_label"],
            "query_target_id": query_meta["query_target_id"],
            "provider_display_name": query_meta["provider_display_name"],
            "fallback_used": bool(query_meta.get("fallback_used")),
        }
    )
    return payload


def write_json_atomic(payload):
    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with tmp.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(OUT)


def fetch_json(api_token, path, query, timeout):
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_token}",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read().decode("utf-8")
        return json.loads(body)


def fetch_outages(api_token, date_range, timeout, limit):
    query = urllib.parse.urlencode(
        {
            "dateRange": date_range,
            "format": "json",
            "limit": limit,
            "location": COUNTRY_LOCATION,
        }
    )
    return fetch_json(
        api_token,
        OUTAGES_API_PATH,
        urllib.parse.parse_qsl(query),
        timeout,
    )


def fetch_traffic_anomalies(api_token, date_range, timeout, limit):
    return fetch_json(
        api_token,
        TRAFFIC_ANOMALIES_API_PATH,
        {
            "dateRange": date_range,
            "format": "json",
            "limit": limit,
            "location": COUNTRY_LOCATION,
            "type": "LOCATION",
        },
        timeout,
    )


def fetch_traffic_anomalies_by_asn(api_token, date_range, timeout, limit, asn):
    return fetch_json(
        api_token,
        TRAFFIC_ANOMALIES_API_PATH,
        {
            "asn": int(asn),
            "dateRange": date_range,
            "format": "json",
            "limit": limit,
            "type": "AS",
        },
        timeout,
    )


def fetch_bgp_route_leaks(api_token, date_range, timeout, limit, *, asn=None, country=None):
    query = {
        "dateRange": date_range,
        "format": "json",
        "page": 1,
        "per_page": limit,
        "sortBy": "TIME",
        "sortOrder": "DESC",
    }
    if asn:
        query["involvedAsn"] = int(asn)
    if country:
        query["involvedCountry"] = country
    return fetch_json(api_token, BGP_ROUTE_LEAKS_API_PATH, query, timeout)


def response_annotations(payload):
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    annotations = result.get("annotations")
    return annotations if isinstance(annotations, list) else []


def response_traffic_anomalies(payload):
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    anomalies = result.get("trafficAnomalies")
    return anomalies if isinstance(anomalies, list) else []


def response_route_leaks(payload):
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    events = result.get("events")
    return events if isinstance(events, list) else []


def is_ongoing(annotation):
    if annotation.get("signal") == "bgp_route_leak":
        return annotation.get("finished") is False
    return parse_ts(annotation.get("endDate")) is None


def is_recent(annotation, now):
    cutoff = now - dt.timedelta(hours=RECENT_WINDOW_HOURS)
    if annotation.get("signal") == "bgp_route_leak":
        for key in ("detected_ts", "max_ts", "min_ts"):
            ts = parse_ts(annotation.get(key))
            if ts and ts >= cutoff:
                return True
        return False
    start = parse_ts(annotation.get("startDate"))
    end = parse_ts(annotation.get("endDate"))
    if start and start >= cutoff:
        return True
    if end and end >= cutoff:
        return True
    return False


def region_label(annotation):
    scope = str(annotation.get("scope") or "").strip()
    if scope:
        return scope

    details = annotation.get("locationsDetails")
    if isinstance(details, list) and details:
        names = [str(item.get("name") or "").strip() for item in details if isinstance(item, dict)]
        names = [name for name in names if name]
        if names:
            return ", ".join(names[:2])

    locations = annotation.get("locations")
    if isinstance(locations, list) and locations:
        labels = [str(item).strip() for item in locations if str(item).strip()]
        if labels:
            return ", ".join(labels[:2])

    return "Unknown region"


def anomaly_region_label(anomaly):
    entity_type = str(anomaly.get("type") or "").strip().upper()
    location_details = anomaly.get("locationDetails")
    if entity_type != "AS" and isinstance(location_details, dict):
        label = str(location_details.get("name") or "").strip()
        if label:
            return label

    asn_details = anomaly.get("asnDetails")
    if isinstance(asn_details, dict):
        label = str(asn_details.get("name") or "").strip()
        if label:
            return label
        location = asn_details.get("location") or asn_details.get("locations")
        if isinstance(location, dict):
            label = str(location.get("name") or "").strip()
            if label:
                return label

    origin_details = anomaly.get("originDetails")
    if isinstance(origin_details, dict):
        label = str(origin_details.get("name") or "").strip()
        if label:
            return label

    return "Unknown region"


def description_label(annotation):
    description = str(annotation.get("description") or "").strip()
    if description:
        return description

    outage = annotation.get("outage")
    if isinstance(outage, dict):
        cause = str(outage.get("outageCause") or "").strip().replace("_", " ").lower()
        outage_type = str(outage.get("outageType") or "").strip().replace("_", " ").lower()
        pieces = [piece for piece in (outage_type, cause) if piece]
        if pieces:
            return " ".join(pieces)

    event_type = str(annotation.get("eventType") or "outage").strip().replace("_", " ").lower()
    return event_type or "outage"


def anomaly_description(anomaly):
    region = anomaly_region_label(anomaly)
    entity_type = str(anomaly.get("type") or "").strip().upper()
    status = str(anomaly.get("status") or "").strip().lower()
    qualifier = "Elevated"
    if status == "verified":
        qualifier = "Verified"

    origin_details = anomaly.get("originDetails")
    if isinstance(origin_details, dict):
        origin_name = str(origin_details.get("name") or "").strip()
        if origin_name:
            return f"{qualifier} traffic anomaly linked to {origin_name}"

    if entity_type == "AS":
        return f"{qualifier} traffic anomaly detected for {region}"
    return f"{qualifier} traffic anomaly detected in {region}"


def normalize_item(annotation):
    started = parse_ts(annotation.get("startDate"))
    ended = parse_ts(annotation.get("endDate"))
    reference = str(annotation.get("linkedUrl") or "").strip()
    return {
        "signal": "outage",
        "region": region_label(annotation),
        "started": iso_utc(started) if started else None,
        "ended": iso_utc(ended) if ended else None,
        "description": description_label(annotation),
        "reference": reference,
    }


def normalize_anomaly(anomaly):
    started = parse_ts(anomaly.get("startDate"))
    ended = parse_ts(anomaly.get("endDate"))
    return {
        "signal": "traffic_anomaly",
        "region": anomaly_region_label(anomaly),
        "started": iso_utc(started) if started else None,
        "ended": iso_utc(ended) if ended else None,
        "description": anomaly_description(anomaly),
        "reference": "",
        "entity_type": str(anomaly.get("type") or "").strip().upper() or None,
        "event_status": str(anomaly.get("status") or "").strip().lower() or None,
        "uuid": str(anomaly.get("uuid") or "").strip() or None,
    }


def normalize_route_leak(event):
    detected = parse_ts(event.get("detected_ts")) or parse_ts(event.get("max_ts")) or parse_ts(event.get("min_ts"))
    countries = event.get("countries") if isinstance(event.get("countries"), list) else []
    region = ", ".join(str(item).strip() for item in countries if str(item).strip()) or "Unknown region"
    event_id = event.get("id")
    leak_asn = event.get("leak_asn")
    description = "Cloudflare Radar BGP route leak event"
    if event_id is not None:
        description += f" {event_id}"
    if leak_asn is not None:
        description += f" involving leaking AS{leak_asn}"
    return {
        "signal": "bgp_route_leak",
        "region": region,
        "started": iso_utc(detected) if detected else None,
        "ended": None if event.get("finished") is False else iso_utc(parse_ts(event.get("max_ts")) or detected) if detected else None,
        "description": description,
        "reference": "",
        "event_id": event_id,
        "finished": event.get("finished"),
        "leak_asn": leak_asn,
        "countries": countries[:5],
        "peer_count": event.get("peer_count"),
        "prefix_count": event.get("prefix_count"),
        "origin_count": event.get("origin_count"),
        "leak_count": event.get("leak_count"),
    }


def item_sort_key(item):
    started = parse_ts(item.get("started"))
    signal_priority = {"outage": 2, "bgp_route_leak": 1, "traffic_anomaly": 0}.get(item.get("signal"), 0)
    return (
        signal_priority,
        started or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        item.get("region") or "",
    )


def latest_signal_at(items):
    timestamps = [parse_ts(item.get("started")) for item in items]
    timestamps = [item for item in timestamps if item is not None]
    return iso_utc(max(timestamps)) if timestamps else None


def status_from_items(items):
    if not items:
        return "normal"
    if any(item.get("ended") is None or item.get("finished") is False for item in items):
        return "disruption"
    return "advisory"


def combine_status(statuses):
    if "disruption" in statuses:
        return "disruption"
    if "advisory" in statuses:
        return "advisory"
    return "normal"


def signal_result(key, label, status, items, query, summary, *, available=True, error=None):
    return {
        "key": key,
        "label": label,
        "available": bool(available),
        "status": status,
        "item_count": len(items),
        "items": items[:MAX_ITEMS],
        "summary": summary,
        "latest_signal_at": latest_signal_at(items),
        "query": query,
        "error": error,
    }


def unavailable_signal_result(key, label, query, exc):
    return signal_result(
        key,
        label,
        "unavailable",
        [],
        query,
        f"{label} unavailable.",
        available=False,
        error=exc.__class__.__name__,
    )


def summarize_country(status, items):
    if status == "normal":
        return "No United States Internet outages or traffic anomalies detected."

    lead_region = items[0]["region"] if items else "an observed region"
    extra_count = max(0, len(items) - 1)
    lead_signal = "outage" if items and items[0].get("signal") == "outage" else "traffic anomaly"
    prefix = "United States Internet outage reported" if lead_signal == "outage" else "United States traffic anomaly detected"
    if status == "advisory":
        prefix = "Recent United States Internet outage reported" if lead_signal == "outage" else "Recent United States traffic anomaly detected"

    if extra_count == 0:
        return f"{prefix} in {lead_region}."
    return f"{prefix} in {lead_region} and {extra_count} more location(s)."


def summarize_asn(status, items, provider_display_name):
    label = str(provider_display_name or "Configured network").strip()
    if status == "normal":
        return f"No {label} traffic anomaly or {label}-involved route leak detected in the last {DEFAULT_DATE_RANGE}. Broad US outage context also normal."

    if status == "advisory":
        return f"Recent Cloudflare Radar Internet condition reported for {label}."
    return f"Cloudflare Radar Internet condition reported for {label}."


def checked_window(config):
    return {
        "date_range": config["CLOUDFLARE_RADAR_DATE_RANGE"],
        "recent_window_hours": RECENT_WINDOW_HOURS,
    }


def recent_route_leaks(events, now):
    recent = []
    cutoff = now - dt.timedelta(hours=RECENT_WINDOW_HOURS)
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("finished") is False:
            recent.append(event)
            continue
        timestamps = [parse_ts(event.get(key)) for key in ("detected_ts", "max_ts", "min_ts")]
        if any(ts and ts >= cutoff for ts in timestamps):
            recent.append(event)
    recent.sort(
        key=lambda item: parse_ts(item.get("detected_ts")) or parse_ts(item.get("max_ts")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        reverse=True,
    )
    return recent


def fetch_signal(key, label, query, fetcher, normal_summary, normalizer, now, *, raw_items):
    try:
        raw = raw_items(fetcher())
        if key == "bgp_route_leaks_asn":
            relevant = recent_route_leaks(raw, now)
        else:
            relevant = [item for item in raw if is_ongoing(item) or is_recent(item, now)]
            relevant.sort(
                key=lambda item: parse_ts(item.get("startDate")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                reverse=True,
            )
        items = [normalizer(item) for item in relevant]
        status = status_from_items(items)
        if status == "normal":
            summary = normal_summary
        elif status == "advisory":
            summary = f"Recent {label.lower()} detected."
        else:
            summary = f"Ongoing {label.lower()} detected."
        return signal_result(key, label, status, items, query, summary)
    except FETCH_ERRORS as exc:
        return unavailable_signal_result(key, label, query, exc)


def broad_us_signal_results(config, now, outages_fetcher=None, traffic_fetcher=None):
    outages_fetcher = outages_fetcher or fetch_outages
    traffic_fetcher = traffic_fetcher or fetch_traffic_anomalies
    token = config["CLOUDFLARE_API_TOKEN"]
    date_range = config["CLOUDFLARE_RADAR_DATE_RANGE"]
    timeout = config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"]
    limit = config["CLOUDFLARE_RADAR_LIMIT"]
    return {
        "us_outages": fetch_signal(
            "us_outages",
            "US outages",
            {"dateRange": date_range, "location": COUNTRY_LOCATION, "limit": limit},
            lambda: outages_fetcher(token, date_range, timeout, limit),
            "No United States Internet outages detected.",
            normalize_item,
            now,
            raw_items=response_annotations,
        ),
        "us_traffic_anomalies": fetch_signal(
            "us_traffic_anomalies",
            "US traffic anomalies",
            {"dateRange": date_range, "location": COUNTRY_LOCATION, "type": "LOCATION", "limit": limit},
            lambda: traffic_fetcher(token, date_range, timeout, limit),
            "No United States traffic anomalies detected.",
            normalize_anomaly,
            now,
            raw_items=response_traffic_anomalies,
        ),
    }


def build_v2_payload(config, query_meta, scope, signal_results, signals_checked):
    available_results = [item for item in signal_results.values() if item.get("available")]
    unavailable = [key for key, item in signal_results.items() if not item.get("available")]
    if not available_results:
        payload = base_payload("unavailable", "Unable to retrieve current Internet conditions.", query_meta, scope, signals_checked)
        payload["checked_window"] = checked_window(config)
        payload["signal_results"] = signal_results
        payload["degradation"] = {"partial": bool(unavailable), "unavailable_signals": unavailable}
        return payload

    items = []
    for result in available_results:
        items.extend(result.get("items", []))
    items.sort(key=item_sort_key, reverse=True)
    status = combine_status([item.get("status") for item in available_results])

    if query_meta["query_mode"] == "asn":
        summary = summarize_asn(status, items, query_meta["provider_display_name"])
        if status == "normal":
            label = query_meta["provider_display_name"]
            summary = f"No {label} traffic anomaly or {label}-involved route leak detected in the last {config['CLOUDFLARE_RADAR_DATE_RANGE']}. Broad US outage context also normal."
    else:
        summary = summarize_country(status, items)

    if unavailable and status == "normal":
        if query_meta["query_mode"] == "asn":
            label = query_meta["provider_display_name"]
            summary = f"No {label} traffic anomaly or {label}-involved route leak detected among completed Cloudflare checks in the last {config['CLOUDFLARE_RADAR_DATE_RANGE']}. Some Internet Conditions checks were unavailable."
        else:
            summary = f"No Internet disruption detected among completed Cloudflare checks in the last {config['CLOUDFLARE_RADAR_DATE_RANGE']}. Some Internet Conditions checks were unavailable."

    payload = base_payload(status, summary, query_meta, scope, signals_checked)
    payload["checked_window"] = checked_window(config)
    payload["signal_results"] = signal_results
    payload["degradation"] = {"partial": bool(unavailable), "unavailable_signals": unavailable}
    payload["items"] = items[:MAX_ITEMS]
    return payload


def build_country_payload(config, query_meta, now=None, outages_fetcher=None, traffic_fetcher=None):
    now = now or utc_now()
    return build_v2_payload(
        config,
        query_meta,
        COUNTRY_SCOPE,
        broad_us_signal_results(config, now, outages_fetcher=outages_fetcher, traffic_fetcher=traffic_fetcher),
        COUNTRY_SIGNALS_CHECKED,
    )


def build_asn_payload(config, query_meta, now=None, traffic_fetcher=None, route_leaks_fetcher=None, outages_fetcher=None, country_traffic_fetcher=None):
    now = now or utc_now()
    traffic_fetcher = traffic_fetcher or fetch_traffic_anomalies_by_asn
    route_leaks_fetcher = route_leaks_fetcher or fetch_bgp_route_leaks
    token = config["CLOUDFLARE_API_TOKEN"]
    date_range = config["CLOUDFLARE_RADAR_DATE_RANGE"]
    timeout = config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"]
    limit = config["CLOUDFLARE_RADAR_LIMIT"]
    asn = config["PRIME_OBSERVER_INTERNET_ASN"]
    label = query_meta["provider_display_name"]
    results = {
        "as_traffic_anomalies": fetch_signal(
            "as_traffic_anomalies",
            f"{label} traffic anomalies",
            {"asn": int(asn), "dateRange": date_range, "type": "AS", "limit": limit},
            lambda: traffic_fetcher(token, date_range, timeout, limit, asn),
            f"No {label} traffic anomalies detected.",
            normalize_anomaly,
            now,
            raw_items=response_traffic_anomalies,
        ),
        "bgp_route_leaks_asn": fetch_signal(
            "bgp_route_leaks_asn",
            f"{label}-involved BGP route leaks",
            {"involvedAsn": int(asn), "dateRange": date_range, "limit": limit},
            lambda: route_leaks_fetcher(token, date_range, timeout, limit, asn=asn),
            f"No {label}-involved BGP route leaks detected.",
            normalize_route_leak,
            now,
            raw_items=response_route_leaks,
        ),
    }
    results.update(broad_us_signal_results(config, now, outages_fetcher=outages_fetcher, traffic_fetcher=country_traffic_fetcher))
    return build_v2_payload(config, query_meta, asn_scope_label(query_meta), results, ASN_SIGNALS_CHECKED)


def build_payload(config, now=None, outages_fetcher=None, traffic_fetcher=None, asn_traffic_fetcher=None, route_leaks_fetcher=None):
    query_meta = requested_query_metadata(config)
    if query_meta["query_mode"] == "asn":
        return build_asn_payload(
            config,
            query_meta,
            now=now,
            traffic_fetcher=asn_traffic_fetcher,
            route_leaks_fetcher=route_leaks_fetcher,
            outages_fetcher=outages_fetcher,
            country_traffic_fetcher=traffic_fetcher,
        )

    return build_country_payload(
        config,
        query_meta,
        now=now,
        outages_fetcher=outages_fetcher,
        traffic_fetcher=traffic_fetcher,
    )


def unavailable_payload(query_meta=None):
    query_meta = query_meta or requested_query_metadata({})
    scope = asn_scope_label(query_meta) if query_meta["query_mode"] == "asn" else COUNTRY_SCOPE
    payload = base_payload(
        "unavailable",
        "Unable to retrieve current Internet conditions.",
        query_meta,
        scope,
        ASN_SIGNALS_CHECKED if query_meta["query_mode"] == "asn" else COUNTRY_SIGNALS_CHECKED,
    )
    payload["checked_window"] = {
        "date_range": DEFAULT_DATE_RANGE,
        "recent_window_hours": RECENT_WINDOW_HOURS,
    }
    return payload


def main():
    config = load_config()
    query_meta = requested_query_metadata(config)
    print_configuration_diagnostics(config, query_meta)
    if not config["CLOUDFLARE_API_TOKEN"]:
        write_json_atomic(unavailable_payload(query_meta))
        print("Cloudflare Radar token missing. Wrote unavailable summary to viz/internet_conditions.json.", file=sys.stderr)
        return 0

    try:
        payload = build_payload(config)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        write_json_atomic(unavailable_payload(query_meta))
        print(f"Cloudflare Radar fetch failed: {exc}", file=sys.stderr)
        return 0

    write_json_atomic(payload)
    print_result_diagnostics(payload)
    print(f"Wrote Internet Conditions to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
