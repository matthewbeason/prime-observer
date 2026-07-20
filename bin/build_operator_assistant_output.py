#!/usr/bin/env python3
from pathlib import Path
import datetime as dt
import json
import os
import shlex
import sys
import time
import urllib.error
import urllib.request


BASE = Path(__file__).resolve().parents[1]
VIZ_DIR = BASE / "viz"
INPUT = VIZ_DIR / "operator_assistant_input.json"
OUT = VIZ_DIR / "operator_assistant_output.json"
STATE_OUT = VIZ_DIR / "operator_assistant_generation_state.json"
LOCK_OUT = VIZ_DIR / ".operator_assistant_generation.lock"
ENV_FILE = BASE / ".env.openrouter"
OPERATOR_CHARTER = BASE / "docs" / "operator-charter.md"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "PrimeObserver/0.9.0"
DEFAULT_MODEL = "google/gemini-3.5-flash"
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_TOKENS = 3000
MAX_ATTEMPTS_PER_HASH = 3
RETRY_BACKOFF_SECONDS = (300, 900, 1800)
LOCK_STALE_SECONDS = 900
MAX_LIST_ITEMS = 5
MAX_TEXT_CHARS = 900
MAX_SHORT_TEXT_CHARS = 240
MAX_STEP_TEXT_CHARS = 360
STRUCTURED_OUTPUT_INSTRUCTIONS = (
    "Return JSON only with the required schema. Keep every field concise and suitable for a compact operator UI.\n\n"
    "Suggested next-step IDs when appropriate: "
    "EXTEND_WINDOW, CHECK_GATEWAY, COMPARE_RESOLVER_AND_INTERNET, RECHECK_PROVIDER_CONTEXT."
)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(ts):
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_env_file(path):
    values = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue

        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue

        if tokens and tokens[0] == "export":
            tokens = tokens[1:]

        if not tokens:
            continue

        if len(tokens) >= 3 and tokens[1] == "=":
            key = tokens[0]
            value = " ".join(tokens[2:])
        elif "=" in tokens[0]:
            key, value = tokens[0].split("=", 1)
        else:
            continue

        key = key.strip()
        value = value.strip()
        if not key:
            continue

        values[key] = value

    return values


def config_value(key, file_values, default=""):
    value = os.environ.get(key)
    if value is None:
        value = file_values.get(key, default)
    return str(value).strip()


def load_config():
    file_values = parse_env_file(ENV_FILE)
    timeout_raw = config_value(
        "OPENROUTER_TIMEOUT_SECONDS",
        file_values,
        str(DEFAULT_TIMEOUT_SECONDS),
    )
    max_tokens_raw = config_value(
        "OPENROUTER_MAX_TOKENS",
        file_values,
        str(DEFAULT_MAX_TOKENS),
    )

    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS

    try:
        max_tokens = int(max_tokens_raw)
    except ValueError:
        max_tokens = DEFAULT_MAX_TOKENS

    configured_model = config_value("OPENROUTER_MODEL", file_values)
    if not configured_model:
        configured_model = config_value(
            "PRIME_OBSERVER_OPERATOR_ASSISTANT_MODEL",
            file_values,
            DEFAULT_MODEL,
        )

    return {
        "OPENROUTER_API_KEY": config_value("OPENROUTER_API_KEY", file_values),
        "OPENROUTER_MODEL": configured_model or DEFAULT_MODEL,
        "OPENROUTER_TIMEOUT_SECONDS": max(1.0, timeout),
        "OPENROUTER_MAX_TOKENS": max(200, min(max_tokens, 4000)),
        "OPENROUTER_RETRY_SLEEP_SECONDS": 0,
        "HTTP_REFERER": config_value("OPENROUTER_HTTP_REFERER", file_values),
        "APP_TITLE": config_value(
            "OPENROUTER_APP_TITLE",
            file_values,
            "Prime Observer",
        ),
    }


def print_configuration_diagnostics(config):
    print("Operator Assistant configuration")
    print(f"Model: {config['OPENROUTER_MODEL']}")
    print(f"API key present: {'yes' if config['OPENROUTER_API_KEY'] else 'no'}")
    print(f"Timeout: {config['OPENROUTER_TIMEOUT_SECONDS']}s")
    print(f"Max tokens: {config['OPENROUTER_MAX_TOKENS']}")


def safe_dict(value):
    return value if isinstance(value, dict) else {}


def safe_list(value):
    return value if isinstance(value, list) else []


def concise_string(value, limit=MAX_TEXT_CHARS):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def load_json_file(path):
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def valid_input_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def valid_output_payload(payload, expected_hash=None):
    if not isinstance(payload, dict):
        return False
    if payload.get("status") != "ok":
        return False
    if expected_hash is not None and payload.get("input_hash") != expected_hash:
        return False
    if not valid_input_hash(payload.get("input_hash")):
        return False
    for key in ("headline", "assessment", "what_is_happening", "affected_scope", "healthy_scope", "likely_fault_domain", "uncertainty", "monitoring_guidance"):
        limit = MAX_SHORT_TEXT_CHARS if key == "headline" else MAX_TEXT_CHARS
        if not concise_string(payload.get(key), limit):
            return False
    if payload.get("confidence") not in {"low", "medium", "high"}:
        return False
    if not isinstance(payload.get("next_steps"), list):
        return False
    for key in ("evidence", "limitations", "evidence_that_would_change_assessment"):
        items = payload.get(key)
        if not isinstance(items, list) or len(items) > MAX_LIST_ITEMS:
            return False
        if any(not concise_string(item, MAX_STEP_TEXT_CHARS) for item in items):
            return False
    if len(payload["next_steps"]) > MAX_LIST_ITEMS:
        return False
    for item in payload["next_steps"]:
        if not isinstance(item, dict):
            return False
        for key in ("id", "label", "reason", "expected_observation", "assessment_change"):
            if not concise_string(item.get(key), MAX_STEP_TEXT_CHARS):
                return False
    return True


def acquire_generation_lock(input_hash, requested_model):
    try:
        if LOCK_OUT.exists():
            age_seconds = time.time() - LOCK_OUT.stat().st_mtime
            if age_seconds > LOCK_STALE_SECONDS:
                LOCK_OUT.unlink()
        fd = os.open(str(LOCK_OUT), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    payload = generation_state(
        input_hash=input_hash,
        status="generating",
        requested_model=requested_model,
    )
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return True


def release_generation_lock():
    try:
        LOCK_OUT.unlink()
    except FileNotFoundError:
        pass


def generation_state(input_hash=None, status="idle", **extra):
    payload = {
        "schema_version": 2,
        "generated_at": iso_utc(utc_now()),
        "input_hash": input_hash,
        "status": status,
        "provider": "openrouter",
    }
    payload.update(extra)
    return payload


def load_input():
    source_file = str(INPUT.relative_to(BASE))
    if not INPUT.exists():
        return None, source_file, "Operator assistant input artifact was not found."
    try:
        payload = json.loads(INPUT.read_text())
    except (OSError, json.JSONDecodeError):
        return None, source_file, "Operator assistant input artifact was unreadable."
    if not isinstance(payload, dict):
        return None, source_file, "Operator assistant input artifact was invalid."
    return payload, source_file, None


def load_operator_charter():
    try:
        charter = OPERATOR_CHARTER.read_text().strip()
    except OSError:
        return None, "Operator Charter was not available. No OpenRouter review was requested."
    if not charter:
        return None, "Operator Charter was empty. No OpenRouter review was requested."
    return charter, None


def bounded_list(items, limit, *, item_type=None):
    values = []
    for item in safe_list(items)[:limit]:
        if item_type is not None and not isinstance(item, item_type):
            continue
        values.append(item)
    return values


def base_payload(
    source_file,
    input_payload,
    *,
    status,
    input_hash,
    reason=None,
    requested_model=None,
    provider_model=None,
):
    payload = {
        "schema_version": 2,
        "generated_at": iso_utc(utc_now()),
        "status": status,
        "provider": "openrouter",
        "input_hash": input_hash,
        "requested_model": requested_model,
        "provider_model": provider_model,
        "source_file": source_file,
        "source_generated_at": safe_dict(input_payload).get("generated_at"),
        "source_investigation_id": safe_dict(safe_dict(input_payload).get("investigation")).get("id"),
        "assessment": None,
        "headline": None,
        "what_is_happening": None,
        "affected_scope": None,
        "healthy_scope": None,
        "likely_fault_domain": None,
        "uncertainty": None,
        "evidence_that_would_change_assessment": [],
        "monitoring_guidance": None,
        "confidence": None,
        "evidence": [],
        "limitations": [],
        "next_steps": [],
        "note": "Prime Observer deterministic evidence remains authoritative; this is the primary operator-facing interpretation generated from that evidence package.",
    }
    if reason:
        payload["reason"] = reason
    return payload


def unavailable_payload(source_file, input_payload, reason, *, input_hash, requested_model=None):
    payload = base_payload(
        source_file,
        input_payload,
        status="unavailable",
        input_hash=input_hash,
        reason=reason,
        requested_model=requested_model,
    )
    payload["limitations"] = [reason]
    return payload


def prompt_messages(input_payload, operator_charter):
    body = json.dumps(input_payload, indent=2, sort_keys=True)
    return [
        {
            "role": "system",
            "content": operator_charter,
        },
        {
            "role": "user",
            "content": (
                "Act as an experienced network reliability engineer reviewing a deterministic Prime Observer evidence package. "
                "Synthesize what is happening, what is affected, what appears healthy, the most likely fault domain, confidence, uncertainty, recommended next actions, evidence that would change the assessment, and what to monitor next. "
                "Use engineering judgment, but distinguish observed facts from likely inference and unknowns. "
                "In the assessment, lead with the practical operator conclusion, then explain decisive reasoning and material uncertainty. "
                "Do not inventory the package or repeat metrics, timestamps, providers, or contextual signals unless they materially change the interpretation. "
                "Do not mention environmental context based only on proximity or coincidence. "
                "Write all operator-facing text in plain English only. "
                "Express supporting evidence qualitatively when exact measurements are unnecessary. "
                "Never call a measurement elevated, degraded, failed, or lossy unless the package supplies that classification; bad samples are not packet failures. "
                "Preserve current attribution and investigation-window attribution as their exact supplied scopes, and calibrate confidence when they disagree. "
                "Use the evidence list for only the facts that support that explanation, and recommend prioritized observations that reduce uncertainty.\n\n"
                f"{STRUCTURED_OUTPUT_INSTRUCTIONS}\n\n"
                f"Evidence package:\n{body}"
            ),
        },
    ]


def response_schema():
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "prime_observer_operator_assistant_review",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string", "maxLength": MAX_SHORT_TEXT_CHARS},
                    "assessment": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                    "what_is_happening": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                    "affected_scope": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                    "healthy_scope": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                    "likely_fault_domain": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                    "uncertainty": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": MAX_STEP_TEXT_CHARS},
                        "maxItems": MAX_LIST_ITEMS,
                    },
                    "limitations": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": MAX_STEP_TEXT_CHARS},
                        "maxItems": MAX_LIST_ITEMS,
                    },
                    "next_steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "maxLength": MAX_SHORT_TEXT_CHARS},
                                "label": {"type": "string", "maxLength": MAX_SHORT_TEXT_CHARS},
                                "reason": {"type": "string", "maxLength": MAX_STEP_TEXT_CHARS},
                                "expected_observation": {"type": "string", "maxLength": MAX_STEP_TEXT_CHARS},
                                "assessment_change": {"type": "string", "maxLength": MAX_STEP_TEXT_CHARS},
                            },
                            "required": ["id", "label", "reason", "expected_observation", "assessment_change"],
                            "additionalProperties": False,
                        },
                        "maxItems": MAX_LIST_ITEMS,
                    },
                    "evidence_that_would_change_assessment": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": MAX_STEP_TEXT_CHARS},
                        "maxItems": MAX_LIST_ITEMS,
                    },
                    "monitoring_guidance": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                },
                "required": [
                    "headline", "assessment", "what_is_happening", "affected_scope",
                    "healthy_scope", "likely_fault_domain", "confidence", "uncertainty",
                    "evidence", "limitations", "next_steps",
                    "evidence_that_would_change_assessment", "monitoring_guidance"
                ],
                "additionalProperties": False,
            },
        },
    }


def post_chat_completion(request_payload, config):
    data = json.dumps(request_payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {config['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-OpenRouter-Title": config["APP_TITLE"],
    }
    if config["HTTP_REFERER"]:
        headers["HTTP-Referer"] = config["HTTP_REFERER"]

    request = urllib.request.Request(
        OPENROUTER_URL,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config["OPENROUTER_TIMEOUT_SECONDS"]) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        if chunks:
            return "".join(chunks)
    return ""


def parse_model_review(api_payload):
    choices = safe_list(api_payload.get("choices"))
    if not choices:
        raise ValueError("OpenRouter response did not include any choices.")
    finish_reason = choices[0].get("finish_reason")
    if finish_reason in {"length", "content_filter"}:
        raise ValueError(f"OpenRouter response was incomplete ({finish_reason}).")
    message = safe_dict(choices[0].get("message"))
    content = parse_content(message.get("content"))
    if not content:
        raise ValueError("OpenRouter response did not include message content.")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("OpenRouter response content was not a JSON object.")
    return parsed


def build_request_payload(input_payload, config, operator_charter):
    return {
        "model": config["OPENROUTER_MODEL"],
        "messages": prompt_messages(input_payload, operator_charter),
        "response_format": response_schema(),
        "max_tokens": config["OPENROUTER_MAX_TOKENS"],
    }


def request_model_review(request_payload, config):
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS_PER_HASH + 1):
        try:
            api_payload = post_chat_completion(request_payload, config)
            return api_payload, parse_model_review(api_payload), attempt
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
            last_error = exc
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc

        sleep_seconds = config.get("OPENROUTER_RETRY_SLEEP_SECONDS", 0)
        if attempt < MAX_ATTEMPTS_PER_HASH and sleep_seconds:
            time.sleep(float(sleep_seconds))

    if last_error is not None:
        raise last_error
    raise RuntimeError("OpenRouter request failed without an exception.")


def review_payload(source_file, input_payload, model_review, api_payload, config):
    payload = base_payload(
        source_file,
        input_payload,
        status="ok",
        input_hash=safe_dict(input_payload).get("input_hash"),
        requested_model=config["OPENROUTER_MODEL"],
        provider_model=api_payload.get("model"),
    )
    payload["headline"] = model_review.get("headline")
    payload["assessment"] = model_review.get("assessment")
    payload["what_is_happening"] = model_review.get("what_is_happening")
    payload["affected_scope"] = model_review.get("affected_scope")
    payload["healthy_scope"] = model_review.get("healthy_scope")
    payload["likely_fault_domain"] = model_review.get("likely_fault_domain")
    payload["uncertainty"] = model_review.get("uncertainty")
    payload["confidence"] = model_review.get("confidence")
    payload["evidence"] = bounded_list(model_review.get("evidence"), MAX_LIST_ITEMS, item_type=str)
    payload["limitations"] = bounded_list(model_review.get("limitations"), MAX_LIST_ITEMS, item_type=str)
    payload["evidence_that_would_change_assessment"] = bounded_list(
        model_review.get("evidence_that_would_change_assessment"),
        MAX_LIST_ITEMS,
        item_type=str,
    )
    payload["monitoring_guidance"] = model_review.get("monitoring_guidance")
    payload["next_steps"] = [
        {
            "id": item.get("id"),
            "label": item.get("label"),
            "reason": item.get("reason"),
            "expected_observation": item.get("expected_observation"),
            "assessment_change": item.get("assessment_change"),
        }
        for item in bounded_list(model_review.get("next_steps"), MAX_LIST_ITEMS)
        if isinstance(item, dict)
    ]
    payload["provider_response_id"] = api_payload.get("id")
    usage = safe_dict(api_payload.get("usage"))
    if usage:
        payload["usage"] = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    return payload


def write_json_atomic(payload, path=None):
    target = path or OUT
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(target)


def failure_result(source_file, input_payload, reason, *, input_hash=None, requested_model=None, error_category="provider_error"):
    existing = load_json_file(OUT)
    reusable = existing if valid_output_payload(existing, input_hash) else None
    retryable = error_category in {
        "provider_http_error",
        "provider_transport_error",
        "provider_invalid_response",
        "input_unavailable",
    }
    state = generation_state(
        input_hash=input_hash,
        status="retry_wait" if retryable else "failed",
        requested_model=requested_model,
        last_error_category=error_category,
        last_error=reason,
        output_validation_result="retained_previous" if reusable else "no_valid_output",
    )
    if retryable:
        state["next_retry_at"] = iso_utc(utc_now() + dt.timedelta(seconds=RETRY_BACKOFF_SECONDS[0]))
    if reusable:
        return {"payload": reusable, "should_write": False, "state_payload": state, "should_write_state": True}
    return {
        "payload": unavailable_payload(source_file, input_payload, reason, input_hash=input_hash, requested_model=requested_model),
        "should_write": False,
        "state_payload": state,
        "should_write_state": True,
    }


def duplicate_generation_result(source_file, input_payload, input_hash, requested_model):
    existing = load_json_file(OUT)
    reusable = existing if valid_output_payload(existing, input_hash) else None
    state = generation_state(
        input_hash=input_hash,
        status="generating",
        requested_model=requested_model,
        output_validation_result="retained_previous" if reusable else "pending_no_valid_output",
    )
    return {
        "payload": reusable or unavailable_payload(
            source_file,
            input_payload,
            "Operator Assistant generation is already in progress for this evidence package.",
            input_hash=input_hash,
            requested_model=requested_model,
        ),
        "should_write": False,
        "state_payload": state,
        "should_write_state": True,
    }


def build_output_result(force=False, lock_owned=False):
    config = load_config()
    print_configuration_diagnostics(config)
    print(f"Requested OpenRouter model: {config['OPENROUTER_MODEL']}")
    print("Requesting Operator Assistant review only when no valid current output exists.")

    input_payload, source_file, error = load_input()
    input_hash = safe_dict(input_payload).get("input_hash")
    if error:
        return failure_result(source_file, None, error, input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="input_unavailable")

    input_payload = safe_dict(input_payload)

    if not valid_input_hash(input_hash):
        return failure_result(source_file, input_payload, "Operator assistant input artifact is missing a valid producer-generated input_hash.", input_hash=None, requested_model=config["OPENROUTER_MODEL"], error_category="input_invalid")

    existing = load_json_file(OUT)
    existing_payload = safe_dict(existing)
    if not force and valid_output_payload(existing_payload, input_hash) and existing_payload.get("requested_model") == config["OPENROUTER_MODEL"]:
        return {
            "payload": existing_payload,
            "should_write": False,
            "state_payload": generation_state(
                input_hash=input_hash,
                status="complete",
                requested_model=config["OPENROUTER_MODEL"],
                completed_at=existing_payload.get("generated_at"),
                output_validation_result="valid_current",
            ),
            "should_write_state": True,
        }

    if safe_dict(input_payload.get("investigation")).get("source_status") == "unavailable":
        return failure_result(source_file, input_payload, "Operator assistant input is unavailable, so no OpenRouter review was requested.", input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="input_unavailable")

    operator_charter, charter_error = load_operator_charter()
    if charter_error:
        return failure_result(source_file, input_payload, charter_error, input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="charter_unavailable")

    if not config["OPENROUTER_API_KEY"]:
        return failure_result(source_file, input_payload, "OPENROUTER_API_KEY not configured. No OpenRouter review was requested.", input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="provider_unconfigured")

    acquired_lock = lock_owned
    if not acquired_lock:
        acquired_lock = acquire_generation_lock(input_hash, config["OPENROUTER_MODEL"])
        if not acquired_lock:
            return duplicate_generation_result(source_file, input_payload, input_hash, config["OPENROUTER_MODEL"])

    request_payload = build_request_payload(input_payload, config, operator_charter)
    print(f"Sending OpenRouter request with configured model: {config['OPENROUTER_MODEL']}")
    try:
        api_payload, model_review, attempts = request_model_review(request_payload, config)
    except urllib.error.HTTPError as exc:
        return failure_result(source_file, input_payload, f"OpenRouter request failed with HTTP {exc.code}.", input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="provider_http_error")
    except urllib.error.URLError as exc:
        return failure_result(source_file, input_payload, f"OpenRouter request failed: {exc.reason}.", input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="provider_transport_error")
    except (ValueError, json.JSONDecodeError) as exc:
        return failure_result(source_file, input_payload, f"OpenRouter response was invalid: {exc}.", input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="provider_invalid_response")
    finally:
        if not lock_owned:
            release_generation_lock()

    payload = review_payload(source_file, input_payload, model_review, api_payload, config)
    if not valid_output_payload(payload, input_hash):
        return failure_result(source_file, input_payload, "OpenRouter response did not satisfy the publication contract.", input_hash=input_hash, requested_model=config["OPENROUTER_MODEL"], error_category="output_validation_failed")
    return {
        "payload": payload,
        "should_write": True,
        "state_payload": generation_state(
            input_hash=input_hash,
            status="complete",
            requested_model=config["OPENROUTER_MODEL"],
            provider_model=payload.get("provider_model"),
            completed_at=payload.get("generated_at"),
            attempts=attempts,
            output_validation_result="valid_published",
        ),
        "should_write_state": True,
    }


def build_output():
    return build_output_result()["payload"]


def main():
    force = "--force" in sys.argv[1:]
    result = build_output_result(force=force)
    if result.get("should_write"):
        write_json_atomic(result["payload"])
    if result.get("should_write_state") and result.get("state_payload"):
        write_json_atomic(result["state_payload"], STATE_OUT)
    if result["payload"].get("status") == "ok":
        print(f"Operator Assistant output is valid at {OUT}")
    else:
        print("No valid Operator Assistant output was published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
