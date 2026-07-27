import datetime as dt
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "fetch_application_experience.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fetch_application_experience", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchApplicationExperienceTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.viz_dir = self.base / "viz"
        self.module.BASE = self.base
        self.module.VIZ_DIR = self.viz_dir
        self.module.OUT = self.viz_dir / "application_experience.json"
        self.module.ENV_FILE = self.base / ".env.application_experience"
        self.now = dt.datetime(2026, 7, 27, 12, 0, tzinfo=dt.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self):
        return {
            "dns_hostname": "example.com",
            "dns_primary_resolver": "192.0.2.10",
            "dns_secondary_resolver": "192.0.2.11",
            "https_url": "https://example.com/status?token=secret",
            "timeout_seconds": 1.0,
            "stale_after_seconds": 300,
        }

    def dns_ok(self, name, resolver, timeout, *, now=None):
        return {
            "type": "direct_dns",
            "target_hostname": name,
            "resolver_endpoint": resolver,
            "checked_at": self.module.iso_utc(now or self.now),
            "status": "ok",
            "success": True,
            "latency_ms": 24.0,
            "timeout": False,
            "rcode": "NOERROR",
            "failure_category": None,
        }

    def system_ok(self, name, timeout, *, now=None):
        return {**self.dns_ok(name, "system", timeout, now=now), "type": "system_dns", "role": "system"}

    def https_ok(self, url, timeout, *, now=None):
        return {
            "target_url": self.module.sanitized_url(url),
            "checked_at": self.module.iso_utc(now or self.now),
            "status": "ok",
            "success": True,
            "http_status": 204,
            "timeout": False,
            "failure_category": None,
            "dns_duration_ms": 10.0,
            "tcp_connect_duration_ms": 20.0,
            "tls_duration_ms": 30.0,
            "time_to_first_byte_ms": 40.0,
            "total_duration_ms": 100.0,
        }

    def build_payload(self, dns_checker=None, system_dns_checker=None, https_checker=None):
        return self.module.build_payload(
            self.config(),
            now=self.now,
            dns_checker=dns_checker or self.dns_ok,
            system_dns_checker=system_dns_checker or self.system_ok,
            https_checker=https_checker or self.https_ok,
        )

    def test_all_checks_healthy(self):
        payload = self.build_payload()

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["failure_counts"]["total"], 0)
        self.assertEqual([item["role"] for item in payload["dns_transactions"]], ["primary", "secondary", "system"])
        self.assertEqual(payload["https_transaction"]["http_status"], 204)

    def test_direct_primary_slow_system_resolver_healthy(self):
        def dns_checker(name, resolver, timeout, *, now=None):
            item = self.dns_ok(name, resolver, timeout, now=now)
            if resolver == "192.0.2.10":
                item["latency_ms"] = 250.0
            return item

        payload = self.build_payload(dns_checker=dns_checker)

        self.assertEqual(payload["status"], "slow")
        self.assertEqual(payload["failure_counts"]["total"], 0)

    def test_direct_secondary_timeout(self):
        def dns_checker(name, resolver, timeout, *, now=None):
            if resolver == "192.0.2.11":
                return self.module.dns_failure("direct_dns", name, resolver, now or self.now, "timeout", timeout=True)
            return self.dns_ok(name, resolver, timeout, now=now)

        payload = self.build_payload(dns_checker=dns_checker)

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["failure_counts"]["dns"], 1)
        self.assertEqual(payload["failure_counts"]["timeouts"], 1)

    def test_both_direct_resolvers_degraded_but_system_resolver_healthy(self):
        def dns_checker(name, resolver, timeout, *, now=None):
            return self.module.dns_failure("direct_dns", name, resolver, now or self.now, "timeout", timeout=True)

        payload = self.build_payload(dns_checker=dns_checker)

        self.assertEqual(payload["failure_counts"]["dns"], 2)
        self.assertTrue(payload["dns_transactions"][2]["success"])

    def test_system_dns_timeout(self):
        def system_timeout(name, timeout, *, now=None):
            return self.module.dns_failure("system_dns", name, "system", now or self.now, "timeout", timeout=True)

        payload = self.build_payload(system_dns_checker=system_timeout)

        self.assertEqual(payload["failure_counts"]["dns"], 1)
        self.assertEqual(payload["dns_transactions"][2]["failure_category"], "timeout")

    def test_https_dns_failure(self):
        payload = self.build_payload(https_checker=lambda url, timeout, *, now=None: self.module.https_failure(url, now or self.now, "dns_failure"))

        self.assertEqual(payload["failure_counts"]["https"], 1)
        self.assertEqual(payload["https_transaction"]["failure_category"], "dns_failure")

    def test_tcp_failure(self):
        payload = self.build_payload(https_checker=lambda url, timeout, *, now=None: self.module.https_failure(url, now or self.now, "tcp_failure"))

        self.assertEqual(payload["https_transaction"]["failure_category"], "tcp_failure")

    def test_tls_failure(self):
        payload = self.build_payload(https_checker=lambda url, timeout, *, now=None: self.module.https_failure(url, now or self.now, "tls_failure"))

        self.assertEqual(payload["https_transaction"]["failure_category"], "tls_failure")

    def test_http_error(self):
        def https_error(url, timeout, *, now=None):
            item = self.https_ok(url, timeout, now=now)
            item.update({"status": "http_error", "success": False, "http_status": 503, "failure_category": "http_error"})
            return item

        payload = self.build_payload(https_checker=https_error)

        self.assertEqual(payload["https_transaction"]["http_status"], 503)
        self.assertEqual(payload["failure_counts"]["https"], 1)

    def test_high_latency_without_failure(self):
        def https_slow(url, timeout, *, now=None):
            item = self.https_ok(url, timeout, now=now)
            item["total_duration_ms"] = 1400.0
            return item

        payload = self.build_payload(https_checker=https_slow)

        self.assertEqual(payload["status"], "slow")
        self.assertEqual(payload["failure_counts"]["total"], 0)

    def test_repeated_broad_transaction_failure(self):
        def dns_fail(name, resolver, timeout, *, now=None):
            return self.module.dns_failure("direct_dns", name, resolver, now or self.now, "timeout", timeout=True)

        def system_fail(name, timeout, *, now=None):
            return self.module.dns_failure("system_dns", name, "system", now or self.now, "timeout", timeout=True)

        payload = self.build_payload(dns_checker=dns_fail, system_dns_checker=system_fail, https_checker=lambda url, timeout, *, now=None: self.module.https_failure(url, now or self.now, "tcp_failure"))

        self.assertEqual(payload["failure_counts"]["total"], 4)
        self.assertEqual(payload["failure_counts"]["timeouts"], 3)

    def test_collector_never_exposes_secrets(self):
        payload = self.build_payload()
        rendered = str(payload)

        with mock.patch.dict(os.environ, {"PRIME_OBSERVER_APP_PROBE_HTTPS_URL": "https://example.com/status?token=secret"}, clear=True):
            config = self.module.load_config()
        metadata = self.module.safe_config_metadata(config)

        self.assertNotIn("token=secret", rendered)
        self.assertNotIn("token=secret", str(metadata))
        self.assertEqual(payload["https_transaction"]["target_url"], "https://example.com/status")

    def test_no_openrouter_reference(self):
        self.assertNotIn("openrouter", MODULE_PATH.read_text().lower())


if __name__ == "__main__":
    unittest.main()
