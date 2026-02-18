#!/usr/bin/env python3
import csv
import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
from pathlib import Path

TARGETS = ["192.168.1.1", "1.1.1.1", "9.9.9.9"]
PING_COUNT = 10
TRACEROUTE_EVERY_MIN = 15
SPEEDTEST_EVERY_MIN = 30
MAX_HOPS = 20

BASE = Path("/Users/mbeason/net-bakeoff")
OUTDIR = BASE / "data"
PHASE_FILE = BASE / "phase.txt"

FIELDNAMES = [
    "ts",
    "phase_label",
    "host",
    "sent",
    "received",
    "loss_pct",
    "avg_ms",
    "p50_ms",
    "p95_ms",
    "max_ms",
    "jitter_ms",
    "traceroute_snip",
    "speedtest_down_mbps",
    "speedtest_up_mbps",
    "speedtest_ping_ms",
    "speedtest_raw_json",
]

PING_RE = re.compile(r"time=([\d\.]+)\s*ms")

def run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def parse_ping_times_ms(ping_stdout: str):
    times = []
    for line in ping_stdout.splitlines():
        m = PING_RE.search(line)
        if m:
            times.append(float(m.group(1)))
    return times

def quantile(values, q):
    if not values:
        return None
    v = sorted(values)
    idx = (len(v) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(v) - 1)
    if hi == lo:
        return v[lo]
    frac = idx - lo
    return v[lo] * (1 - frac) + v[hi] * frac

def ping_target(host: str, count: int):
    # macOS ping defaults to 1/sec; set interval so a 20-ping sample completes quickly.
    # -i 0.2 => ~4s for 20 pings
    # -W 1000 => (macOS) wait up to 1000ms for each reply
    args = [
        "/sbin/ping",
        "-n",
        "-c", str(count),
        "-i", "0.2",
        "-W", "1000",
        host
    ]

    # Hard cap in Python so it never blocks the whole run.
    # Give a little cushion: ~4s expected + parsing overhead
    r = run(args, timeout=8)

    times = parse_ping_times_ms(r.stdout)

    sent = count
    received = len(times)
    loss_pct = 100.0 * (sent - received) / sent if sent else 0.0

    avg = statistics.mean(times) if times else None
    p50 = quantile(times, 0.50) if times else None
    p95 = quantile(times, 0.95) if times else None
    mx = max(times) if times else None
    jitter = statistics.pstdev(times) if len(times) >= 2 else 0.0

    return {
        "sent": sent,
        "received": received,
        "loss_pct": round(loss_pct, 2),
        "avg_ms": round(avg, 3) if avg is not None else "",
        "p50_ms": round(p50, 3) if p50 is not None else "",
        "p95_ms": round(p95, 3) if p95 is not None else "",
        "max_ms": round(mx, 3) if mx is not None else "",
        "jitter_ms": round(jitter, 3) if jitter is not None else "",
    }

def traceroute_snip(host: str):
    r = run(["/usr/sbin/traceroute", "-n", "-m", str(MAX_HOPS), host], timeout=90)
    lines = r.stdout.splitlines()
    # Keep it compact and CSV-safe (no embedded newlines)
    return " | ".join(lines[:12]).strip()

def have_ookla_speedtest():
    return shutil.which("speedtest") is not None

def run_ookla_speedtest():
    if not have_ookla_speedtest():
        return None

    r = run(["speedtest", "--accept-license", "--accept-gdpr", "-f", "json"], timeout=180)
    if r.returncode != 0:
        return None

    try:
        data = json.loads(r.stdout)
        down_b = data.get("download", {}).get("bandwidth", None)
        up_b = data.get("upload", {}).get("bandwidth", None)
        ping_ms = data.get("ping", {}).get("latency", None)

        def bw_to_mbps(x):
            if x is None:
                return None
            # Ookla "bandwidth" is typically bytes/sec
            return (float(x) * 8.0) / 1_000_000.0

        return {
            "down_mbps": round(bw_to_mbps(down_b), 2) if down_b is not None else None,
            "up_mbps": round(bw_to_mbps(up_b), 2) if up_b is not None else None,
            "ping_ms": round(float(ping_ms), 2) if ping_ms is not None else None,
            "raw_json": r.stdout.strip(),
        }
    except Exception:
        return None

def minute_bucket(now: dt.datetime) -> int:
    return now.hour * 60 + now.minute

def read_phase():
    env_phase = os.environ.get("PHASE", "").strip()
    if env_phase:
        return env_phase
    if PHASE_FILE.exists():
        return PHASE_FILE.read_text().strip() or "UNKNOWN"
    return "UNKNOWN"

def ensure_csv(path: Path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()

def main():
    phase = read_phase()
    now = dt.datetime.now().astimezone()
    ts = now.isoformat(timespec="seconds")

    dayfile = OUTDIR / f"bakeoff_{now.strftime('%Y%m%d')}.csv"
    ensure_csv(dayfile)

    bucket = minute_bucket(now)
    do_tr = (bucket % TRACEROUTE_EVERY_MIN == 0)
    do_st = (bucket % SPEEDTEST_EVERY_MIN == 0)

    st = run_ookla_speedtest() if do_st else None

    with dayfile.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        for host in TARGETS:
            row = {
                "ts": ts,
                "phase_label": phase,
                "host": host,
                "traceroute_snip": traceroute_snip(host) if do_tr else "",
                "speedtest_down_mbps": st["down_mbps"] if st else "",
                "speedtest_up_mbps": st["up_mbps"] if st else "",
                "speedtest_ping_ms": st["ping_ms"] if st else "",
                "speedtest_raw_json": st["raw_json"] if st else "",
            }
            row.update(ping_target(host, PING_COUNT))
            w.writerow(row)

if __name__ == "__main__":
    main()
