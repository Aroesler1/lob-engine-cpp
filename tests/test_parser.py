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
    cli_binary = build_dir / executable_name("lob_engine")

    cpp_tests = run_command([str(test_binary)], cwd=build_dir)
    assert "ALL TESTS PASSED" in cpp_tests.stdout

    book_cpp_tests = run_command([str(book_test_binary)], cwd=build_dir)
    assert "ALL TESTS PASSED" in book_cpp_tests.stdout

    cli_run = run_command([str(cli_binary), str(sample_file)], cwd=build_dir)
    assert "Parsed:" in cli_run.stdout
    assert "Malformed:" in cli_run.stdout
    assert "Replay backend=map" in cli_run.stdout

    cli_both = run_command(
        [str(cli_binary), str(sample_file), "--backend", "both", "--repeat", "2"],
        cwd=build_dir,
    )
    assert "Replay backend=map" in cli_both.stdout
    assert "Replay backend=flat_vector" in cli_both.stdout
