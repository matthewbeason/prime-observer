import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "build_operator_assistant_output.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_operator_assistant_output", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildOperatorAssistantOutputTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.original_load_config = self.module.load_config
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz_dir = self.base / "viz"
        self.viz_dir.mkdir()
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz_dir
        self.module.INPUT = self.viz_dir / "operator_assistant_input.json"
        self.module.OUT = self.viz_dir / "operator_assistant_output.json"
        self.module.ENV_FILE = self.base / ".env.openrouter"
        self.module.load_config = lambda: {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "google/gemini-3.5-flash",
            "OPENROUTER_TIMEOUT_SECONDS": 45.0,
            "OPENROUTER_MAX_TOKENS": 700,
            "HTTP_REFERER": "",
            "APP_TITLE": "Prime Observer",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write_input(self, payload):
        self.module.INPUT.write_text(json.dumps(payload))

    def capture_stdout(self, func, *args, **kwargs):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stream:
            result = func(*args, **kwargs)
        return result, stream.getvalue()

    def input_payload(self):
        return {
            "schema_version": 1,
            "generated_at": "2026-07-10T22:00:00Z",
            "investigation": {
                "id": "investigation-1",
                "source_status": "available",
            },
            "attribution": {
                "current": {"status": "likely_upstream", "label": "Likely upstream (ISP / path)"},
                "window": {"status": "inconclusive", "label": "Inconclusive"},
            },
            "episode": {
                "status": "sustained_degradation",
                "label": "Sustained degradation",
                "target_class": "resolver_probe",
            },
            "evidence": {
                "resolver": {"raw_bad_count": 8, "sustained_bad_count": 2, "max_p95_ms": 173.6},
                "internet": {"raw_bad_count": 0, "sustained_bad_count": 0, "max_p95_ms": 114.1},
                "lan": {"elevated_count": 0, "max_p95_ms": 109.4},
            },
            "environmental_context": {
                "dns": {"available": True, "status": "ok"},
                "internet_conditions": {"available": True, "status": "normal"},
                "power": {"available": True, "status": "events_reported"},
            },
            "limitations": ["Current attribution and window attribution disagree and should be preserved as separate scopes."],
        }

    def test_missing_input_writes_unavailable_output(self):
        output = self.module.build_output()

        self.assertEqual(output["status"], "unavailable")
        self.assertIn("not found", output["reason"])

    def test_missing_api_key_writes_unavailable_output(self):
        self.write_input(self.input_payload())
        self.module.load_config = lambda: {
            "OPENROUTER_API_KEY": "",
            "OPENROUTER_MODEL": "google/gemini-3.5-flash",
            "OPENROUTER_TIMEOUT_SECONDS": 45.0,
            "OPENROUTER_MAX_TOKENS": 700,
            "HTTP_REFERER": "",
            "APP_TITLE": "Prime Observer",
        }

        output = self.module.build_output()

        self.assertEqual(output["status"], "unavailable")
        self.assertIn("OPENROUTER_API_KEY not configured", output["reason"])

    def test_unavailable_input_short_circuits_review(self):
        payload = self.input_payload()
        payload["investigation"]["source_status"] = "unavailable"
        self.write_input(payload)

        output = self.module.build_output()

        self.assertEqual(output["status"], "unavailable")
        self.assertIn("input is unavailable", output["reason"])

    def test_successful_review_writes_bounded_output(self):
        self.write_input(self.input_payload())

        def fake_post(request_payload, config):
            self.assertEqual(request_payload["model"], "google/gemini-3.5-flash")
            self.assertIn("response_format", request_payload)
            return {
                "id": "gen-123",
                "model": "openai/gpt-5",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assessment": "Resolver-path degradation is the strongest supported explanation.",
                                    "confidence": "medium",
                                    "evidence": [
                                        "Resolver probes recorded sustained bad samples.",
                                        "Internet probes stayed below bad-sample thresholds.",
                                    ],
                                    "limitations": [
                                        "Current and window attribution differ.",
                                    ],
                                    "next_steps": [
                                        {
                                            "id": "COMPARE_RESOLVER_AND_INTERNET",
                                            "label": "Compare resolver and internet evidence",
                                            "reason": "Resolver probes were isolated.",
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }

        self.module.post_chat_completion = fake_post
        output = self.module.build_output()

        self.assertEqual(output["status"], "ok")
        self.assertEqual(output["requested_model"], "google/gemini-3.5-flash")
        self.assertEqual(output["provider_model"], "openai/gpt-5")
        self.assertRegex(output["input_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(output["assessment"], "Resolver-path degradation is the strongest supported explanation.")
        self.assertEqual(output["confidence"], "medium")
        self.assertEqual(output["provider_response_id"], "gen-123")
        self.assertEqual(output["usage"]["total_tokens"], 150)
        self.assertLessEqual(len(output["evidence"]), 5)
        self.assertLessEqual(len(output["next_steps"]), 5)

    def test_rebuild_timestamp_does_not_change_input_hash(self):
        payload = self.input_payload()
        older_hash = self.module.input_hash_for_payload(payload)
        payload["generated_at"] = "2026-07-10T22:30:00Z"
        payload["investigation"]["source_generated_at"] = "2026-07-10T22:29:00Z"
        payload["environmental_context"]["dns"]["generated_at"] = "2026-07-10T22:28:00Z"
        payload["environmental_context"]["dns"]["minutes_from_event_midpoint"] = 42.0
        payload["environmental_context"]["internet_conditions"]["generated_at"] = "2026-07-10T22:27:00Z"
        payload["environmental_context"]["power"]["generated_at"] = "2026-07-10T22:26:00Z"
        newer_hash = self.module.input_hash_for_payload(payload)

        self.assertEqual(older_hash, newer_hash)

    def test_invalid_response_writes_unavailable_output(self):
        self.write_input(self.input_payload())
        self.module.post_chat_completion = lambda request_payload, config: {
            "choices": [{"message": {"content": "not-json"}}]
        }

        output = self.module.build_output()

        self.assertEqual(output["status"], "unavailable")
        self.assertIn("response was invalid", output["reason"])

    def test_unchanged_input_skips_request_and_preserves_existing_output(self):
        payload = self.input_payload()
        self.write_input(payload)
        existing = {
            "schema_version": 1,
            "generated_at": "2026-07-10T22:10:00Z",
            "status": "ok",
            "provider": "openrouter",
            "input_hash": self.module.input_hash_for_payload(payload),
            "requested_model": "google/gemini-3.5-flash",
            "provider_model": "openai/gpt-5",
            "source_file": "viz/operator_assistant_input.json",
            "source_generated_at": payload["generated_at"],
            "source_investigation_id": payload["investigation"]["id"],
            "assessment": "Existing assessment",
            "confidence": "medium",
            "evidence": ["Existing evidence"],
            "limitations": [],
            "next_steps": [],
            "note": "Operator Assistant review is derived from the generated evidence package. Prime Observer evidence and deterministic observations remain authoritative.",
        }
        self.module.OUT.write_text(json.dumps(existing))

        call_count = {"count": 0}

        def fake_post(_request_payload, _config):
            call_count["count"] += 1
            raise AssertionError("OpenRouter should not be called for unchanged evidence")

        self.module.post_chat_completion = fake_post
        result, stdout = self.capture_stdout(self.module.build_output_result)

        self.assertFalse(result["should_write"])
        self.assertEqual(call_count["count"], 0)
        self.assertEqual(result["payload"], existing)
        self.assertIn("Configured OpenRouter model request: google/gemini-3.5-flash", stdout)
        self.assertIn("Reusing cached Operator Assistant review because evidence hash and requested model match", stdout)

    def test_changed_input_requests_new_assessment(self):
        payload = self.input_payload()
        self.write_input(payload)
        existing = {
            "schema_version": 1,
            "generated_at": "2026-07-10T22:10:00Z",
            "status": "ok",
            "provider": "openrouter",
            "input_hash": "0" * 64,
            "requested_model": "google/gemini-3.5-flash",
            "provider_model": "openai/gpt-4.1",
            "source_file": "viz/operator_assistant_input.json",
            "source_generated_at": payload["generated_at"],
            "source_investigation_id": payload["investigation"]["id"],
            "assessment": "Old assessment",
            "confidence": "low",
            "evidence": [],
            "limitations": [],
            "next_steps": [],
            "note": "Operator Assistant review is derived from the generated evidence package. Prime Observer evidence and deterministic observations remain authoritative.",
        }
        self.module.OUT.write_text(json.dumps(existing))

        call_count = {"count": 0}

        def fake_post(_request_payload, _config):
            call_count["count"] += 1
            return {
                "id": "gen-456",
                "model": "openai/gpt-5",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assessment": "Fresh assessment",
                                    "confidence": "high",
                                    "evidence": [],
                                    "limitations": [],
                                    "next_steps": [],
                                }
                            )
                        }
                    }
                ],
            }

        self.module.post_chat_completion = fake_post
        result = self.module.build_output_result()

        self.assertTrue(result["should_write"])
        self.assertEqual(call_count["count"], 1)
        self.assertEqual(result["payload"]["assessment"], "Fresh assessment")

    def test_same_input_with_changed_requested_model_requests_new_assessment(self):
        payload = self.input_payload()
        self.write_input(payload)
        existing = {
            "schema_version": 1,
            "generated_at": "2026-07-10T22:10:00Z",
            "status": "ok",
            "provider": "openrouter",
            "input_hash": self.module.input_hash_for_payload(payload),
            "requested_model": "openrouter/auto",
            "provider_model": "openai/gpt-5",
            "source_file": "viz/operator_assistant_input.json",
            "source_generated_at": payload["generated_at"],
            "source_investigation_id": payload["investigation"]["id"],
            "assessment": "Old assessment",
            "confidence": "low",
            "evidence": [],
            "limitations": [],
            "next_steps": [],
            "note": "Operator Assistant review is derived from the generated evidence package. Prime Observer evidence and deterministic observations remain authoritative.",
        }
        self.module.OUT.write_text(json.dumps(existing))

        call_count = {"count": 0}

        def fake_post(request_payload, _config):
            call_count["count"] += 1
            self.assertEqual(request_payload["model"], "google/gemini-3.5-flash")
            return {
                "id": "gen-789",
                "model": "google/gemini-3.5-flash",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "assessment": "Model-specific refreshed assessment",
                                    "confidence": "medium",
                                    "evidence": [],
                                    "limitations": [],
                                    "next_steps": [],
                                }
                            )
                        }
                    }
                ],
            }

        self.module.post_chat_completion = fake_post
        result, stdout = self.capture_stdout(self.module.build_output_result)

        self.assertTrue(result["should_write"])
        self.assertEqual(call_count["count"], 1)
        self.assertEqual(result["payload"]["requested_model"], "google/gemini-3.5-flash")
        self.assertIn(
            "configured model changed from openrouter/auto to google/gemini-3.5-flash",
            stdout,
        )

    def test_default_model_is_google_gemini_flash(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = self.original_load_config()

        self.assertEqual(config["OPENROUTER_MODEL"], "google/gemini-3.5-flash")

    def test_environment_model_override_is_preferred(self):
        self.module.ENV_FILE.write_text("OPENROUTER_MODEL=openrouter/auto\n")

        with mock.patch.dict(os.environ, {"OPENROUTER_MODEL": "openai/gpt-5-mini"}, clear=True):
            config = self.original_load_config()

        self.assertEqual(config["OPENROUTER_MODEL"], "openai/gpt-5-mini")

    def test_dotenv_model_override_is_used_when_environment_missing(self):
        self.module.ENV_FILE.write_text("OPENROUTER_MODEL=openai/gpt-5-nano\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            config = self.original_load_config()

        self.assertEqual(config["OPENROUTER_MODEL"], "openai/gpt-5-nano")

    def test_explicit_openrouter_auto_override_remains_supported(self):
        self.module.ENV_FILE.write_text("OPENROUTER_MODEL=openrouter/auto\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            config = self.original_load_config()

        self.assertEqual(config["OPENROUTER_MODEL"], "openrouter/auto")

    def test_configuration_diagnostics_do_not_log_api_key_value(self):
        config = {
            "OPENROUTER_API_KEY": "super-secret-key",
            "OPENROUTER_MODEL": "google/gemini-3.5-flash",
            "OPENROUTER_TIMEOUT_SECONDS": 45.0,
            "OPENROUTER_MAX_TOKENS": 700,
            "HTTP_REFERER": "",
            "APP_TITLE": "Prime Observer",
        }

        _, stdout = self.capture_stdout(self.module.print_configuration_diagnostics, config)

        self.assertIn("API key present: yes", stdout)
        self.assertNotIn("super-secret-key", stdout)

    def test_output_write_is_independent(self):
        payload = self.input_payload()
        self.write_input(payload)
        original = self.module.INPUT.read_text()
        self.module.post_chat_completion = lambda request_payload, config: {
            "id": "gen-123",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "assessment": "Assessment",
                                "confidence": "low",
                                "evidence": [],
                                "limitations": [],
                                "next_steps": [],
                            }
                        )
                    }
                }
            ],
        }

        output = self.module.build_output()
        self.module.write_json_atomic(output)

        self.assertEqual(self.module.INPUT.read_text(), original)
        written = json.loads(self.module.OUT.read_text())
        self.assertEqual(written["status"], "ok")


if __name__ == "__main__":
    unittest.main()
