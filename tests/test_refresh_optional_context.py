import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "bin" / "refresh_optional_context.sh"
PLIST_PATH = ROOT / "launchd" / "com.mbeason.prime-observer.nextdns-refresh.plist"
DOC_PATH = ROOT / "docs" / "nextdns-launchagent.md"


class RefreshOptionalContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "bin").mkdir(parents=True)
        self.log_path = self.base / "refresh.log"

    def tearDown(self):
        self.tmp.cleanup()

    def write_python_script(self, name, body):
        path = self.base / "bin" / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def run_wrapper(self):
        env = os.environ.copy()
        env["PRIME_OBSERVER_BASE"] = str(self.base)
        result = subprocess.run(
            ["/bin/zsh", str(SCRIPT_PATH)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return result

    def test_wrapper_runs_nextdns_then_cloudflare_then_aps(self):
        order_file = self.base / "order.txt"
        self.write_python_script(
            "fetch_nextdns_summary.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("nextdns\\n")\n'
                'print("nextdns ok")\n'
            ),
        )
        self.write_python_script(
            "fetch_cloudflare_radar.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("cloudflare\\n")\n'
                'print("cloudflare ok")\n'
            ),
        )
        self.write_python_script(
            "fetch_aps_power_context.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("aps\\n")\n'
                'print("aps ok")\n'
            ),
        )

        result = self.run_wrapper()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(order_file.read_text().splitlines(), ["nextdns", "cloudflare", "aps"])
        self.assertIn("Starting NextDNS summary refresh.", result.stdout)
        self.assertIn("Starting Internet Conditions refresh.", result.stdout)
        self.assertIn("Starting APS power context refresh.", result.stdout)
        self.assertIn("Optional context refresh finished.", result.stdout)

    def test_wrapper_keeps_later_steps_after_nextdns_failure(self):
        order_file = self.base / "order.txt"
        self.write_python_script(
            "fetch_nextdns_summary.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("nextdns\\n")\n'
                'raise SystemExit(2)\n'
            ),
        )
        self.write_python_script(
            "fetch_cloudflare_radar.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("cloudflare\\n")\n'
                'print("cloudflare ok")\n'
            ),
        )
        self.write_python_script(
            "fetch_aps_power_context.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("aps\\n")\n'
                'print("aps ok")\n'
            ),
        )

        result = self.run_wrapper()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(order_file.read_text().splitlines(), ["nextdns", "cloudflare", "aps"])
        self.assertIn("non-fatal exit code 2", result.stdout)

    def test_wrapper_keeps_aps_step_after_cloudflare_failure(self):
        order_file = self.base / "order.txt"
        self.write_python_script(
            "fetch_nextdns_summary.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("nextdns\\n")\n'
                'print("nextdns ok")\n'
            ),
        )
        self.write_python_script(
            "fetch_cloudflare_radar.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("cloudflare\\n")\n'
                'raise SystemExit(3)\n'
            ),
        )
        self.write_python_script(
            "fetch_aps_power_context.py",
            (
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\n"
                f'Path(r"{order_file}").open("a").write("aps\\n")\n'
                'print("aps ok")\n'
            ),
        )

        result = self.run_wrapper()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(order_file.read_text().splitlines(), ["nextdns", "cloudflare", "aps"])
        self.assertIn("non-fatal exit code 3", result.stdout)

    def test_launchagent_uses_refresh_wrapper(self):
        body = PLIST_PATH.read_text()
        self.assertIn("./bin/refresh_optional_context.sh", body)
        self.assertNotIn("./bin/fetch_nextdns_summary.py || true", body)

    def test_launchagent_doc_mentions_all_scheduled_optional_providers(self):
        body = DOC_PATH.read_text()
        self.assertIn("bin/refresh_optional_context.sh", body)
        self.assertIn(".env.cloudflare", body)
        self.assertIn("viz/internet_conditions.json", body)
        self.assertIn("viz/aps_power_context.json", body)
        self.assertIn("bin/fetch_aps_power_context.py", body)
        self.assertIn("No token values are printed.", body)


if __name__ == "__main__":
    unittest.main()
