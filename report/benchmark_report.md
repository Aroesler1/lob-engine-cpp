# Benchmark Report

This document is a hand-maintained reproducibility note for the replay benchmark harness. The repository does not track generated benchmark outputs, build trees, or machine-specific report artifacts.

## Fixture scope

The checked-in CSV files are reduced reproducibility fixtures that match the LOBSTER message schema, not full proprietary LOBSTER distributions.

| Dataset | Purpose | Rows on disk | Parsed rows | Malformed rows |
| --- | --- | ---: | ---: | ---: |
| `data/AAPL_sample_messages.csv` | Reduced AAPL-like benchmark fixture | 25 | 20 | 5 |
| `data/MSFT_sample_messages.csv` | Reduced MSFT-like benchmark fixture | 25 | 20 | 5 |
| `data/NVDA_sample_messages.csv` | Reduced NVDA-like benchmark fixture | 25 | 20 | 5 |
| `data/TSLA_sample_messages.csv` | Reduced TSLA-like benchmark fixture | 25 | 20 | 5 |
| `data/sample_messages.csv` | Legacy parser/integration-test alias of the AAPL fixture | 25 | 20 | 5 |

The fixtures intentionally include malformed rows so parser error accounting is exercised during correctness checks. They also include obviously synthetic values, so benchmark output should be interpreted only as a local engineering signal for this repository, not as a publishable claim about full proprietary datasets.

## Fresh-clone sequence

```bash
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/lob-engine-build.XXXXXX")"
cmake -S . -B "$build_dir" -DCMAKE_BUILD_TYPE=Release
cmake --build "$build_dir" --config Release
ctest --test-dir "$build_dir" --output-on-failure -C Release
python -m pytest tests -q --tb=short
"$build_dir/lob_benchmark" --dataset data/AAPL_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
"$build_dir/lob_benchmark" --dataset data/MSFT_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
"$build_dir/lob_benchmark" --dataset data/NVDA_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
"$build_dir/lob_benchmark" --dataset data/TSLA_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
```

`ctest` covers the C++ test executables plus a smoke run of `lob_benchmark`. `python -m pytest tests -q --tb=short` reruns the existing Python integration suite, which configures and reuses a separate `.cmake-test-build/` directory under the repo root and emits local analytics CSV byproducts there.

## Benchmark commands

```bash
"$build_dir/lob_benchmark" --dataset data/AAPL_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
"$build_dir/lob_benchmark" --dataset data/MSFT_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
"$build_dir/lob_benchmark" --dataset data/NVDA_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
"$build_dir/lob_benchmark" --dataset data/TSLA_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
```

Each command prints:

- parsed and malformed row counts
- throughput for `map` and `flat_vector`
- reserve `off` and `on`
- final top-of-book snapshot for a quick sanity check

## Scope

- timed section: replay-only
- analytics/export: available in `lob_engine`, but excluded from the replay throughput timer
- backends compared: `std::map` and flat sorted `std::vector`
- allocation mode compared: reserve/preallocation `off` vs `on`

## Report regeneration

There is no checked-in script that rewrites this file. To refresh the report, rerun the build, verifier, and benchmark commands above, then update this markdown manually with any observations you want to preserve.
