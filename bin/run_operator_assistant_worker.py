#!/usr/bin/env python3
from pathlib import Path
import datetime as dt
import os

import build_operator_assistant_output as producer


BASE = Path(__file__).resolve().parents[1]
VIZ_DIR = BASE / "viz"
INPUT = VIZ_DIR / "operator_assistant_input.json"
OUT = VIZ_DIR / "operator_assistant_output.json"
STATE_OUT = VIZ_DIR / "operator_assistant_generation_state.json"
LOCK_OUT = VIZ_DIR / ".operator_assistant_generation.lock"
MAX_WORKER_ATTEMPTS = 3
WORKER_BACKOFF_SECONDS = (300, 900)
TRANSIENT_ERROR_CATEGORIES = {
    "provider_transport_error",
    "provider_invalid_response",
    "input_unavailable",
    "worker_internal_error",
}
PERSISTENT_ERROR_CATEGORIES = {
    "provider_unconfigured",
    "charter_unavailable",
    "input_invalid",
    "output_validation_failed",
}


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value):
    raw = str(value or "").strip()
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
    return parsed.astimezone(dt.timezone.utc)


def sync_producer_paths():
    producer.BASE = BASE
    producer.VIZ_DIR = VIZ_DIR
    producer.INPUT = INPUT
    producer.OUT = OUT
    producer.STATE_OUT = STATE_OUT
    producer.LOCK_OUT = LOCK_OUT
    producer.ENV_FILE = BASE / ".env.openrouter"
    producer.OPERATOR_CHARTER = BASE / "docs" / "operator-charter.md"


def load_object(path):
    return producer.load_json_file(path)


def write_state(payload):
    producer.write_json_atomic(payload, STATE_OUT)


def state_payload(status, input_hash, now, previous=None, **extra):
    previous = previous if isinstance(previous, dict) else {}
    requested_at = previous.get("requested_at") or previous.get("generated_at") or iso_utc(now)
    payload = {
        "schema_version": 2,
        "status": status,
        "provider": "openrouter",
        "input_hash": input_hash,
        "requested_at": requested_at,
        "updated_at": iso_utc(now),
        "attempt_count": int(previous.get("attempt_count") or 0),
    }
    payload.update(extra)
    return payload


def http_error_is_transient(message):
    return any(f"HTTP {code}" in str(message or "") for code in (429, 500, 502, 503, 504))


def transient_failure(category, message):
    if category == "provider_http_error":
        return http_error_is_transient(message)
    return category in TRANSIENT_ERROR_CATEGORIES


def valid_current_output(input_hash, config):
    output = load_object(OUT) or {}
    return (
        producer.valid_output_payload(output, input_hash)
        and output.get("requested_model") == config["OPENROUTER_MODEL"]
    )


def retry_time(now, attempt_count):
    index = min(max(attempt_count - 1, 0), len(WORKER_BACKOFF_SECONDS) - 1)
    return now + dt.timedelta(seconds=WORKER_BACKOFF_SECONDS[index])


def run_once(now=None, worker_id=None):
    sync_producer_paths()
    now = now or utc_now()
    worker_id = worker_id or f"pid:{os.getpid()}"

    if not STATE_OUT.exists():
        print("Operator Assistant worker: no generation state; no work.")
        return "no_work"

    state = load_object(STATE_OUT)
    if state is None:
        print("Operator Assistant worker: generation state is malformed; no work.")
        return "no_work"

    input_payload = load_object(INPUT)
    input_hash = input_payload.get("input_hash") if input_payload else None
    if not producer.valid_input_hash(input_hash):
        print("Operator Assistant worker: input artifact is missing or invalid; no work.")
        return "no_work"

    if state.get("input_hash") != input_hash:
        state = state_payload(
            "pending",
            input_hash,
            now,
            requested_at=iso_utc(now),
            requested_by="bin/run_operator_assistant_worker.py",
            reason="semantic input hash changed",
            attempt_count=0,
        )
        write_state(state)

    status = state.get("status")
    attempt_count = int(state.get("attempt_count") or 0)
    config = producer.load_config()

    if status in {"complete", "completed", "current"}:
        print("Operator Assistant worker: input hash is already complete; no work.")
        return "no_work"

    if status == "failed":
        print("Operator Assistant worker: work is permanently failed for this input hash; no work.")
        return "no_work"

    if status == "retry_wait":
        next_retry_at = parse_ts(state.get("next_retry_at"))
        if attempt_count >= MAX_WORKER_ATTEMPTS:
            failed = state_payload(
                "failed",
                input_hash,
                now,
                state,
                last_error_category=state.get("last_error_category"),
                output_validation_result=state.get("output_validation_result"),
            )
            write_state(failed)
            print("Operator Assistant worker: retry limit reached; no work.")
            return "terminal_failure"
        if next_retry_at and now < next_retry_at:
            print("Operator Assistant worker: retry delay has not elapsed; no work.")
            return "no_work"

    if status in {"failed_no_valid_output", "failed_retained_previous"}:
        category = state.get("last_error_category")
        if not transient_failure(category, state.get("last_error")):
            failed = state_payload(
                "failed",
                input_hash,
                now,
                state,
                last_error_category=category,
                output_validation_result=state.get("output_validation_result"),
            )
            write_state(failed)
            print("Operator Assistant worker: persistent failure requires operator correction; no work.")
            return "terminal_failure"
        next_retry_at = parse_ts(state.get("next_retry_at"))
        if next_retry_at and now < next_retry_at:
            waiting = state_payload(
                "retry_wait",
                input_hash,
                now,
                state,
                next_retry_at=iso_utc(next_retry_at),
                last_error_category=category,
                output_validation_result=state.get("output_validation_result"),
            )
            write_state(waiting)
            print("Operator Assistant worker: retry delay has not elapsed; no work.")
            return "no_work"

    if valid_current_output(input_hash, config):
        output = load_object(OUT) or {}
        complete = state_payload(
            "complete",
            input_hash,
            now,
            state,
            completed_at=output.get("generated_at") or iso_utc(now),
            output_input_hash=input_hash,
            output_validation_result="valid_current",
            attempt_count=attempt_count,
        )
        write_state(complete)
        print("Operator Assistant worker: valid current output already exists.")
        return "no_work"

    if not producer.acquire_generation_lock(input_hash, config["OPENROUTER_MODEL"]):
        in_progress = state_payload(
            "generating",
            input_hash,
            now,
            state,
            worker_id=worker_id,
            output_validation_result="retained_previous" if producer.valid_output_payload(load_object(OUT), input_hash) else "pending_no_valid_output",
            attempt_count=attempt_count,
        )
        write_state(in_progress)
        print("Operator Assistant worker: another worker owns the generation lock.")
        return "lock_held"

    current_attempt = attempt_count + 1
    generating = state_payload(
        "generating",
        input_hash,
        now,
        state,
        started_at=iso_utc(now),
        worker_id=worker_id,
        attempt_count=current_attempt,
    )
    write_state(generating)

    try:
        result = producer.build_output_result(force=False, lock_owned=True)
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if result.get("should_write") and producer.valid_output_payload(payload, input_hash):
            producer.write_json_atomic(payload, OUT)

        if producer.valid_output_payload(payload, input_hash):
            complete = state_payload(
                "complete",
                input_hash,
                now,
                generating,
                completed_at=payload.get("generated_at") or iso_utc(now),
                output_input_hash=input_hash,
                output_validation_result="valid_published" if result.get("should_write") else "valid_current",
                attempt_count=current_attempt,
            )
            write_state(complete)
            print("Operator Assistant worker: generation complete.")
            return "generation_completed"

        producer_state = result.get("state_payload") if isinstance(result.get("state_payload"), dict) else {}
        category = producer_state.get("last_error_category") or "output_validation_failed"
        message = producer_state.get("last_error")
        output_validation_result = producer_state.get("output_validation_result")
        is_transient = transient_failure(category, message)
        finished_at = now
        if is_transient and current_attempt < MAX_WORKER_ATTEMPTS:
            next_retry_at = retry_time(finished_at, current_attempt)
            waiting = state_payload(
                "retry_wait",
                input_hash,
                finished_at,
                generating,
                next_retry_at=iso_utc(next_retry_at),
                last_error_category=category,
                output_validation_result=output_validation_result,
                attempt_count=current_attempt,
            )
            write_state(waiting)
            print("Operator Assistant worker: transient failure; retry scheduled.")
            return "retry_scheduled"

        failed = state_payload(
            "failed",
            input_hash,
            finished_at,
            generating,
            last_error_category=category,
            output_validation_result=output_validation_result,
            attempt_count=current_attempt,
        )
        write_state(failed)
        print("Operator Assistant worker: generation stopped for this input hash.")
        return "terminal_failure"
    except Exception:
        finished_at = now
        if current_attempt < MAX_WORKER_ATTEMPTS:
            waiting = state_payload(
                "retry_wait",
                input_hash,
                finished_at,
                generating,
                next_retry_at=iso_utc(retry_time(finished_at, current_attempt)),
                last_error_category="worker_internal_error",
                attempt_count=current_attempt,
            )
            write_state(waiting)
            print("Operator Assistant worker: internal failure; retry scheduled.")
            return "retry_scheduled"
        failed = state_payload(
            "failed",
            input_hash,
            finished_at,
            generating,
            last_error_category="worker_internal_error",
            attempt_count=current_attempt,
        )
        write_state(failed)
        print("Operator Assistant worker: retry limit reached after internal failure.")
        return "terminal_failure"
    finally:
        producer.release_generation_lock()


def main():
    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
