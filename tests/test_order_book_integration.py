import re
import subprocess
import tempfile
import unittest
from pathlib import Path


class OrderBookIntegrationTest(unittest.TestCase):
    def test_cli_replays_sample_file_without_negative_spreads(self):
        repo_root = Path(__file__).resolve().parents[1]
        sample_file = repo_root / "data" / "sample_messages.csv"

        with tempfile.TemporaryDirectory() as tmpdir:
            build_dir = Path(tmpdir) / "build"

            configure = subprocess.run(
                ["cmake", "-S", str(repo_root), "-B", str(build_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(configure.returncode, 0, configure.stderr)

            build = subprocess.run(
                ["cmake", "--build", str(build_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)

            cli = build_dir / "lobster_cli"
            run = subprocess.run(
                [str(cli), str(sample_file)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(run.returncode, 0, run.stderr)

            bbo_lines = [line for line in run.stdout.splitlines() if "BBO:" in line]
            self.assertGreaterEqual(len(bbo_lines), 10)

            spread_pattern = re.compile(r"spread=(\d+)")
            for line in bbo_lines:
                match = spread_pattern.search(line)
                if match:
                    self.assertGreaterEqual(int(match.group(1)), 0, line)

            self.assertNotIn("ERROR", run.stderr)


if __name__ == "__main__":
    unittest.main()
