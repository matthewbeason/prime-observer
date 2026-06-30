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
API_PATH = "/radar/annotations/outages"
USER_AGENT = "PrimeObserver/0.8.2"
DEFAULT_DATE_RANGE = "7d"
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_LIMIT = 10
RECENT_WINDOW_HOURS = 24


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
    }


def base_payload(status, summary):
    return {
        "schema_version": 1,
        "generated_at": iso_utc(utc_now()),
        "provider": "cloudflare_radar",
        "status": status,
        "summary": summary,
        "items": [],
    }


def write_json_atomic(payload):
    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with tmp.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(OUT)


def fetch_outages(api_token, date_range, timeout, limit):
    query = urllib.parse.urlencode(
        {
            "dateRange": date_range,
            "format": "json",
            "limit": limit,
        }
    )
    url = f"{API_BASE}{API_PATH}?{query}"
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


def response_annotations(payload):
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    annotations = result.get("annotations")
    return annotations if isinstance(annotations, list) else []


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


def normalize_item(annotation):
    started = parse_ts(annotation.get("startDate"))
    reference = str(annotation.get("linkedUrl") or "").strip()
    return {
        "region": region_label(annotation),
        "started": iso_utc(started) if started else None,
        "description": description_label(annotation),
        "reference": reference,
    }


def summarize(status, items):
    if status == "normal":
        return "No regional Internet disruptions detected."

    lead_region = items[0]["region"] if items else "an observed region"
    extra_count = max(0, len(items) - 1)
    prefix = "Regional Internet disruption reported"
    if status == "advisory":
        prefix = "Recent regional Internet disruption reported"

    if extra_count == 0:
        return f"{prefix} in {lead_region}."
    return f"{prefix} in {lead_region} and {extra_count} more location(s)."


def build_payload(config, now=None, fetcher=None):
    now = now or utc_now()
    fetcher = fetcher or fetch_outages

    annotations = response_annotations(
        fetcher(
            config["CLOUDFLARE_API_TOKEN"],
            config["CLOUDFLARE_RADAR_DATE_RANGE"],
            config["CLOUDFLARE_RADAR_TIMEOUT_SECONDS"],
            config["CLOUDFLARE_RADAR_LIMIT"],
        )
    )

    relevant = [item for item in annotations if is_ongoing(item) or is_recent(item, now)]
    relevant.sort(
        key=lambda item: (
            parse_ts(item.get("startDate")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            region_label(item),
        ),
        reverse=True,
    )

    items = [normalize_item(item) for item in relevant]
    status = "normal"
    if items:
        status = "disruption" if any(is_ongoing(item) for item in relevant) else "advisory"

    payload = base_payload(status, summarize(status, items))
    payload["items"] = items
    return payload


def unavailable_payload():
    return base_payload("unavailable", "Unable to retrieve current Internet conditions.")


def main():
    config = load_config()
    if not config["CLOUDFLARE_API_TOKEN"]:
        write_json_atomic(unavailable_payload())
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
        write_json_atomic(unavailable_payload())
        print(f"Cloudflare Radar fetch failed: {exc}", file=sys.stderr)
        return 0

    write_json_atomic(payload)
    print(f"Wrote Internet Conditions to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
