# Real-Time Limit Order Book Engine in C++

This repository implements a small, deterministic C++ limit-order-book engine for LOBSTER-style message data. It includes:

- typed CSV ingestion for LOBSTER message rows
- order lifecycle processing for add, cancel, and execute events
- aggregated bid/ask levels plus order-ID lookup
- two price-level backends: `std::map` and flat sorted `std::vector`
- rolling analytics and CSV export after every processed message
- deterministic C++ and Python integration tests
- replay benchmark tooling and a checked-in benchmark report

## Repository layout

- `include/lob/`: public headers for parsing, order book state, replay, and analytics
- `src/`: parser, order book engine, replay, analytics, and CLI entrypoint
- `tests/`: C++ unit tests plus Python integration coverage for the CMake workflow
- `benchmark/`: replay benchmark harness
- `data/`: checked-in small sample datasets used for deterministic tests and reproducible benchmark captures
- `report/`: benchmark and methodology notes

## Build

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build --output-on-failure
```

## CLI usage

Replay a dataset and print final top-of-book state:

```bash
./build/lob_engine data/AAPL_sample_messages.csv --backend both --depth 10 --repeat 5
```

Export analytics rows after every processed message:

```bash
./build/lob_engine \
  data/AAPL_sample_messages.csv \
  --backend both \
  --analytics-out build/analytics.csv \
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

The benchmark harness focuses on replay throughput and simple preallocation effects:

```bash
./build/lob_benchmark \
  --dataset data/AAPL_sample_messages.csv \
  --backend both \
  --reserve both \
  --depth 5 \
  --repeat 100000
```

What the benchmark compares:

- `map` vs `flat`
- reserve/preallocation `off` vs `on`

`--reserve on` enables:

- `unordered_map::reserve()` for order lookup
- vector capacity reservation for the flat backend

This is the bounded hot-path allocation reduction implemented in the repo. The benchmark report records the measured effect on the checked-in sample datasets.

On a fresh build of this repository on a 4-core AMD EPYC-Rome VM, the fastest AAPL replay configuration processed `60.1 million messages/second` with the flat-vector backend and reserve disabled.

## Dataset note

The repo ships small checked-in reproducibility datasets:

- `AAPL_sample_messages.csv`
- `MSFT_sample_messages.csv`
- `NVDA_sample_messages.csv`
- `TSLA_sample_messages.csv`

They are intentionally tiny and deterministic so the build, tests, and benchmark report can run in CI or on a fresh clone without external data dependencies. They are suitable for correctness checks and relative replay comparisons, not production-grade market simulation.

## Why this is useful for quant / HFT workflows

This codebase gives a compact environment for validating:

- message parsing assumptions
- order-book state transitions
- top-of-book and depth analytics
- replay throughput tradeoffs between container choices
- how much simple preallocation changes replay performance on shallow books

It is intentionally small enough to audit but still structured like a real research prototype: deterministic tests, reproducible build flow, benchmark tooling, and clear documentation.
