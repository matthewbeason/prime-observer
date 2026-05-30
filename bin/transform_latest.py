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

# Keep the historical bakeoff_*.csv naming for compatibility, but treat these
# files as Prime Observer telemetry history now that the provider bakeoff phase
# is over.
TELEMETRY_PATTERN = "bakeoff_*.csv"
BASELINE_FILE_COUNT = 10

WAN_HOSTS = {"1.1.1.1", "9.9.9.9"}
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
    for col in BASELINE_COLUMNS:
        if col not in out:
            out.append(col)
    return out


def apply_baseline_fields(row, timestamp, baseline_by_hour, baseline_sample_counts):
    host = (row.get("host") or "").strip()

    if host not in WAN_HOSTS:
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

    print(f"Wrote {len(rows_out)} rows to {OUT} from telemetry source {src.name}")
    print(f"WAN baseline files used: {', '.join(baseline_sources) if baseline_sources else 'none'}")
    print(f"WAN baseline hours available: {sorted(baseline_by_hour.keys())}")


if __name__ == "__main__":
    main()
