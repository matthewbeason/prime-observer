import datetime as dt
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "interval_summary.py"


def load_module():
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location("interval_summary", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class IntervalSummaryTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.start = dt.datetime(2026, 8, 2, 21, 0, tzinfo=dt.timezone.utc)
        self.end = self.start + dt.timedelta(minutes=30)

    def row(self, minute, host, p95, jitter=5, loss=0, received="10"):
        return {
            "ts": (self.start + dt.timedelta(minutes=minute)).isoformat(),
            "phase_label": "fiber",
            "host": host,
            "sent": "10",
            "received": received,
            "loss_pct": str(loss),
            "avg_ms": "20",
            "p50_ms": "20",
            "p95_ms": str(p95),
            "max_ms": str(p95 + 10),
            "jitter_ms": str(jitter),
        }

    def rows(self, resolver=35, internet=30, gateway=8, loss=0, received="10"):
        out = []
        for minute in (0, 10, 20):
            out.extend([
                self.row(minute, "192.168.1.1", gateway),
                self.row(minute, "1.1.1.1", internet),
                self.row(minute, "45.90.30.134", resolver, loss=loss, received=received),
            ])
        return out

    def app(self, dns=True, https=True, timeout=False):
        return {
            "is_current": True,
            "dns_transactions": [
                {"role": "secondary", "resolver_endpoint": "45.90.30.134", "success": dns, "timeout": timeout},
                {"role": "system", "success": dns, "timeout": timeout},
            ],
            "https_transaction": {"success": https, "timeout": timeout},
            "failure_counts": {"total": 0 if dns and https and not timeout else 1},
            "evidence": ["DNS and HTTPS checks completed."],
        }

    def health(self, state="within_target", eligible=False):
        return {
            "adaptive_baseline": {
                "resolver_members": [{
                    "member_id": "nextdns_secondary",
                    "role": "secondary",
                    "baseline_state": state,
                    "baseline_source": "durable",
                    "incident_eligible": eligible,
                    "incident_suppression_reason": "established_degraded_baseline" if not eligible else None,
                }]
            }
        }

    def build(self, rows=None, **kwargs):
        return self.module.build_interval_summary(
            rows=rows or self.rows(),
            start=self.start,
            end=self.end,
            generated_at=self.end,
            incidents=kwargs.get("incidents", []),
            health_dimensions=kwargs.get("health_dimensions", self.health()),
            application_experience=kwargs.get("application_experience", self.app()),
            source_path="data/test.csv",
        )

    def test_healthy_interval(self):
        summary = self.build()

        self.assertEqual(summary["overall_condition"], "healthy")
        self.assertEqual(summary["incident_overlap"]["count"], 0)
        self.assertEqual(summary["metrics"]["dns_success"], True)
        self.assertEqual(summary["metrics"]["https_success"], True)

    def test_adaptive_baseline_interval(self):
        summary = self.build(rows=self.rows(resolver=176), health_dimensions=self.health("elevated_but_stable", False))

        self.assertEqual(summary["overall_condition"], "elevated_but_stable")
        self.assertEqual(summary["likely_issue"], "Established degraded resolver baseline")
        self.assertIn("established degraded baseline", summary["summary"])

    def test_loss_and_timeout_are_reported(self):
        loss = self.build(rows=self.rows(resolver=176, loss=3), health_dimensions=self.health("failing", True))
        timeout = self.build(rows=self.rows(resolver=176, received="0"), application_experience=self.app(timeout=True), health_dimensions=self.health("failing", True))

        self.assertEqual(loss["metrics"]["resolver"]["state"], "failing")
        self.assertGreater(loss["metrics"]["resolver"]["loss_rate_pct"], 1)
        self.assertGreater(timeout["metrics"]["timeout_count"], 0)
        self.assertEqual(timeout["application_summary"]["state"], "failing")

    def test_dns_https_and_gateway_failure(self):
        app_failure = self.build(application_experience=self.app(dns=False, https=False))
        gateway_failure = self.build(rows=self.rows(gateway=180))

        self.assertEqual(app_failure["application_summary"]["state"], "failing")
        self.assertEqual(app_failure["likely_issue"], "Application transaction failure")
        self.assertEqual(gateway_failure["metrics"]["gateway"]["state"], "elevated")
        self.assertIn("Gateway", gateway_failure["affected_services"])

    def test_incident_overlap_none_partial_one_and_multiple(self):
        incidents = [
            {"id": "before", "target_class": "resolver_probe", "start": (self.start - dt.timedelta(hours=1)).isoformat(), "end": (self.start - dt.timedelta(minutes=30)).isoformat()},
            {"id": "partial", "target_class": "resolver_probe", "start": (self.start - dt.timedelta(minutes=5)).isoformat(), "end": (self.start + dt.timedelta(minutes=5)).isoformat()},
            {"id": "inside", "target_class": "internet_probe", "start": (self.start + dt.timedelta(minutes=10)).isoformat(), "end": (self.start + dt.timedelta(minutes=20)).isoformat()},
        ]
        summary = self.build(incidents=incidents)

        self.assertEqual(summary["incident_overlap"]["count"], 2)
        relations = {item["id"]: item["relation"] for item in summary["incident_overlap"]["items"]}
        self.assertEqual(relations["partial"], "partial_overlap")
        self.assertEqual(relations["inside"], "incident_inside_interval")

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            self.module.build_interval_summary(rows=[], start=self.end, end=self.start, generated_at=self.end)


if __name__ == "__main__":
    unittest.main()
