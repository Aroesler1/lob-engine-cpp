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
    build_dir = repo_root / "build"
    sample_file = repo_root / "data" / "sample_messages.csv"

    run_command(["cmake", "-S", str(repo_root), "-B", str(build_dir)], cwd=repo_root)
    run_command(["cmake", "--build", str(build_dir)], cwd=repo_root)

    test_binary = build_dir / executable_name("test_parser")
    cli_binary = build_dir / executable_name("lob_engine")
    benchmark_binary = build_dir / executable_name("run_benchmarks")
    verify_binary = build_dir / executable_name("verify_correctness")

    cpp_tests = run_command([str(test_binary)], cwd=build_dir)
    assert "ALL TESTS PASSED" in cpp_tests.stdout

    cli_run = run_command([str(cli_binary), str(sample_file)], cwd=build_dir)
    assert "Parsed:" in cli_run.stdout
    assert "Malformed:" in cli_run.stdout

    verify_run = run_command([str(verify_binary), "--data", str(sample_file)], cwd=build_dir)
    assert "IDENTICAL" in verify_run.stdout

    benchmark_run = run_command(
        [
            str(benchmark_binary),
            "--data",
            str(sample_file),
            "--container",
            "map",
            "--iterations",
            "1",
            "--warmup",
            "0",
        ],
        cwd=build_dir,
    )
    assert "throughput_mean_msg_s" in benchmark_run.stdout
