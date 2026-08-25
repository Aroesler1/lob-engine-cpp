# Real-Time Limit Order Book Engine in C++

This repository implements a small, deterministic C++ limit-order-book engine for LOBSTER-style message data. The parser and replay code operate on the LOBSTER six-column message schema, but the checked-in CSVs are tiny synthetic/reduced fixtures for reproducibility, not full proprietary LOBSTER distributions. The repo includes:

- typed CSV ingestion for LOBSTER message rows
- order lifecycle processing for add, cancel, and execute events
- aggregated bid/ask levels plus order-ID lookup
- two price-level backends: `std::map` and flat sorted `std::vector`
- rolling analytics and CSV export after every processed message
- optional post-replay prediction summary reporting by message horizon
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

Emit a separate prediction summary after replay without changing the analytics CSV rows:

```bash
"$build_dir/lob_engine" \
  data/AAPL_sample_messages.csv \
  --backend map \
  --analytics-out "$build_dir/analytics.csv" \
  --prediction-report-out "$build_dir/prediction_report.csv" \
  --prediction-horizons 100,500
```

`--prediction-report-out` requires `--prediction-horizons`. If both flags are omitted, prediction work stays disabled.

Seed the opening book from the first row of a LOBSTER orderbook file (message streams begin at 09:30 and reference pre-open resting orders; see Real-data validation below):

```bash
"$build_dir/lob_engine" MSFT_message_10.csv --backend map --seed-book MSFT_orderbook_10.csv --analytics-out analytics.csv
```

## Analytics

Each processed message produces a row with:

- `timestamp`
- `best_bid`, `best_ask`, `spread`, `mid`
- `bid_depth_{1,5,10}`, `ask_depth_{1,5,10}`
- `order_imbalance`
- `rolling_vwap`
- `trade_flow_imbalance`
- `rolling_realized_vol`
- `ofi_event`, `rolling_ofi`

`ofi_event` is the L1 order flow imbalance of Cont, Kukanov and Stoikov (2014), computed per message from best-quote transitions: `e_n = 1{Pb >= Pb'} qb - 1{Pb <= Pb'} qb' - 1{Pa <= Pa'} qa + 1{Pa >= Pa'} qa'`. A vanished side contributes as a move away from the touch, and the first observed book contributes zero (state, not flow). `rolling_ofi` sums `e_n` over the trailing `trade_window_messages` events. OFI and the static depth imbalance answer different questions: on the LOBSTER sample day below, OFI is by far the stronger *contemporaneous* impact variable (the CKS result) while depth imbalance is the better *predictor* of the next mid move, so both are exported and the comparison is reproducible via `scripts/ofi_predictive_power.py`. Hand-computed transition sequences are covered in `test_analytics`.

The default rolling windows match the project objective:

- trailing `1000` messages for trade-based metrics
- trailing `300` seconds for realized volatility

Prediction reporting is a separate CSV keyed by message horizon. For each row `t`, the label is the sign of the first non-zero mid-price move found in `t+1 ... t+H` relative to mid at `t`. Rows with invalid current mid or no non-zero future move inside the horizon are skipped. The report includes labeled sample counts, up/down move counts, hit rate from `sign(order_imbalance_top5)` on non-zero-signal rows, and information coefficient computed as the Pearson correlation between the raw top-5 imbalance value and the future move sign. Zero-signal rows stay in the labeled sample and IC calculation but increment `skipped_zero_signal` so they are excluded from the hit-rate denominator.

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

## Real-data validation (LOBSTER sample day, 2026-08)

The engine was run against the canonical LOBSTER sample day (MSFT
2012-06-21, level 10: 668,765 messages, level-10 orderbook file alongside).
The files are not checked in (~200 MB); they are the standard LOBSTER
academic sample, mirrored in several public research repos.

**Replay**: all 668,765 messages parse with 0 malformed rows; full-day
replay throughput measured locally (Apple clang, `-O2`, M-series laptop)
was ~14.4M msgs/s (`map`) and ~15.1M msgs/s (`flat_vector`). Treat as
order-of-magnitude local numbers, not publishable benchmarks.

**Book reconstruction vs the vendor's own orderbook rows**
(`scripts/validate_l1_reconstruction.py`): LOBSTER message streams begin at
09:30 and reference orders resting from before the window, so the engine
supports seeding the opening book from the vendor's first orderbook row
(`--seed-book`), with cancels/executions of unknown order ids consuming
seeded liquidity at their price level. Seeded replay matches the vendor L1
book **exactly for the first 4,710 messages**, and the first divergence is
attributable, by message-level accounting, to a documented property of the
data product rather than the engine: level-N LOBSTER message files omit
events for orders whose level is outside the top N at event time, so
liquidity that leaves the window and later scrolls back carries no removal
messages (concrete example on this day: three bid orders totaling 1,600
shares at 310200 are added on-stream but the file contains no removal for
them, while the vendor book empties the level). Exact stateful replay
across window exits is therefore impossible from a level-scoped message
file by construction; the validator quantifies where that boundary is.

**OFI vs depth imbalance** (`scripts/ofi_predictive_power.py`, computed on
the vendor's L1 series so reconstruction error cannot contaminate the
result), replicating two standard microstructure findings on this day:

| relation | horizon (events) | signal | Pearson | Spearman |
|---|---|---|---|---|
| contemporaneous | 1000 | rolling OFI (1000) | **+0.85** | **+0.94** |
| contemporaneous | 1000 | depth imbalance L1 | +0.22 | +0.26 |
| forward | 100 | rolling OFI (1000) | +0.13 | +0.15 |
| forward | 100 | depth imbalance L1 | **+0.48** | **+0.46** |

Exactly as the literature says: OFI (Cont-Kukanov-Stoikov) is the strong
*contemporaneous* impact variable, while queue/depth imbalance is the
better *predictor* of the next mid move. Both are exported per message so
the comparison can be rerun on any dataset.

## Why this is useful for quant / HFT workflows

This codebase gives a compact environment for validating:

- message parsing assumptions
- order-book state transitions
- top-of-book and depth analytics
- replay throughput tradeoffs between container choices
- how much simple preallocation changes replay performance on shallow books

It is intentionally small enough to audit but still structured like a real research prototype: deterministic tests, reproducible build flow, benchmark tooling, and clear documentation.
