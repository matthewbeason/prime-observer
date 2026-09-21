import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "launchd/rotation/newsyslog.conf"


class LogRotationTests(unittest.TestCase):
    def test_all_prime_launchd_log_paths_have_private_bounded_policy(self):
        configured = {}
        for line in CONFIG.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            path, owner, mode, count, size, when, flags = line.split()
            self.assertNotIn(path, configured)
            self.assertEqual((owner, mode, count, size, when),
                             ("mbeason:staff", "600", "7", "5120", "24"))
            self.assertEqual(set(flags), set("BNZ"))
            configured[path] = True
        referenced = set()
        for plist in (ROOT / "launchd").rglob("*.plist"):
            with plist.open("rb") as handle:
                payload = plistlib.load(handle)
            for key in ("StandardOutPath", "StandardErrorPath"):
                if key in payload:
                    referenced.add(payload[key])
        self.assertEqual(set(configured), referenced)

    def test_native_rotation_retains_compressed_private_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sample.log"
            log.write_text("prime rotation test\n")
            config = Path(directory) / "newsyslog.conf"
            config.write_text(f"{log} 600 7 1 24 BNZ\n")
            dry = subprocess.run(
                ["/usr/sbin/newsyslog", "-nr", "-f", str(config)],
                capture_output=True, text=True, check=True)
            self.assertIn("trimming", dry.stdout)
            self.assertFalse((Path(str(log) + ".0.gz")).exists())
            subprocess.run(["/usr/sbin/newsyslog", "-Fr", "-f", str(config)],
                           capture_output=True, text=True, check=True)
            archive = Path(str(log) + ".0.gz")
            self.assertTrue(archive.exists())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            for generation in range(1, 9):
                log.write_text(f"generation {generation}\n")
                subprocess.run(["/usr/sbin/newsyslog", "-Fr", "-f", str(config)],
                               capture_output=True, text=True, check=True)
            # macOS newsyslog keeps numbered generations .0 through .7.
            self.assertEqual(len(list(Path(directory).glob("sample.log.*.gz"))), 8)
            self.assertFalse(Path(str(log) + ".8.gz").exists())


if __name__ == "__main__":
    unittest.main()
