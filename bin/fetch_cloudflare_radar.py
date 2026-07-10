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


BASE = Path("/Users/mbeason/prime-observer")
VIZ_DIR = BASE / "viz"
OUT = VIZ_DIR / "internet_conditions.json"
ENV_FILE = BASE / ".env.cloudflare"

API_BASE = "https://api.cloudflare.com/client/v4"
OUTAGES_API_PATH = "/radar/annotations/outages"
TRAFFIC_ANOMALIES_API_PATH = "/radar/traffic_anomalies"
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
COUNTRY_SIGNALS_CHECKED = ["Outages", "Traffic anomalies"]
ASN_SIGNALS_CHECKED = ["Traffic anomalies"]
COUNTRY_PROVIDER_DISPLAY_NAME = "US Radar"


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
        "generated_at": iso_utc(utc_now()),
        "provider": "cloudflare_radar",
        "status": status,
        "summary": summary,
        "scope": dict(scope),
        "signals_checked": list(signals_checked),
        "items": [],
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


def is_ongoing(annotation):
    return parse_ts(annotation.get("endDate")) is None


def is_recent(annotation, now):
    cutoff = now - dt.timedelta(hours=RECENT_WINDOW_HOURS)
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
    reference = str(annotation.get("linkedUrl") or "").strip()
    return {
        "signal": "outage",
        "region": region_label(annotation),
        "started": iso_utc(started) if started else None,
        "description": description_label(annotation),
        "reference": reference,
    }


def normalize_anomaly(anomaly):
    started = parse_ts(anomaly.get("startDate"))
    return {
        "signal": "traffic_anomaly",
        "region": anomaly_region_label(anomaly),
        "started": iso_utc(started) if started else None,
        "description": anomaly_description(anomaly),
        "reference": "",
    }


def item_sort_key(item):
    started = parse_ts(item.get("started"))
    signal_priority = 1 if item.get("signal") == "outage" else 0
    return (
        signal_priority,
        started or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        item.get("region") or "",
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
        return f"No Cloudflare Radar traffic anomalies detected for {label}."

    if status == "advisory":
        return f"Recent Cloudflare Radar traffic anomaly detected for {label}."
    return f"Cloudflare Radar traffic anomaly detected for {label}."


def build_country_payload(config, query_meta, now=None, outages_fetcher=None, traffic_fetcher=None):
    now = now or utc_now()
    outages_fetcher = outages_fetcher or fetch_outages
    traffic_fetcher = traffic_fetcher or fetch_traffic_anomalies

    annotations = response_annotations(
        outages_fetcher(
            config["CLOUDFLARE_API_TOKEN"],
            config["CLOUDFLARE_RADAR_DATE_RANGE"],
            config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"],
            config["CLOUDFLARE_RADAR_LIMIT"],
        )
    )
    anomalies = response_traffic_anomalies(
        traffic_fetcher(
            config["CLOUDFLARE_API_TOKEN"],
            config["CLOUDFLARE_RADAR_DATE_RANGE"],
            config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"],
            config["CLOUDFLARE_RADAR_LIMIT"],
        )
    )

    relevant_annotations = [item for item in annotations if is_ongoing(item) or is_recent(item, now)]
    relevant_annotations.sort(
        key=lambda item: (
            parse_ts(item.get("startDate")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            region_label(item),
        ),
        reverse=True,
    )
    relevant_anomalies = [item for item in anomalies if is_ongoing(item) or is_recent(item, now)]
    relevant_anomalies.sort(
        key=lambda item: (
            parse_ts(item.get("startDate")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            anomaly_region_label(item),
        ),
        reverse=True,
    )

    items = [normalize_item(item) for item in relevant_annotations]
    items.extend(normalize_anomaly(item) for item in relevant_anomalies)
    items.sort(key=item_sort_key, reverse=True)
    items = items[:MAX_ITEMS]
    status = "normal"
    if items:
        status = "disruption" if any(is_ongoing(item) for item in relevant_annotations + relevant_anomalies) else "advisory"

    payload = base_payload(
        status,
        summarize_country(status, items),
        query_meta,
        COUNTRY_SCOPE,
        COUNTRY_SIGNALS_CHECKED,
    )
    payload["items"] = items
    return payload


def build_asn_payload(config, query_meta, now=None, traffic_fetcher=None):
    now = now or utc_now()
    traffic_fetcher = traffic_fetcher or fetch_traffic_anomalies_by_asn

    anomalies = response_traffic_anomalies(
        traffic_fetcher(
            config["CLOUDFLARE_API_TOKEN"],
            config["CLOUDFLARE_RADAR_DATE_RANGE"],
            config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"],
            config["CLOUDFLARE_RADAR_LIMIT"],
            config["PRIME_OBSERVER_INTERNET_ASN"],
        )
    )

    relevant_anomalies = [item for item in anomalies if is_ongoing(item) or is_recent(item, now)]
    relevant_anomalies.sort(
        key=lambda item: (
            parse_ts(item.get("startDate")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            anomaly_region_label(item),
        ),
        reverse=True,
    )

    items = [normalize_anomaly(item) for item in relevant_anomalies]
    items.sort(key=item_sort_key, reverse=True)
    items = items[:MAX_ITEMS]
    status = "normal"
    if items:
        status = "disruption" if any(is_ongoing(item) for item in relevant_anomalies) else "advisory"

    payload = base_payload(
        status,
        summarize_asn(status, items, query_meta["provider_display_name"]),
        query_meta,
        asn_scope_label(query_meta),
        ASN_SIGNALS_CHECKED,
    )
    payload["items"] = items
    return payload


def build_payload(config, now=None, outages_fetcher=None, traffic_fetcher=None, asn_traffic_fetcher=None):
    query_meta = requested_query_metadata(config)
    if query_meta["query_mode"] == "asn":
        try:
            return build_asn_payload(
                config,
                query_meta,
                now=now,
                traffic_fetcher=asn_traffic_fetcher,
            )
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
        ):
            fallback_meta = dict(query_meta)
            fallback_meta["fallback_used"] = True
            fallback_meta["provider_display_name"] = COUNTRY_PROVIDER_DISPLAY_NAME
            return build_country_payload(
                config,
                fallback_meta,
                now=now,
                outages_fetcher=outages_fetcher,
                traffic_fetcher=traffic_fetcher,
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
    return base_payload(
        "unavailable",
        "Unable to retrieve current Internet conditions.",
        query_meta,
        scope,
        ASN_SIGNALS_CHECKED if query_meta["query_mode"] == "asn" else COUNTRY_SIGNALS_CHECKED,
    )


def main():
    config = load_config()
    query_meta = requested_query_metadata(config)
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
    print(f"Wrote Internet Conditions to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
