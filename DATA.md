# Data provenance

**Primary source:** Databento `XNAS.ITCH` (Nasdaq TotalView-ITCH), MBO schema, accessed under the Berkeley MFE programme's Databento account.

LOBSTER is a reconstruction of the same Nasdaq TotalView-ITCH feed, so Databento MBO is that feed at full depth. MBO carries every order event at every price level with nanosecond `ts_recv`/`ts_event` timestamps and order IDs, which is what makes queue-position work and exact book reconstruction possible.

## What is committed

- Source code, build files, and tests
- Small reduced message fixtures under `data/*.csv` (25 lines, 20 valid messages each) so the build and tests run on a fresh clone with no external data
- Derived results: benchmark tables, latency percentiles, analytics summaries

## What is not committed

- Databento extracts. They live outside the repository under `$DATABENTO_RAW_DIR`, in the vendor's own layout `<SYMBOL>/<YYYY-MM-DD>.<schema>.dbn.zst`, shared with the impact repository. No absolute path to them appears anywhere in this repo: a script that cannot find the environment variable says so and exits rather than guessing.
- Derived per-session artifacts (`$LOB_WORK_DIR`, default `data/databento/`, gitignored): the LOBSTER message stream, the sequence sidecar, and a compact depth-3 book cache. All are rebuilt on demand by `scripts/build_sessions.py` from the raw extract, so they are cached rather than committed.
- The LOBSTER academic sample, which is free but redistributed under its own terms

## Sessions used

**Fifteen symbol-days on three names, all in 2024.** Every one is an ordinary
trading day; the sample contains no stress days, no halts and no index events.
`INTC 2024-08-02` is the single exception and is a single-name event day, the
session after Intel's Q2 report and dividend suspension. It is kept because it
is genuinely different and it is flagged wherever it behaves differently.

Nothing measured on this sample is a population or a regime claim. Three names
on ordinary days support statements about Microsoft, Intel and Apple on those
days, and nothing wider.

All fifteen cost **$0.0000**, confirmed with `metadata.get_cost` rather than
estimated: `XNAS.ITCH` is covered outright by the programme entitlement. Each
extract covers the full Nasdaq extended session, 04:00-20:00 exchange time.

| session | MBO records | MBO billable | MBP-10 billable | cost |
|---|---:|---:|---:|---:|
| MSFT 2024-02-01 | 6,345,232 | 355.3 MB | 643.9 MB | $0.0000 |
| MSFT 2024-04-01 | 5,356,893 | 300.0 MB | 536.8 MB | $0.0000 |
| MSFT 2024-06-03 | 4,003,833 | 224.2 MB | 492.7 MB | $0.0000 |
| MSFT 2024-10-01 | 4,456,930 | 249.6 MB | 541.7 MB | $0.0000 |
| MSFT 2024-12-02 | 2,247,212 | 125.8 MB | 255.5 MB | $0.0000 |
| INTC 2024-02-01 | 1,346,743 | 75.4 MB | 339.4 MB | $0.0000 |
| INTC 2024-04-01 | 1,090,053 | 61.0 MB | 256.5 MB | $0.0000 |
| INTC 2024-08-02 | 2,356,988 | 132.0 MB | 553.2 MB | $0.0000 |
| INTC 2024-10-01 | 1,223,055 | 68.5 MB | 292.5 MB | $0.0000 |
| INTC 2024-12-02 | 1,617,999 | 90.6 MB | 381.6 MB | $0.0000 |
| AAPL 2024-02-01 | 3,863,316 | 216.3 MB | 643.1 MB | $0.0000 |
| AAPL 2024-04-01 | 2,178,352 | 122.0 MB | 419.1 MB | $0.0000 |
| AAPL 2024-06-03 | 3,493,209 | 195.6 MB | 600.4 MB | $0.0000 |
| AAPL 2024-08-01 | 5,366,912 | 300.5 MB | 864.5 MB | $0.0000 |
| AAPL 2024-10-01 | 9,134,860 | 511.6 MB | 1,359.6 MB | $0.0000 |
| **total** | **51,972,467** | **3.0 GB** | **8.2 GB** | **$0.0000** |

The MBO record counts are the vendor's; the converter emits slightly fewer
LOBSTER messages after dropping the execution-mirror cancels, which is why
`build_sessions.py` reports 51,421,738 messages against 51,972,467 records.

## Reproducing

Pull one symbol-day and convert it to the LOBSTER message layout:

```bash
export DATABENTO_RAW_DIR=/path/to/XNAS.ITCH        # <SYMBOL>/<DATE>.<schema>.dbn.zst
python scripts/fetch_databento_session.py --symbol INTC --date 2024-08-02 --confirm
python scripts/build_sessions.py                    # all fifteen, one at a time
```

`build_sessions.py` converts each MBO extract to the LOBSTER layout, replays it
through the engine, and caches a compact depth-3 book. Three levels a side is
provably enough for everything here and it is the difference between roughly
3 GB of cache and roughly 10 GB; the argument is in the module docstring of
`scripts/sessions.py`.

Requires `DATABENTO_API_KEY` and an entitlement to `XNAS.ITCH`.

## Licence and retention

Databento data is accessed under a programme licence and is not redistributed here. Raw extracts are deleted at the end of the associated academic affiliation; code, derived statistics, and figures are not derived-from-restriction and remain.
