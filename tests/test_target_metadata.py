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

    def test_unknown_target_falls_back_safely(self):
        self.assertEqual(self.module.target_metadata("203.0.113.10"), {
            "target_label": "203.0.113.10",
            "target_class": "unknown_probe",
        })


if __name__ == "__main__":
    unittest.main()
