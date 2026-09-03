# Handoff: lob-engine-cpp

## Current goal (complete as of 2026-09-03)

Two tranches of work, both complete and both on branch
`second-session-validation` (PR #15, still OPEN -- this work is stacked on it,
not on `main`, because the queue-reactive work reuses the LOB-Bench tooling that
only exists on that branch):

1. Put the correctness claim on **two** Databento sessions and add LOB-Bench.
2. Calibrate the **queue-reactive model** (Huang-Lehalle-Rosenbaum, JASA 2015)
   -- the repo's first fitted model.

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

## Queue-reactive calibration (tranche 2)

`scripts/queue_reactive.py --session {INTC_2024-08-02,MSFT_2024-06-03}`.
Runs end to end in ~70s on INTC, ~2min on MSFT. 22 tests in
`tests/test_queue_reactive.py`, including a synthetic Poisson book with
closed-form known intensities.

**Headline: the two sessions land on opposite sides of the model's assumptions.**
INTC has the best quote inside the +/-3 queue window 100.0% of the time; MSFT
70.8%, with only 12.7% of its messages landing on a modelled queue at all. The
sharpest diagnostic is the implied theta = p_ref moves / best-queue depletions:
0.51 on INTC, **7.34 on MSFT** -- an impossible probability, because with a
5-tick spread the price moves for reasons the model cannot see.

**The model is not stationary as specified, and this is measured.** Fitting the
paper's three intensities leaves Q+1 with 283,771 adds against 261,399
cancels+executions (+16.1M shares). That surplus is queue content leaving by
RE-INDEXING when p_ref moves -- not an order event, so it appears in none of the
three rates. Simulated unbounded with Model I, Q1 runs to 3,125 AES against a
real 17.8.

Three things were needed to get a usable simulation, in order of how much they
mattered:
1. A fitted state-dependent price-move intensity replacing the paper's scalar
   theta. It turns out to be a step function: 16.1/s at an empty touch, ~0
   otherwise.
2. Model IIb coupling (touch rates conditioned on a coarse class of the opposite
   queue). Queue independence is the binding constraint: real 1s drift of Q+1 at
   fixed own size swings -3.3 -> +1.2 AES/s with the opposite touch.
3. A reflecting cap at the largest size the real session reached. This is an
   ADMISSION, not a fix, and is documented as such; the queue-size row of the
   comparison table stays wrong on purpose.

**LOB-Bench battery** (`--lob-bench <clone>`): simulated as "generated" vs real,
both written at K=3 levels. INTC scores 0.09-0.70 across the battery where the
Brief-5 reconstruction scored 0.000000, which calibrates those zeros as real
rather than the metric being blind. Best row is limit-order depth (0.094 on
INTC); worst is inter-arrival (0.62-0.64 both sessions) -- the burstiness
failure found independently by someone else's code. MSFT's two 0.000 rows at the
touch are vacuous, not skill: Q1 is empty in both books. `time_to_cancel` is
excluded on purpose (needs order identity; a queue-size process has none).

**Traps already hit and fixed (do not re-introduce):**
- theta estimated as "p_ref moved on the SAME book row as a depletion" gives
  0.05 on INTC against a true 0.51. The book takes several messages to settle.
- `round(q/AES)` lumps a genuinely empty queue in with a thin one. `normalise()`
  reserves n=0 for empty, because the whole price-move mechanism keys on it.
- `_side_sizes` must only call a deep queue "unknown" when the ladder is FULL at
  10 levels; otherwise the book already reported everything it had.
- Order sizes were checked as a suspect and are NOT the issue: the imbalance is
  worse in shares (+1.86 AES/s) than in events (+0.96).

## Next action

Nothing outstanding. Both tranches are pushed. If extending:
- re-measure latency for both sessions on one pinned host to make that table
  two-session too;
- the Hawkes extension (Wu, Rambaldi, Muzy & Bacry, arXiv 1901.08938) is the
  documented next step for the burstiness failure -- real median inter-arrival
  57 microseconds against a simulated 6.2 ms.
