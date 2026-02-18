#!/usr/bin/env python3
from pathlib import Path
import csv
import datetime as dt

BASE = Path("/Users/mbeason/net-bakeoff")
DATA_DIR = BASE / "data"
VIZ_DIR  = BASE / "viz"
OUT      = VIZ_DIR / "latest.csv"

WINDOW_HOURS = 24  # align with dashboard
WINDOW = dt.timedelta(hours=WINDOW_HOURS)

def sanitize_field(s: str) -> str:
    if s is None:
        return ""
    return " | ".join(str(s).splitlines()).replace("\t", " ").strip()

def newest_by_mtime():
    files = list(DATA_DIR.glob("bakeoff_*.csv"))
    if not files:
        return None
    # choose by modified time, not by filename
    return max(files, key=lambda p: p.stat().st_mtime)

def parse_ts(ts: str):
    # ts like: 2026-02-11T16:18:01-07:00
    try:
        return dt.datetime.fromisoformat(ts)
    except Exception:
        return None

def main():
    VIZ_DIR.mkdir(parents=True, exist_ok=True)

    src = newest_by_mtime()
    if not src:
        print("No bakeoff_*.csv files found.")
        return

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - WINDOW

    rows_out = []
    with src.open("r", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for r in reader:
            t = parse_ts(r.get("ts", ""))
            if t is None:
                continue
            # ensure timezone-aware for compare
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if t.astimezone(dt.timezone.utc) < cutoff:
                continue

            # sanitize risky fields
            if "traceroute_snip" in r:
                r["traceroute_snip"] = sanitize_field(r.get("traceroute_snip", ""))
            if "speedtest_raw_json" in r:
                r["speedtest_raw_json"] = sanitize_field(r.get("speedtest_raw_json", ""))

            rows_out.append(r)

    # Write atomically
    tmp = OUT.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for r in rows_out:
            writer.writerow(r)
    tmp.replace(OUT)

    print(f"Wrote {len(rows_out)} rows to {OUT} from source {src.name}")

if __name__ == "__main__":
    main()
