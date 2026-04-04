import csv
import subprocess
import tempfile
import unittest
from pathlib import Path


EXPECTED_HEADER = [
    "timestamp",
    "best_bid",
    "best_ask",
    "spread",
    "mid",
    "bid_depth_1",
    "ask_depth_1",
    "bid_depth_5",
    "ask_depth_5",
    "bid_depth_10",
    "ask_depth_10",
    "order_imbalance",
    "rolling_vwap",
    "trade_flow_imbalance",
    "rolling_realized_vol",
]


def parse_optional_float(value: str):
    return None if value == "" else float(value)


def build_project(repo_root: Path, build_dir: Path) -> Path:
    configure = subprocess.run(
        ["cmake", "-S", str(repo_root), "-B", str(build_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if configure.returncode != 0:
        raise AssertionError(configure.stderr)

    build = subprocess.run(
        ["cmake", "--build", str(build_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise AssertionError(build.stderr)

    return build_dir / "lobster_cli"


def read_rows(csv_path: Path):
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return reader.fieldnames, rows


class AnalyticsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.build_dir = Path(cls.tempdir.name) / "build"
        cls.cli = build_project(cls.repo_root, cls.build_dir)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def run_cli(self, input_path: Path, output_path: Path):
        run = subprocess.run(
            [str(self.cli), "--analytics-out", str(output_path), str(input_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(output_path.exists())
        return run

    def test_sample_replay_emits_well_formed_analytics_csv(self):
        sample_file = self.repo_root / "data" / "sample_messages.csv"
        analytics_file = Path(self.tempdir.name) / "sample_analytics.csv"

        run = self.run_cli(sample_file, analytics_file)
        self.assertEqual(len(run.stdout.splitlines()), 25)

        header, rows = read_rows(analytics_file)
        self.assertEqual(header, EXPECTED_HEADER)

        with sample_file.open(newline="") as handle:
            input_rows = list(csv.reader(handle))
        self.assertEqual(len(rows), len(input_rows) - 1)

        trade_prices = []
        for timestamp, event_type, order_id, size, price, direction in input_rows[1:]:
            del timestamp, order_id, direction
            if int(event_type) in {4, 5, 6} and int(size) > 0 and int(price) > 0:
                trade_prices.append(int(price) / 10000.0)

        self.assertTrue(trade_prices)

        saw_empty_mid = False
        saw_vwap = False

        for row in rows:
            self.assertEqual(list(row.keys()), EXPECTED_HEADER)
            self.assertEqual(len(row), len(EXPECTED_HEADER))

            best_bid = parse_optional_float(row["best_bid"])
            best_ask = parse_optional_float(row["best_ask"])
            spread = parse_optional_float(row["spread"])
            mid = parse_optional_float(row["mid"])
            bid_depth_1 = int(row["bid_depth_1"])
            ask_depth_1 = int(row["ask_depth_1"])
            bid_depth_5 = int(row["bid_depth_5"])
            ask_depth_5 = int(row["ask_depth_5"])
            bid_depth_10 = int(row["bid_depth_10"])
            ask_depth_10 = int(row["ask_depth_10"])
            order_imbalance = float(row["order_imbalance"])
            rolling_vwap = parse_optional_float(row["rolling_vwap"])
            trade_flow_imbalance = float(row["trade_flow_imbalance"])
            rolling_realized_vol = parse_optional_float(row["rolling_realized_vol"])

            self.assertGreaterEqual(bid_depth_1, 0)
            self.assertGreaterEqual(ask_depth_1, 0)
            self.assertLessEqual(bid_depth_1, bid_depth_5)
            self.assertLessEqual(bid_depth_5, bid_depth_10)
            self.assertLessEqual(ask_depth_1, ask_depth_5)
            self.assertLessEqual(ask_depth_5, ask_depth_10)
            self.assertGreaterEqual(order_imbalance, -1.0)
            self.assertLessEqual(order_imbalance, 1.0)
            self.assertGreaterEqual(trade_flow_imbalance, -1.0)
            self.assertLessEqual(trade_flow_imbalance, 1.0)

            if best_bid is not None and best_ask is not None:
                self.assertGreaterEqual(best_ask, best_bid)
                self.assertAlmostEqual(spread, best_ask - best_bid, places=4)
                self.assertAlmostEqual(mid, (best_bid + best_ask) / 2.0, places=4)
            else:
                saw_empty_mid = True
                self.assertIsNone(spread)
                self.assertIsNone(mid)

            if rolling_vwap is not None:
                saw_vwap = True
                self.assertGreaterEqual(rolling_vwap, min(trade_prices))
                self.assertLessEqual(rolling_vwap, max(trade_prices))

            if rolling_realized_vol is not None:
                self.assertGreaterEqual(rolling_realized_vol, 0.0)

        self.assertTrue(saw_empty_mid)
        self.assertTrue(saw_vwap)

    def test_edge_case_sequence_preserves_empty_price_fields_and_trade_metrics(self):
        edge_case_file = Path(self.tempdir.name) / "edge_case.csv"
        analytics_file = Path(self.tempdir.name) / "edge_case_analytics.csv"
        edge_case_file.write_text(
            "\n".join(
                [
                    "timestamp,event_type,order_id,size,price,direction",
                    "1.000000,2,999,5,0,0",
                    "1.000001,1,1,10,1000000,1",
                    "1.000002,1,2,12,1002000,-1",
                    "1.000003,4,2,4,1002000,-1",
                    "1.000004,3,1,10,1000000,1",
                ]
            )
            + "\n"
        )

        self.run_cli(edge_case_file, analytics_file)
        header, rows = read_rows(analytics_file)
        self.assertEqual(header, EXPECTED_HEADER)
        self.assertEqual(len(rows), 5)

        row0, row1, row2, row3, row4 = rows

        self.assertEqual(row0["best_bid"], "")
        self.assertEqual(row0["best_ask"], "")
        self.assertEqual(row0["spread"], "")
        self.assertEqual(row0["mid"], "")
        self.assertEqual(float(row0["order_imbalance"]), 0.0)
        self.assertEqual(row0["rolling_vwap"], "")

        self.assertAlmostEqual(float(row1["best_bid"]), 100.0, places=4)
        self.assertEqual(row1["best_ask"], "")
        self.assertEqual(int(row1["bid_depth_1"]), 10)
        self.assertEqual(int(row1["bid_depth_5"]), 10)
        self.assertEqual(int(row1["bid_depth_10"]), 10)
        self.assertEqual(int(row1["ask_depth_10"]), 0)
        self.assertAlmostEqual(float(row1["order_imbalance"]), 1.0, places=6)

        self.assertAlmostEqual(float(row2["best_bid"]), 100.0, places=4)
        self.assertAlmostEqual(float(row2["best_ask"]), 100.2, places=4)
        self.assertAlmostEqual(float(row2["spread"]), 0.2, places=4)
        self.assertAlmostEqual(float(row2["mid"]), 100.1, places=4)

        self.assertAlmostEqual(float(row3["rolling_vwap"]), 100.2, places=4)
        self.assertAlmostEqual(float(row3["trade_flow_imbalance"]), -1.0, places=6)
        self.assertAlmostEqual(float(row3["rolling_realized_vol"]), 0.0, places=6)

        self.assertEqual(row4["best_bid"], "")
        self.assertEqual(row4["spread"], "")
        self.assertEqual(row4["mid"], "")
        self.assertAlmostEqual(float(row4["rolling_vwap"]), 100.2, places=4)

    def test_cpp_analytics_target_passes_under_ctest(self):
        ctest = subprocess.run(
            ["ctest", "--test-dir", str(self.build_dir), "--output-on-failure", "-R", "analytics_cpp"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ctest.returncode, 0, ctest.stdout + ctest.stderr)
        self.assertIn("analytics_cpp", ctest.stdout + ctest.stderr)


if __name__ == "__main__":
    unittest.main()
