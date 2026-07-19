#!/usr/bin/env python3
from pathlib import Path
import csv
import datetime as dt
import json
from collections import defaultdict
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from target_metadata import (
    GATEWAY_HOST,
    is_gateway_probe,
    is_wan_probe,
    target_metadata,
)
from health_model import (
    ATTRIBUTION_CUT_MINUTES,
    HEAT_BIN_MINUTES,
    WAN_BAD,
    WAN_BAD_PERSISTENCE,
    bucket_start,
    is_turbulence_bucket,
    is_wan_bad,
    lan_elevation,
)
from observation_domain import (
    OBSERVATION_PROJECTION_MODEL_VERSION,
    build_attribution_observations,
    build_episode_observations,
    build_projection,
)
from investigation_model import (
    build_automatic_investigation,
    write_completed_investigation_history,
    write_if_changed as write_investigation_if_changed,
)
from build_operator_assistant_input import build_from_path as build_assistant_input_from_path
from build_operator_assistant_input import write_json as write_assistant_input_json

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"
VIZ_DIR  = BASE / "viz"
OUT      = VIZ_DIR / "latest.csv"
ATTRIBUTION_OUT = VIZ_DIR / "network_attribution.json"
OBSERVATIONS_OUT = VIZ_DIR / "observations.json"
DASHBOARD_HEALTH_OUT = VIZ_DIR / "dashboard_health.json"
INVESTIGATION_OUT = VIZ_DIR / "investigation.json"
OPERATOR_ASSISTANT_INPUT_OUT = VIZ_DIR / "operator_assistant_input.json"

WINDOW_HOURS = 24  # align with dashboard
WINDOW = dt.timedelta(hours=WINDOW_HOURS)

# Keep the historical bakeoff_*.csv naming for compatibility, but treat these
# files as Prime Observer telemetry history now that the provider bakeoff phase
# is over.
TELEMETRY_PATTERN = "bakeoff_*.csv"
BASELINE_FILE_COUNT = 10

TARGET_COLUMNS = ("target_label", "target_class")
BASELINE_COLUMNS = ("baseline_p95", "baseline_delta_pct", "baseline_sample_count")


def sanitize_field(s: str) -> str:
    if s is None:
        return ""
    return " | ".join(str(s).splitlines()).replace("\t", " ").strip()


def telemetry_files():
    return sorted(DATA_DIR.glob(TELEMETRY_PATTERN))


def newest_by_mtime():
    files = telemetry_files()
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def recent_baseline_files():
    files = telemetry_files()
    if not files:
        return []
    return files[-min(len(files), BASELINE_FILE_COUNT):]


def parse_ts(ts: str):
    try:
        return dt.datetime.fromisoformat(ts)
    except Exception:
        return None


def parse_float(value, fallback=None):
    try:
        return float(str(value).strip())
    except Exception:
        return fallback


def js_round(value):
    return int(value + 0.5)


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def compute_hourly_wan_baseline():
    """Return hourly WAN p95 medians and sample counts using recent telemetry history."""
    by_hour = defaultdict(list)
    used_files = []

    for src in recent_baseline_files():
        try:
            with src.open("r", newline="") as f:
                reader = csv.DictReader(f)
                row_count = 0

                for r in reader:
                    host = (r.get("host") or "").strip()
                    if target_metadata(host)["target_class"] != "internet_probe":
                        continue

                    t = parse_ts(r.get("ts", ""))
                    if t is None:
                        continue

                    try:
                        p95 = float((r.get("p95_ms") or "").strip())
                    except Exception:
                        continue

                    by_hour[t.hour].append(p95)
                    row_count += 1

                if row_count:
                    used_files.append(src.name)

        except Exception as exc:
            print(f"Warning: skipped baseline source {src.name}: {exc}")
            continue

    baseline = {}
    for hour, vals in by_hour.items():
        m = median(vals)
        if m is not None:
            baseline[hour] = m

    sample_counts = {hour: len(vals) for hour, vals in by_hour.items()}

    return baseline, sample_counts, used_files


def ensure_fieldnames(fieldnames):
    out = list(fieldnames or [])
    for col in TARGET_COLUMNS:
        if col not in out:
            out.append(col)
    for col in BASELINE_COLUMNS:
        if col not in out:
            out.append(col)
    return out


def apply_target_fields(row):
    host = (row.get("host") or "").strip()
    meta = target_metadata(host)
    row["target_label"] = (row.get("target_label") or meta["target_label"]).strip()
    row["target_class"] = (row.get("target_class") or meta["target_class"]).strip()
    return row


def apply_baseline_fields(row, timestamp, baseline_by_hour, baseline_sample_counts):
    host = (row.get("host") or "").strip()

    if target_metadata(host)["target_class"] != "internet_probe":
        row["baseline_p95"] = ""
        row["baseline_delta_pct"] = ""
        row["baseline_sample_count"] = ""
        return row

    baseline = baseline_by_hour.get(timestamp.hour)
    sample_count = baseline_sample_counts.get(timestamp.hour, "")

    try:
        current_p95 = float((row.get("p95_ms") or "").strip())
    except Exception:
        current_p95 = None

    if baseline is None:
        row["baseline_p95"] = ""
        row["baseline_delta_pct"] = ""
        row["baseline_sample_count"] = ""
        return row

    row["baseline_p95"] = f"{baseline:.1f}"
    row["baseline_sample_count"] = str(sample_count)

    if current_p95 is not None and baseline > 0:
        delta_pct = ((current_p95 - baseline) / baseline) * 100.0
        row["baseline_delta_pct"] = f"{delta_pct:.1f}"
    else:
        row["baseline_delta_pct"] = ""

    return row


def normalize_dashboard_sample(row):
    t = parse_ts((row.get("ts") or "").strip())
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    else:
        t = t.astimezone(dt.timezone.utc)

    host = (row.get("host") or "").strip()
    p95 = parse_float(row.get("p95_ms"), None)
    if not host or p95 is None:
        return None
    target = target_metadata(host)
    if not is_gateway_probe(host) and not is_wan_probe(host):
        return None

    return {
        "t": t,
        "host": host,
        "target_label": (row.get("target_label") or target["target_label"]).strip(),
        "target_class": (row.get("target_class") or target["target_class"]).strip(),
        "phase": ((row.get("phase_label") or "FIBER").strip().upper()),
        "p95": p95,
        "jitter": parse_float(row.get("jitter_ms"), 0.0),
        "loss": parse_float(row.get("loss_pct"), 0.0),
    }


def to_dashboard_series(rows):
    lan = {}
    wan = defaultdict(dict)

    for row in rows:
        sample = normalize_dashboard_sample(row)
        if sample is None:
            continue

        key = (sample["phase"], sample["t"])
        if sample["target_class"] == "gateway_probe":
            series = lan
        else:
            key = (sample["phase"], sample["target_class"], sample["t"])
            series = wan[sample["target_class"]]
        prev = series.get(key)
        if prev is None or sample["p95"] > prev["p95"]:
            series[key] = sample

    wan_series = []
    for values in wan.values():
        wan_series.extend(values.values())

    return (
        sorted(lan.values(), key=lambda d: d["t"]),
        sorted(wan_series, key=lambda d: (d["target_class"], d["t"])),
    )


def is_wan_bad_row(sample):
    return is_wan_bad(sample)


def mark_persistent_wan_bad(series, min_streak=WAN_BAD_PERSISTENCE):
    streaks = {}
    marked = []

    for sample in series:
        key = (sample.get("phase"), sample.get("target_class"))
        raw_bad = is_wan_bad_row(sample)
        streaks[key] = streaks.get(key, 0) + 1 if raw_bad else 0
        item = dict(sample)
        item["raw_bad"] = raw_bad
        item["is_bad"] = streaks[key] >= min_streak
        marked.append(item)

    return marked


def classify_buckets(wan_series):
    bin_seconds = HEAT_BIN_MINUTES * 60
    buckets = {}

    for sample in wan_series:
        start = bucket_start(sample["t"])
        key = (sample.get("phase"), sample.get("target_class"), start)
        obj = buckets.setdefault(key, {
            "phase": sample["phase"] or "FIBER",
            "target_class": sample.get("target_class") or "unknown_probe",
            "rows": [],
        })
        obj["rows"].append(sample)

    out = []
    for (_phase, target_class, start), bucket in buckets.items():
        rows = sorted(bucket["rows"], key=lambda d: d["t"])
        total = len(rows)
        bad = len([d for d in rows if d.get("is_bad")])
        raw_bad = len([d for d in rows if d.get("raw_bad")])
        p95_bad = len([d for d in rows if d.get("p95", 0.0) > WAN_BAD["p95"]])
        jitter_bad = len([d for d in rows if d.get("jitter", 0.0) > WAN_BAD["jitter"]])
        loss_bad = len([d for d in rows if d.get("loss", 0.0) > WAN_BAD["loss"]])
        raw_run = 0
        max_raw_run = 0

        for row in rows:
            raw_run = raw_run + 1 if row.get("raw_bad") else 0
            max_raw_run = max(max_raw_run, raw_run)

        out.append({
            "phase": bucket["phase"],
            "target_class": target_class,
            "t": dt.datetime.fromtimestamp(start, tz=dt.timezone.utc),
            "t2": dt.datetime.fromtimestamp(start + bin_seconds, tz=dt.timezone.utc),
            "total": total,
            "bad": bad,
            "raw_bad": raw_bad,
            "p95_bad": p95_bad,
            "jitter_bad": jitter_bad,
            "loss_bad": loss_bad,
            "max_raw_run": max_raw_run,
            "is_turbulence": is_turbulence_bucket(raw_bad, bad, max_raw_run),
        })

    return sorted(out, key=lambda d: d["t"])


def target_group_summary(series):
    groups = {}
    for target_class in ("internet_probe", "resolver_probe"):
        rows = [d for d in series if d.get("target_class") == target_class]
        buckets = classify_buckets(rows)
        bad_buckets = [b for b in buckets if b.get("bad")]
        turbulence_buckets = [b for b in buckets if b.get("is_turbulence")]
        groups[target_class] = {
            "sample_count": len(rows),
            "host_counts": host_counts(rows),
            "raw_bad_samples": len([d for d in rows if d.get("raw_bad")]),
            "sustained_bad_samples": len([d for d in rows if d.get("is_bad")]),
            "bad_buckets": len(bad_buckets),
            "turbulence_buckets": len(turbulence_buckets),
        }
    return groups


def attribution_evidence_counts(groups, recent_lan, lan_elevated):
    internet = groups.get("internet_probe", {}) or {}
    resolver = groups.get("resolver_probe", {}) or {}
    internet_bad_buckets = internet.get("bad_buckets", 0) or 0
    resolver_bad_buckets = resolver.get("bad_buckets", 0) or 0
    internet_turbulence_buckets = internet.get("turbulence_buckets", 0) or 0
    resolver_turbulence_buckets = resolver.get("turbulence_buckets", 0) or 0
    internet_sustained = internet.get("sustained_bad_samples", 0) or 0
    resolver_sustained = resolver.get("sustained_bad_samples", 0) or 0

    internet_degraded = bool(internet_sustained or internet_turbulence_buckets)
    resolver_degraded = bool(resolver_sustained or resolver_turbulence_buckets)
    wan_bad_buckets = internet_bad_buckets + resolver_bad_buckets
    wan_turbulence_buckets = internet_turbulence_buckets + resolver_turbulence_buckets
    wan_sustained_bad_samples = internet_sustained + resolver_sustained

    return {
        "internet_probe_degraded": internet_degraded,
        "resolver_probe_degraded": resolver_degraded,
        "lan_elevated": bool(lan_elevated),
        "internet_bad_buckets": internet_bad_buckets,
        "resolver_bad_buckets": resolver_bad_buckets,
        "internet_sustained_bad_samples": internet_sustained,
        "resolver_sustained_bad_samples": resolver_sustained,
        "internet_turbulence_buckets": internet_turbulence_buckets,
        "resolver_turbulence_buckets": resolver_turbulence_buckets,
        "wan_degraded_target_groups": len([v for v in (internet_degraded, resolver_degraded) if v]),
        "wan_bad_buckets": wan_bad_buckets,
        "wan_turbulence_buckets": wan_turbulence_buckets,
        "wan_sustained_bad_samples": wan_sustained_bad_samples,
        "wan_evidence_points": wan_sustained_bad_samples + (wan_bad_buckets * WAN_BAD_PERSISTENCE) + wan_turbulence_buckets,
        "lan_recent_samples": len(recent_lan),
        "lan_elevated_samples": len(lan_elevated),
    }


def host_counts(samples):
    counts = {}
    for sample in samples:
        host = sample.get("host")
        if host:
            counts[host] = counts.get(host, 0) + 1
    return counts


def target_group_fact(groups, recent_lan, lan_elevated, lan_bad):
    internet = groups.get("internet_probe", {})
    resolver = groups.get("resolver_probe", {})
    internet_bad = bool(internet.get("sustained_bad_samples") or internet.get("turbulence_buckets"))
    resolver_bad = bool(resolver.get("sustained_bad_samples") or resolver.get("turbulence_buckets"))
    facts = []

    if internet.get("sample_count") and resolver.get("sample_count"):
        if internet_bad and not resolver_bad:
            facts.append("Internet probes degraded while resolver probes remained healthy.")
        elif resolver_bad and not internet_bad:
            facts.append("Resolver probes degraded while internet probes remained healthy.")
        elif internet_bad and resolver_bad:
            facts.append("Both internet and resolver probes degraded.")
        else:
            facts.append("Internet and resolver probes both remained below degradation thresholds.")
    elif internet.get("sample_count"):
        facts.append("Internet probe evidence is available; resolver probe evidence is not yet available.")
    elif resolver.get("sample_count"):
        facts.append("Resolver probe evidence is available; internet probe evidence is not available.")
    else:
        facts.append("No WAN target-group evidence is available.")

    if lan_bad:
        facts.append("LAN/gateway also degraded.")
    elif recent_lan:
        facts.append(f"LAN/gateway elevated samples: {len(lan_elevated)}/{len(recent_lan)}.")
    else:
        facts.append("LAN/gateway evidence is unavailable.")

    return facts


def attribution_status(label):
    return {
        "No recent data": "no_recent_data",
        "Likely local (LAN / Wi\u2011Fi)": "likely_local",
        "Likely upstream (ISP / path)": "likely_upstream",
        "No network issue detected": "no_network_issue_detected",
        "Mixed evidence": "mixed_evidence",
        "Inconclusive": "inconclusive",
    }.get(label, "inconclusive")


def reporting_status(label):
    return {
        "No recent data": "inconclusive",
        "Likely local (LAN / Wi\u2011Fi)": "likely_local",
        "Likely upstream (ISP / path)": "likely_upstream",
        "No network issue detected": "no_issue_detected",
        "Mixed evidence": "mixed_evidence",
        "Inconclusive": "inconclusive",
    }.get(label, "inconclusive")


def reporting_confidence(confidence):
    return (confidence or "Low").lower()


def compute_recent_attribution(lan_series, wan_series_marked, generated_at):
    cut = generated_at - dt.timedelta(minutes=ATTRIBUTION_CUT_MINUTES)

    recent_wan = [d for d in wan_series_marked if d["t"] >= cut]
    recent_lan = [d for d in lan_series if d["t"] >= cut]
    recent_buckets = [b for b in classify_buckets(wan_series_marked) if b["t2"] >= cut]
    groups = target_group_summary(recent_wan)

    wan_bad = any(d.get("is_bad") for d in recent_wan)
    wan_turbulence = any(b.get("is_turbulence") for b in recent_buckets)

    lan = lan_elevation(recent_lan)
    lan_elevated = lan["elevated"]
    lan_elevated_rate = lan["elevated_rate"]
    lan_bad = lan["lan_bad"]
    group_facts = target_group_fact(groups, recent_lan, lan_elevated, lan_bad)
    evidence_counts = attribution_evidence_counts(groups, recent_lan, lan_elevated)

    label = "Inconclusive"
    confidence = "Low"
    why = "Signals are mixed across LAN and WAN."

    if not recent_wan and not recent_lan:
        label = "No recent data"
        confidence = "Low"
        why = "No LAN or WAN samples in the last 15 minutes."
    elif lan_bad and (wan_bad or wan_turbulence):
        wan_points = evidence_counts["wan_evidence_points"]
        lan_points = evidence_counts["lan_elevated_samples"]
        wan_groups = evidence_counts["wan_degraded_target_groups"]
        wan_dominates = wan_points >= max(lan_points * 2, WAN_BAD_PERSISTENCE) or (
            wan_groups >= 2 and evidence_counts["wan_bad_buckets"] >= len(lan_elevated)
        )
        lan_dominates = lan_points >= max(3, wan_points * 1.5) and lan_elevated_rate >= 0.5
        if wan_dominates:
            label = "Likely upstream (ISP / path)"
            confidence = "Medium"
            why = (
                f"WAN evidence spans {wan_groups} target group(s) with {evidence_counts['wan_bad_buckets']} sustained bad bucket(s); "
                f"LAN has {len(lan_elevated)}/{len(recent_lan)} elevated sample(s)."
            )
        elif lan_dominates:
            label = "Likely local (LAN / Wi\u2011Fi)"
            confidence = "Medium"
            why = (
                f"LAN has {len(lan_elevated)}/{len(recent_lan)} elevated sample(s) and materially exceeds WAN evidence counts."
            )
        else:
            label = "Mixed evidence"
            confidence = "Medium"
            why = (
                f"WAN evidence and LAN elevation are both present: {evidence_counts['wan_bad_buckets']} WAN sustained bad bucket(s), "
                f"{len(lan_elevated)}/{len(recent_lan)} LAN elevated sample(s)."
            )
    elif lan_bad and not wan_bad and not wan_turbulence:
        label = "Likely local (LAN / Wi\u2011Fi)"
        confidence = "High"
        why = f"LAN is elevated ({len(lan_elevated)}/{len(recent_lan)} samples, {js_round(lan_elevated_rate * 100)}%) while WAN remains stable."
    elif not lan_bad and (wan_bad or wan_turbulence):
        label = "Likely upstream (ISP / path)"
        confidence = "High" if wan_bad else "Medium"
        why = (
            f"WAN shows sustained degradation while LAN stays below local threshold ({len(lan_elevated)}/{len(recent_lan) or 0} elevated)."
            if wan_bad
            else f"WAN shows turbulence while LAN stays below local threshold ({len(lan_elevated)}/{len(recent_lan) or 0} elevated)."
        )
    elif not lan_bad and not wan_bad and not wan_turbulence:
        label = "No network issue detected"
        confidence = "High"
        why = "LAN and WAN both look stable in the last 15 minutes."

    return {
        "attribution_status": attribution_status(label),
        "attribution_label": label,
        "attribution_confidence": confidence,
        "attribution_evidence": {
            "summary": why,
            "target_group_facts": group_facts,
            "target_groups": groups,
            "classification_counts": evidence_counts,
            **evidence_counts,
            "internet_probe_summary": groups.get("internet_probe"),
            "resolver_probe_summary": groups.get("resolver_probe"),
            "lookback_minutes": ATTRIBUTION_CUT_MINUTES,
            "lan_recent_samples": len(recent_lan),
            "wan_recent_samples": len(recent_wan),
            "lan_elevated_samples": len(lan_elevated),
            "lan_elevated_rate_pct": round(lan_elevated_rate * 100, 1),
            "lan_bad": lan_bad,
            "wan_bad": wan_bad,
            "wan_turbulence": wan_turbulence,
            "wan_recent_bad_samples": len([d for d in recent_wan if d.get("is_bad")]),
            "wan_recent_raw_bad_samples": len([d for d in recent_wan if d.get("raw_bad")]),
            "turbulence_buckets": len([b for b in recent_buckets if b.get("is_turbulence")]),
        },
    }


def find_sustained_wan_incidents(wan_series_marked):
    incidents = []
    run = []

    for sample in wan_series_marked:
        if sample.get("raw_bad"):
            run.append(sample)
            continue

        if len(run) >= WAN_BAD_PERSISTENCE:
            incidents.append(run)
        run = []

    if len(run) >= WAN_BAD_PERSISTENCE:
        incidents.append(run)

    return incidents


def lan_evidence_for_interval(lan_series, start, end):
    samples = [d for d in lan_series if start <= d["t"] <= end]
    return lan_elevation(samples)


def classify_incident(run, lan_series):
    start = run[0]["t"]
    end = run[-1]["t"]
    target_class = run[0].get("target_class") or "unknown_probe"
    lan = lan_evidence_for_interval(lan_series, start, end)
    evidence = [f"{target_class} degradation"]

    if lan["lan_bad"]:
        status = "likely_local"
        label = "Likely local (LAN / Wi-Fi)"
        confidence = "high"
        evidence.append("local gateway persistently degraded")
    elif lan["lan_stable"]:
        status = "likely_upstream"
        label = "Likely upstream (ISP / path)"
        confidence = "high" if len(lan["samples"]) >= 2 else "medium"
        evidence.append("local gateway stable")
    elif not lan["samples"]:
        status = "inconclusive"
        label = "Inconclusive"
        confidence = "low"
        evidence.append("local gateway evidence unavailable")
    else:
        status = "inconclusive"
        label = "Inconclusive"
        confidence = "low"
        evidence.append("local gateway evidence mixed or insufficient")

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "status": status,
        "label": label,
        "evidence": evidence,
        "confidence": confidence,
        "metrics": {
            "wan_samples": len(run),
            "wan_raw_bad_samples": len([d for d in run if d.get("raw_bad")]),
            "wan_sustained_bad_samples": len([d for d in run if d.get("is_bad")]),
            "lan_samples": len(lan["samples"]),
            "lan_elevated_samples": len(lan["elevated"]),
            "lan_elevated_rate_pct": round(lan["elevated_rate"] * 100, 1),
            "target_class": target_class,
            "target_hosts": host_counts(run),
        },
    }


def compute_window_attribution(incidents, lan_series, wan_series_marked):
    if not incidents:
        return {
            "status": "no_issue_detected",
            "label": "No network issue detected",
            "confidence": "high",
            "evidence": ["no sustained WAN target-group degradation intervals"],
            "metrics": {
                "incident_count": 0,
                "likely_upstream_incidents": 0,
                "likely_local_incidents": 0,
                "inconclusive_incidents": 0,
                "lan_samples": len(lan_series),
                "wan_samples": len(wan_series_marked),
            },
        }

    upstream = len([d for d in incidents if d["status"] == "likely_upstream"])
    local = len([d for d in incidents if d["status"] == "likely_local"])
    inconclusive = len([d for d in incidents if d["status"] == "inconclusive"])

    evidence = [f"{len(incidents)} sustained WAN target-group incident(s)"]
    if upstream:
        evidence.append(f"{upstream} incident(s) with stable local gateway")
    if local:
        evidence.append(f"{local} incident(s) with persistent local gateway degradation")
    if inconclusive:
        evidence.append(f"{inconclusive} incident(s) with mixed or insufficient local evidence")

    if upstream and not local and not inconclusive:
        status = "likely_upstream"
        label = "Likely upstream (ISP / path)"
        confidence = "high"
    elif upstream and not local:
        status = "likely_upstream"
        label = "Likely upstream (ISP / path)"
        confidence = "medium"
    elif local and not upstream and not inconclusive:
        status = "likely_local"
        label = "Likely local (LAN / Wi-Fi)"
        confidence = "high"
    elif local and not upstream:
        status = "likely_local"
        label = "Likely local (LAN / Wi-Fi)"
        confidence = "medium"
    else:
        status = "inconclusive"
        label = "Inconclusive"
        confidence = "medium" if (upstream or local) else "low"

    return {
        "status": status,
        "label": label,
        "confidence": confidence,
        "evidence": evidence,
        "metrics": {
            "incident_count": len(incidents),
            "likely_upstream_incidents": upstream,
            "likely_local_incidents": local,
            "inconclusive_incidents": inconclusive,
            "lan_samples": len(lan_series),
            "wan_samples": len(wan_series_marked),
        },
    }


def compute_network_attribution(rows, generated_at):
    lan_series, wan_series = to_dashboard_series(rows)
    wan_series_marked = mark_persistent_wan_bad(wan_series)
    current = compute_recent_attribution(lan_series, wan_series_marked, generated_at)
    incident_runs = find_sustained_wan_incidents(wan_series_marked)
    incidents = [classify_incident(run, lan_series) for run in incident_runs]
    window_attribution = compute_window_attribution(incidents, lan_series, wan_series_marked)
    all_groups = target_group_summary(wan_series_marked)

    current_label = current["attribution_label"]
    current_confidence = current["attribution_confidence"]
    current_summary = current["attribution_evidence"]["summary"]

    window_start = min((d["t"] for d in (lan_series + wan_series_marked)), default=None)
    window_end = max((d["t"] for d in (lan_series + wan_series_marked)), default=None)

    return {
        "attribution_status": current["attribution_status"],
        "attribution_label": current_label,
        "attribution_confidence": current_confidence,
        "attribution_evidence": current["attribution_evidence"],
        "current_attribution": {
            "status": reporting_status(current_label),
            "label": current_label,
            "confidence": reporting_confidence(current_confidence),
            "evidence": [current_summary],
            "metrics": {
                k: v
                for k, v in current["attribution_evidence"].items()
                if k != "summary"
            },
        },
        "window_attribution": window_attribution,
        "target_groups": all_groups,
        "internet_probe_summary": all_groups.get("internet_probe"),
        "resolver_probe_summary": all_groups.get("resolver_probe"),
        "incidents": incidents,
        "generated_at": generated_at.isoformat(),
        "observation_window": {
            "hours": WINDOW_HOURS,
            "start": window_start.isoformat() if window_start else None,
            "end": window_end.isoformat() if window_end else None,
            "lan_samples": len(lan_series),
            "wan_samples": len(wan_series_marked),
            "internet_probe_samples": all_groups.get("internet_probe", {}).get("sample_count", 0),
            "resolver_probe_samples": all_groups.get("resolver_probe", {}).get("sample_count", 0),
        },
    }


def camelize_classification_counts(counts):
    return {
        "internetProbeDegraded": counts.get("internet_probe_degraded", False),
        "resolverProbeDegraded": counts.get("resolver_probe_degraded", False),
        "lanElevated": counts.get("lan_elevated", False),
        "internetBadBuckets": counts.get("internet_bad_buckets", 0),
        "resolverBadBuckets": counts.get("resolver_bad_buckets", 0),
        "internetSustainedBadSamples": counts.get("internet_sustained_bad_samples", 0),
        "resolverSustainedBadSamples": counts.get("resolver_sustained_bad_samples", 0),
        "internetTurbulenceBuckets": counts.get("internet_turbulence_buckets", 0),
        "resolverTurbulenceBuckets": counts.get("resolver_turbulence_buckets", 0),
        "wanDegradedTargetGroups": counts.get("wan_degraded_target_groups", 0),
        "wanBadBuckets": counts.get("wan_bad_buckets", 0),
        "wanTurbulenceBuckets": counts.get("wan_turbulence_buckets", 0),
        "wanSustainedBadSamples": counts.get("wan_sustained_bad_samples", 0),
        "wanEvidencePoints": counts.get("wan_evidence_points", 0),
        "lanRecentSamples": counts.get("lan_recent_samples", 0),
        "lanElevatedSamples": counts.get("lan_elevated_samples", 0),
    }


def dashboard_bucket(bucket, generated_at):
    recent_cut = generated_at - dt.timedelta(minutes=60)
    return {
        "phase": bucket.get("phase"),
        "targetClass": bucket.get("target_class"),
        "t": bucket["t"].isoformat(),
        "t2": bucket["t2"].isoformat(),
        "total": bucket.get("total", 0),
        "bad": bucket.get("bad", 0),
        "rawBad": bucket.get("raw_bad", 0),
        "p95Bad": bucket.get("p95_bad", 0),
        "jitterBad": bucket.get("jitter_bad", 0),
        "lossBad": bucket.get("loss_bad", 0),
        "rate": (bucket.get("bad", 0) / bucket.get("total", 0)) if bucket.get("total") else 0,
        "rawRate": (bucket.get("raw_bad", 0) / bucket.get("total", 0)) if bucket.get("total") else 0,
        "maxRawRun": bucket.get("max_raw_run", 0),
        "isBadBucket": bucket.get("bad", 0) > 0,
        "isTurbulence": bucket.get("is_turbulence", False),
        "recent": bucket["t2"] >= recent_cut,
        "semanticSource": "dashboard_health",
    }


def empty_dashboard_group():
    return {
        "total": 0,
        "bad": 0,
        "rawBad": 0,
        "p95Bad": 0,
        "jitterBad": 0,
        "lossBad": 0,
        "maxRawRun": 0,
        "isBadBucket": False,
        "isTurbulence": False,
        "semanticSource": "dashboard_health",
    }


def lan_bucket_evidence(lan_series, start, end):
    samples = [d for d in lan_series if start <= d["t"] < end]
    lan = lan_elevation(samples)
    return {
        "lanElevatedSamples": len(lan["elevated"]),
        "lanSamples": len(samples),
        "lanElevatedRatePct": round(lan["elevated_rate"] * 100, 1),
        "lanBad": lan["lan_bad"],
        "lanStable": lan["lan_stable"],
    }


def build_composite_dashboard_buckets(wan_buckets, lan_series, generated_at):
    composites = {}
    for bucket in wan_buckets:
        key = (bucket["phase"], bucket["t"])
        obj = composites.setdefault(key, {
            "phase": bucket["phase"],
            "targetClass": "composite_wan",
            "t": bucket["t"],
            "t2": bucket["t2"],
            "groups": {},
            "total": 0,
            "bad": 0,
            "rawBad": 0,
            "p95Bad": 0,
            "jitterBad": 0,
            "lossBad": 0,
            "maxRawRun": 0,
        })
        group = dashboard_bucket(bucket, generated_at)
        obj["groups"][bucket["target_class"]] = group
        obj["total"] += group["total"]
        obj["bad"] += group["bad"]
        obj["rawBad"] += group["rawBad"]
        obj["p95Bad"] += group["p95Bad"]
        obj["jitterBad"] += group["jitterBad"]
        obj["lossBad"] += group["lossBad"]
        obj["maxRawRun"] = max(obj["maxRawRun"], group["maxRawRun"])

    out = []
    for bucket in composites.values():
        bucket["groups"].setdefault("internet_probe", empty_dashboard_group())
        bucket["groups"].setdefault("resolver_probe", empty_dashboard_group())
        bucket["isBadBucket"] = bool(
            bucket["groups"]["internet_probe"]["isBadBucket"]
            or bucket["groups"]["resolver_probe"]["isBadBucket"]
        )
        bucket["isTurbulence"] = not bucket["isBadBucket"] and bool(
            bucket["groups"]["internet_probe"]["isTurbulence"]
            or bucket["groups"]["resolver_probe"]["isTurbulence"]
        )
        bucket["rate"] = bucket["bad"] / bucket["total"] if bucket["total"] else 0
        bucket["rawRate"] = bucket["rawBad"] / bucket["total"] if bucket["total"] else 0
        bucket["recent"] = bucket["t2"] >= (generated_at - dt.timedelta(minutes=60))
        bucket["selectedEvidence"] = {
            "internetSustainedBadSamples": bucket["groups"]["internet_probe"]["bad"],
            "internetRawBadSamples": bucket["groups"]["internet_probe"]["rawBad"],
            "internetSamples": bucket["groups"]["internet_probe"]["total"],
            "resolverSustainedBadSamples": bucket["groups"]["resolver_probe"]["bad"],
            "resolverRawBadSamples": bucket["groups"]["resolver_probe"]["rawBad"],
            "resolverSamples": bucket["groups"]["resolver_probe"]["total"],
            **lan_bucket_evidence(lan_series, bucket["t"], bucket["t2"]),
        }
        bucket["semanticSource"] = "dashboard_health"
        bucket["t"] = bucket["t"].isoformat()
        bucket["t2"] = bucket["t2"].isoformat()
        out.append(bucket)
    return sorted(out, key=lambda item: item["t"])


def build_dashboard_health(rows, attribution, generated_at):
    lan_series, wan_series = to_dashboard_series(rows)
    wan_series_marked = mark_persistent_wan_bad(wan_series)
    wan_buckets = classify_buckets(wan_series_marked)
    window_groups = target_group_summary(wan_series_marked)
    window_lan = lan_elevation(lan_series)
    window_counts = attribution_evidence_counts(window_groups, lan_series, window_lan["elevated"])
    window_start = min((d["t"] for d in (lan_series + wan_series_marked)), default=None)
    window_end = max((d["t"] for d in (lan_series + wan_series_marked)), default=None)
    return {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "model_version": "prime_observer.dashboard_health.v1",
        "dashboard_window": {
            "hours": WINDOW_HOURS,
            "start": window_start.isoformat() if window_start else None,
            "end": window_end.isoformat() if window_end else None,
            "lan_samples": len(lan_series),
            "wan_samples": len(wan_series_marked),
        },
        "wan_samples": [
            {
                "ts": sample["t"].isoformat(),
                "phase": sample.get("phase"),
                "host": sample.get("host"),
                "targetClass": sample.get("target_class"),
                "rawBad": sample.get("raw_bad", False),
                "isBad": sample.get("is_bad", False),
            }
            for sample in wan_series_marked
        ],
        "wan_target_group_buckets": [dashboard_bucket(bucket, generated_at) for bucket in wan_buckets],
        "composite_wan_buckets": build_composite_dashboard_buckets(wan_buckets, lan_series, generated_at),
        "lan_evidence": {
            "window": {
                "lanSamples": len(lan_series),
                "lanElevatedSamples": len(lan_elevation(lan_series)["elevated"]),
                "lanBad": lan_elevation(lan_series)["lan_bad"],
            }
        },
        "attribution_evidence_counts": camelize_classification_counts(window_counts),
    }


def write_json_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def main():
    VIZ_DIR.mkdir(parents=True, exist_ok=True)

    src = newest_by_mtime()
    if not src:
        print(f"No telemetry CSV files found matching {TELEMETRY_PATTERN}.")
        return

    baseline_by_hour, baseline_sample_counts, baseline_sources = compute_hourly_wan_baseline()

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - WINDOW

    rows_out = []

    with src.open("r", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = ensure_fieldnames(reader.fieldnames)

        for row in reader:
            t = parse_ts(row.get("ts", ""))
            if t is None:
                continue

            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)

            if t.astimezone(dt.timezone.utc) < cutoff:
                continue

            if "traceroute_snip" in row:
                row["traceroute_snip"] = sanitize_field(row.get("traceroute_snip", ""))

            if "speedtest_raw_json" in row:
                row["speedtest_raw_json"] = sanitize_field(row.get("speedtest_raw_json", ""))

            row = apply_target_fields(row)
            row = apply_baseline_fields(row, t, baseline_by_hour, baseline_sample_counts)
            rows_out.append(row)

    tmp = OUT.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows_out:
            writer.writerow(row)

    tmp.replace(OUT)

    attribution = compute_network_attribution(rows_out, now)
    write_json_atomic(ATTRIBUTION_OUT, attribution)
    dashboard_health = build_dashboard_health(rows_out, attribution, now)
    write_json_atomic(DASHBOARD_HEALTH_OUT, dashboard_health)
    lan_series, wan_series = to_dashboard_series(rows_out)
    wan_series_marked = mark_persistent_wan_bad(wan_series)
    incident_runs = find_sustained_wan_incidents(wan_series_marked)
    incidents = [classify_incident(run, lan_series) for run in incident_runs]
    turbulence_buckets = [bucket for bucket in classify_buckets(wan_series_marked) if bucket.get("is_turbulence")]
    attribution_observations = build_attribution_observations(
        attribution,
        generated_at=now,
        telemetry_source_path=f"data/{src.name}",
    )
    episode_observations = build_episode_observations(
        incident_runs=incident_runs,
        incidents=incidents,
        turbulence_buckets=turbulence_buckets,
        generated_at=now,
        telemetry_source_path=f"data/{src.name}",
    )
    observations_projection = build_projection(
        attribution_observations + episode_observations,
        model_version=OBSERVATION_PROJECTION_MODEL_VERSION,
        generated_at=now,
    )
    write_json_atomic(OBSERVATIONS_OUT, observations_projection)
    investigation = build_automatic_investigation(
        rows_out=rows_out,
        generated_at=now,
        wan_series_marked=wan_series_marked,
        attribution=attribution,
        dashboard_health=dashboard_health,
        observations_projection=observations_projection,
    )
    investigation_write = write_investigation_if_changed(INVESTIGATION_OUT, investigation)
    investigation_changed = investigation_write["artifact_written"]
    assistant_semantic_changed = investigation_write["assistant_semantic_changed"]
    history_write = write_completed_investigation_history(
        rows_out=rows_out,
        generated_at=now,
        wan_series_marked=wan_series_marked,
        attribution=attribution,
        dashboard_health=dashboard_health,
        observations_projection=observations_projection,
        investigations_dir=VIZ_DIR / "investigations",
        catalog_path=VIZ_DIR / "investigation_catalog.json",
        current_investigation=investigation,
    )
    if assistant_semantic_changed or not OPERATOR_ASSISTANT_INPUT_OUT.exists():
        assistant_input = build_assistant_input_from_path(INVESTIGATION_OUT)
        write_assistant_input_json(OPERATOR_ASSISTANT_INPUT_OUT, assistant_input)

    print(f"Wrote {len(rows_out)} rows to {OUT} from telemetry source {src.name}")
    print(f"Wrote network attribution export to {ATTRIBUTION_OUT}")
    print(f"Wrote dashboard health projection to {DASHBOARD_HEALTH_OUT}")
    print(f"Wrote observations projection to {OBSERVATIONS_OUT}")
    print(f"Investigation artifact {'updated' if investigation_changed else 'unchanged'} at {INVESTIGATION_OUT}")
    print(
        f"Investigation history contains {history_write['snapshot_count']} immutable snapshots; "
        f"wrote {len(history_write['snapshots_written'])} new snapshots"
    )
    print(f"Operator Assistant input {'updated' if assistant_semantic_changed else 'unchanged'} at {OPERATOR_ASSISTANT_INPUT_OUT}")
    print(f"WAN baseline files used: {', '.join(baseline_sources) if baseline_sources else 'none'}")
    print(f"WAN baseline hours available: {sorted(baseline_by_hour.keys())}")


if __name__ == "__main__":
    main()
