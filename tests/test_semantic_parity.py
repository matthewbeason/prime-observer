import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    path = ROOT / "bin" / f"{name}.py"
    sys.path.insert(0, str(ROOT / "bin"))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class SemanticParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transform = load_module("transform_latest")
        cls.interval = load_module("interval_summary")

    def setUp(self):
        self.start = dt.datetime(2026, 8, 24, 18, 0, tzinfo=dt.timezone.utc)
        self.end = self.start + dt.timedelta(minutes=15)

    def row(self, minute, host, p95, *, jitter=5, loss=0, received=10):
        row = {
            "ts": (self.start + dt.timedelta(minutes=minute)).isoformat(),
            "phase_label": "FIBER",
            "host": host,
            "sent": "10",
            "received": str(received),
            "loss_pct": str(loss),
            "avg_ms": "20",
            "p50_ms": "20",
            "p95_ms": str(p95),
            "max_ms": str(p95 + 10),
            "jitter_ms": str(jitter),
        }
        row.update(self.transform.target_metadata(host))
        return row

    def rows(self, *, primary=40, secondary=155, internet=35, gateway=12,
             secondary_loss=0, secondary_received=10):
        rows = []
        for minute in (1, 5, 10):
            rows.extend([
                self.row(minute, "192.168.1.1", gateway),
                self.row(minute, "1.1.1.1", internet),
                self.row(minute, "45.90.28.134", primary),
                self.row(
                    minute,
                    "45.90.30.134",
                    secondary,
                    loss=secondary_loss,
                    received=secondary_received,
                ),
            ])
        return rows

    def baseline_history(self):
        def target(member, median, p95, state):
            return {
                "identity": {"target_class": "resolver_probe", "member_id": member},
                "baseline_version": 7,
                "sample_count": 96,
                "median": median,
                "p75": p95 - 10,
                "p90": p95 - 3,
                "p95": p95,
                "accepted_state": state,
                "guardrail_status": {"status": "clear", "breaches": []},
                "baseline_change_status": "retained",
            }
        return {
            "schema_version": 1,
            "targets": {
                "FIBER|resolver_probe|nextdns_primary": target("nextdns_primary", 40, 115, "within_target"),
                "FIBER|resolver_probe|nextdns_secondary": target("nextdns_secondary", 140, 168, "elevated_but_stable"),
            },
        }

    def rapid_worsening_baseline_history(self):
        history = self.baseline_history()
        history["targets"]["FIBER|resolver_probe|nextdns_secondary"]["blocked_update"] = {
            "reasons": ["rapid_worsening"]
        }
        return history

    def app(self, *, healthy=True, timeout=False):
        return {
            "is_current": True,
            "failure_counts": {"total": 0 if healthy and not timeout else 1},
            "dns_transactions": [
                {"role": "primary", "success": healthy, "timeout": timeout},
                {"role": "secondary", "success": healthy, "timeout": timeout},
                {"role": "system", "success": healthy, "timeout": timeout},
            ],
            "https_transaction": {"success": healthy, "timeout": timeout},
        }

    def surfaces(self, rows, *, app=None, baseline=None):
        app = app or self.app()
        baseline = self.baseline_history() if baseline is None else baseline
        dashboard = self.transform.build_dashboard_health(
            rows,
            {},
            self.end,
            baseline_history=baseline,
            application_experience=app,
        )
        interval = self.interval.build_interval_summary(
            rows=rows,
            start=self.start,
            end=self.end,
            generated_at=self.end,
            incidents=[],
            health_dimensions={},
            application_experience=app,
            baseline_history=baseline,
            source_path="data/test.csv",
        )
        return dashboard, interval

    def assert_surface_badness(self, dashboard, interval, expected):
        self.assertEqual(dashboard["semantic_model_version"], "prime_observer.semantic_health.v1")
        self.assertEqual(interval["semantic_model_version"], "prime_observer.semantic_health.v1")
        self.assertEqual(dashboard["composite_wan_buckets"][0]["isBadBucket"], expected)
        self.assertEqual(interval["operator_facing_bad"], expected)

    def test_learned_normal_resolver_absolute_excursion_is_not_operator_bad(self):
        dashboard, interval = self.surfaces(self.rows(secondary=155))

        self.assert_surface_badness(dashboard, interval, False)
        resolver = interval["metrics"]["resolver"]
        self.assertTrue(resolver["absolute_threshold_excursion"])
        self.assertEqual(resolver["learned_normal_state"], "elevated_but_stable")
        self.assertEqual(resolver["state"], "elevated_but_stable")
        projected = dashboard["composite_wan_buckets"][0]["groups"]["resolver_probe"]
        self.assertEqual(projected["absoluteExcursions"], 3)
        self.assertEqual(projected["rawBad"], 0)

    def test_learned_deviation_is_operator_bad(self):
        dashboard, interval = self.surfaces(self.rows(secondary=195))

        self.assert_surface_badness(dashboard, interval, True)
        self.assertEqual(interval["metrics"]["resolver"]["learned_normal_state"], "degraded_from_baseline")

    def test_interval_preserves_persistence_carried_across_bucket_boundary(self):
        rows = [
            self.row(-1, "45.90.30.134", 195),
            self.row(1, "45.90.30.134", 195),
        ]
        dashboard, interval = self.surfaces(rows)

        dashboard_group = dashboard["composite_wan_buckets"][-1]["groups"]["resolver_probe"]
        self.assertEqual(dashboard_group["bad"], 1)
        self.assertEqual(interval["metrics"]["resolver"]["persistent_bad_samples"], 1)

    def test_packet_loss_preserves_guardrail(self):
        dashboard, interval = self.surfaces(self.rows(secondary=155, secondary_loss=3))

        self.assert_surface_badness(dashboard, interval, True)
        self.assertIn("packet_loss_above_threshold", interval["guardrail_breaches"])

    def test_timeout_preserves_guardrail(self):
        dashboard, interval = self.surfaces(
            self.rows(secondary=155, secondary_received=0),
            app=self.app(healthy=False, timeout=True),
        )

        self.assert_surface_badness(dashboard, interval, True)
        self.assertIn("timeout", interval["guardrail_breaches"])

    def test_both_resolvers_degraded_are_not_suppressed(self):
        dashboard, interval = self.surfaces(self.rows(primary=150, secondary=195))

        self.assert_surface_badness(dashboard, interval, True)
        self.assertIn("both_resolver_members_degraded", interval["guardrail_breaches"])

    def test_resolver_and_internet_correlation_remains_bad(self):
        dashboard, interval = self.surfaces(self.rows(secondary=195, internet=190))

        self.assert_surface_badness(dashboard, interval, True)
        self.assertIn(
            "broad_correlated_resolver_and_internet_degradation",
            interval["guardrail_breaches"],
        )

    def test_no_learned_baseline_uses_absolute_threshold(self):
        dashboard, interval = self.surfaces(self.rows(secondary=155), baseline={"schema_version": 1, "targets": {}})

        self.assert_surface_badness(dashboard, interval, True)
        self.assertEqual(interval["metrics"]["resolver"]["learned_normal_state"], "fallback_absolute_threshold")

    def test_elevated_but_stable_resolver_agrees_across_surfaces(self):
        dashboard, interval = self.surfaces(self.rows(secondary=160))

        self.assert_surface_badness(dashboard, interval, False)
        self.assertEqual(interval["overall_condition"], "elevated_but_stable")

    def test_application_failure_does_not_reclassify_normal_resolver_measurements(self):
        dashboard, interval = self.surfaces(
            self.rows(secondary=40),
            app=self.app(healthy=False),
        )

        self.assertFalse(dashboard["composite_wan_buckets"][0]["isBadBucket"])
        self.assertEqual(interval["metrics"]["resolver"]["operator_bad_samples"], 0)
        self.assertTrue(interval["operator_facing_bad"])
        self.assertEqual(interval["application_summary"]["state"], "failing")

    def test_gateway_degradation_is_an_explicit_interval_guardrail(self):
        dashboard, interval = self.surfaces(self.rows(gateway=180))

        self.assertFalse(dashboard["composite_wan_buckets"][0]["isBadBucket"])
        self.assertIn("gateway_degradation", interval["guardrail_breaches"])

    def test_rapid_worsening_does_not_mark_within_range_non_excursion_bad(self):
        dashboard, interval = self.surfaces(
            self.rows(secondary=100),
            baseline=self.rapid_worsening_baseline_history(),
        )

        self.assert_surface_badness(dashboard, interval, False)
        self.assertNotIn("rapid_worsening", interval["guardrail_breaches"])

    def test_rapid_worsening_keeps_absolute_excursion_bad(self):
        dashboard, interval = self.surfaces(
            self.rows(secondary=155),
            baseline=self.rapid_worsening_baseline_history(),
        )

        self.assert_surface_badness(dashboard, interval, True)
        self.assertIn("rapid_worsening", interval["guardrail_breaches"])


if __name__ == "__main__":
    unittest.main()
