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
OUT = VIZ_DIR / "nextdns_summary.json"
ENV_FILE = BASE / ".env.nextdns"

API_BASE = "https://api.nextdns.io"
USER_AGENT = "PrimeObserver/0.4.0"
DEFAULT_WINDOW = "-24h"
DEFAULT_TIMEOUT_SECONDS = 8

REQUIRED_CONFIG = ("NEXTDNS_PROFILE_ID", "NEXTDNS_API_KEY")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(ts):
    return ts.isoformat().replace("+00:00", "Z")


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
    config = {}

    for key in REQUIRED_CONFIG:
        config[key] = config_value(key, file_values)

    config["NEXTDNS_WINDOW"] = config_value("NEXTDNS_WINDOW", file_values, DEFAULT_WINDOW)
    config["NEXTDNS_TIMEOUT_SECONDS"] = config_value(
        "NEXTDNS_TIMEOUT_SECONDS",
        file_values,
        str(DEFAULT_TIMEOUT_SECONDS),
    )

    try:
        config["NEXTDNS_TIMEOUT_SECONDS"] = float(config["NEXTDNS_TIMEOUT_SECONDS"])
    except ValueError:
        config["NEXTDNS_TIMEOUT_SECONDS"] = DEFAULT_TIMEOUT_SECONDS

    return config


def print_config_summary(config):
    api_key = config.get("NEXTDNS_API_KEY", "")
    profile = config.get("NEXTDNS_PROFILE_ID", "")
    present = "yes" if bool(api_key) else "no"
    suffix = profile_suffix(profile) or "missing"
    print(
        "NextDNS config: "
        f"profile suffix {suffix}; "
        f"API key present: {present}; "
        f"API key length: {len(api_key)}; "
        f"window: {config.get('NEXTDNS_WINDOW') or DEFAULT_WINDOW}; "
        f"timeout: {config.get('NEXTDNS_TIMEOUT_SECONDS')}s"
    )


def profile_suffix(profile_id):
    profile_id = (profile_id or "").strip()
    if not profile_id:
        return None
    return profile_id[-4:]


def summary_base(config, status):
    profile_id = config.get("NEXTDNS_PROFILE_ID", "")
    return {
        "schema_version": 1,
        "source": "nextdns",
        "profile_id_suffix": profile_suffix(profile_id),
        "window": config.get("NEXTDNS_WINDOW") or DEFAULT_WINDOW,
        "generated_at": iso_utc(utc_now()),
        "status": status,
    }


def failure_payload(config, kind, message):
    payload = summary_base(config, "unavailable")
    payload["summary"] = None
    payload["error"] = {
        "kind": kind,
        "message": message,
    }
    return payload


def write_json_atomic(payload):
    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(OUT)


def fetch_json(profile_id, api_key, endpoint, window, timeout, extra_params=None):
    params = {"from": window}
    if extra_params:
        params.update(extra_params)

    encoded_profile = urllib.parse.quote(profile_id, safe="")
    query = urllib.parse.urlencode(params)
    url = f"{API_BASE}/profiles/{encoded_profile}/analytics/{endpoint}?{query}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-Api-Key": api_key,
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read().decode("utf-8")
        return json.loads(body)


def data_list(response):
    data = response.get("data")
    return data if isinstance(data, list) else []


def count_by_key(rows, key):
    counts = {}
    for row in rows:
        name = str(row.get(key, "")).strip()
        if not name:
            name = "unknown"
        try:
            queries = int(row.get("queries") or 0)
        except (TypeError, ValueError):
            queries = 0
        counts[name] = counts.get(name, 0) + queries
    return counts


def pct(part, whole):
    if whole <= 0:
        return None
    return round((part / whole) * 100.0, 1)


def build_summary(config):
    profile_id = config["NEXTDNS_PROFILE_ID"].strip()
    api_key = config["NEXTDNS_API_KEY"].strip()
    window = config["NEXTDNS_WINDOW"].strip() or DEFAULT_WINDOW
    timeout = config["NEXTDNS_TIMEOUT_SECONDS"]

    status_rows = data_list(fetch_json(profile_id, api_key, "status", window, timeout))
    reasons_rows = data_list(fetch_json(profile_id, api_key, "reasons", window, timeout, {"limit": 5}))
    encryption_rows = data_list(fetch_json(profile_id, api_key, "encryption", window, timeout))

    status_counts = count_by_key(status_rows, "status")
    total_queries = sum(status_counts.values())
    blocked_queries = status_counts.get("blocked", 0)
    allowed_queries = status_counts.get("allowed", 0)
    default_queries = status_counts.get("default", 0)

    encrypted_queries = 0
    unencrypted_queries = 0
    for row in encryption_rows:
        try:
            queries = int(row.get("queries") or 0)
        except (TypeError, ValueError):
            queries = 0

        if row.get("encrypted") is True:
            encrypted_queries += queries
        elif row.get("encrypted") is False:
            unencrypted_queries += queries

    encryption_total = encrypted_queries + unencrypted_queries

    top_reasons = []
    for row in reasons_rows[:5]:
        name = str(row.get("name") or row.get("id") or "Unknown").strip()
        try:
            queries = int(row.get("queries") or 0)
        except (TypeError, ValueError):
            queries = 0
        top_reasons.append({"name": name, "queries": queries})

    payload = summary_base(config, "ok")
    payload["summary"] = {
        "total_queries": total_queries,
        "blocked_queries": blocked_queries,
        "allowed_queries": allowed_queries,
        "default_queries": default_queries,
        "other_status_queries": max(0, total_queries - blocked_queries - allowed_queries - default_queries),
        "block_rate_pct": pct(blocked_queries, total_queries),
        "encrypted_queries": encrypted_queries,
        "unencrypted_queries": unencrypted_queries,
        "encrypted_rate_pct": pct(encrypted_queries, encryption_total),
        "top_reasons": top_reasons,
    }
    payload["error"] = None
    return payload


def main():
    config = load_config()
    print_config_summary(config)
    missing = [key for key in REQUIRED_CONFIG if not config.get(key)]

    if missing:
        payload = failure_payload(
            config,
            "configuration",
            "Missing required NextDNS configuration: " + ", ".join(missing),
        )
        write_json_atomic(payload)
        print(f"Wrote unavailable NextDNS summary to {OUT} (missing configuration).")
        return 2

    try:
        payload = build_summary(config)
        write_json_atomic(payload)
        print(f"Wrote NextDNS summary to {OUT}.")
        return 0
    except urllib.error.HTTPError as exc:
        message = f"NextDNS API returned HTTP {exc.code}"
        payload = failure_payload(config, "http_error", message)
    except urllib.error.URLError:
        payload = failure_payload(config, "network_error", "Unable to reach NextDNS API")
    except TimeoutError:
        payload = failure_payload(config, "timeout", "NextDNS request timed out")
    except json.JSONDecodeError:
        payload = failure_payload(config, "invalid_response", "NextDNS API returned invalid JSON")
    except Exception as exc:
        payload = failure_payload(config, "unexpected_error", exc.__class__.__name__)

    write_json_atomic(payload)
    print(f"Wrote unavailable NextDNS summary to {OUT}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
