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
USER_AGENT = "PrimeObserver/0.8.1"
DEFAULT_WINDOW = "-24h"
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_EXPORT_DOMAIN_NAMES = "1"
DEFAULT_TOP_ENTITIES_LIMIT = 5
MAX_TOP_ENTITIES_LIMIT = 50

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
    config["NEXTDNS_EXPORT_DOMAIN_NAMES"] = config_value(
        "NEXTDNS_EXPORT_DOMAIN_NAMES",
        file_values,
        DEFAULT_EXPORT_DOMAIN_NAMES,
    )
    config["NEXTDNS_TOP_ENTITIES_LIMIT"] = config_value(
        "NEXTDNS_TOP_ENTITIES_LIMIT",
        file_values,
        str(DEFAULT_TOP_ENTITIES_LIMIT),
    )

    try:
        config["NEXTDNS_TIMEOUT_SECONDS"] = float(config["NEXTDNS_TIMEOUT_SECONDS"])
    except ValueError:
        config["NEXTDNS_TIMEOUT_SECONDS"] = DEFAULT_TIMEOUT_SECONDS

    config["NEXTDNS_EXPORT_DOMAIN_NAMES"] = parse_bool(config["NEXTDNS_EXPORT_DOMAIN_NAMES"])
    config["NEXTDNS_TOP_ENTITIES_LIMIT"] = parse_int(
        config["NEXTDNS_TOP_ENTITIES_LIMIT"],
        DEFAULT_TOP_ENTITIES_LIMIT,
        minimum=1,
        maximum=MAX_TOP_ENTITIES_LIMIT,
    )

    return config


def parse_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value, default, minimum=None, maximum=None):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


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
        f"timeout: {config.get('NEXTDNS_TIMEOUT_SECONDS')}s; "
        f"export domain names: {'yes' if config.get('NEXTDNS_EXPORT_DOMAIN_NAMES') else 'no'}; "
        f"top entities limit: {config.get('NEXTDNS_TOP_ENTITIES_LIMIT')}"
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


def query_count(row):
    try:
        return int(row.get("queries") or 0)
    except (TypeError, ValueError):
        return 0


def pct(part, whole):
    if whole <= 0:
        return None
    return round((part / whole) * 100.0, 1)


def share(part, whole):
    if whole <= 0:
        return None
    return round(part / whole, 4)


def optional_share(part, whole):
    if part is None:
        return None
    return share(part, whole)


def dominance_ratio(rows):
    if len(rows) < 2:
        return None

    first = rows[0]["count"]
    second = rows[1]["count"]
    if second <= 0:
        return None
    return round(first / second, 2)


def build_top_entities(rows, total_queries, export_names):
    entities = []

    for idx, row in enumerate(rows, 1):
        queries = query_count(row)

        entity = {
            "entity_type": "domain",
            "label": f"entity_{idx}",
            "name_redacted": not export_names,
            "count": queries,
            "share_of_total": share(queries, total_queries),
            "dominance_ratio": None,
        }

        if export_names:
            name = str(row.get("domain") or "").strip()
            if name:
                entity["name"] = name

        entities.append(entity)

    top_dominance_ratio = dominance_ratio(entities)
    if entities:
        entities[0]["dominance_ratio"] = top_dominance_ratio

    return entities, top_dominance_ratio


def top_domain_list(rows, denominator, export_names, limit):
    items = []
    for idx, row in enumerate(rows[:limit], 1):
        domain = str(row.get("domain") or "").strip()
        count = query_count(row)
        item = {
            "entity_type": "domain",
            "label": f"entity_{idx}",
            "name_redacted": bool(domain) and not export_names,
            "count": count,
            "share": share(count, denominator),
        }
        if export_names and domain:
            item["domain"] = domain
        items.append(item)
    return items


def top_domain_fact(rows, denominator, export_names):
    if not rows:
        return {
            "domain": None,
            "count": None,
            "share": None,
            "redacted": False,
        }

    row = rows[0]
    domain = str(row.get("domain") or "").strip()
    count = query_count(row)
    redacted = bool(domain) and not export_names

    return {
        "domain": domain if export_names and domain else None,
        "count": count,
        "share": share(count, denominator),
        "redacted": redacted,
    }


def optional_domains_rows(
    profile_id,
    api_key,
    window,
    timeout,
    limit,
    status=None,
    warning_kind="domains_unavailable",
    warning_message="NextDNS domains analytics unavailable",
):
    params = {"limit": limit}
    if status:
        params["status"] = status

    try:
        rows = data_list(
            fetch_json(
                profile_id,
                api_key,
                "domains",
                window,
                timeout,
                params,
            )
        )
        return rows, None
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ):
        return [], {
            "kind": warning_kind,
            "message": warning_message,
        }


def build_summary(config):
    profile_id = config["NEXTDNS_PROFILE_ID"].strip()
    api_key = config["NEXTDNS_API_KEY"].strip()
    window = config["NEXTDNS_WINDOW"].strip() or DEFAULT_WINDOW
    timeout = config["NEXTDNS_TIMEOUT_SECONDS"]
    export_domain_names = config["NEXTDNS_EXPORT_DOMAIN_NAMES"]
    top_entities_limit = config["NEXTDNS_TOP_ENTITIES_LIMIT"]

    status_rows = data_list(fetch_json(profile_id, api_key, "status", window, timeout))
    reasons_rows = data_list(fetch_json(profile_id, api_key, "reasons", window, timeout, {"limit": 5}))
    encryption_rows = data_list(fetch_json(profile_id, api_key, "encryption", window, timeout))
    warnings = []
    domains_rows, domains_warning = optional_domains_rows(
        profile_id,
        api_key,
        window,
        timeout,
        top_entities_limit,
    )
    blocked_domains_rows, blocked_domains_warning = optional_domains_rows(
        profile_id,
        api_key,
        window,
        timeout,
        top_entities_limit,
        status="blocked",
        warning_kind="blocked_domains_unavailable",
        warning_message="NextDNS blocked-domain analytics unavailable",
    )
    resolved_domains_rows, resolved_domains_warning = optional_domains_rows(
        profile_id,
        api_key,
        window,
        timeout,
        top_entities_limit,
        status="default",
        warning_kind="resolved_domains_unavailable",
        warning_message="NextDNS resolved-domain analytics unavailable",
    )

    for warning in (domains_warning, blocked_domains_warning, resolved_domains_warning):
        if warning:
            warnings.append(warning)

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
        queries = query_count(row)
        top_reasons.append({"name": name, "queries": queries})

    top_entities, top_entity_dominance_ratio = build_top_entities(
        domains_rows[:top_entities_limit],
        total_queries,
        export_domain_names,
    )
    top_queries = top_domain_list(
        domains_rows,
        total_queries,
        export_domain_names,
        top_entities_limit,
    )
    top_blocked_domains = top_domain_list(
        blocked_domains_rows,
        blocked_queries,
        export_domain_names,
        top_entities_limit,
    )
    top_entity_share = top_entities[0]["share_of_total"] if top_entities else None
    top_queried = top_domain_fact(domains_rows, total_queries, export_domain_names)
    top_blocked = top_domain_fact(blocked_domains_rows, blocked_queries, export_domain_names)
    top_resolved = top_domain_fact(resolved_domains_rows, default_queries, export_domain_names)
    top_blocked_share_of_total = optional_share(top_blocked["count"], total_queries)
    top_resolved_share_of_total = optional_share(top_resolved["count"], total_queries)
    top_reason = top_reasons[0] if top_reasons else {}

    payload = summary_base(config, "ok")
    payload["summary"] = {
        "queries": total_queries,
        "blocked": blocked_queries,
        "blocked_percent": pct(blocked_queries, total_queries),
        "encrypted_percent": pct(encrypted_queries, encryption_total),
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
        "dns_block_rate": share(blocked_queries, total_queries),
        "dns_encrypted_rate": share(encrypted_queries, encryption_total),
        "top_queried_domain": top_queried["domain"],
        "top_queried_domain_count": top_queried["count"],
        "top_queried_domain_share": top_queried["share"],
        "top_queried_domain_redacted": top_queried["redacted"],
        "top_resolved_domain": top_resolved["domain"],
        "top_resolved_domain_count": top_resolved["count"],
        "top_resolved_domain_share": top_resolved["share"],
        "top_resolved_domain_share_of_resolved": top_resolved["share"],
        "top_resolved_domain_share_of_total": top_resolved_share_of_total,
        "top_resolved_domain_redacted": top_resolved["redacted"],
        "top_blocked_domain": top_blocked["domain"],
        "top_blocked_domain_count": top_blocked["count"],
        "top_blocked_domain_share": top_blocked["share"],
        "top_blocked_domain_share_of_blocked": top_blocked["share"],
        "top_blocked_domain_share_of_total": top_blocked_share_of_total,
        "top_blocked_domain_redacted": top_blocked["redacted"],
        "top_blocked_reason": top_reason.get("name"),
        "top_blocked_reason_queries": top_reason.get("queries"),
        "top_queries": top_queries,
        "top_blocked": top_blocked_domains,
        "top_entity_share": top_entity_share,
        "top_entity_dominance_ratio": top_entity_dominance_ratio,
        "top_entities": top_entities,
    }
    payload["error"] = None
    if warnings:
        payload["warnings"] = warnings
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
