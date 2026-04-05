# Real-Time Limit Order Book Engine in C++

This repository implements a small, deterministic C++ limit-order-book engine for LOBSTER-style message data. The parser and replay code operate on the LOBSTER six-column message schema, but the checked-in CSVs are tiny synthetic/reduced fixtures for reproducibility, not full proprietary LOBSTER distributions. The repo includes:

- typed CSV ingestion for LOBSTER message rows
- order lifecycle processing for add, cancel, and execute events
- aggregated bid/ask levels plus order-ID lookup
- two price-level backends: `std::map` and flat sorted `std::vector`
- rolling analytics and CSV export after every processed message
- deterministic C++ and Python integration tests
- replay benchmark tooling and a hand-maintained benchmark reproducibility note

## Repository layout

- `include/lob/`: public headers for parsing, order book state, replay, and analytics
- `src/`: parser, order book engine, replay, analytics, and CLI entrypoint
- `tests/`: C++ unit tests plus Python integration coverage for the CMake workflow
- `benchmark/`: replay benchmark harness
- `data/`: checked-in small sample datasets used for deterministic tests and reproducible benchmark captures
- `report/`: benchmark and methodology notes

## Reproducible build

From a fresh clone, run the build, verifier, and benchmark commands below in order. Start with a clean temporary build directory instead of an in-repo build tree:

```bash
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/lob-engine-build.XXXXXX")"
cmake -S . -B "$build_dir" -DCMAKE_BUILD_TYPE=Release
cmake --build "$build_dir" --config Release
```

## Correctness verification

Run the CMake/CTest verifier from that build directory, then run the existing Python test suite from the repo root:

```bash
ctest --test-dir "$build_dir" --output-on-failure -C Release
python -m pytest tests -q --tb=short
```

`ctest` runs the three C++ test executables plus the `lob_benchmark_smoke` path. `python -m pytest tests -q --tb=short` configures and reuses a separate `.cmake-test-build/` directory under the repo root; that directory and the analytics CSVs produced there are ignored local test artifacts.

## CLI usage

Replay a dataset and print final top-of-book state:

```bash
"$build_dir/lob_engine" data/AAPL_sample_messages.csv --backend both --depth 10 --repeat 5
```

Export analytics rows after every processed message:

```bash
"$build_dir/lob_engine" \
  data/AAPL_sample_messages.csv \
  --backend both \
  --analytics-out "$build_dir/analytics.csv" \
  --trade-window-messages 1000 \
  --realized-vol-window-seconds 300
```

If `--backend both` is selected, the CLI writes one CSV per backend by suffixing the output path.

## Analytics

Each processed message produces a row with:

- `timestamp`
- `best_bid`, `best_ask`, `spread`, `mid`
- `bid_depth_{1,5,10}`, `ask_depth_{1,5,10}`
- `order_imbalance`
- `rolling_vwap`
- `trade_flow_imbalance`
- `rolling_realized_vol`

The default rolling windows match the project objective:

- trailing `1000` messages for trade-based metrics
- trailing `300` seconds for realized volatility

## Backends

Two backends are implemented behind the same `OrderBook` interface:

- `map`
  - sorted levels via `std::map`
  - stable `O(log n)` insert/update/remove at the level container
- `flat`
  - sorted levels in a binary-searched `std::vector`
  - better cache locality on shallow books
  - more expensive interior insert/erase at larger active depth

Deterministic parity tests assert that both backends produce identical book snapshots after each message in the shared test sequences.

## Benchmarking

The checked-in benchmark numbers below come from the existing `lob_benchmark` replay harness in `Release` mode. The timer still covers replay/book updates, not CSV export. Analytics correctness and backend parity stay covered by `test_analytics`, and the CLI analytics path now shares the same derived book reserve hints plus pre-sized rolling buffers.

Exact hot-path allocation changes in this branch:

- derive `expected_orders` from the peak active-order count in the parsed message stream before replay instead of hard-coding `messages.size()`
- derive `expected_levels_per_side` from the peak active bid/ask level count instead of hard-coding `64`
- pre-size the rolling trade window and realized-vol sample buffer in analytics, and retain that capacity across `AnalyticsEngine::reset()`
- construct `AnalyticsRow` values in place during replay instead of pushing a temporary row object per message

Measurement method used for the recorded table:

- baseline tree: clean `origin/main` checkout at `d627b73`
- optimized tree: this worktree after the reserve/buffer changes below
- build: `cmake -S . -B "$build_dir" -DCMAKE_BUILD_TYPE=Release && cmake --build "$build_dir" --config Release`
- warmup: one untimed `taskset -c 0 "$build_dir/lob_benchmark" --dataset data/AAPL_sample_messages.csv --backend both --reserve on --depth 5 --repeat 10000`
- measured commands: the four `taskset -c 0 "$build_dir/lob_benchmark" --dataset ... --backend both --reserve on --depth 5 --repeat 100000` invocations listed below
- host: Linux 6.8.0-106-generic, `g++ 13.3.0`, AMD EPYC-Rome Processor, benchmark process pinned to CPU 0

These four commands are the recorded measurement step:

```bash
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/AAPL_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/MSFT_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/NVDA_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
taskset -c 0 "$build_dir/lob_benchmark" --dataset data/TSLA_sample_messages.csv --backend both --reserve on --depth 5 --repeat 100000
```

On the optimized tree, `lob_benchmark` now prints the derived reserve hints alongside each run. For the four checked-in ticker fixtures, the derived replay hints are `expected_orders=3` and `expected_levels_per_side=3`.

Recorded throughput on this host:

| Dataset | Backend | Baseline elapsed ms | Baseline msgs/sec | Optimized elapsed ms | Optimized msgs/sec | Delta |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `AAPL` | `map` | 39.272 | 50,926,679.688 | 32.665 | 61,228,168.484 | +20.23% |
| `AAPL` | `flat_vector` | 36.477 | 54,829,135.007 | 30.653 | 65,245,476.645 | +19.00% |
| `MSFT` | `map` | 34.837 | 57,409,888.578 | 33.477 | 59,743,302.149 | +4.06% |
| `MSFT` | `flat_vector` | 36.481 | 54,822,944.367 | 30.856 | 64,816,866.749 | +18.23% |
| `NVDA` | `map` | 37.224 | 53,729,301.089 | 33.445 | 59,799,408.267 | +11.30% |
| `NVDA` | `flat_vector` | 37.173 | 53,801,878.832 | 32.955 | 60,689,226.916 | +12.80% |
| `TSLA` | `map` | 34.614 | 57,780,646.523 | 33.194 | 60,252,569.734 | +4.28% |
| `TSLA` | `flat_vector` | 36.451 | 54,868,240.915 | 31.245 | 64,010,909.507 | +16.66% |

Post-optimization backend comparison on these fixtures:

- `flat_vector` remains faster than `map` on all four reduced ticker fixtures after the change: +6.56% on `AAPL`, +8.49% on `MSFT`, +1.49% on `NVDA`, and +6.24% on `TSLA`
- the margin stays narrow because these fixtures top out at three active orders and three active levels per side after malformed rows are dropped
- these numbers are local measurements on tiny synthetic fixtures; do not generalize them to deeper books or proprietary full-session datasets

`--reserve on` enables:

- auto-derived `unordered_map::reserve()` sizing for order lookup
- auto-derived vector capacity reservation for the flat backend price-level storage

This is the bounded hot-path allocation reduction implemented in the repo. Throughput numbers are host-dependent and should be treated as local measurements on the checked-in reduced fixtures, not as publishable claims about full vendor datasets. See `report/benchmark_report.md` for the exact datasets and commands used for reproducible reruns.

## Dataset note

The repo ships five checked-in reproducibility fixtures:

- `AAPL_sample_messages.csv`
- `MSFT_sample_messages.csv`
- `NVDA_sample_messages.csv`
- `TSLA_sample_messages.csv`
- `sample_messages.csv`

The four ticker-named files are 25-line reduced fixtures with 20 valid messages plus 5 intentionally malformed rows each. `sample_messages.csv` is a legacy generic fixture with the same contents as `AAPL_sample_messages.csv`, kept because the parser and Python integration tests reference it directly.

These files are intentionally tiny and deterministic so the build, tests, and benchmark workflow can run on a fresh clone without external data dependencies. They are suitable for correctness checks and relative replay comparisons, not production-grade market simulation or claims about full vendor data.

## Why this is useful for quant / HFT workflows

This codebase gives a compact environment for validating:

- message parsing assumptions
- order-book state transitions
- top-of-book and depth analytics
- replay throughput tradeoffs between container choices
- how much simple preallocation changes replay performance on shallow books

It is intentionally small enough to audit but still structured like a real research prototype: deterministic tests, reproducible build flow, benchmark tooling, and clear documentation.
