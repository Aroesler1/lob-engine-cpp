# Real-Time Limit Order Book Engine in C++

## Current Status

This checkout currently implements a small but well-tested subset of the intended project:

- `lob_parser`: a C++17 library for parsing headerless LOBSTER-style message CSV rows into typed records.
- `lob_engine`: a CLI that reads one CSV file and prints parse statistics to stdout.
- Deterministic C++ and Python tests that build the project, validate parsing behavior, and smoke-test the CLI.

This checkout does **not** yet contain a reconstructed limit order book, rolling analytics, analytics CSV export, benchmark executables, or measured benchmark results. The documentation below is intentionally aligned with what the repository builds and runs today.

## Overview And Motivation

LOBSTER message files are a common starting point for market microstructure research, execution analysis, and feature engineering. Before a full order book or downstream analytics can be trusted, the raw event stream has to be ingested correctly, typed safely, and checked for malformed rows.

The code in this repository focuses on that ingestion layer first. It parses message rows, validates event and side codes, skips malformed lines without aborting the run, and exposes both batch and streaming APIs. The current CLI is a summary tool rather than a full order book engine.

## Architecture

The current pipeline is:

```text
LOBSTER CSV
  -> LobsterParser
       include/lob/parser.hpp
       src/parser.cpp
  -> typed LobsterMessage records
       include/lob/types.hpp
  -> lob_engine CLI
       src/main.cpp
  -> stdout summary
       Parsed
       Malformed
       Time range
       Event type counts
```

Two parser access patterns are implemented:

- Batch mode: `parse_file(...)` returns `std::vector<LobsterMessage>`.
- Streaming mode: `open(...)` plus repeated `next(...)` lets callers process one parsed row at a time.

There is no in-repo stage today for:

- order book reconstruction
- bid/ask level maintenance
- rolling analytics
- analytics CSV export

## Repository Layout

- `include/lob/types.hpp`: strongly typed enums and structs for parsed messages and level metadata.
- `include/lob/parser.hpp`: parser interface.
- `src/parser.cpp`: CSV parsing, validation, malformed-line accounting.
- `src/main.cpp`: CLI entrypoint and summary reporting.
- `tests/test_parser.cpp`: native parser tests.
- `tests/test_parser.py`: Python test that configures/builds CMake targets and runs the binaries.
- `tests/test_smoke.py`: minimal workspace smoke test.
- `data/sample_messages.csv`: sample LOBSTER-style message file used by tests and examples.
- `benchmark/`: benchmark documentation placeholder; no benchmark code or results are checked in.
- `report/`: placeholder directory only in this checkout.
- `src/project_real_time_limit_order_book_engine_in_c_1775258936/`: minimal Python package stub for the workspace.
- `build/`: tracked build artifacts from a prior local build. They are not portable across worktrees, so prefer a fresh build directory such as `build/local`.

## Data Assumptions And LOBSTER Notes

The parser expects exactly six comma-separated fields per row and no header row:

| Column | Type In Code | Accepted Values | Notes |
| --- | --- | --- | --- |
| `timestamp` | `double` | any parseable floating-point value | Stored as-is. The code does not normalize timezone, session, or clock units beyond numeric parsing. |
| `event_type` | `lob::EventType` | integers `1` through `7` | Values outside `1..7` are rejected as malformed. |
| `order_id` | `std::int64_t` | any parseable signed 64-bit integer | Stored as-is. |
| `size` | `std::int32_t` | any parseable signed 32-bit integer | Stored as-is. |
| `price` | `std::int64_t` | any parseable signed 64-bit integer | Stored as the raw integer from the CSV; there is no conversion to dollars, ticks, or decimals. |
| `direction` | `lob::Side` | `1` for buy, `-1` for sell | Any other value is rejected as malformed. |

Supported event-code mapping from `include/lob/types.hpp`:

| Code | Enum |
| --- | --- |
| `1` | `NewOrder` |
| `2` | `PartialCancel` |
| `3` | `FullCancel` |
| `4` | `ExecutionVisible` |
| `5` | `ExecutionHidden` |
| `6` | `CrossTrade` |
| `7` | `TradingHalt` |

Important implementation details:

- Leading and trailing whitespace is trimmed before numeric parsing.
- Rows with the wrong field count, invalid numeric values, invalid event codes, or invalid directions are counted in `malformed_count()` and skipped.
- The parser preserves file order. The CLI reports the timestamp range using the first and last successfully parsed rows, not the min/max over a resorted dataset.
- Event semantics are not interpreted beyond validation and counting. For example, the current code does not apply cancels, executions, or halts to an in-memory order book.
- The parser does not enforce market-data invariants such as positive sizes, valid price ladders, or event-specific field rules.

## Build

### Toolchain

- CMake `>= 3.14`
- A C++17 compiler
- Python `>= 3.11` if you want to run the Python test suite from `pyproject.toml`

### Configure And Build

Because this repository currently includes a tracked `build/` directory with an existing CMake cache, use a fresh build directory rather than reusing `build/` directly:

```bash
cmake -S . -B build/local
cmake --build build/local
```

Release build example:

```bash
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release
cmake --build build/release
```

Targets produced by the default build:

- `lob_parser` static library
- `lob_engine` CLI executable
- `test_parser` native test executable

## Run

CLI usage is currently positional-only:

```text
lob_engine <lobster_csv_file>
```

Example using the checked-in sample data:

```bash
./build/local/lob_engine data/sample_messages.csv
```

Current output on the sample file:

```text
Parsed: 20
Malformed: 5
Time range: 34200.123456 - 57600.000000
Event type counts: 1:4 2:3 3:2 4:3 5:3 6:3 7:2
```

What the CLI supports today:

- exactly one positional CSV path
- summary reporting to stdout

What the CLI does not support today:

- named flags
- analytics export paths
- CSV output files
- configurable rolling windows

## Analytics Schema

No analytics schema is implemented in this checkout. There is no analytics CSV exporter and no CLI option for writing derived fields.

Requested milestone fields that are **not** present in the current codebase:

| Field | Status |
| --- | --- |
| `timestamp` | not exported as an analytics row |
| `best_bid` | not implemented |
| `best_ask` | not implemented |
| `spread` | not implemented |
| `mid` | not implemented |
| `bid_depth_1` | not implemented |
| `bid_depth_5` | not implemented |
| `bid_depth_10` | not implemented |
| `ask_depth_1` | not implemented |
| `ask_depth_5` | not implemented |
| `ask_depth_10` | not implemented |
| `order_imbalance` | not implemented |
| `rolling_vwap` | not implemented |
| `trade_flow_imbalance` | not implemented |
| `rolling_realized_vol` | not implemented |

There are also no in-code defaults for trailing message windows, wall-clock volatility windows, or analytics-specific configuration knobs. The only outputs implemented today are the summary fields printed by `src/main.cpp`:

- parsed record count
- malformed record count
- first/last parsed timestamp range
- counts by event type code

## Design Notes

The current implementation makes a few narrow design choices:

- Strongly typed enums in `include/lob/types.hpp` keep event and side codes explicit instead of passing raw integers around the codebase.
- `LobsterParser` exposes both batch and streaming interfaces so callers can choose between collecting all parsed records or processing them incrementally.
- Malformed data is handled by counting and skipping bad rows instead of terminating the process on the first parse failure.

Design items described in the original milestone but **not** implemented in this checkout:

- no `std::unordered_map` order lookup structure
- no `std::map` price-level container
- no flat sorted-vector alternative for book levels
- no pre-allocation or hot-path allocation tuning specific to order book updates

In other words, there are no order book container tradeoffs to analyze yet because the code stops at message parsing and summary reporting.

## Benchmarks And Reports

There is currently no benchmark executable, benchmark script, benchmark dataset harness, or measured throughput artifact checked into this repository.

Current benchmark-related paths:

- `benchmark/` contains [benchmark/report.md](benchmark/report.md) plus a placeholder `.gitkeep`
- `report/` contains only `.gitkeep`
- [benchmark/report.md](benchmark/report.md) documents the current benchmark status

Because no benchmark results are present, this README intentionally does **not** report throughput numbers, hardware specs, compiler benchmark settings, ticker coverage, or `std::map` versus flat-container findings.

## Relevance To HFT And Quant Workflows

Even in its current form, the parser is useful as an ingestion and validation layer:

- sanity-checking raw LOBSTER message files before downstream research
- measuring malformed-row rates on new datasets
- verifying event-type distributions and file coverage quickly from the CLI
- providing a typed starting point for later order book reconstruction work

What is still missing for a fuller HFT or research workflow:

- maintained bid/ask state
- depth snapshots
- rolling microstructure features
- execution-quality analytics
- replay or backtesting hooks

## Testing

Requested Python test command:

```bash
python -m pytest tests -q --tb=short
```

That test flow configures CMake, builds the project, runs the native `test_parser` executable, and smoke-tests the `lob_engine` CLI against `data/sample_messages.csv`.

The native tests cover:

- parsing of a known-valid row
- malformed-line counting
- invalid direction rejection
- invalid event-type rejection
- batch versus streaming parity
- sample-file counts
- empty-file behavior
