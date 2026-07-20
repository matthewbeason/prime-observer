import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "build_operator_assistant_output.py"
INPUT_MODULE_PATH = ROOT / "bin" / "build_operator_assistant_input.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_operator_assistant_output", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildOperatorAssistantOutputTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.original_load_config = self.module.load_config
        input_spec = importlib.util.spec_from_file_location("build_operator_assistant_input_for_output_tests", INPUT_MODULE_PATH)
        self.input_module = importlib.util.module_from_spec(input_spec)
        input_spec.loader.exec_module(self.input_module)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz_dir = self.base / "viz"
        self.viz_dir.mkdir()
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz_dir
        self.module.INPUT = self.viz_dir / "operator_assistant_input.json"
        self.module.OUT = self.viz_dir / "operator_assistant_output.json"
        self.module.STATE_OUT = self.viz_dir / "operator_assistant_generation_state.json"
        self.module.ENV_FILE = self.base / ".env.openrouter"
        self.module.OPERATOR_CHARTER = self.base / "docs" / "operator-charter.md"
        self.module.OPERATOR_CHARTER.parent.mkdir()
        self.module.OPERATOR_CHARTER.write_text("# Operator Charter v2.0\n\nUse only supplied evidence.\n")
        self.module.load_config = lambda: {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "google/gemini-3.5-flash",
            "OPENROUTER_TIMEOUT_SECONDS": 45.0,
            "OPENROUTER_MAX_TOKENS": 3000,
            "OPENROUTER_RETRY_SLEEP_SECONDS": 0,
            "HTTP_REFERER": "",
            "APP_TITLE": "Prime Observer",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def input_payload(self):
        payload = {
            "schema_version": 2,
            "semantic_schema_version": "operator_assistant_input.v2",
            "generated_at": "2026-07-10T22:00:00Z",
            "investigation": {"id": "investigation-1", "source_status": "available"},
            "selected_event": {"id": "event-1", "target_class": "resolver_probe", "lifecycle_state": "active"},
            "operator_brief": {"headline": "Resolver degradation is active."},
            "scope_impact": {"scope_conclusion": "Resolver probes degraded while internet probes stayed healthy."},
            "evidence_argument": {"supporting_evidence": ["Resolver probes showed sustained degradation."]},
            "limitations": [],
        }
        payload["input_hash"] = self.input_module.input_hash_for_payload(payload)
        return payload

    def write_input(self, payload=None):
        self.module.INPUT.write_text(json.dumps(payload or self.input_payload()))

    def model_review(self):
        return {
            "headline": "Resolver-path degradation is the leading assessment.",
            "assessment": "Resolver probes are degraded while comparison probes narrow the scope.",
            "what_is_happening": "Sustained resolver degradation is present.",
            "affected_scope": "Resolver probes",
            "healthy_scope": "Internet probes and gateway evidence",
            "likely_fault_domain": "Most consistent with upstream resolver path.",
            "confidence": "medium",
            "uncertainty": "Provider-specific cause is not proven.",
            "evidence": ["Resolver probes recorded sustained bad samples."],
            "limitations": ["Cause is inferred, not proven."],
            "next_steps": [
                {
                    "id": "COMPARE_RESOLVER_AND_INTERNET",
                    "label": "Compare resolver and internet probes",
                    "reason": "Confirms whether scope remains resolver-specific.",
                    "expected_observation": "Resolver probes remain worse than internet probes.",
                    "assessment_change": "If internet probes degrade too, broaden the fault domain.",
                }
            ],
            "evidence_that_would_change_assessment": ["Gateway degradation appears."],
            "monitoring_guidance": "Watch recovery through the stable window.",
        }

    def fake_api(self):
        return {
            "id": "gen-123",
            "model": "google/gemini-3.5-flash-20260719",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.model_review())}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180},
        }

    def capture_stdout(self, func, *args, **kwargs):
        with mock.patch("sys.stdout") as stdout:
            result = func(*args, **kwargs)
        return result, "\n".join(str(call.args[0]) for call in stdout.write.call_args_list if call.args)

    def test_successful_output_is_validated_and_published(self):
        self.write_input()
        self.module.post_chat_completion = lambda _request, _config: self.fake_api()

        result = self.module.build_output_result()

        self.assertTrue(result["should_write"])
        self.assertEqual(result["payload"]["status"], "ok")
        self.assertEqual(result["payload"]["schema_version"], 2)
        self.assertEqual(result["payload"]["headline"], self.model_review()["headline"])
        self.assertEqual(result["payload"]["input_hash"], self.input_payload()["input_hash"])
        self.assertEqual(result["state_payload"]["status"], "complete")

    def test_missing_input_records_state_without_output_publication(self):
        result = self.module.build_output_result()

        self.assertFalse(result["should_write"])
        self.assertEqual(result["payload"]["status"], "unavailable")
        self.assertEqual(result["state_payload"]["last_error_category"], "input_unavailable")
        self.assertFalse(self.module.OUT.exists())

    def test_operator_charter_is_loaded_into_prompt(self):
        payload = self.input_payload()
        messages = self.module.prompt_messages(payload, "# Charter\nUse only supplied evidence.")

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Use only supplied evidence", messages[0]["content"])
        self.assertIn(payload["input_hash"], messages[1]["content"])
        self.assertIn("plain English", messages[1]["content"])

    def test_missing_operator_charter_fails_without_provider_request(self):
        self.write_input()
        self.module.OPERATOR_CHARTER.unlink()
        self.module.post_chat_completion = mock.Mock(side_effect=AssertionError("provider should not be called"))

        result = self.module.build_output_result()

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["last_error_category"], "charter_unavailable")
        self.module.post_chat_completion.assert_not_called()

    def test_response_schema_2_is_bounded_and_structured(self):
        schema = self.module.response_schema()["json_schema"]["schema"]

        self.assertEqual(schema["properties"]["assessment"]["maxLength"], self.module.MAX_TEXT_CHARS)
        self.assertEqual(schema["properties"]["next_steps"]["maxItems"], self.module.MAX_LIST_ITEMS)
        self.assertIn("expected_observation", schema["properties"]["next_steps"]["items"]["required"])
        self.assertIn("evidence_that_would_change_assessment", schema["required"])

    def test_unavailable_input_short_circuits_review(self):
        payload = self.input_payload()
        payload["investigation"]["source_status"] = "unavailable"
        payload["input_hash"] = self.input_module.input_hash_for_payload(payload)
        self.write_input(payload)
        self.module.post_chat_completion = mock.Mock(side_effect=AssertionError("provider should not be called"))

        result = self.module.build_output_result()

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["last_error_category"], "input_unavailable")
        self.module.post_chat_completion.assert_not_called()

    def test_missing_producer_hash_does_not_call_provider(self):
        payload = self.input_payload()
        payload.pop("input_hash")
        self.write_input(payload)
        self.module.post_chat_completion = mock.Mock(side_effect=AssertionError("provider should not be called"))

        result = self.module.build_output_result()

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["last_error_category"], "input_invalid")
        self.module.post_chat_completion.assert_not_called()

    def test_matching_valid_output_does_not_trigger_duplicate_generation(self):
        payload = self.input_payload()
        self.write_input(payload)
        existing = self.module.review_payload("viz/operator_assistant_input.json", payload, self.model_review(), self.fake_api(), self.module.load_config())
        self.module.OUT.write_text(json.dumps(existing))
        self.module.post_chat_completion = mock.Mock(side_effect=AssertionError("provider should not be called"))

        result = self.module.build_output_result()

        self.assertFalse(result["should_write"])
        self.assertEqual(result["payload"]["assessment"], existing["assessment"])
        self.assertEqual(result["state_payload"]["status"], "complete")
        self.module.post_chat_completion.assert_not_called()

    def test_changed_input_hash_requests_new_assessment(self):
        old_payload = self.input_payload()
        self.module.OUT.write_text(json.dumps(self.module.review_payload("viz/operator_assistant_input.json", old_payload, self.model_review(), self.fake_api(), self.module.load_config())))
        new_payload = self.input_payload()
        new_payload["operator_brief"] = {"headline": "Different semantics."}
        new_payload["input_hash"] = self.input_module.input_hash_for_payload(new_payload)
        self.write_input(new_payload)
        self.module.post_chat_completion = mock.Mock(return_value=self.fake_api())

        result = self.module.build_output_result()

        self.assertTrue(result["should_write"])
        self.assertEqual(self.module.post_chat_completion.call_count, 1)
        self.assertEqual(result["payload"]["input_hash"], new_payload["input_hash"])

    def test_same_input_with_changed_requested_model_requests_new_assessment(self):
        payload = self.input_payload()
        self.write_input(payload)
        existing = self.module.review_payload("viz/operator_assistant_input.json", payload, self.model_review(), self.fake_api(), self.module.load_config())
        existing["requested_model"] = "openai/previous"
        self.module.OUT.write_text(json.dumps(existing))
        self.module.post_chat_completion = mock.Mock(return_value=self.fake_api())

        result = self.module.build_output_result()

        self.assertTrue(result["should_write"])
        self.assertEqual(self.module.post_chat_completion.call_count, 1)

    def test_force_regeneration_allows_explicit_refresh(self):
        payload = self.input_payload()
        self.write_input(payload)
        existing = self.module.review_payload("viz/operator_assistant_input.json", payload, self.model_review(), self.fake_api(), self.module.load_config())
        self.module.OUT.write_text(json.dumps(existing))
        self.module.post_chat_completion = mock.Mock(return_value=self.fake_api())

        result = self.module.build_output_result(force=True)

        self.assertTrue(result["should_write"])
        self.assertEqual(self.module.post_chat_completion.call_count, 1)

    def test_duplicate_generation_is_suppressed_by_lock(self):
        payload = self.input_payload()
        self.write_input(payload)
        self.assertTrue(self.module.acquire_generation_lock(payload["input_hash"], "google/gemini-3.5-flash"))
        self.addCleanup(self.module.release_generation_lock)
        self.module.post_chat_completion = mock.Mock(side_effect=AssertionError("provider should not be called"))

        result = self.module.build_output_result(force=True)

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["status"], "generating")
        self.module.post_chat_completion.assert_not_called()

    def test_stale_generation_lock_is_replaced(self):
        payload = self.input_payload()
        self.module.LOCK_OUT.write_text("{}")
        old_time = 1
        self.module.os.utime(self.module.LOCK_OUT, (old_time, old_time))

        self.assertTrue(self.module.acquire_generation_lock(payload["input_hash"], "google/gemini-3.5-flash"))
        self.module.release_generation_lock()

    def test_provider_failure_does_not_delete_prior_valid_output(self):
        payload = self.input_payload()
        self.write_input(payload)
        existing = self.module.review_payload("viz/operator_assistant_input.json", payload, self.model_review(), self.fake_api(), self.module.load_config())
        self.module.OUT.write_text(json.dumps(existing))
        self.module.post_chat_completion = mock.Mock(side_effect=urllib.error.URLError("temporary outage"))

        result = self.module.build_output_result(force=True)

        self.assertFalse(result["should_write"])
        self.assertEqual(result["payload"], existing)
        self.assertEqual(json.loads(self.module.OUT.read_text()), existing)
        self.assertEqual(result["state_payload"]["status"], "retry_wait")
        self.assertIn("next_retry_at", result["state_payload"])

    def test_transient_provider_failure_retries_with_bounded_attempts(self):
        self.write_input()
        calls = {"count": 0}

        def flaky(_request, _config):
            calls["count"] += 1
            if calls["count"] < 3:
                raise urllib.error.URLError("temporary outage")
            return self.fake_api()

        self.module.post_chat_completion = flaky

        result = self.module.build_output_result(force=True)

        self.assertTrue(result["should_write"])
        self.assertEqual(calls["count"], 3)
        self.assertEqual(result["state_payload"]["attempts"], 3)

    def test_finish_reason_length_is_rejected_as_incomplete(self):
        self.write_input()
        self.module.post_chat_completion = lambda _request, _config: {
            "choices": [{"finish_reason": "length", "message": {"content": json.dumps(self.model_review())}}]
        }

        result = self.module.build_output_result(force=True)

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["last_error_category"], "provider_invalid_response")

    def test_malformed_output_is_rejected_without_publication(self):
        self.write_input()
        self.module.post_chat_completion = lambda _request, _config: {"choices": [{"message": {"content": "not-json"}}]}

        result = self.module.build_output_result()

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["status"], "retry_wait")
        self.assertFalse(self.module.OUT.exists())

    def test_truncated_json_is_rejected_without_publication(self):
        self.write_input()
        self.module.post_chat_completion = lambda _request, _config: {"choices": [{"message": {"content": '{"headline":"cut'}}]}

        result = self.module.build_output_result(force=True)

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["last_error_category"], "provider_invalid_response")
        self.assertFalse(self.module.OUT.exists())

    def test_valid_output_larger_than_old_token_budget_is_accepted(self):
        self.write_input()
        review = self.model_review()
        review["assessment"] = " ".join(["Resolver path degradation remains isolated to the affected resolver probes."] * 10)
        review["what_is_happening"] = " ".join(["Recovery is in progress while comparison probes remain healthy."] * 8)
        review["limitations"] = ["Cause is inferred, not proven.", "External provider context was unavailable.", "Recovery still needs observation."]
        self.module.post_chat_completion = lambda _request, _config: {
            "id": "gen-large",
            "model": "google/gemini-3.5-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(review)}}],
            "usage": {"completion_tokens": 900},
        }

        result = self.module.build_output_result(force=True)

        self.assertTrue(result["should_write"])
        self.assertGreater(len(json.dumps(result["payload"])), 700)

    def test_overlong_output_is_rejected_for_ui_concision(self):
        self.write_input()
        review = self.model_review()
        review["assessment"] = "x" * (self.module.MAX_TEXT_CHARS + 1)
        self.module.post_chat_completion = lambda _request, _config: {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(review)}}]
        }

        result = self.module.build_output_result(force=True)

        self.assertFalse(result["should_write"])
        self.assertEqual(result["state_payload"]["last_error_category"], "output_validation_failed")

    def test_request_payload_uses_larger_default_budget_and_plain_english_prompt(self):
        config = self.module.load_config()
        payload = self.module.build_request_payload(self.input_payload(), config, "Charter")

        self.assertEqual(self.module.DEFAULT_MAX_TOKENS, 3000)
        self.assertEqual(config["OPENROUTER_MAX_TOKENS"], 3000)
        self.assertEqual(payload["max_tokens"], 3000)
        self.assertIn("plain English", payload["messages"][1]["content"])

    def test_default_model_and_token_clamp_from_real_config(self):
        self.module.load_config = self.original_load_config
        with mock.patch.dict(os.environ, {"OPENROUTER_MAX_TOKENS": "999999"}, clear=True):
            config = self.module.load_config()

        self.assertEqual(config["OPENROUTER_MODEL"], "google/gemini-3.5-flash")
        self.assertEqual(config["OPENROUTER_MAX_TOKENS"], 4000)

    def test_configuration_diagnostics_do_not_log_api_key_value(self):
        config = {
            "OPENROUTER_API_KEY": "super-secret-key",
            "OPENROUTER_MODEL": "google/gemini-3.5-flash",
            "OPENROUTER_TIMEOUT_SECONDS": 45.0,
            "OPENROUTER_MAX_TOKENS": 3000,
        }

        with mock.patch("builtins.print") as printed:
            self.module.print_configuration_diagnostics(config)

        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("API key present: yes", output)
        self.assertNotIn("super-secret-key", output)

    def test_output_write_is_independent_of_input_file(self):
        payload = self.input_payload()
        self.write_input(payload)
        original = self.module.INPUT.read_text()

        self.module.write_json_atomic(self.module.review_payload("viz/operator_assistant_input.json", payload, self.model_review(), self.fake_api(), self.module.load_config()))

        self.assertEqual(self.module.INPUT.read_text(), original)
        self.assertEqual(json.loads(self.module.OUT.read_text())["status"], "ok")

    def test_missing_api_key_is_state_only_when_no_valid_output_exists(self):
        self.write_input()
        self.module.load_config = lambda: {
            "OPENROUTER_API_KEY": "",
            "OPENROUTER_MODEL": "google/gemini-3.5-flash",
            "OPENROUTER_TIMEOUT_SECONDS": 45.0,
            "OPENROUTER_MAX_TOKENS": 700,
            "HTTP_REFERER": "",
            "APP_TITLE": "Prime Observer",
        }

        result = self.module.build_output_result()

        self.assertFalse(result["should_write"])
        self.assertEqual(result["payload"]["status"], "unavailable")
        self.assertFalse(self.module.OUT.exists())
        self.assertEqual(result["state_payload"]["last_error_category"], "provider_unconfigured")

    def test_main_writes_state_separately_from_output(self):
        self.write_input()
        self.module.post_chat_completion = lambda _request, _config: self.fake_api()

        self.module.main()

        self.assertEqual(json.loads(self.module.OUT.read_text())["status"], "ok")
        self.assertEqual(json.loads(self.module.STATE_OUT.read_text())["status"], "complete")


if __name__ == "__main__":
    unittest.main()
