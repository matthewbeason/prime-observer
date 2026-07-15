"""Canonical deterministic health-model logic shared by Python producers.

Python owns health-model classification. Browser renderers should consume
generated semantics instead of reimplementing these rules.
"""

import datetime as dt


WAN_BAD = {"p95": 140.0, "jitter": 50.0, "loss": 1.0}
WAN_BAD_PERSISTENCE = 2
TURBULENCE_MIN_RAW_BAD = 4
HEAT_BIN_MINUTES = 15
ATTRIBUTION_CUT_MINUTES = 15
LAN_BAD_P95 = 120.0
LAN_BAD_MIN_ELEVATED = 3
LAN_BAD_MIN_RATE = 0.2


def is_wan_bad(sample, *, p95_key="p95", jitter_key="jitter", loss_key="loss"):
    return (
        sample[p95_key] > WAN_BAD["p95"]
        or sample[jitter_key] > WAN_BAD["jitter"]
        or sample[loss_key] > WAN_BAD["loss"]
    )


def is_turbulence_bucket(raw_bad_count, sustained_bad_count, max_raw_run):
    return (
        sustained_bad_count == 0
        and raw_bad_count >= TURBULENCE_MIN_RAW_BAD
        and max_raw_run < WAN_BAD_PERSISTENCE
    )


def bucket_start(timestamp, *, minutes=HEAT_BIN_MINUTES):
    seconds = minutes * 60
    return int(timestamp.timestamp() // seconds) * seconds


def bucket_interval(timestamp, *, minutes=HEAT_BIN_MINUTES):
    start = bucket_start(timestamp, minutes=minutes)
    seconds = minutes * 60
    return (
        dt.datetime.fromtimestamp(start, tz=dt.timezone.utc),
        dt.datetime.fromtimestamp(start + seconds, tz=dt.timezone.utc),
    )


def lan_elevation(samples, *, p95_key="p95"):
    elevated = [sample for sample in samples if (sample.get(p95_key) or 0.0) > LAN_BAD_P95]
    rate = len(elevated) / len(samples) if samples else 0.0
    return {
        "samples": samples,
        "elevated": elevated,
        "elevated_rate": rate,
        "lan_bad": len(elevated) >= LAN_BAD_MIN_ELEVATED and rate > LAN_BAD_MIN_RATE,
        "lan_stable": bool(samples) and not elevated,
    }
