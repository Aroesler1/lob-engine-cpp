# Benchmark Report

This document records the measured replay benchmark output for the small hot-path allocation reduction in this worktree. The repository still does not track generated benchmark outputs, build trees, compiled binaries, or analytics CSVs.

## Exact code changes in scope

- pre-replay reserve sizing is now derived from the parsed message stream instead of being hard-coded in the CLI and benchmark frontends
- the order lookup `std::unordered_map` now reserves to the peak active-order count implied by the replay messages
- the flat-vector backend now reserves to the peak active level count per side implied by the replay messages
- the analytics trade window is now capacity-stable, the realized-vol sample window is pre-sized from the replay message count, and `AnalyticsEngine::reset()` reuses the allocated storage
- `replay_with_analytics()` now constructs output rows in place instead of pushing a temporary `AnalyticsRow`

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

## Validation sequence

```bash
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/lob-engine-build.XXXXXX")"
cmake -S . -B "$build_dir" -DCMAKE_BUILD_TYPE=Release
cmake --build "$build_dir" --config Release
ctest --test-dir "$build_dir" --output-on-failure -C Release
python -m pytest tests -q --tb=short
```

`ctest` covers the C++ test executables plus a smoke run of `lob_benchmark`. `python -m pytest tests -q --tb=short` reruns the existing Python integration suite, which configures and reuses a separate `.cmake-test-build/` directory under the repo root and emits local analytics CSV byproducts there.

The measured validation run in this worktree passed with:

```bash
ctest --test-dir "$build_dir" --output-on-failure -C Release
python -m pytest tests -q --tb=short
```

## Measurement methodology

- baseline variant: clean `origin/main` tree at commit `d627b73`
- optimized variant: current worktree from the same base commit with the reserve/buffer changes applied
- build type: `Release`
- compiler: `g++ 13.3.0`
- host: Linux 6.8.0-106-generic on an AMD EPYC-Rome Processor
- CPU topology: 4 CPUs online, 1 thread per core
- single-core note: each benchmark process was pinned to CPU 0 via `taskset -c 0`
- warmup: one untimed AAPL run per build, `--repeat 10000`
- measured run count: `--repeat 100000` for every recorded row
- processed messages per recorded row: `20 parsed messages * 100000 repeats = 2,000,000`

Baseline and optimized trees were both measured with the same commands:

```bash
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/AAPL_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/MSFT_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/NVDA_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/TSLA_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
```

On the optimized tree, the harness now prints the derived reserve hints. For the four reduced ticker fixtures above, the measured optimized runs all derived:

- `expected_orders=3`
- `expected_levels_per_side=3`

## Before/After Results

Stable markdown table of the recorded runs:

| Variant | Dataset | Backend | Compiler / build | Run count | Processed messages | Elapsed ms | Messages/sec |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| `baseline` | `AAPL` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 39.272 | 50,926,679.688 |
| `optimized` | `AAPL` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 32.665 | 61,228,168.484 |
| `baseline` | `AAPL` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 36.477 | 54,829,135.007 |
| `optimized` | `AAPL` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 30.653 | 65,245,476.645 |
| `baseline` | `MSFT` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 34.837 | 57,409,888.578 |
| `optimized` | `MSFT` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 33.477 | 59,743,302.149 |
| `baseline` | `MSFT` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 36.481 | 54,822,944.367 |
| `optimized` | `MSFT` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 30.856 | 64,816,866.749 |
| `baseline` | `NVDA` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 37.224 | 53,729,301.089 |
| `optimized` | `NVDA` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 33.445 | 59,799,408.267 |
| `baseline` | `NVDA` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 37.173 | 53,801,878.832 |
| `optimized` | `NVDA` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 32.955 | 60,689,226.916 |
| `baseline` | `TSLA` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 34.614 | 57,780,646.523 |
| `optimized` | `TSLA` | `map` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 33.194 | 60,252,569.734 |
| `baseline` | `TSLA` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 36.451 | 54,868,240.915 |
| `optimized` | `TSLA` | `flat_vector` | `g++ 13.3.0 / Release` | 100000 | 2000000 | 31.245 | 64,010,909.507 |

Per-fixture before/after deltas:

| Dataset | Backend | Baseline msgs/sec | Optimized msgs/sec | Delta |
| --- | --- | ---: | ---: | ---: |
| `AAPL` | `map` | 50,926,679.688 | 61,228,168.484 | +20.23% |
| `AAPL` | `flat_vector` | 54,829,135.007 | 65,245,476.645 | +19.00% |
| `MSFT` | `map` | 57,409,888.578 | 59,743,302.149 | +4.06% |
| `MSFT` | `flat_vector` | 54,822,944.367 | 64,816,866.749 | +18.23% |
| `NVDA` | `map` | 53,729,301.089 | 59,799,408.267 | +11.30% |
| `NVDA` | `flat_vector` | 53,801,878.832 | 60,689,226.916 | +12.80% |
| `TSLA` | `map` | 57,780,646.523 | 60,252,569.734 | +4.28% |
| `TSLA` | `flat_vector` | 54,868,240.915 | 64,010,909.507 | +16.66% |

## Post-Optimization Backend Comparison

On these four checked-in shallow fixtures, `flat_vector` remains faster than `map` after the reserve change:

| Dataset | Optimized `map` msgs/sec | Optimized `flat_vector` msgs/sec | `flat_vector` vs `map` |
| --- | ---: | ---: | ---: |
| `AAPL` | 61,228,168.484 | 65,245,476.645 | +6.56% |
| `MSFT` | 59,743,302.149 | 64,816,866.749 | +8.49% |
| `NVDA` | 59,799,408.267 | 60,689,226.916 | +1.49% |
| `TSLA` | 60,252,569.734 | 64,010,909.507 | +6.24% |

Tradeoffs and limitations:

- `flat_vector` keeps the edge on these fixtures because the books stay extremely shallow and the contiguous level storage benefits from the tighter reserve hint
- `map` stays competitive and still has the more predictable insertion/erase profile as active depth grows
- the checked-in fixtures are tiny, synthetic, and malformed-row-heavy by design; the table above should not be extrapolated to deeper books or proprietary full-session feeds

## Scope

- timed section: replay-only
- analytics/export: correctness-validated via `cpp_analytics` and the Python wrapper, but excluded from the replay throughput timer
- backends compared: `std::map` and flat sorted `std::vector`
- baseline/optimized method: clean `origin/main` build versus current worktree build, each run with the same `--reserve on` commands

## Report regeneration

There is still no checked-in script that rewrites this file. To refresh the report, rerun the validation sequence, rebuild a clean `origin/main` tree and the current worktree in `Release`, rerun the pinned benchmark commands above, and then update this markdown with the new measured rows.
