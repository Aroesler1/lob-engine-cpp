# Data provenance

**Primary source:** Databento `XNAS.ITCH` (Nasdaq TotalView-ITCH), MBO schema, accessed under the Berkeley MFE programme's Databento account.

LOBSTER is a reconstruction of the same Nasdaq TotalView-ITCH feed, so Databento MBO is that feed at full depth. MBO carries every order event at every price level with nanosecond `ts_recv`/`ts_event` timestamps and order IDs, which is what makes queue-position work and exact book reconstruction possible.

## What is committed

- Source code, build files, and tests
- Small reduced message fixtures under `data/*.csv` (25 lines, 20 valid messages each) so the build and tests run on a fresh clone with no external data
- Derived results: benchmark tables, latency percentiles, analytics summaries

## What is not committed

- Databento extracts (`data/databento/`, gitignored). The two sessions total ~194 MB compressed on disk and ~1.4 GB billable across MBO and MBP-10; committing them would be both a licence problem and a repository problem.
- The LOBSTER academic sample, which is free but redistributed under its own terms

## Sessions used

Two symbol-days, chosen to differ in kind rather than to repeat one regime.
Both cover the full Nasdaq extended session, 04:00-20:00 exchange time.

| session | why | MBO records | billable | cost |
|---|---|---:|---:|---:|
| MSFT 2024-06-03 | small-tick, spread-dominated (one tick = 0.24 bp of mid) | 4,003,833 | 224.2 MB | $0.00 |
| INTC 2024-08-02 | large-tick, queue-dominated (4.73 bp); post-earnings gap day | 2,356,988 | 132.0 MB | $0.00 |

Cost is $0.00 because `XNAS.ITCH` is covered outright by the programme
entitlement; it is not an estimate. `scripts/fetch_databento_session.py` prices
a request with `metadata.get_cost` and downloads nothing unless `--confirm` is
passed, so the spend is always visible before it is incurred.

## Reproducing

Pull one symbol-day and convert it to the LOBSTER message layout:

```bash
python scripts/fetch_databento_session.py --symbol INTC --date 2024-08-02 --confirm
python scripts/databento_to_lobster.py <mbo.dbn.zst> --out messages.csv --sequence-out seq.csv
```

Requires `DATABENTO_API_KEY` and an entitlement to `XNAS.ITCH`.

## Licence and retention

Databento data is accessed under a programme licence and is not redistributed here. Raw extracts are deleted at the end of the associated academic affiliation; code, derived statistics, and figures are not derived-from-restriction and remain.
