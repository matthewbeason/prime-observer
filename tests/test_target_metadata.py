import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "target_metadata.py"


def load_module():
    spec = importlib.util.spec_from_file_location("target_metadata", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TargetMetadataTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_known_targets_are_classified(self):
        self.assertEqual(self.module.target_class("1.1.1.1"), "internet_probe")
        self.assertEqual(self.module.target_label("1.1.1.1"), "Cloudflare")
        self.assertEqual(self.module.target_class("9.9.9.9"), "internet_probe")
        self.assertEqual(self.module.target_label("9.9.9.9"), "Quad9")
        self.assertEqual(self.module.target_class("45.90.28.134"), "resolver_probe")
        self.assertEqual(self.module.target_label("45.90.28.134"), "NextDNS primary")
        self.assertEqual(self.module.target_class("45.90.30.134"), "resolver_probe")
        self.assertEqual(self.module.target_label("45.90.30.134"), "NextDNS secondary")
        self.assertEqual(self.module.target_class("192.168.1.1"), "gateway_probe")

    def test_resolver_targets_include_provider_neutral_dependency_metadata(self):
        primary = self.module.target_metadata("45.90.28.134")
        secondary = self.module.target_metadata("45.90.30.134")

        self.assertEqual(primary["dependency_group_id"], secondary["dependency_group_id"])
        self.assertEqual(primary["dependency_type"], "dns_resolver_pair")
        self.assertEqual(primary["role"], "primary")
        self.assertEqual(secondary["role"], "secondary")
        self.assertEqual(primary["endpoint"], "45.90.28.134")
        self.assertEqual(secondary["endpoint"], "45.90.30.134")

    def test_unknown_target_falls_back_safely(self):
        self.assertEqual(self.module.target_metadata("203.0.113.10"), {
            "target_label": "203.0.113.10",
            "target_class": "unknown_probe",
        })


if __name__ == "__main__":
    unittest.main()
