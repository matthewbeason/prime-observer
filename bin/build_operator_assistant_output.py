#!/usr/bin/env python3
from pathlib import Path
import datetime as dt
import json
import os
import shlex
import sys
import urllib.error
import urllib.request


BASE = Path("/Users/mbeason/prime-observer")
VIZ_DIR = BASE / "viz"
INPUT = VIZ_DIR / "operator_assistant_input.json"
OUT = VIZ_DIR / "operator_assistant_output.json"
ENV_FILE = BASE / ".env.openrouter"
OPERATOR_CHARTER = BASE / "docs" / "operator-charter.md"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "PrimeObserver/0.9.0"
DEFAULT_MODEL = "google/gemini-3.5-flash"
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_TOKENS = 700
MAX_LIST_ITEMS = 5
STRUCTURED_OUTPUT_INSTRUCTIONS = (
    "Return JSON only with the required schema.\n\n"
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
        "OPENROUTER_MAX_TOKENS": max(200, min(max_tokens, 2000)),
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


def load_existing_output():
    if not OUT.exists():
        return None
    try:
        payload = json.loads(OUT.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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
        "schema_version": 1,
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
        "confidence": None,
        "evidence": [],
        "limitations": [],
        "next_steps": [],
        "note": "Operator Assistant review is derived from the generated evidence package. Prime Observer evidence and deterministic observations remain authoritative.",
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
                "Explain this investigation to help the operator understand what happened. "
                "In the assessment, lead with the incident conclusion, then explain only the decisive reasoning and material uncertainty. "
                "Do not inventory the package or repeat metrics, timestamps, providers, or contextual signals unless they materially change the interpretation. "
                "Do not mention environmental context based only on proximity or coincidence. "
                "Express supporting evidence qualitatively when exact measurements are unnecessary. "
                "Never call a measurement elevated, degraded, failed, or lossy unless the package supplies that classification; bad samples are not packet failures. "
                "Preserve current attribution and investigation-window attribution as their exact supplied scopes, and calibrate confidence when they disagree. "
                "Use the evidence list for only the facts that support that explanation, and recommend the observation most likely to reduce the remaining uncertainty.\n\n"
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
                    "assessment": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_LIST_ITEMS,
                    },
                    "limitations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_LIST_ITEMS,
                    },
                    "next_steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["id", "label", "reason"],
                            "additionalProperties": False,
                        },
                        "maxItems": MAX_LIST_ITEMS,
                    },
                },
                "required": ["assessment", "confidence", "evidence", "limitations", "next_steps"],
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


def review_payload(source_file, input_payload, model_review, api_payload, config):
    payload = base_payload(
        source_file,
        input_payload,
        status="ok",
        input_hash=safe_dict(input_payload).get("input_hash"),
        requested_model=config["OPENROUTER_MODEL"],
        provider_model=api_payload.get("model"),
    )
    payload["assessment"] = model_review.get("assessment")
    payload["confidence"] = model_review.get("confidence")
    payload["evidence"] = bounded_list(model_review.get("evidence"), MAX_LIST_ITEMS, item_type=str)
    payload["limitations"] = bounded_list(model_review.get("limitations"), MAX_LIST_ITEMS, item_type=str)
    payload["next_steps"] = [
        {
            "id": item.get("id"),
            "label": item.get("label"),
            "reason": item.get("reason"),
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


def reusable_success_output(existing_payload, current_input_hash, requested_model):
    payload = safe_dict(existing_payload)
    if payload.get("status") != "ok":
        return None
    if payload.get("input_hash") != current_input_hash:
        return None
    if payload.get("requested_model") != requested_model:
        return None
    return payload


def write_json_atomic(payload):
    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with tmp.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(OUT)


def build_output_result():
    config = load_config()
    print_configuration_diagnostics(config)
    print(f"Configured OpenRouter model request: {config['OPENROUTER_MODEL']}")

    input_payload, source_file, error = load_input()
    input_hash = safe_dict(input_payload).get("input_hash")
    if error:
        return {
            "payload": unavailable_payload(
                source_file,
                None,
                error,
                input_hash=input_hash,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }

    if not isinstance(input_hash, str) or len(input_hash) != 64 or any(
        character not in "0123456789abcdef" for character in input_hash
    ):
        return {
            "payload": unavailable_payload(
                source_file,
                input_payload,
                "Operator assistant input artifact is missing a valid producer-generated input_hash.",
                input_hash=None,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }

    if safe_dict(input_payload.get("investigation")).get("source_status") == "unavailable":
        return {
            "payload": unavailable_payload(
                source_file,
                input_payload,
                "Operator assistant input is unavailable, so no OpenRouter review was requested.",
                input_hash=input_hash,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }

    operator_charter, charter_error = load_operator_charter()
    if charter_error:
        return {
            "payload": unavailable_payload(
                source_file,
                input_payload,
                charter_error,
                input_hash=input_hash,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }

    existing_output = load_existing_output()
    existing_requested_model = safe_dict(existing_output).get("requested_model")
    reused = reusable_success_output(
        existing_output,
        input_hash,
        config["OPENROUTER_MODEL"],
    )
    if reused:
        print(
            "Reusing cached Operator Assistant review because evidence hash and requested model match: "
            f"{input_hash} ({config['OPENROUTER_MODEL']})"
        )
        return {
            "payload": reused,
            "should_write": False,
        }

    if existing_output and safe_dict(existing_output).get("status") == "ok":
        existing_hash = safe_dict(existing_output).get("input_hash")
        if existing_hash == input_hash and existing_requested_model != config["OPENROUTER_MODEL"]:
            print(
                "Requesting a fresh Operator Assistant review because the configured model changed "
                f"from {existing_requested_model or 'unknown'} to {config['OPENROUTER_MODEL']}."
            )
        else:
            print(
                "Requesting a fresh Operator Assistant review because the evidence package changed."
            )
    else:
        print("Requesting a fresh Operator Assistant review because no reusable successful cache was found.")

    if not config["OPENROUTER_API_KEY"]:
        return {
            "payload": unavailable_payload(
                source_file,
                input_payload,
                "OPENROUTER_API_KEY not configured. No OpenRouter review was requested.",
                input_hash=input_hash,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }

    request_payload = build_request_payload(input_payload, config, operator_charter)
    print(f"Sending OpenRouter request with configured model: {config['OPENROUTER_MODEL']}")
    try:
        api_payload = post_chat_completion(request_payload, config)
        model_review = parse_model_review(api_payload)
    except urllib.error.HTTPError as exc:
        return {
            "payload": unavailable_payload(
                source_file,
                input_payload,
                f"OpenRouter request failed with HTTP {exc.code}.",
                input_hash=input_hash,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }
    except urllib.error.URLError as exc:
        return {
            "payload": unavailable_payload(
                source_file,
                input_payload,
                f"OpenRouter request failed: {exc.reason}.",
                input_hash=input_hash,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "payload": unavailable_payload(
                source_file,
                input_payload,
                f"OpenRouter response was invalid: {exc}.",
                input_hash=input_hash,
                requested_model=config["OPENROUTER_MODEL"],
            ),
            "should_write": True,
        }

    return {
        "payload": review_payload(source_file, input_payload, model_review, api_payload, config),
        "should_write": True,
    }


def build_output():
    return build_output_result()["payload"]


def main():
    result = build_output_result()
    if result["should_write"]:
        write_json_atomic(result["payload"])
        print(f"Wrote operator assistant output to {OUT}")
    else:
        print(f"Preserved existing operator assistant output at {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
