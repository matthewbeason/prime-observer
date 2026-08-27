#!/usr/bin/env python3
"""Compare complete Python semantic generation from CSV and SQLite raw inputs."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import build_investigation
import raw_observation_source
import transform_latest


BASE = Path(__file__).resolve().parents[1]
ARTIFACTS = (
    "latest.csv",
    "dashboard_health.json",
    "observations.json",
    "network_attribution.json",
    "interval_summary.json",
    "time_context.json",
    "investigation.json",
    "baseline_history.json",
    "incident_similarity.json",
    "operational_learnings.json",
)
RUNTIME_ONLY_KEYS = {
    "generated_at",
    "execution_duration",
    "elapsed_seconds",
    "requested_at",
}


def normalized(value):
    if isinstance(value, dict):
        return {
            key: normalized(item)
            for key, item in sorted(value.items())
            if key not in RUNTIME_ONLY_KEYS
        }
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def configure_outputs(directory: Path) -> None:
    mapping = {
        "OUT": "latest.csv",
        "ATTRIBUTION_OUT": "network_attribution.json",
        "OBSERVATIONS_OUT": "observations.json",
        "DASHBOARD_HEALTH_OUT": "dashboard_health.json",
        "BASELINE_HISTORY_OUT": "baseline_history.json",
        "INTERVAL_SUMMARY_OUT": "interval_summary.json",
        "INCIDENT_SIMILARITY_OUT": "incident_similarity.json",
        "OPERATIONAL_LEARNINGS_OUT": "operational_learnings.json",
        "TIME_CONTEXT_OUT": "time_context.json",
        "INVESTIGATION_OUT": "investigation.json",
        "OPERATOR_ASSISTANT_INPUT_OUT": "operator_assistant_input.json",
        "OPERATOR_ASSISTANT_GENERATION_STATE_OUT": "operator_assistant_generation_state.json",
    }
    transform_latest.VIZ_DIR = directory
    for attribute, name in mapping.items():
        setattr(transform_latest, attribute, directory / name)
    for attribute in (
        "DIAGNOSTIC_EVIDENCE_IN",
        "APPLICATION_EXPERIENCE_IN",
        "OPERATOR_IMPACT_FEEDBACK_IN",
        "INTERNET_CONDITIONS_IN",
        "APS_POWER_CONTEXT_IN",
    ):
        setattr(transform_latest, attribute, directory / Path(getattr(transform_latest, attribute)).name)


def seed(directory: Path) -> None:
    directory.mkdir(parents=True)
    live = BASE / "viz"
    for name in (
        "baseline_history.json",
        "diagnostic_evidence.json",
        "application_experience.json",
        "operator_impact_feedback.json",
        "internet_conditions.json",
        "aps_power_context.json",
        "mesh_context.json",
        "investigation_catalog.json",
    ):
        source = live / name
        if source.exists():
            shutil.copy2(source, directory / name)
    if (live / "investigations").exists():
        shutil.copytree(live / "investigations", directory / "investigations")


def run_transform(directory: Path, policy: str, generated_at: str) -> float:
    configure_outputs(directory)
    old_policy = os.environ.get(transform_latest.RAW_READ_POLICY_ENVIRONMENT)
    old_time = os.environ.get("PRIME_OBSERVER_GENERATED_AT")
    os.environ[transform_latest.RAW_READ_POLICY_ENVIRONMENT] = policy
    os.environ["PRIME_OBSERVER_GENERATED_AT"] = generated_at
    started = time.perf_counter()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            transform_latest.main()
    finally:
        if old_policy is None:
            os.environ.pop(transform_latest.RAW_READ_POLICY_ENVIRONMENT, None)
        else:
            os.environ[transform_latest.RAW_READ_POLICY_ENVIRONMENT] = old_policy
        if old_time is None:
            os.environ.pop("PRIME_OBSERVER_GENERATED_AT", None)
        else:
            os.environ["PRIME_OBSERVER_GENERATED_AT"] = old_time
    return time.perf_counter() - started


def compare_artifact(csv_path: Path, sqlite_path: Path) -> dict[str, object]:
    if csv_path.suffix == ".csv":
        left = csv_path.read_bytes()
        right = sqlite_path.read_bytes()
        comparison = "byte_exact"
    else:
        left = normalized(json.loads(csv_path.read_text()))
        right = normalized(json.loads(sqlite_path.read_text()))
        comparison = "structural_exact_ignoring_runtime_metadata"
    return {"equivalent": left == right, "comparison": comparison}


def manual_investigation_comparison(start: str, end: str, pad_minutes: int) -> dict[str, object]:
    previous = os.environ.get(build_investigation.RAW_READ_POLICY_ENVIRONMENT)
    payloads = {}
    timings = {}
    try:
        for policy in (raw_observation_source.CSV_ONLY, raw_observation_source.SQLITE_ONLY):
            os.environ[build_investigation.RAW_READ_POLICY_ENVIRONMENT] = policy
            started = time.perf_counter()
            with contextlib.redirect_stderr(io.StringIO()):
                payloads[policy] = build_investigation.build_investigation(start, end, pad_minutes)
            timings[policy] = time.perf_counter() - started
    finally:
        if previous is None:
            os.environ.pop(build_investigation.RAW_READ_POLICY_ENVIRONMENT, None)
        else:
            os.environ[build_investigation.RAW_READ_POLICY_ENVIRONMENT] = previous
    return {
        "equivalent": normalized(payloads[raw_observation_source.CSV_ONLY])
        == normalized(payloads[raw_observation_source.SQLITE_ONLY]),
        "comparison": "structural_exact_ignoring_runtime_metadata",
        "seconds": timings,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--investigation-start")
    parser.add_argument("--investigation-end")
    parser.add_argument("--pad-minutes", type=int, default=30)
    args = parser.parse_args(argv)
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    with tempfile.TemporaryDirectory(prefix="prime-semantic-equivalence-") as temporary:
        root = Path(temporary)
        csv_directory = root / "csv" / "viz"
        sqlite_directory = root / "sqlite" / "viz"
        seed(csv_directory)
        seed(sqlite_directory)
        csv_seconds = run_transform(csv_directory, raw_observation_source.CSV_ONLY, generated_at)
        sqlite_seconds = run_transform(sqlite_directory, raw_observation_source.SQLITE_ONLY, generated_at)
        artifacts = {
            name: compare_artifact(csv_directory / name, sqlite_directory / name)
            for name in ARTIFACTS
        }
        result = {
            "equivalent": all(item["equivalent"] for item in artifacts.values()),
            "generated_at": generated_at,
            "artifacts": artifacts,
            "transform_seconds": {"csv": csv_seconds, "sqlite": sqlite_seconds},
        }
        if args.investigation_start and args.investigation_end:
            result["manual_investigation"] = manual_investigation_comparison(
                args.investigation_start, args.investigation_end, args.pad_minutes
            )
            result["equivalent"] = bool(
                result["equivalent"] and result["manual_investigation"]["equivalent"]
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
