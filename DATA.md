# Data provenance

**Primary source:** Databento `XNAS.ITCH` (Nasdaq TotalView-ITCH), MBO schema, accessed under the Berkeley MFE programme's Databento account.

LOBSTER is a reconstruction of the same Nasdaq TotalView-ITCH feed, so Databento MBO is that feed at full depth. MBO carries every order event at every price level with nanosecond `ts_recv`/`ts_event` timestamps and order IDs, which is what makes queue-position work and exact book reconstruction possible.

## What is committed

- Source code, build files, and tests
- Small reduced message fixtures under `data/*.csv` (25 lines, 20 valid messages each) so the build and tests run on a fresh clone with no external data
- Derived results: benchmark tables, latency percentiles, analytics summaries

## What is not committed

- Databento extracts (`data/databento/`, gitignored). One symbol-day of MBO is ~68 MB compressed / ~224 MB billable; committing them would be both a licence problem and a repository problem.
- The LOBSTER academic sample, which is free but redistributed under its own terms

## Reproducing

Pull one symbol-day and convert it to the LOBSTER message layout:

```bash
python scripts/databento_to_lobster.py <mbo.dbn.zst> --out messages.csv --sequence-out seq.csv
```

Requires `DATABENTO_API_KEY` and an entitlement to `XNAS.ITCH`.

## Licence and retention

Databento data is accessed under a programme licence and is not redistributed here. Raw extracts are deleted at the end of the associated academic affiliation; code, derived statistics, and figures are not derived-from-restriction and remain.
