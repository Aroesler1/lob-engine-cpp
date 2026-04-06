import os
import subprocess
from pathlib import Path


def run_command(args, cwd):
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def executable_name(base_name: str) -> str:
    return f"{base_name}.exe" if os.name == "nt" else base_name


def test_cmake_build_and_cpp_binaries():
    repo_root = Path(__file__).resolve().parent.parent
    build_dir = repo_root / ".cmake-test-build"
    sample_file = repo_root / "data" / "sample_messages.csv"

    run_command(["cmake", "-S", str(repo_root), "-B", str(build_dir)], cwd=repo_root)
    run_command(["cmake", "--build", str(build_dir)], cwd=repo_root)

    test_binary = build_dir / executable_name("test_parser")
    book_test_binary = build_dir / executable_name("test_order_book")
    analytics_test_binary = build_dir / executable_name("test_analytics")
    cli_binary = build_dir / executable_name("lob_engine")
    benchmark_binary = build_dir / executable_name("lob_benchmark")
    analytics_output = build_dir / "analytics_map.csv"
    prediction_analytics_output = build_dir / "analytics_prediction.csv"
    prediction_report_output = build_dir / "prediction_report.csv"

    cpp_tests = run_command([str(test_binary)], cwd=build_dir)
    assert "ALL TESTS PASSED" in cpp_tests.stdout

    book_cpp_tests = run_command([str(book_test_binary)], cwd=build_dir)
    assert "ALL TESTS PASSED" in book_cpp_tests.stdout

    analytics_cpp_tests = run_command([str(analytics_test_binary)], cwd=build_dir)
    assert "ALL TESTS PASSED" in analytics_cpp_tests.stdout

    cli_run = run_command([str(cli_binary), str(sample_file)], cwd=build_dir)
    assert "Parsed:" in cli_run.stdout
    assert "Malformed:" in cli_run.stdout
    assert "Replay backend=map" in cli_run.stdout

    cli_export = run_command(
        [
            str(cli_binary),
            str(sample_file),
            "--analytics-out",
            str(analytics_output),
            "--backend",
            "both",
        ],
        cwd=build_dir,
    )
    assert "Analytics CSV=" in cli_export.stdout
    baseline_map_csv_path = analytics_output.with_name("analytics_map_map.csv")
    baseline_flat_csv_path = analytics_output.with_name("analytics_map_flat_vector.csv")
    assert baseline_map_csv_path.exists()
    assert baseline_flat_csv_path.exists()
    baseline_analytics_csv = baseline_map_csv_path.read_text()
    baseline_flat_analytics_csv = baseline_flat_csv_path.read_text()
    assert baseline_analytics_csv.startswith(
        "timestamp,best_bid,best_ask,spread,mid,bid_depth_1,bid_depth_5,bid_depth_10,"
        "ask_depth_1,ask_depth_5,ask_depth_10,order_imbalance,rolling_vwap,trade_flow_imbalance,"
        "rolling_realized_vol\n"
    )
    assert baseline_flat_analytics_csv == baseline_analytics_csv
    assert len(baseline_analytics_csv.strip().splitlines()) == 21

    cli_prediction = run_command(
        [
            str(cli_binary),
            str(sample_file),
            "--analytics-out",
            str(prediction_analytics_output),
            "--prediction-report-out",
            str(prediction_report_output),
            "--prediction-horizons",
            "100,500",
            "--backend",
            "both",
        ],
        cwd=build_dir,
    )
    assert "Prediction report=" in cli_prediction.stdout
    prediction_map_csv = prediction_analytics_output.with_name("analytics_prediction_map.csv")
    prediction_report_map = prediction_report_output.with_name("prediction_report_map.csv")
    prediction_report_flat = prediction_report_output.with_name("prediction_report_flat_vector.csv")
    assert prediction_map_csv.exists()
    assert prediction_report_map.exists()
    assert prediction_report_flat.exists()
    assert prediction_map_csv.read_text() == baseline_analytics_csv
    assert prediction_analytics_output.with_name("analytics_prediction_flat_vector.csv").read_text() == baseline_flat_analytics_csv
    prediction_report_lines = prediction_report_map.read_text().strip().splitlines()
    prediction_report_flat_lines = prediction_report_flat.read_text().strip().splitlines()
    assert prediction_report_lines[0] == (
        "horizon_messages,total_rows_seen,eligible_rows_with_valid_mid,labeled_rows,"
        "skipped_no_valid_mid,skipped_no_future_move_within_horizon,skipped_zero_signal,"
        "up_moves,down_moves,correct_predictions,incorrect_predictions,hit_rate,"
        "information_coefficient,coverage_vs_total"
    )
    assert len(prediction_report_lines) == 3
    assert prediction_report_flat_lines == prediction_report_lines

    bad_prediction = subprocess.run(
        [
            str(cli_binary),
            str(sample_file),
            "--prediction-report-out",
            str(prediction_report_output),
            "--prediction-horizons",
            "1,,2",
        ],
        cwd=build_dir,
        check=False,
        text=True,
        capture_output=True,
    )
    assert bad_prediction.returncode != 0
    assert "Prediction horizons must not contain empty entries" in bad_prediction.stderr

    cli_both = run_command(
        [str(cli_binary), str(sample_file), "--backend", "both", "--repeat", "2"],
        cwd=build_dir,
    )
    assert "Replay backend=map" in cli_both.stdout
    assert "Replay backend=flat_vector" in cli_both.stdout

    benchmark_run = run_command(
        [
            str(benchmark_binary),
            "--dataset",
            str(sample_file),
            "--backend",
            "both",
            "--reserve",
            "both",
            "--repeat",
            "5",
        ],
        cwd=build_dir,
    )
    assert "reserve=off" in benchmark_run.stdout
    assert "reserve=on" in benchmark_run.stdout
