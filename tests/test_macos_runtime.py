import plistlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "launchd" / "system"


class MacOSRuntimeArchitectureTests(unittest.TestCase):
    def load(self, leaf):
        with (SYSTEM / leaf).open("rb") as handle:
            return plistlib.load(handle)

    def test_core_daemons_drop_privileges_and_use_unique_labels(self):
        payloads = [self.load(path.name) for path in sorted(SYSTEM.glob("*.plist"))]
        self.assertEqual(len(payloads), 4)
        self.assertEqual(len({item["Label"] for item in payloads}), 4)
        for payload in payloads:
            self.assertEqual(payload["UserName"], "mbeason")
            self.assertEqual(payload["GroupName"], "staff")
            self.assertNotIn("EnvironmentVariables", payload)

    def test_collector_and_transform_preserve_intervals(self):
        collector = self.load("com.mbeason.prime-observer.collector.plist")
        transform = self.load("com.mbeason.prime-observer.transform.plist")
        self.assertEqual(collector["StartInterval"], 30)
        self.assertEqual(transform["StartInterval"], 60)
        self.assertTrue(collector["RunAtLoad"])
        self.assertTrue(transform["RunAtLoad"])

    def test_http_is_single_loopback_keepalive_service(self):
        payload = self.load("com.mbeason.prime-observer.http.plist")
        arguments = payload["ProgramArguments"]
        self.assertTrue(payload["KeepAlive"])
        self.assertEqual(arguments[arguments.index("--bind") + 1], "127.0.0.1")
        self.assertEqual(arguments[arguments.index("http.server") + 1], "8000")

    def test_core_backup_is_local_and_keeps_calendar_schedule(self):
        payload = self.load("com.mbeason.prime-observer.storage-backup.plist")
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 3, "Minute": 15})
        self.assertIn("--no-replicate", payload["ProgramArguments"])

    def test_runtime_tool_uses_explicit_domains_and_preserves_rollback(self):
        body = (ROOT / "bin" / "prime_runtime.sh").read_text()
        self.assertIn('launchctl bootstrap system', body)
        self.assertIn('launchctl bootout "gui/$OWNER_UID/$label"', body)
        self.assertIn("LaunchAgent Rollback", body)
        self.assertIn("Prime core: running (system ownership", body)
        self.assertIn("Optional iCloud replication", body)
        self.assertNotIn("sudo launchctl", body)


if __name__ == "__main__":
    unittest.main()
