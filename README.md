# Build project: Real-Time Limit Order Book Engine in C++

Private repo-backed C++ engine/library with CLI entrypoint. Core scope covers LOBSTER CSV ingestion, order lifecycle processing (add/cancel/execute), maintained aggregated bid/ask levels plus order lookup map, configurable rolling analytics, CSV export, deterministic tests, and benchmark reporting. Performance engineering is explicitly in scope: pre-allocation, minimizing hot-path heap allocation, and comparing two level-container choices. Primary success criteria are correctness of reconstructed book transitions, complete analytics output, clean build/test workflow via CMake, and benchmark evidence on AAPL plus 2-3 additional tickers. Stretch alpha feature is included as optional final milestone, not a blocker for core approval.

## Order book backends

`lob_engine` can replay the same message stream through either `map`, `flat`, or `both` backends via `--backend`.

The flat-vector backend keeps each side in sorted price order using a `std::vector` and binary search. It can win on shallow books because of cache locality and compact storage, but interior inserts/erases remain `O(n)`. The `std::map` backend keeps `O(log n)` updates and may behave better once active level counts grow or churn is concentrated away from the top of book. There are no intended behavioral differences between the two implementations; deterministic parity tests compare semantic snapshots after every processed event.
