#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import datetime as dt
import json
import os
import random
import shlex
import socket
import ssl
import struct
import time
import urllib.parse


BASE = Path(__file__).resolve().parents[1]
VIZ_DIR = BASE / "viz"
OUT = VIZ_DIR / "application_experience.json"
ENV_FILE = BASE / ".env.application_experience"

SCHEMA_VERSION = 1
MODEL_VERSION = "prime_observer.application_experience.v1"
USER_AGENT = "PrimeObserver/0.9.0"
DEFAULT_DNS_HOSTNAME = "example.com"
DEFAULT_HTTPS_URL = "https://www.gstatic.com/generate_204"
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_STALE_AFTER_SECONDS = 5 * 60
DNS_SLOW_MS = 200.0
HTTPS_SLOW_MS = 1200.0


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_env_file(path: Path) -> dict[str, str]:
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
        if key.strip():
            values[key.strip()] = value.strip()
    return values


def config_value(key: str, file_values: dict[str, str], default: str = "") -> str:
    value = os.environ.get(key)
    if value is None:
        value = file_values.get(key, default)
    return str(value).strip()


def parse_float(value: str, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_config() -> dict[str, object]:
    file_values = parse_env_file(ENV_FILE)
    timeout = max(0.2, parse_float(config_value("PRIME_OBSERVER_APP_PROBE_TIMEOUT_SECONDS", file_values, str(DEFAULT_TIMEOUT_SECONDS)), DEFAULT_TIMEOUT_SECONDS))
    stale_after = max(30, parse_int(config_value("PRIME_OBSERVER_APP_PROBE_STALE_AFTER_SECONDS", file_values, str(DEFAULT_STALE_AFTER_SECONDS)), DEFAULT_STALE_AFTER_SECONDS))
    return {
        "dns_hostname": config_value("PRIME_OBSERVER_APP_PROBE_DNS_HOSTNAME", file_values, DEFAULT_DNS_HOSTNAME),
        "dns_primary_resolver": config_value("PRIME_OBSERVER_APP_PROBE_PRIMARY_RESOLVER", file_values, "45.90.28.134"),
        "dns_secondary_resolver": config_value("PRIME_OBSERVER_APP_PROBE_SECONDARY_RESOLVER", file_values, "45.90.30.134"),
        "https_url": config_value("PRIME_OBSERVER_APP_PROBE_HTTPS_URL", file_values, DEFAULT_HTTPS_URL),
        "timeout_seconds": timeout,
        "stale_after_seconds": stale_after,
    }


def safe_config_metadata(config: dict[str, object]) -> dict[str, object]:
    parsed = urllib.parse.urlparse(str(config.get("https_url") or ""))
    return {
        "dns_hostname": config.get("dns_hostname"),
        "primary_resolver_configured": bool(config.get("dns_primary_resolver")),
        "secondary_resolver_configured": bool(config.get("dns_secondary_resolver")),
        "https_endpoint_host": parsed.hostname,
        "https_endpoint_path": parsed.path or "/",
        "timeout_seconds": config.get("timeout_seconds"),
        "stale_after_seconds": config.get("stale_after_seconds"),
    }


def dns_query_name(hostname: str) -> bytes:
    labels = [label for label in hostname.strip(".").split(".") if label]
    return b"".join(bytes([len(label)]) + label.encode("ascii", "ignore") for label in labels) + b"\x00"


def build_dns_query(hostname: str, query_id: int) -> bytes:
    header = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    question = dns_query_name(hostname) + struct.pack("!HH", 1, 1)
    return header + question


def dns_rcode(response: bytes) -> str | None:
    if len(response) < 4:
        return None
    code = response[3] & 0x0F
    return {
        0: "NOERROR",
        1: "FORMERR",
        2: "SERVFAIL",
        3: "NXDOMAIN",
        4: "NOTIMP",
        5: "REFUSED",
    }.get(code, str(code))


def direct_dns_transaction(name: str, resolver: str, timeout: float, *, now: dt.datetime | None = None) -> dict[str, object]:
    checked_at = now or utc_now()
    if not resolver:
        return {
            "type": "direct_dns",
            "target_hostname": name,
            "resolver_endpoint": None,
            "checked_at": iso_utc(checked_at),
            "status": "unavailable",
            "success": False,
            "timeout": False,
            "failure_category": "resolver_not_configured",
        }
    query_id = random.randint(0, 65535)
    packet = build_dns_query(name, query_id)
    start = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(packet, (resolver, 53))
        response, endpoint = sock.recvfrom(512)
        latency = (time.perf_counter() - start) * 1000.0
        response_id = struct.unpack("!H", response[:2])[0] if len(response) >= 2 else None
        ok = response_id == query_id and dns_rcode(response) in {"NOERROR", "NXDOMAIN"}
        return {
            "type": "direct_dns",
            "target_hostname": name,
            "resolver_endpoint": endpoint[0] if endpoint else resolver,
            "checked_at": iso_utc(checked_at),
            "status": "ok" if ok else "failed",
            "success": bool(ok),
            "latency_ms": round(latency, 1),
            "timeout": False,
            "rcode": dns_rcode(response),
            "failure_category": None if ok else "dns_response_error",
        }
    except socket.timeout:
        return dns_failure("direct_dns", name, resolver, checked_at, "timeout", timeout=True)
    except OSError as exc:
        return dns_failure("direct_dns", name, resolver, checked_at, "network_error", str(exc))
    finally:
        sock.close()


def dns_failure(kind: str, name: str, resolver: str | None, checked_at: dt.datetime, category: str, message: str | None = None, *, timeout: bool = False) -> dict[str, object]:
    payload = {
        "type": kind,
        "target_hostname": name,
        "resolver_endpoint": resolver,
        "checked_at": iso_utc(checked_at),
        "status": "timeout" if timeout else "failed",
        "success": False,
        "timeout": timeout,
        "rcode": None,
        "failure_category": category,
    }
    if message:
        payload["error"] = message
    return payload


def system_dns_transaction(name: str, timeout: float, *, now: dt.datetime | None = None) -> dict[str, object]:
    checked_at = now or utc_now()
    start = time.perf_counter()
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
        return {
            "type": "system_dns",
            "target_hostname": name,
            "resolver_endpoint": "system",
            "checked_at": iso_utc(checked_at),
            "status": "ok",
            "success": True,
            "latency_ms": round((time.perf_counter() - start) * 1000.0, 1),
            "timeout": False,
            "rcode": None,
            "failure_category": None,
        }
    except socket.timeout:
        return dns_failure("system_dns", name, "system", checked_at, "timeout", timeout=True)
    except socket.gaierror as exc:
        return dns_failure("system_dns", name, "system", checked_at, "dns_error", str(exc))
    except OSError as exc:
        return dns_failure("system_dns", name, "system", checked_at, "network_error", str(exc))
    finally:
        socket.setdefaulttimeout(old_timeout)


def https_transaction(url: str, timeout: float, *, now: dt.datetime | None = None) -> dict[str, object]:
    checked_at = now or utc_now()
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    if parsed.scheme != "https" or not host:
        return https_failure(url, checked_at, "invalid_url")
    port = parsed.port or 443
    total_start = time.perf_counter()
    timings: dict[str, float] = {}
    sock = None
    tls_sock = None
    try:
        dns_start = time.perf_counter()
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        timings["dns_ms"] = elapsed_ms(dns_start)
        if not infos:
            return https_failure(url, checked_at, "dns_failure", timings=timings)

        family, socktype, proto, _canonname, sockaddr = infos[0]
        connect_start = time.perf_counter()
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        timings["tcp_connect_ms"] = elapsed_ms(connect_start)

        tls_start = time.perf_counter()
        context = ssl.create_default_context()
        tls_sock = context.wrap_socket(sock, server_hostname=host)
        sock = None
        timings["tls_ms"] = elapsed_ms(tls_start)

        request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {USER_AGENT}\r\nAccept: */*\r\nConnection: close\r\n\r\n"
        first_byte_start = time.perf_counter()
        tls_sock.sendall(request.encode("ascii"))
        first = tls_sock.recv(1)
        timings["time_to_first_byte_ms"] = elapsed_ms(first_byte_start)
        rest = tls_sock.recv(4096) if first else b""
        header = (first + rest).split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
        status = parse_http_status(header)
        total = elapsed_ms(total_start)
        ok = status is not None and 200 <= status < 400
        return {
            "target_url": sanitized_url(url),
            "checked_at": iso_utc(checked_at),
            "status": "ok" if ok else "http_error",
            "success": ok,
            "http_status": status,
            "timeout": False,
            "failure_category": None if ok else "http_error",
            "dns_duration_ms": round(timings.get("dns_ms", 0.0), 1),
            "tcp_connect_duration_ms": round(timings.get("tcp_connect_ms", 0.0), 1),
            "tls_duration_ms": round(timings.get("tls_ms", 0.0), 1),
            "time_to_first_byte_ms": round(timings.get("time_to_first_byte_ms", 0.0), 1),
            "total_duration_ms": round(total, 1),
        }
    except socket.gaierror as exc:
        return https_failure(url, checked_at, "dns_failure", str(exc), timings=timings, total_start=total_start)
    except socket.timeout:
        return https_failure(url, checked_at, "timeout", timings=timings, total_start=total_start, timeout=True)
    except ssl.SSLError as exc:
        return https_failure(url, checked_at, "tls_failure", str(exc), timings=timings, total_start=total_start)
    except OSError as exc:
        category = "tcp_failure" if "tcp_connect_ms" not in timings else "connection_failure"
        return https_failure(url, checked_at, category, str(exc), timings=timings, total_start=total_start)
    finally:
        if tls_sock is not None:
            tls_sock.close()
        if sock is not None:
            sock.close()


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def parse_http_status(status_line: str) -> int | None:
    parts = status_line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def sanitized_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path or "/", "", "", ""))


def https_failure(url: str, checked_at: dt.datetime, category: str, message: str | None = None, *, timings: dict[str, float] | None = None, total_start: float | None = None, timeout: bool = False) -> dict[str, object]:
    timings = timings or {}
    payload = {
        "target_url": sanitized_url(url),
        "checked_at": iso_utc(checked_at),
        "status": "timeout" if timeout else "failed",
        "success": False,
        "http_status": None,
        "timeout": timeout,
        "failure_category": category,
        "dns_duration_ms": round(timings.get("dns_ms", 0.0), 1) if "dns_ms" in timings else None,
        "tcp_connect_duration_ms": round(timings.get("tcp_connect_ms", 0.0), 1) if "tcp_connect_ms" in timings else None,
        "tls_duration_ms": round(timings.get("tls_ms", 0.0), 1) if "tls_ms" in timings else None,
        "time_to_first_byte_ms": None,
        "total_duration_ms": round(elapsed_ms(total_start), 1) if total_start is not None else None,
    }
    if message:
        payload["error"] = message
    return payload


def latency_summary(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return {"min_ms": None, "max_ms": None, "avg_ms": None}
    return {
        "min_ms": round(clean[0], 1),
        "max_ms": round(clean[-1], 1),
        "avg_ms": round(sum(clean) / len(clean), 1),
    }


def build_payload(config: dict[str, object], *, now: dt.datetime | None = None, dns_checker=direct_dns_transaction, system_dns_checker=system_dns_transaction, https_checker=https_transaction) -> dict[str, object]:
    generated_at = now or utc_now()
    hostname = str(config.get("dns_hostname") or DEFAULT_DNS_HOSTNAME)
    timeout = parse_float(str(config.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS), DEFAULT_TIMEOUT_SECONDS)
    dns_transactions = [
        {"role": "primary", **dns_checker(hostname, str(config.get("dns_primary_resolver") or ""), timeout, now=generated_at)},
        {"role": "secondary", **dns_checker(hostname, str(config.get("dns_secondary_resolver") or ""), timeout, now=generated_at)},
        {"role": "system", **system_dns_checker(hostname, timeout, now=generated_at)},
    ]
    https = https_checker(str(config.get("https_url") or DEFAULT_HTTPS_URL), timeout, now=generated_at)
    failures = [item for item in dns_transactions if not item.get("success")]
    if not https.get("success"):
        failures.append(https)
    slow_dns = [item for item in dns_transactions if item.get("success") and (item.get("latency_ms") or 0.0) > DNS_SLOW_MS]
    slow_https = bool(https.get("success") and (https.get("total_duration_ms") or 0.0) > HTTPS_SLOW_MS)
    if not failures and not slow_dns and not slow_https:
        status = "ok"
    elif failures:
        status = "degraded"
    else:
        status = "slow"
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "generated_at": iso_utc(generated_at),
        "status": status,
        "overall_status": status,
        "freshness": {"state": "fresh", "stale_after_seconds": config.get("stale_after_seconds")},
        "dns_transactions": dns_transactions,
        "https_transaction": https,
        "failure_counts": {
            "total": len(failures),
            "dns": len([item for item in dns_transactions if not item.get("success")]),
            "https": 0 if https.get("success") else 1,
            "timeouts": len([item for item in failures if item.get("timeout")]),
        },
        "latency_summaries": {
            "dns": latency_summary([parse_float(str(item.get("latency_ms")), 0.0) for item in dns_transactions if item.get("latency_ms") is not None]),
            "https_total_ms": https.get("total_duration_ms"),
        },
        "source": {"producer": "bin/fetch_application_experience.py", "network_calls": "collector_only"},
        "config": safe_config_metadata(config),
        "limitations": [],
    }


def write_json_atomic(payload: dict[str, object]) -> None:
    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(OUT)


def main() -> None:
    config = load_config()
    print(
        "Application Experience config: "
        f"DNS host {config['dns_hostname']}; "
        f"primary configured: {'yes' if config.get('dns_primary_resolver') else 'no'}; "
        f"secondary configured: {'yes' if config.get('dns_secondary_resolver') else 'no'}; "
        f"HTTPS host: {safe_config_metadata(config).get('https_endpoint_host')}; "
        f"timeout: {config['timeout_seconds']}s"
    )
    payload = build_payload(config)
    write_json_atomic(payload)
    print(f"Wrote application experience artifact to {OUT} with status {payload['status']}.")


if __name__ == "__main__":
    main()
