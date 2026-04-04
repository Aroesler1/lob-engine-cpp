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
    assert analytics_output.with_name("analytics_map_map.csv").exists()
    assert analytics_output.with_name("analytics_map_flat_vector.csv").exists()

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
