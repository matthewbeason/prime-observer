import datetime as dt
import importlib.util
import json
import os
import plistlib
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = ROOT / "bin"
MODULE_PATH = BIN_DIR / "run_operator_assistant_worker.py"
PLIST_PATH = ROOT / "launchd" / "com.mbeason.prime-observer.operator-assistant.plist"


def load_module():
    sys.path.insert(0, str(BIN_DIR))
    try:
        spec = importlib.util.spec_from_file_location("run_operator_assistant_worker_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class OperatorAssistantWorkerTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz = self.base / "viz"
        self.viz.mkdir()
        docs = self.base / "docs"
        docs.mkdir()
        (docs / "operator-charter.md").write_text("# Operator Charter\nUse supplied evidence only.\n")
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz
        self.module.INPUT = self.viz / "operator_assistant_input.json"
        self.module.OUT = self.viz / "operator_assistant_output.json"
        self.module.STATE_OUT = self.viz / "operator_assistant_generation_state.json"
        self.module.LOCK_OUT = self.viz / ".operator_assistant_generation.lock"
        self.module.sync_producer_paths()
        self.config = {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "google/gemini-3.5-flash",
            "OPENROUTER_TIMEOUT_SECONDS": 45.0,
            "OPENROUTER_MAX_TOKENS": 3000,
            "OPENROUTER_RETRY_SLEEP_SECONDS": 0,
            "HTTP_REFERER": "",
            "APP_TITLE": "Prime Observer",
        }
        self.module.producer.load_config = lambda: dict(self.config)
        self.now = dt.datetime(2026, 7, 20, 5, 0, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def input_payload(self, input_hash="a" * 64):
        return {
            "schema_version": 2,
            "input_hash": input_hash,
            "generated_at": "2026-07-20T04:59:00Z",
            "investigation": {"id": "investigation-1", "source_status": "available"},
        }

    def write_input(self, input_hash="a" * 64):
        payload = self.input_payload(input_hash)
        self.module.INPUT.write_text(json.dumps(payload))
        return payload

    def write_state(self, status="pending", input_hash="a" * 64, **extra):
        payload = {
            "schema_version": 2,
            "status": status,
            "provider": "openrouter",
            "input_hash": input_hash,
            "requested_at": "2026-07-20T04:59:00Z",
            "updated_at": "2026-07-20T04:59:00Z",
            "attempt_count": 0,
        }
        payload.update(extra)
        self.module.STATE_OUT.write_text(json.dumps(payload))
        return payload

    def model_review(self):
        return {
            "headline": "Resolver degradation is recovering.",
            "assessment": "Resolver probes are recovering while comparison probes remain healthy.",
            "what_is_happening": "Recovery is in progress.",
            "affected_scope": "Resolver probes.",
            "healthy_scope": "Internet probes and gateway.",
            "likely_fault_domain": "Likely upstream resolver path.",
            "confidence": "high",
            "uncertainty": "Recovery still needs observation.",
            "evidence": ["Resolver degradation was sustained."],
            "limitations": ["Cause is inferred."],
            "next_steps": [{
                "id": "EXTEND_WINDOW",
                "label": "Continue recovery observation",
                "reason": "Confirm recovery holds.",
                "expected_observation": "Healthy resolver samples continue.",
                "assessment_change": "A renewed anomaly returns the event to active.",
            }],
            "evidence_that_would_change_assessment": ["Internet probes also degrade."],
            "monitoring_guidance": "Watch resolver and comparison probes.",
        }

    def api_payload(self):
        return {
            "id": "gen-1",
            "model": "google/gemini-3.5-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(self.model_review())}}],
        }

    def valid_output(self, input_hash="a" * 64):
        payload = self.input_payload(input_hash)
        return self.module.producer.review_payload(
            "viz/operator_assistant_input.json",
            payload,
            self.model_review(),
            self.api_payload(),
            self.config,
        )

    def test_missing_state_is_noop(self):
        self.write_input()
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "no_work")
        post.assert_not_called()

    def test_malformed_state_is_noop(self):
        self.write_input()
        self.module.STATE_OUT.write_text("{bad")
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "no_work")
        post.assert_not_called()

    def test_complete_state_is_noop(self):
        self.write_input()
        self.write_state("complete")
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "no_work")
        post.assert_not_called()

    def test_pending_with_valid_output_becomes_complete_without_request(self):
        self.write_input()
        self.write_state()
        self.module.OUT.write_text(json.dumps(self.valid_output()))
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "no_work")
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["output_input_hash"], "a" * 64)
        post.assert_not_called()

    def test_retry_wait_before_next_retry_is_noop(self):
        self.write_input()
        self.write_state("retry_wait", next_retry_at="2026-07-20T05:05:00Z", attempt_count=1)
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "no_work")
        post.assert_not_called()

    def test_persistent_failure_is_noop(self):
        self.write_input()
        self.write_state("failed", last_error_category="provider_unconfigured", attempt_count=1)
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "no_work")
        post.assert_not_called()

    def test_pending_generation_invokes_one_request_and_completes(self):
        self.write_input()
        self.write_state()
        post = mock.Mock(return_value=self.api_payload())
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "generation_completed")
        post.assert_called_once()
        output = json.loads(self.module.OUT.read_text())
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(output["input_hash"], "a" * 64)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["output_input_hash"], output["input_hash"])
        self.assertFalse(self.module.LOCK_OUT.exists())
        self.assertFalse(list(self.viz.glob("*.tmp")))

    def test_completed_hash_does_not_request_twice(self):
        self.write_input()
        self.write_state()
        post = mock.Mock(return_value=self.api_payload())
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "generation_completed")
        self.assertEqual(self.module.run_once(now=self.now + dt.timedelta(minutes=1)), "no_work")
        self.assertEqual(post.call_count, 1)

    def test_transient_failure_records_retry_wait_and_early_run_is_noop(self):
        self.write_input()
        self.write_state()
        post = mock.Mock(side_effect=urllib.error.URLError("temporary outage"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "retry_scheduled")
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["status"], "retry_wait")
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["next_retry_at"], "2026-07-20T05:05:00Z")
        first_count = post.call_count
        self.assertEqual(self.module.run_once(now=self.now + dt.timedelta(minutes=1)), "no_work")
        self.assertEqual(post.call_count, first_count)

    def test_retry_limit_stops_future_generation(self):
        self.write_input()
        self.write_state("retry_wait", next_retry_at="2026-07-20T04:59:00Z", attempt_count=2)
        self.module.producer.post_chat_completion = mock.Mock(side_effect=urllib.error.URLError("temporary outage"))

        self.assertEqual(self.module.run_once(now=self.now), "terminal_failure")
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["attempt_count"], 3)
        calls = self.module.producer.post_chat_completion.call_count
        self.assertEqual(self.module.run_once(now=self.now + dt.timedelta(hours=1)), "no_work")
        self.assertEqual(self.module.producer.post_chat_completion.call_count, calls)

    def test_new_semantic_hash_resets_prior_failure(self):
        self.write_input("b" * 64)
        self.write_state("failed", input_hash="a" * 64, attempt_count=3, last_error_category="provider_unconfigured")
        post = mock.Mock(return_value=self.api_payload())
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "generation_completed")
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["input_hash"], "b" * 64)
        self.assertEqual(state["attempt_count"], 1)
        post.assert_called_once()

    def test_missing_api_key_fails_without_minute_retry_loop(self):
        self.write_input()
        self.write_state()
        self.config["OPENROUTER_API_KEY"] = ""
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "terminal_failure")
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["last_error_category"], "provider_unconfigured")
        self.assertNotIn("next_retry_at", state)
        self.assertEqual(self.module.run_once(now=self.now + dt.timedelta(minutes=1)), "no_work")
        post.assert_not_called()

    def test_failed_generation_preserves_last_known_good_output(self):
        self.write_input("b" * 64)
        self.write_state(input_hash="b" * 64)
        old_output = self.valid_output("a" * 64)
        self.module.OUT.write_text(json.dumps(old_output))
        self.module.producer.post_chat_completion = mock.Mock(side_effect=urllib.error.URLError("temporary outage"))

        self.assertEqual(self.module.run_once(now=self.now), "retry_scheduled")
        self.assertEqual(json.loads(self.module.OUT.read_text()), old_output)

    def test_malformed_provider_response_preserves_last_known_good_output(self):
        self.write_input("b" * 64)
        self.write_state(input_hash="b" * 64)
        old_output = self.valid_output("a" * 64)
        self.module.OUT.write_text(json.dumps(old_output))
        self.module.producer.post_chat_completion = mock.Mock(return_value={"choices": [{"message": {"content": "{bad"}}]})

        self.assertEqual(self.module.run_once(now=self.now), "retry_scheduled")
        self.assertEqual(json.loads(self.module.OUT.read_text()), old_output)

    def test_duplicate_lock_exits_successfully_without_request(self):
        self.write_input()
        self.write_state()
        self.module.LOCK_OUT.write_text("{}")
        post = mock.Mock(side_effect=AssertionError("provider should not be called"))
        self.module.producer.post_chat_completion = post

        self.assertEqual(self.module.run_once(now=self.now), "lock_held")
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["status"], "generating")
        post.assert_not_called()

    def test_stale_lock_is_removed_and_generation_completes(self):
        self.write_input()
        self.write_state()
        self.module.LOCK_OUT.write_text("{}")
        os.utime(self.module.LOCK_OUT, (1, 1))
        self.module.producer.post_chat_completion = mock.Mock(return_value=self.api_payload())

        self.assertEqual(self.module.run_once(now=self.now), "generation_completed")
        self.assertFalse(self.module.LOCK_OUT.exists())

    def test_stale_generating_state_without_lock_recovers(self):
        self.write_input()
        self.write_state("generating", attempt_count=1, started_at="2026-07-20T04:40:00Z")
        self.module.producer.post_chat_completion = mock.Mock(return_value=self.api_payload())

        self.assertEqual(self.module.run_once(now=self.now), "generation_completed")
        state = json.loads(self.module.STATE_OUT.read_text())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["attempt_count"], 2)
        self.assertFalse(self.module.LOCK_OUT.exists())

    def test_two_worker_invocations_make_at_most_one_provider_request(self):
        self.write_input()
        self.write_state()
        entered = threading.Event()
        release = threading.Event()

        def blocking_post(_request, _config):
            entered.set()
            release.wait(timeout=5)
            return self.api_payload()

        post = mock.Mock(side_effect=blocking_post)
        self.module.producer.post_chat_completion = post
        first_result = []
        thread = threading.Thread(target=lambda: first_result.append(self.module.run_once(now=self.now, worker_id="first")))
        thread.start()
        self.assertTrue(entered.wait(timeout=5))
        second = self.module.run_once(now=self.now, worker_id="second")
        release.set()
        thread.join(timeout=5)

        self.assertEqual(second, "lock_held")
        self.assertEqual(first_result, ["generation_completed"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(json.loads(self.module.STATE_OUT.read_text())["status"], "complete")


class OperatorAssistantLaunchAgentTest(unittest.TestCase):
    def test_plist_uses_worker_without_secret_or_keepalive_loop(self):
        with PLIST_PATH.open("rb") as handle:
            payload = plistlib.load(handle)

        self.assertEqual(payload["Label"], "com.mbeason.prime-observer.operator-assistant")
        self.assertEqual(payload["ProgramArguments"][0], "/usr/bin/python3")
        self.assertEqual(payload["ProgramArguments"][1], "/Users/mbeason/Projects/prime-observer/bin/run_operator_assistant_worker.py")
        self.assertEqual(payload["WorkingDirectory"], "/Users/mbeason/Projects/prime-observer")
        self.assertEqual(payload["StartInterval"], 60)
        self.assertTrue(payload["RunAtLoad"])
        self.assertNotIn("KeepAlive", payload)
        self.assertNotIn("EnvironmentVariables", payload)
        self.assertNotIn("OPENROUTER_API_KEY", PLIST_PATH.read_text())
        self.assertEqual(payload["StandardOutPath"], "/Users/mbeason/Projects/prime-observer/logs/operator-assistant-worker.log")
        self.assertEqual(payload["StandardErrorPath"], "/Users/mbeason/Projects/prime-observer/logs/operator-assistant-worker.log")

    def test_worker_doc_creates_log_directory_before_bootstrap(self):
        body = (ROOT / "docs" / "operator-assistant-worker.md").read_text()

        self.assertIn("mkdir -p logs ~/Library/LaunchAgents", body)
        self.assertIn("logs/operator-assistant-worker.log", body)

    def test_architecture_keeps_provider_calls_out_of_transform_and_browser(self):
        transform = (ROOT / "bin" / "transform_latest.py").read_text()
        browser = (ROOT / "viz" / "investigate.html").read_text()
        worker = MODULE_PATH.read_text()

        self.assertNotIn("build_operator_assistant_output", transform)
        self.assertNotIn("openrouter.ai", transform.lower())
        self.assertNotIn("openrouter.ai", browser.lower())
        self.assertIn("import build_operator_assistant_output as producer", worker)
        self.assertNotIn("urllib.request", worker)


if __name__ == "__main__":
    unittest.main()
