# Benchmark Report

This report captures reproducible replay benchmarks from a clean checkout of the repository after the analytics/export and preallocation work landed.

## Reproduction

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build --output-on-failure
./build/lob_benchmark --dataset data/AAPL_sample_messages.csv --backend both --reserve both --depth 5 --repeat 200000
./build/lob_benchmark --dataset data/MSFT_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
./build/lob_benchmark --dataset data/NVDA_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
./build/lob_benchmark --dataset data/TSLA_sample_messages.csv --backend both --reserve both --depth 5 --repeat 100000
```

## Scope

- timed section: replay-only
- analytics/export: available in `lob_engine`, but excluded from the replay throughput timer
- backends compared: `std::map` and flat sorted `std::vector`
- allocation mode compared: reserve/preallocation `off` vs `on`

## Results

### AAPL sample

| Backend | Reserve | Throughput msgs/s | Avg ns/msg |
| --- | --- | ---: | ---: |
| map | off | 55,621,183 | 17.979 |
| flat | off | 59,151,913 | 16.906 |
| map | on | 57,645,505 | 17.347 |
| flat | on | 56,307,627 | 17.760 |

Takeaway: on the checked-in AAPL sample, `flat` wins without reserve, while `map` slightly benefits from preallocation.

### MSFT sample

| Backend | Reserve | Throughput msgs/s | Avg ns/msg |
| --- | --- | ---: | ---: |
| map | off | 29,049,272 | 34.424 |
| flat | off | 55,216,799 | 18.110 |
| map | on | 51,246,182 | 19.514 |
| flat | on | 49,013,940 | 20.402 |

Takeaway: the reserve hint materially improves the `map` path on this sample and narrows the gap to the flat-vector backend.

### NVDA sample

| Backend | Reserve | Throughput msgs/s | Avg ns/msg |
| --- | --- | ---: | ---: |
| map | off | 46,925,367 | 21.310 |
| flat | off | 54,675,249 | 18.290 |
| map | on | 53,910,207 | 18.549 |
| flat | on | 45,742,321 | 21.862 |

Takeaway: preallocation helps the `map` backend more than the flat-vector backend on this sample.

### TSLA sample

| Backend | Reserve | Throughput msgs/s | Avg ns/msg |
| --- | --- | ---: | ---: |
| map | off | 55,859,805 | 17.902 |
| flat | off | 57,432,210 | 17.412 |
| map | on | 55,976,073 | 17.865 |
| flat | on | 52,862,960 | 18.917 |

Takeaway: the two container choices are close on the TSLA sample, with flat-vector slightly ahead without reserve.

## Interpretation

- The flat-vector backend can outperform `std::map` on these shallow sample books because the active level count stays low and the contiguous representation is cache-friendly.
- Reserve/preallocation mainly helps the order-ID `unordered_map` and removes some rehash churn on the replay path.
- The effect is workload-dependent. There is no single winner across every sample dataset, which is exactly why both container choices are kept in the repo and exposed through the same interface.

## Important note

The checked-in datasets are intentionally tiny reproducibility samples, not large production LOBSTER files. These numbers should be treated as relative engineering signals for this repo, not as final claims about live-market performance.
