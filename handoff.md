# Handoff: lob-engine-cpp

## Current goal (complete as of 2026-09-03)

Put the repo's correctness claim on **two** Databento sessions instead of one,
and validate a second time with an external yardstick (LOB-Bench).

## Verified state

- Release build in `build/`; `ctest` **4/4 pass**, `pytest tests -q` **8/8 pass**
  (2 pre-existing + 6 new in `tests/test_scripts.py`).
- **Both sessions reach 100.0000% MBP-10 agreement with the vendor.**

| | MSFT 2024-06-03 | INTC 2024-08-02 |
|---|---:|---:|
| MBO records | 4,003,834 | 2,356,988 |
| LOBSTER messages | 3,862,854 | 2,051,624 |
| cells compared | 53,551,466 | 60,128,762 |
| both agree absent | 734 | 4,638 |
| one side only | 0 | 0 |

- INTC hit 100.0000% on the **first run, with no code changes**. The three MSFT
  fixes were corrections to a misread of Databento's event model, not tuning.
- LOB-Bench: all 12 statistics score 0.000000 on L1 and Wasserstein-1 for both
  sessions. Tables local at `report/lob_bench_*.csv` (gitignored by
  `/report/*.csv`); the README carries the numbers.
- Multi-level OFI now run on both. New cross-session finding: in the large-tick
  book best-level OFI explains far less (0.0163 vs MSFT 0.1121 at 10 events) and
  the multi-level lift is 6.3x against MSFT's 2.0x.

## Data

`data/databento/` (gitignored). Both sessions cost **$0.00** — `XNAS.ITCH` is
covered outright by the programme entitlement, confirmed via
`metadata.get_cost`, not estimated.

**`MSFT_2024-06-03_message.csv` was stale** and has been regenerated. The old
file had 3,933,364 rows against the current converter's 3,862,854; the 70,510
difference is exactly the execution-mirror cancels the `(sequence, order_id)`
dedup drops, so it predated that fix. Preserved as
`MSFT_2024-06-03_message.stale-predup.csv` for reference — safe to delete.
This mattered downstream: see the sibling impact repo's handoff.

## New tooling

- `scripts/fetch_databento_session.py` — prices a request with
  `metadata.get_cost` and downloads nothing without `--confirm`.
- `scripts/run_lob_bench.py` — builds a vendor-aligned book, emits both sides as
  LOBSTER files, runs LOB-Bench's own scoring.
- `scripts/validate_mbp10_vs_vendor.py` — now returns a `Counts` namedtuple and
  prints explicit slot accounting (compared + both-absent + one-sided = total),
  so the "zero one-sided" claim is reproducible rather than a one-off number.

## Gotchas worth keeping

- `DATABENTO_API_KEY` lives in `~/.zshrc`, which a **non-interactive** shell does
  not source. `zsh -lc` sees nothing; use `zsh -ic`.
- LOB-Bench is a clone-and-run repo, not a pip package. Deps: numpy, pandas,
  scipy, sklearn, matplotlib, plotly, seaborn, statsmodels, tqdm (+ databento
  for `run_lob_bench.py`). A scratchpad venv was used, nothing installed into
  system python.
- The vendor's dollar prices scale to e.g. `215899.99999999997`. The diff
  tolerates it with `atol=0.5`; any integer export must **round, not truncate**.
  Pinned by `test_price_cast_rounds_rather_than_truncates`.

## Deliberately not done

- The latency/benchmark tables stay single-session. They are host-specific, and
  an INTC number taken on different hardware would corrupt the comparison rather
  than broaden it. Stated as such in the README.

## Next action

Nothing outstanding. If extending: re-measure latency for both sessions on one
pinned host to make that table two-session too.
