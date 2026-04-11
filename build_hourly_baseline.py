#!/usr/bin/env python3
from pathlib import Path
import csv
import datetime as dt
from collections import defaultdict
from statistics import mean

BASE = Path.cwd()
DATA_DIR = BASE / "data"
OUT_DIR = BASE / "analysis"
OUT_FILE = OUT_DIR / "hourly_baseline_fiber.csv"

WAN_HOSTS = {"1.1.1.1", "9.9.9.9"}
PHASE = "FIBER"

WAN_BAD_P95 = 80.0
WAN_BAD_JITTER = 15.0
WAN_BAD_LOSS = 0.0

def parse_ts(value: str):
    try:
        return dt.datetime.fromisoformat(value)
    except Exception:
        return None

def parse_float(value: str, default=None):
    try:
        return float(value)
    except Exception:
        return default

def is_bad(row: dict) -> bool:
    return (
        (row["p95_ms"] is not None and row["p95_ms"] > WAN_BAD_P95)
        or (row["jitter_ms"] is not None and row["jitter_ms"] > WAN_BAD_JITTER)
        or (row["loss_pct"] is not None and row["loss_pct"] > WAN_BAD_LOSS)
    )

def quantile(values, q: float):
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    idx = (len(vals) - 1) * q
    lower = int(idx)
    upper = min(lower + 1, len(vals) - 1)
    frac = idx - lower
    return vals[lower] * (1 - frac) + vals[upper] * frac

def main():
    files = sorted(DATA_DIR.glob("bakeoff_*.csv"))
    if not files:
        raise SystemExit(f"No bakeoff_*.csv files found in {DATA_DIR}")

    by_timestamp = {}

    for path in files:
        with path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                phase = (raw.get("phase_label") or "").strip().upper()
                host = (raw.get("host") or "").strip()
                if phase != PHASE:
                    continue
                if host not in WAN_HOSTS:
                    continue

                ts = parse_ts(raw.get("ts", ""))
                if ts is None:
                    continue

                row = {
                    "ts": ts,
                    "host": host,
                    "p95_ms": parse_float(raw.get("p95_ms")),
                    "jitter_ms": parse_float(raw.get("jitter_ms"), 0.0),
                    "loss_pct": parse_float(raw.get("loss_pct"), 0.0),
                }

                if row["p95_ms"] is None:
                    continue

                key = ts.isoformat()
                prev = by_timestamp.get(key)
                if prev is None or row["p95_ms"] > prev["p95_ms"]:
                    by_timestamp[key] = row

    collapsed = sorted(by_timestamp.values(), key=lambda r: r["ts"])
    if not collapsed:
        raise SystemExit("No collapsed WAN rows found after filtering.")

    buckets = defaultdict(list)
    for row in collapsed:
        buckets[row["ts"].hour].append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with OUT_FILE.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "hour",
            "samples",
            "avg_wan_p95_ms",
            "median_wan_p95_ms",
            "p95_wan_p95_ms",
            "avg_jitter_ms",
            "bad_rate_pct",
        ])

        for hour in range(24):
            rows = buckets.get(hour, [])
            if not rows:
                writer.writerow([hour, 0, "", "", "", "", ""])
                continue

            p95s = [r["p95_ms"] for r in rows if r["p95_ms"] is not None]
            jitters = [r["jitter_ms"] for r in rows if r["jitter_ms"] is not None]
            bad_rate = 100.0 * sum(1 for r in rows if is_bad(r)) / len(rows)

            writer.writerow([
                hour,
                len(rows),
                round(mean(p95s), 2),
                round(quantile(p95s, 0.50), 2),
                round(quantile(p95s, 0.95), 2),
                round(mean(jitters), 2) if jitters else "",
                round(bad_rate, 2),
            ])

    print(f"Wrote {OUT_FILE}")
    print(f"Collapsed WAN samples used: {len(collapsed)}")
    print("Preview:")
    with OUT_FILE.open("r", newline="") as f:
        for i, line in enumerate(f):
            print(line.rstrip())
            if i >= 10:
                break

if __name__ == "__main__":
    main()
