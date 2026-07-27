#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
VIZ_DIR = BASE / "viz"
INVESTIGATION = VIZ_DIR / "investigation.json"
OUT = VIZ_DIR / "operator_impact_feedback.json"

SCHEMA_VERSION = 1
MODEL_VERSION = "prime_observer.operator_impact_feedback.v1"
ALLOWED_IMPACTS = {
    "none_observed",
    "minor_slowness",
    "intermittent_failures",
    "major_disruption",
    "full_outage",
    "unknown",
}
MAX_NOTE_CHARS = 500


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_note(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    return text[:MAX_NOTE_CHARS]


def read_current_incident(path: Path | None = None) -> tuple[str | None, str | None]:
    path = path or INVESTIGATION
    if not path.exists():
        return None, "Current investigation artifact does not exist."
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, "Current investigation artifact is unreadable."
    if not isinstance(payload, dict):
        return None, "Current investigation artifact is invalid."
    selected_raw = payload.get("selected_event")
    selected = selected_raw if isinstance(selected_raw, dict) else {}
    incident_id = selected.get("id")
    if not incident_id:
        return None, "No active or selected investigation incident is available."
    return str(incident_id), None


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def feedback_payload(incident_id: str, impact: str, note: str, observed_at: dt.datetime) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "incident_id": incident_id,
        "observed_at": iso(observed_at),
        "impact_state": impact,
        "note": sanitize_note(note),
        "source": "operator",
        "freshness": {
            "state": "fresh",
            "association": "current_incident",
        },
    }


def clear_payload(incident_id: str, observed_at: dt.datetime) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "incident_id": incident_id,
        "observed_at": iso(observed_at),
        "impact_state": "unknown",
        "note": "",
        "source": "operator",
        "cleared": True,
        "freshness": {
            "state": "fresh",
            "association": "current_incident",
        },
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Record local operator-observed incident impact.")
    p.add_argument("--impact", choices=sorted(ALLOWED_IMPACTS), help="Observed impact value to record.")
    p.add_argument("--note", default="", help="Optional bounded operator note.")
    p.add_argument("--incident-id", help="Explicit incident ID. Defaults to current investigation selected_event.id.")
    p.add_argument("--list-values", action="store_true", help="List allowed --impact values and exit.")
    p.add_argument("--clear", action="store_true", help="Clear feedback for the current or specified incident.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.list_values:
        for value in sorted(ALLOWED_IMPACTS):
            print(value)
        return 0

    incident_id = args.incident_id
    if not incident_id:
        incident_id, error = read_current_incident()
        if error:
            print(error)
            return 2
    incident_id = str(incident_id)
    if args.clear:
        write_json_atomic(OUT, clear_payload(incident_id, utc_now()))
        print(f"Cleared operator impact feedback for {incident_id}.")
        return 0
    if not args.impact:
        parser().error("--impact is required unless --list-values or --clear is used")
    write_json_atomic(OUT, feedback_payload(incident_id, args.impact, args.note, utc_now()))
    print(f"Recorded {args.impact} operator impact feedback for {incident_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
