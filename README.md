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
- a vendor cross-check that reaches exact MBP-10 agreement on two Databento
  sessions, and a LOB-Bench run as an outside second opinion

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

**Replay**: all 668,765 messages parse with 0 malformed rows.

## Vendor cross-check: exact agreement on MBP-10, two sessions (2026-09)

Databento derives every schema from MBO, so their MBP-10 and a book built here
from their MBO are two independent derivations of one source. Agreement is a
real correctness check on the book logic rather than a self-consistency check.

`scripts/validate_mbp10_vs_vendor.py` diffs them, aligned exactly on the venue
`sequence` (many MBO events share a `ts_recv`, so timestamp matching compares
the engine at a different point in the stream and manufactures disagreement).

**Result: 100.0000% of compared cells match on both sessions** — every cell, at
every one of the ten levels, under both alignments.

| | MSFT 2024-06-03 | INTC 2024-08-02 |
|---|---:|---:|
| MBO records in | 4,003,834 | 2,356,988 |
| LOBSTER messages out | 3,862,854 | 2,051,624 |
| malformed rows | 0 | 0 |
| **agreement** | **100.0000%** | **100.0000%** |
| cells compared | 53,551,466 | 60,128,762 |
| — vendor `A`/`C`/`F` vs engine post-event | 49,611,426 | 51,939,050 |
| — vendor `T` prints vs engine pre-fill | 3,940,040 | 8,189,712 |
| slots where both agree the level is absent | 734 | 4,638 |
| **slots where one side sees a level and the other does not** | **0** | **0** |

Nothing is excluded to reach either number. The last row is the one that keeps
the percentage honest: a figure computed over mutually-present cells could hide
a disagreement about whether a level exists at all, so every slot is accounted
for explicitly and the validator prints that reconciliation.

### The second session is a different kind of book

A second MSFT day would mostly re-test the same regime. INTC on 2024-08-02 — the
session after Intel's Q2 report, where the repricing arrived as an overnight gap
— is large-tick and queue-dominated where MSFT is small-tick and
spread-dominated, measured on the engine's own L1 output over RTH:

| | MSFT 2024-06-03 | INTC 2024-08-02 |
|---|---:|---:|
| RTH mid, open → close | 415.63 → 413.64 | 21.95 → 21.48 |
| one tick, in bp of mid | 0.24 bp | 4.73 bp |
| median spread | $0.05 (1.21 bp) | $0.01 (4.73 bp) |
| share of session at a one-tick spread | 1.0% | 81.1% |
| median touch size, bid / ask | 68 / 63 | 2,415 / 3,000 |
| displayed fills | 70,510 | 152,683 |

INTC carries **2.2x the displayed fills on 53% of the message count** and a touch
roughly 35-48x deeper. Whatever the engine gets right, it is not getting right by
being tuned to one book shape.

The two fixes in the engine's input (below) were found on MSFT and applied
unchanged to INTC, which reached 100.0000% on the first run with no further
work. That is the useful part: they were corrections to a misread of the vendor's
event model, not adjustments fitted to one session. On INTC the mirror-cancel
dedup matches **152,683 of 152,683** fills, the same 100% the `order_id` key
gives on MSFT.

Getting to the first of those numbers took three fixes, two in the engine's input
and one in the harness that measures it. The sequence is the point: each wrong
answer was disproved by a test rather than argued away.

**1. Execution double-count (98.23% → 99.74%).** **Databento emits three MBO
records for one displayed execution**: a `T` print, an `F` fill against the
resting order, and a `C` removing that same quantity from the book. Applying
both reduced the resting order twice, which is why engine depth ran
systematically below the vendor's — smaller in 87,583 of 98,102 mismatches. That
is the same duplication as the `T`/`F` pair one layer deeper, and it is
invisible without a vendor-derived book to diff against.

**2. Mixed snapshot conventions in the harness (99.74% → 99.9938%).** An earlier
version of this section attributed the residual 0.26% to the vendor reporting
the pre-trade book while the engine reported post-trade. That was wrong, and
testing it is what found the real cause: rolling the engine back to its
pre-trade state made agreement *worse*, 99.74% → 99.17%.

The vendor's MBP-10 contains essentially no `F` rows (3 in the entire session).
It represents a displayed execution as a `T` print followed by a `C` removal,
and those two rows carry **different book states** — `T` the book before the
execution, `C` the book after. Trade sequences split 60,292 emitting only `(T,)`
against 38,209 emitting `(T, C)`, so keeping the last vendor row per sequence
compared against a post-trade snapshot on some sequences and a pre-trade
snapshot on others. Aligned on the right row, the vendor's `C` row matched the
engine's post-fill state on 100.000% of ask cells. The giveaway was the 3,001
fills that fully consumed the touch: the vendor still showed the consumed price
in **3,001 of 3,001**.

**3. The dedup was keyed on the wrong field (99.9938% → 100.0000%).** The mirror
cancel was matched on `(sequence, price, size)`, which catches 70,254 of 70,510
fills — 99.64%, close enough to look finished. Each of the **256 misses** left a
cancel in the stream that double-decremented a resting order, and the book then
carried that error until the order left. A handful of events produced 3,321
mismatched cells across 1,886 sequences, 1,585 of which were single-record
sequences merely downstream of the damage.

Keying on `(sequence, order_id)` matches **70,510 of 70,510**, because the fill
names the resting order it executed against and the mirror cancel removes
quantity from that same order — so the pair necessarily agrees on the id, while
price and size are only a proxy for it.

What made this findable was measuring distance rather than inspecting cases:
mismatched sequences sat a median **15,055 sequences after the nearest dedup
miss**, against **4,683,657** for sequences that agreed. Two competing
explanations were tested and rejected first — modify records (there are none in
this session) and multi-record sequence ordering, which turned out to be
*under*-represented among the mismatches at 0.3×, and that is what redirected
the search toward downstream drift.

Also ruled out: general cancel semantics (1,925,732 cancels, zero over-cancels)
and price truncation (zero sub-penny prices, so the fixed-point conversion is
lossless).

Reproduce either session end to end (`--confirm` is what actually spends):

```bash
python scripts/fetch_databento_session.py --symbol INTC --date 2024-08-02 --confirm
python scripts/databento_to_lobster.py data/databento/INTC_2024-08-02_mbo.dbn.zst \
    --out data/databento/INTC_2024-08-02_message.csv \
    --sequence-out data/databento/INTC_2024-08-02_seq.csv
build/lob_engine data/databento/INTC_2024-08-02_message.csv \
    --backend map --depth 10 --book-out data/databento/INTC_2024-08-02_book10.csv
python scripts/validate_mbp10_vs_vendor.py \
    --engine-book data/databento/INTC_2024-08-02_book10.csv \
    --vendor data/databento/INTC_2024-08-02_mbp10.dbn.zst \
    --sequences data/databento/INTC_2024-08-02_seq.csv \
    --messages data/databento/INTC_2024-08-02_message.csv
```

## Second opinion: LOB-Bench (2026-09)

The cell diff above is this repo marking its own homework — our alignment, our
comparison, our tolerance. [LOB-Bench](https://github.com/peernagy/lob_bench)
(Nagy et al., ICML 2025) is the standard evaluation suite for LOB generative
models, and running the engine's output through it substitutes someone else's
metric implementations for ours.

`scripts/run_lob_bench.py` maps the engine's reconstructed book to LOB-Bench's
"generated" side and Databento's own MBP-10 to its "real" side, cuts the session
into 100 windows of 4,096 messages spread across RTH, and reports L1 and
Wasserstein-1 distances between the two distributions.

**Every statistic scores 0.000000 on both sessions**, on both metrics:

| statistic | MSFT 2024-06-03 | INTC 2024-08-02 |
|---|---:|---:|
| spread | 0.000000 | 0.000000 |
| orderbook imbalance | 0.000000 | 0.000000 |
| ask / bid volume at touch | 0.000000 | 0.000000 |
| ask / bid volume over 10 levels | 0.000000 | 0.000000 |
| limit order depth, ask / bid | 0.000000 | 0.000000 |
| cancellation depth, ask / bid | 0.000000 | 0.000000 |
| log inter-arrival time | 0.000000 | 0.000000 |
| log time to cancel | 0.000000 | 0.000000 |

**What this does and does not establish.** Both sides consume the same message
stream, so where cell agreement is already exact these zeros are exact *by
construction*. This is not independent evidence that the engine produces
realistic markets — it cannot be, and reading it that way would be the mistake
the table exists to avoid. What it does establish is two things the cell diff
does not:

1. **The engine's LOBSTER export is well-formed enough for the standard academic
   toolchain to consume unmodified**, through a third-party parser rather than
   ours — including the message-derived statistics (inter-arrival, time to
   cancel) that the MBP-10 diff never touches.
2. **It is a regression check with real teeth.** Any non-zero entry would mean
   the books differ somewhere the cell diff did not look, or that the export is
   malformed. Building it caught exactly that class of bug in the harness: the
   vendor's dollar prices scale to values like `215899.99999999997`, and
   truncating rather than rounding on the integer cast manufactured a one-tick
   disagreement on nearly every price cell (`spread` L1 0.172, cancellation
   depth 0.119) while leaving every size-derived statistic at zero. The diff
   tolerates that with `atol=0.5`; an integer export has to round.

```bash
python scripts/run_lob_bench.py \
    --engine-book data/databento/INTC_2024-08-02_book10.csv \
    --vendor data/databento/INTC_2024-08-02_mbp10.dbn.zst \
    --sequences data/databento/INTC_2024-08-02_seq.csv \
    --messages data/databento/INTC_2024-08-02_message.csv \
    --symbol INTC --date 2024-08-02 \
    --lob-bench <clone of peernagy/lob_bench> --work-dir /tmp/lobbench_intc \
    --out report/lob_bench_INTC_2024-08-02.csv
```

`--out` writes the full score table; like the other generated CSVs under
`report/` it is a local artefact and not committed, so the tables above are the
checked-in record.

## Multi-level integrated OFI (2026-09)

Cont, Cucuringu and Zhang ([QF 2023](https://arxiv.org/abs/2112.13213)) show that
combining order flow imbalance across the top book levels into one integrated
variable explains contemporaneous price impact far better than best-level OFI.
`scripts/multi_level_ofi.py` reproduces that on Databento MBP-10 for both
sessions (MSFT 1,338,802 events; INTC 1,503,326). Vendor depth is used rather
than this engine's reconstruction, so the result is a statement about the market
rather than about the book-building code.

**Contemporaneous R²** (price change regressed on trailing OFI over the same window):

| horizon (events) | MSFT L1 | MSFT naive sum | MSFT PCA | INTC L1 | INTC naive sum | INTC PCA |
|---|---:|---:|---:|---:|---:|---:|
| 10 | 0.1121 | 0.2206 | **0.2231** | 0.0163 | 0.1018 | **0.1029** |
| 50 | 0.2852 | 0.4398 | **0.4430** | 0.1078 | 0.2645 | **0.2658** |
| 100 | 0.3519 | 0.5171 | **0.5195** | 0.2031 | 0.3849 | **0.3866** |
| 500 | 0.4130 | 0.5999 | **0.5997** | 0.3423 | 0.4694 | **0.4711** |

**Predictive R²** (next window's price change):

| horizon (events) | MSFT L1 | MSFT naive sum | MSFT PCA | INTC L1 | INTC naive sum | INTC PCA |
|---|---:|---:|---:|---:|---:|---:|
| 10 | **0.0168** | 0.0141 | 0.0145 | **0.0013** | 0.0002 | 0.0003 |
| 50 | **0.0262** | 0.0234 | 0.0239 | **0.0152** | 0.0013 | 0.0013 |
| 100 | **0.0124** | 0.0113 | 0.0115 | **0.0189** | 0.0042 | 0.0042 |
| 500 | 0.0012 | 0.0038 | 0.0038 | **0.0077** | 0.0035 | 0.0035 |

Four readings, including one that cuts against the method:

1. **Using the whole book raises contemporaneous explanatory power on both
   sessions.** On MSFT R² roughly doubles (0.11 to 0.22 at 10 events). The CCZ
   result reproduces cleanly.
2. **The gain is far larger in the large-tick book, and that is the payoff of
   the second session.** On INTC, best-level OFI explains almost nothing at
   short horizons (0.0163 at 10 events, against MSFT's 0.1121) while the
   integrated variable recovers 0.1029 — a **6.3x** lift where MSFT sees 2.0x.
   The fitted level-1 PCA weight drops to +0.10 on INTC from +0.18 on MSFT.
   This is what the microstructure of a large-tick name predicts and one
   session could not have shown: with the spread pinned at one tick 81% of the
   time and 2,400-3,000 shares queued at the touch, best-quote *transitions*
   are rare and carry little information, so almost everything informative is
   happening in the queue behind the touch.
3. **The PCA integration is barely distinguishable from a naive sum** on either
   session (MSFT 0.2231 vs 0.2206; INTC 0.1029 vs 0.1018). The fitted weights
   are close enough to uniform that the first principal component is nearly a
   plain sum. The gain comes from *using multiple levels at all*, not from how
   they are combined — worth stating rather than presenting PCA as the source
   of the improvement.
4. **Predictive power stays negligible on both, and L1 is the best of the three
   on both.** Multi-level integration helps explain impact; it does not help
   forecast it. Note the sign flip against reading 2: the deep book is where the
   contemporaneous explanatory power lives and the touch is where what little
   predictive power exists lives, and INTC separates the two more sharply than
   MSFT (0.0189 for L1 against 0.0042 integrated, at 100 events).

That last point is the same pattern this repository's L1 study found, and the
same one the propagator calibration in the impact repository found on both
sessions: order flow explains contemporaneous returns strongly and predicts them
barely at all. Independent measurements, one conclusion.

```bash
python scripts/multi_level_ofi.py --vendor <mbp10.dbn.zst>
```

## Performance on full-depth data (Databento XNAS.ITCH MBO, 2026-08)

Aggregate throughput is the wrong headline for an order book engine: it hides
the tail, and comparable public engines quote latency percentiles. Both figures
below are reported because they answer different questions, and quoting only one
would mislead. MSFT 2024-06-03, 3,862,854 messages, `map` backend, 8 trials with
2 discarded as warmup (`scripts/latency_profile.py`).

Unlike the correctness sections above, this one is deliberately **not** extended
to the second session. These are host-specific timings, and the INTC run would
have to be measured on the machine this table was recorded on to be comparable;
putting a number taken on different hardware in the same table would corrupt the
comparison rather than broaden it. Correctness generalises across sessions,
latency does not generalise across hosts.

| | p50 | p99 | max | implied throughput |
|---|---|---|---|---|
| replay only (book apply) | 127 ns/msg | 127 ns/msg | 127 ns/msg | 7.90M msgs/sec |
| end to end (parse + replay + startup) | 1,085 ns/msg | 1,090 ns/msg | 1,090 ns/msg | 922k msgs/sec |

Parsing a 155 MB CSV dominates end-to-end cost by roughly 8.5x. Quoting only the
replay figure would overstate what a user waits for; quoting only end-to-end
would understate the engine.

Re-measured after the dedup rekey changed the message count. Repeat runs on this
machine move p50 by 1-3 ns/msg, so treat the third digit as noise rather than
signal.

These are amortised per-message costs across whole replays, not a timestamped
per-`apply()` histogram, so they bound the mean rather than the true tail. They
are single-threaded userspace timings on an unpinned laptop core: comparable
across commits on this machine, not across machines or against colocated
production systems.

### Backend choice depends on book depth

On full-depth MBO the `map` backend beats `flat_vector` by roughly **7.6x**
(median of 5 trials on an identical 200k slice: 6.96M vs 0.92M msgs/sec, both
producing byte-identical final book state). Trial-to-trial spread is wide on
this hardware — 6.63-7.33M for `map` against 0.81-1.08M for `flat_vector`, so
the ratio itself ranges 6.1x to 9.0x. The order of magnitude is the finding;
the second digit is not. An earlier version of this line quoted a single run as
"6.6x", which read as more precise than the measurement supports.

The flat sorted vector's O(n) insert is competitive only while the number of
live price levels stays small, which level-N sample files enforce and real
full-depth data does not.

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
