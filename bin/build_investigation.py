#!/usr/bin/env python3
from pathlib import Path
import argparse
import csv
import datetime as dt
import json
import sys


BASE = Path("/Users/mbeason/prime-observer")
DATA_DIR = BASE / "data"
VIZ_DIR = BASE / "viz"
OUT = VIZ_DIR / "investigation.json"
INDEX_OUT = VIZ_DIR / "investigation_index.json"
NEXTDNS_SUMMARY = VIZ_DIR / "nextdns_summary.json"

TELEMETRY_PATTERN = "bakeoff_*.csv"
GATEWAY_HOST = "192.168.1.1"
WAN_HOSTS = {"1.1.1.1", "9.9.9.9"}

WAN_BAD = {"p95": 140.0, "jitter": 50.0, "loss": 1.0}
WAN_BAD_PERSISTENCE = 2
TURBULENCE_MIN_RAW_BAD = 4
HEAT_BIN_MINUTES = 15


def parse_ts(value):
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def utc_ts(value):
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def iso(value):
    if value is None:
        return None
    return value.isoformat()


def event_id(prefix, start, end):
    start_part = iso(start).replace("+00:00", "Z") if start is not None else "unknown"
    end_part = iso(end).replace("+00:00", "Z") if end is not None else "unknown"
    safe = "".join(ch if ch.isalnum() else "-" for ch in f"{start_part}_{end_part}")
    return f"{prefix}-{safe.strip('-').lower()}"


def parse_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def percentile(values, pct):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * (pct / 100.0)
    lower = int(pos)
    upper = min(lower + 1, len(vals) - 1)
    weight = pos - lower
    return vals[lower] + ((vals[upper] - vals[lower]) * weight)


def median(values):
    return percentile(values, 50)


def rounded(value, digits=1):
    if value is None:
        return None
    return round(value, digits)


def telemetry_files():
    return sorted(DATA_DIR.glob(TELEMETRY_PATTERN))


def row_to_sample(row, source_file):
    timestamp = parse_ts(row.get("ts"))
    if timestamp is None:
        return None

    host = (row.get("host") or "").strip()
    if host != GATEWAY_HOST and host not in WAN_HOSTS:
        return None

    p95 = parse_float(row.get("p95_ms"))
    if p95 is None:
        return None

    jitter = parse_float(row.get("jitter_ms"))
    loss = parse_float(row.get("loss_pct"))
    max_ms = parse_float(row.get("max_ms"))

    return {
        "ts": timestamp,
        "ts_utc": timestamp.astimezone(dt.timezone.utc),
        "source_file": source_file,
        "phase": ((row.get("phase_label") or "").strip().upper() or "UNKNOWN"),
        "host": host,
        "kind": "lan" if host == GATEWAY_HOST else "wan",
        "sent": parse_float(row.get("sent")),
        "received": parse_float(row.get("received")),
        "loss_pct": loss if loss is not None else 0.0,
        "avg_ms": parse_float(row.get("avg_ms")),
        "p50_ms": parse_float(row.get("p50_ms")),
        "p95_ms": p95,
        "max_ms": max_ms,
        "jitter_ms": jitter if jitter is not None else 0.0,
    }


def read_samples(window_start_utc, window_end_utc):
    samples = []
    sources = []

    for path in telemetry_files():
        matched = 0
        try:
            with path.open("r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sample = row_to_sample(row, path.name)
                    if sample is None:
                        continue
                    if window_start_utc <= sample["ts_utc"] <= window_end_utc:
                        samples.append(sample)
                        matched += 1
        except OSError as exc:
            print(f"Warning: skipped {path}: {exc}", file=sys.stderr)
            continue

        if matched:
            sources.append({"path": str(path.relative_to(BASE)), "rows": matched})

    return sorted(samples, key=lambda item: (item["ts_utc"], item["host"])), sources


def is_wan_bad(sample):
    return (
        sample["p95_ms"] > WAN_BAD["p95"]
        or sample["jitter_ms"] > WAN_BAD["jitter"]
        or sample["loss_pct"] > WAN_BAD["loss"]
    )


def mark_sustained_wan(samples):
    marked = []
    streaks = {}
    for sample in sorted(samples, key=lambda item: (item["phase"], item["ts_utc"], item["host"])):
        key = (sample["phase"], sample["host"])
        raw_bad = is_wan_bad(sample)
        streaks[key] = streaks.get(key, 0) + 1 if raw_bad else 0
        item = dict(sample)
        item["raw_bad"] = raw_bad
        item["sustained_bad"] = streaks[key] >= WAN_BAD_PERSISTENCE
        marked.append(item)
    return sorted(marked, key=lambda item: (item["ts_utc"], item["host"]))


def classify_buckets(wan_samples):
    buckets = {}
    bin_seconds = HEAT_BIN_MINUTES * 60

    for sample in wan_samples:
        bucket_start = int(sample["ts_utc"].timestamp() // bin_seconds) * bin_seconds
        bucket = buckets.setdefault(
            bucket_start,
            {
                "start": dt.datetime.fromtimestamp(bucket_start, tz=dt.timezone.utc),
                "end": dt.datetime.fromtimestamp(bucket_start + bin_seconds, tz=dt.timezone.utc),
                "samples": [],
            },
        )
        bucket["samples"].append(sample)

    out = []
    for bucket in buckets.values():
        rows = sorted(bucket["samples"], key=lambda item: item["ts_utc"])
        raw_run = 0
        max_raw_run = 0
        for row in rows:
            raw_run = raw_run + 1 if row.get("raw_bad") else 0
            max_raw_run = max(max_raw_run, raw_run)

        raw_bad = len([row for row in rows if row.get("raw_bad")])
        sustained_bad = len([row for row in rows if row.get("sustained_bad")])
        out.append({
            "start": iso(bucket["start"]),
            "end": iso(bucket["end"]),
            "sample_count": len(rows),
            "raw_bad_count": raw_bad,
            "sustained_bad_count": sustained_bad,
            "max_raw_bad_run": max_raw_run,
            "turbulence": sustained_bad == 0 and raw_bad >= TURBULENCE_MIN_RAW_BAD and max_raw_run < WAN_BAD_PERSISTENCE,
            "max_p95_ms": rounded(max((row["p95_ms"] for row in rows), default=None)),
            "max_jitter_ms": rounded(max((row["jitter_ms"] for row in rows), default=None)),
            "max_loss_pct": rounded(max((row["loss_pct"] for row in rows), default=None), 2),
        })

    return sorted(out, key=lambda item: item["start"])


def summarize(samples, kind):
    rows = [sample for sample in samples if sample["kind"] == kind]
    p95 = [sample["p95_ms"] for sample in rows]
    jitter = [sample["jitter_ms"] for sample in rows]
    loss = [sample["loss_pct"] for sample in rows]

    summary = {
        "sample_count": len(rows),
        "host_counts": host_counts(rows),
        "median_p95_ms": rounded(median(p95)),
        "max_p95_ms": rounded(max(p95), 1) if p95 else None,
        "p95_95th_ms": rounded(percentile(p95, 95)),
        "jitter_95th_ms": rounded(percentile(jitter, 95)),
        "max_loss_pct": rounded(max(loss), 2) if loss else None,
    }

    if kind == "wan":
        marked = mark_sustained_wan(rows)
        buckets = classify_buckets(marked)
        summary.update({
            "raw_bad_count": len([row for row in marked if row["raw_bad"]]),
            "sustained_bad_count": len([row for row in marked if row["sustained_bad"]]),
            "turbulence_bucket_count": len([bucket for bucket in buckets if bucket["turbulence"]]),
            "bad_bucket_count": len([bucket for bucket in buckets if bucket["sustained_bad_count"] > 0]),
        })
    else:
        summary.update({
            "elevated_p95_count": len([row for row in rows if row["p95_ms"] > 120.0]),
        })

    return summary


def host_counts(samples):
    counts = {}
    for sample in samples:
        counts[sample["host"]] = counts.get(sample["host"], 0) + 1
    return counts


def period_for(sample, start_utc, end_utc):
    if sample["ts_utc"] < start_utc:
        return "before"
    if sample["ts_utc"] > end_utc:
        return "after"
    return "during"


def period_payload(samples, name):
    wan = [sample for sample in samples if sample["kind"] == "wan"]
    marked_wan = mark_sustained_wan(wan)
    return {
        "period": name,
        "start": iso(min((sample["ts_utc"] for sample in samples), default=None)),
        "end": iso(max((sample["ts_utc"] for sample in samples), default=None)),
        "total_samples": len(samples),
        "wan": summarize(samples, "wan"),
        "lan": summarize(samples, "lan"),
        "wan_buckets": classify_buckets(marked_wan),
    }


def event_payload(event_id_value, event_type, title, start, end, period, evidence_window, details=None):
    return {
        "id": event_id_value,
        "type": event_type,
        "title": title,
        "start": iso(start),
        "end": iso(end),
        "period": period,
        "evidence_window": {
            "start": iso(evidence_window[0]),
            "end": iso(evidence_window[1]),
        },
        "details": details or {},
    }


def event_midpoint(event):
    start = utc_ts(event.get("start"))
    end = utc_ts(event.get("end"))
    if start is None or end is None:
        return None
    return start + ((end - start) / 2)


def evidence_window(event):
    window = event.get("evidence_window") if isinstance(event.get("evidence_window"), dict) else {}
    return utc_ts(window.get("start")), utc_ts(window.get("end"))


def overlaps(a_start, a_end, b_start, b_end):
    return (
        a_start is not None
        and a_end is not None
        and b_start is not None
        and b_end is not None
        and a_start <= b_end
        and b_start <= a_end
    )


def build_events(periods_payload, start_utc, end_utc, window_start, window_end):
    selected_id = event_id("requested", start_utc, end_utc)
    events = [
        event_payload(
            selected_id,
            "requested_window",
            "Requested investigation window",
            start_utc,
            end_utc,
            "during",
            (window_start, window_end),
            {"source": "cli_input"},
        )
    ]

    for period_name, period in periods_payload.items():
        for bucket in period.get("wan_buckets", []):
            if not (bucket.get("raw_bad_count") or bucket.get("sustained_bad_count") or bucket.get("turbulence")):
                continue
            bucket_start = utc_ts(bucket.get("start"))
            bucket_end = utc_ts(bucket.get("end"))
            if bucket_start is None or bucket_end is None:
                continue
            events.append(
                event_payload(
                    event_id("wan-bucket", bucket_start, bucket_end),
                    "wan_bucket_observation",
                    "WAN bucket observation",
                    bucket_start,
                    bucket_end,
                    period_name,
                    (bucket_start, bucket_end),
                    {
                        "sample_count": bucket.get("sample_count"),
                        "raw_bad_count": bucket.get("raw_bad_count"),
                        "sustained_bad_count": bucket.get("sustained_bad_count"),
                        "turbulence": bucket.get("turbulence"),
                        "max_raw_bad_run": bucket.get("max_raw_bad_run"),
                    },
                )
            )

    events.sort(key=lambda item: (item["start"] or "", item["end"] or "", item["id"]))
    return events


def navigation_payload(events):
    if not events:
        return {
            "first_event": None,
            "last_event": None,
            "events": {},
        }

    nav_events = {}
    for idx, event in enumerate(events):
        nav_events[event["id"]] = {
            "previous_event": events[idx - 1]["id"] if idx > 0 else None,
            "next_event": events[idx + 1]["id"] if idx < len(events) - 1 else None,
        }

    return {
        "first_event": events[0]["id"],
        "last_event": events[-1]["id"],
        "events": nav_events,
    }


def neighborhood_payload(events):
    neighborhoods = []
    for event in events:
        midpoint = event_midpoint(event)
        event_window_start, event_window_end = evidence_window(event)
        nearby = []

        for candidate in events:
            if candidate["id"] == event["id"]:
                continue
            candidate_midpoint = event_midpoint(candidate)
            candidate_window_start, candidate_window_end = evidence_window(candidate)
            minutes = (
                rounded(abs((candidate_midpoint - midpoint).total_seconds()) / 60.0, 2)
                if midpoint is not None and candidate_midpoint is not None
                else None
            )
            shared_window = overlaps(
                event_window_start,
                event_window_end,
                candidate_window_start,
                candidate_window_end,
            )
            signals = ["shared_investigation_membership"]
            if minutes is not None:
                signals.append("temporal_proximity")
            if shared_window:
                signals.append("shared_evidence_window")

            nearby.append({
                "event_id": candidate["id"],
                "minutes_from_event": minutes,
                "shared_investigation_membership": True,
                "shared_evidence_window": shared_window,
                "signals": signals,
            })

        nearby.sort(key=lambda item: (
            item["minutes_from_event"] if item["minutes_from_event"] is not None else float("inf"),
            item["event_id"],
        ))
        neighborhoods.append({
            "event_id": event["id"],
            "nearby_events": nearby,
        })

    return neighborhoods


def compact_sample(sample):
    return {
        "ts": sample["ts"].isoformat(),
        "source_file": sample["source_file"],
        "phase": sample["phase"],
        "host": sample["host"],
        "kind": sample["kind"],
        "p95_ms": rounded(sample["p95_ms"]),
        "jitter_ms": rounded(sample["jitter_ms"]),
        "loss_pct": rounded(sample["loss_pct"], 2),
        "max_ms": rounded(sample["max_ms"]),
    }


def key_samples(samples, limit=240):
    if len(samples) <= limit:
        selected = samples
    else:
        selected = []
        step = max(1, len(samples) // limit)
        for idx, sample in enumerate(samples):
            if idx % step == 0:
                selected.append(sample)
            if len(selected) >= limit:
                break

    return [compact_sample(sample) for sample in selected]


def dns_context(event_midpoint_utc):
    if not NEXTDNS_SUMMARY.exists():
        return {
            "available": False,
            "source_file": str(NEXTDNS_SUMMARY.relative_to(BASE)),
            "reason": "NextDNS summary file not found",
        }

    try:
        payload = json.loads(NEXTDNS_SUMMARY.read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "available": False,
            "source_file": str(NEXTDNS_SUMMARY.relative_to(BASE)),
            "reason": "NextDNS summary file was unreadable",
        }

    generated_at = utc_ts(payload.get("generated_at"))
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_entities = summary.get("top_entities") if isinstance(summary.get("top_entities"), list) else []

    return {
        "available": payload.get("status") == "ok" and bool(summary),
        "source_file": str(NEXTDNS_SUMMARY.relative_to(BASE)),
        "status": payload.get("status"),
        "generated_at": payload.get("generated_at"),
        "minutes_from_event_midpoint": (
            rounded(abs((generated_at - event_midpoint_utc).total_seconds()) / 60.0)
            if generated_at is not None
            else None
        ),
        "window": payload.get("window"),
        "profile_id_suffix": payload.get("profile_id_suffix"),
        "summary": {
            "total_queries": summary.get("total_queries"),
            "blocked_queries": summary.get("blocked_queries"),
            "block_rate_pct": summary.get("block_rate_pct"),
            "encrypted_rate_pct": summary.get("encrypted_rate_pct"),
            "top_blocked_reason": summary.get("top_blocked_reason"),
            "top_blocked_reason_queries": summary.get("top_blocked_reason_queries"),
            "top_entities": top_entities[:5],
        },
        "note": "NextDNS summary is the closest generated summary available locally; it is not a historical DNS log.",
    }


def build_investigation(start, end, pad_minutes):
    start_utc = utc_ts(start)
    end_utc = utc_ts(end)
    if start_utc is None or end_utc is None:
        raise ValueError("--start and --end must be ISO-8601 timestamps")
    if end_utc < start_utc:
        raise ValueError("--end must be greater than or equal to --start")

    pad = dt.timedelta(minutes=pad_minutes)
    window_start = start_utc - pad
    window_end = end_utc + pad
    samples, sources = read_samples(window_start, window_end)

    periods = {"before": [], "during": [], "after": []}
    for sample in samples:
        periods[period_for(sample, start_utc, end_utc)].append(sample)

    event_midpoint = start_utc + ((end_utc - start_utc) / 2)

    periods_payload = {
        name: period_payload(rows, name)
        for name, rows in periods.items()
    }
    events = build_events(periods_payload, start_utc, end_utc, window_start, window_end)

    return {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "id": event_id("investigation", start_utc, end_utc),
        "title": f"Investigation {iso(start_utc)} to {iso(end_utc)}",
        "status": "available" if samples else "no_samples",
        "input": {
            "start": start,
            "end": end,
            "pad_minutes": pad_minutes,
        },
        "event_window": {
            "start": iso(start_utc),
            "end": iso(end_utc),
            "duration_minutes": rounded((end_utc - start_utc).total_seconds() / 60.0, 2),
            "context_start": iso(window_start),
            "context_end": iso(window_end),
        },
        "thresholds": {
            "wan_bad_p95_ms": WAN_BAD["p95"],
            "wan_bad_jitter_ms": WAN_BAD["jitter"],
            "wan_bad_loss_pct": WAN_BAD["loss"],
            "wan_bad_persistence": WAN_BAD_PERSISTENCE,
            "turbulence_min_raw_bad": TURBULENCE_MIN_RAW_BAD,
            "bucket_minutes": HEAT_BIN_MINUTES,
        },
        "sources": {
            "telemetry_files": sources,
            "nextdns_summary": str(NEXTDNS_SUMMARY.relative_to(BASE)),
        },
        "periods": periods_payload,
        "events": events,
        "navigation": navigation_payload(events),
        "event_neighborhoods": neighborhood_payload(events),
        "timeline_samples": key_samples(samples),
        "dns_context": dns_context(event_midpoint),
        "notes": [
            "Prime Observer investigation output is factual telemetry evidence, not interpretation.",
            "LAN and WAN evidence are reported separately.",
            "DNS context uses only the existing generated public-safe NextDNS summary.",
        ],
    }


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def read_index(path):
    if not path.exists():
        return {"schema_version": 1, "investigations": []}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "investigations": []}
    if not isinstance(payload, dict):
        return {"schema_version": 1, "investigations": []}
    investigations = payload.get("investigations")
    if not isinstance(investigations, list):
        investigations = []
    return {
        "schema_version": payload.get("schema_version", 1),
        "generated_at": payload.get("generated_at"),
        "investigations": investigations,
    }


def catalog_entry(payload, out_path):
    return {
        "id": payload["id"],
        "title": payload["title"],
        "created_at": payload["generated_at"],
        "event_count": len(payload.get("events") or []),
        "status": payload.get("status", "available"),
        "path": str(out_path.relative_to(BASE)) if out_path.is_absolute() and out_path.is_relative_to(BASE) else str(out_path),
    }


def update_index(index_path, investigation_payload, out_path):
    index = read_index(index_path)
    entry = catalog_entry(investigation_payload, out_path)
    entries = [
        item for item in index.get("investigations", [])
        if isinstance(item, dict) and item.get("id") != entry["id"]
    ]
    entries.append(entry)
    entries.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""), reverse=True)
    updated = {
        "schema_version": index.get("schema_version", 1),
        "generated_at": investigation_payload["generated_at"],
        "investigations": entries,
    }
    write_json(index_path, updated)
    return updated


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a factual Prime Observer investigation JSON for a historical time window."
    )
    parser.add_argument("--start", required=True, help="Event start timestamp, ISO-8601")
    parser.add_argument("--end", required=True, help="Event end timestamp, ISO-8601")
    parser.add_argument("--pad-minutes", type=int, default=30, help="Context minutes before and after the event")
    parser.add_argument("--out", type=Path, default=OUT, help="Output JSON path")
    parser.add_argument("--index-out", type=Path, default=INDEX_OUT, help="Generated investigation catalog JSON path")
    parser.add_argument("--no-index", action="store_true", help="Skip updating the generated investigation catalog")
    args = parser.parse_args(argv)

    if args.pad_minutes < 0:
        parser.error("--pad-minutes must be >= 0")

    try:
        payload = build_investigation(args.start, args.end, args.pad_minutes)
    except ValueError as exc:
        parser.error(str(exc))

    write_json(args.out, payload)
    if not args.no_index:
        update_index(args.index_out, payload, args.out)
    print(f"Wrote investigation to {args.out}")
    if not args.no_index:
        print(f"Updated investigation index at {args.index_out}")
    print(
        "Telemetry samples: "
        f"{sum(period['total_samples'] for period in payload['periods'].values())}; "
        f"files: {len(payload['sources']['telemetry_files'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
