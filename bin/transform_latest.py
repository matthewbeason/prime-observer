#!/usr/bin/env python3
from pathlib import Path
import csv
import datetime as dt
from collections import defaultdict

BASE = Path("/Users/mbeason/prime-observer")
DATA_DIR = BASE / "data"
VIZ_DIR  = BASE / "viz"
OUT      = VIZ_DIR / "latest.csv"

WINDOW_HOURS = 24  # align with dashboard
WINDOW = dt.timedelta(hours=WINDOW_HOURS)
BASELINE_FILE_COUNT = 10
WAN_HOSTS = {"1.1.1.1", "9.9.9.9"}

def sanitize_field(s: str) -> str:
    if s is None:
        return ""
    return " | ".join(str(s).splitlines()).replace("\t", " ").strip()

def newest_by_mtime():
    files = list(DATA_DIR.glob("bakeoff_*.csv"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)

def recent_baseline_files():
    files = sorted(DATA_DIR.glob("bakeoff_*.csv"))
    if not files:
        return []
    return files[-min(len(files), BASELINE_FILE_COUNT):]

def parse_ts(ts: str):
    try:
        return dt.datetime.fromisoformat(ts)
    except Exception:
        return None

def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0

def compute_hourly_baseline():
    by_hour = defaultdict(list)
    used_files = []
    for src in recent_baseline_files():
        try:
            with src.open("r", newline="") as f:
                reader = csv.DictReader(f)
                row_count = 0
                for r in reader:
                    host = (r.get("host") or "").strip()
                    if host not in WAN_HOSTS:
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
        except Exception:
            continue

    baseline = {}
    for hour, vals in by_hour.items():
        m = median(vals)
        if m is not None:
            baseline[hour] = m
    return baseline, used_files

def main():
    VIZ_DIR.mkdir(parents=True, exist_ok=True)

    src = newest_by_mtime()
    if not src:
        print("No bakeoff_*.csv files found.")
        return

    baseline_by_hour, used_files = compute_hourly_baseline()

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - WINDOW

    rows_out = []
    with src.open("r", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        if "baseline_p95" not in fieldnames:
            fieldnames.append("baseline_p95")
        if "baseline_delta_pct" not in fieldnames:
            fieldnames.append("baseline_delta_pct")

        for r in reader:
            t = parse_ts(r.get("ts", ""))
            if t is None:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if t.astimezone(dt.timezone.utc) < cutoff:
                continue

            if "traceroute_snip" in r:
                r["traceroute_snip"] = sanitize_field(r.get("traceroute_snip", ""))
            if "speedtest_raw_json" in r:
                r["speedtest_raw_json"] = sanitize_field(r.get("speedtest_raw_json", ""))

            host = (r.get("host") or "").strip()
            if host in WAN_HOSTS:
                hour = t.hour
                baseline = baseline_by_hour.get(hour)
                try:
                    current_p95 = float((r.get("p95_ms") or "").strip())
                except Exception:
                    current_p95 = None

                if baseline is not None:
                    r["baseline_p95"] = f"{baseline:.1f}"
                    if current_p95 is not None and baseline > 0:
                        delta_pct = ((current_p95 - baseline) / baseline) * 100.0
                        r["baseline_delta_pct"] = f"{delta_pct:.1f}"
                    else:
                        r["baseline_delta_pct"] = ""
                else:
                    r["baseline_p95"] = ""
                    r["baseline_delta_pct"] = ""
            else:
                r["baseline_p95"] = ""
                r["baseline_delta_pct"] = ""

            rows_out.append(r)

    tmp = OUT.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for r in rows_out:
            writer.writerow(r)
    tmp.replace(OUT)

    print(f"Wrote {len(rows_out)} rows to {OUT} from source {src.name}")
    print(f"Baseline files used: {', '.join(used_files) if used_files else 'none'}")
    print(f"Baseline hours available: {sorted(baseline_by_hour.keys())}")

if __name__ == "__main__":
    main()
