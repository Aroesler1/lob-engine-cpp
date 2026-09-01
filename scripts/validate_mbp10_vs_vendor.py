#!/usr/bin/env python3
"""Diff this engine's self-derived MBP-10 against Databento's own MBP-10.

Databento subscribes only to the most granular feed per publisher and derives
every other schema from MBO. So for the same session, their MBP-10 and a book
built here from their MBO are two independent derivations of the same source.
Agreement is therefore a real correctness check on the book logic -- add,
cancel, partial fill, price-level creation and removal, and ordering -- rather
than a self-consistency check.

This is a stronger validation than the LOBSTER L1 comparison the repo already
has, for two reasons: it covers ten levels rather than one, and MBO carries
every order event, so unlike a level-scoped LOBSTER message file there is no
structural reason for the two to diverge.

Alignment: both sides are keyed on the exchange-local seconds-since-midnight
derived from the same `ts_recv` nanosecond field, using the identical conversion
in databento_to_lobster.py. Vendor MBP-10 emits a row only for events that
change the top ten, so vendor rows are a subset of engine rows and the engine is
sampled at the vendor's timestamps.

CURRENT RESULT -- READ BEFORE CITING THIS
----------------------------------------
On MSFT 2024-06-03, with exact sequence alignment, engine-derived MBP-10 agrees
with Databento's own MBP-10 on 98.23% of compared cells (51.1M of 52.0M).

That is NOT a clean validation and is not presented as one. The residual 1.77%
is only partly explained:

  - Prices agree far better than sizes (98.99% vs 92.46% at level 0), so the
    book's structure is right while quantities drift.
  - Where they differ, the engine is smaller in 87,583 of 98,102 cases
    (median -6 shares), i.e. the engine removes liquidity the vendor still shows.
  - 29,794 of those mismatches sit on a sequence carrying a fill, and in 20,270
    of them the gap equals the fill size exactly -- consistent with the vendor
    reporting the pre-trade book on trade events while the engine reports
    post-trade. That accounts for roughly a fifth of the disagreement.
  - The remaining 68,308 mismatches are NOT on fill sequences and remain
    unexplained.

Ruled out so far: cancel semantics (Databento `C` carries the delta cancelled
and never exceeds the remaining size -- verified across 1,925,732 cancels, zero
over-cancels), and modify handling (this session contains no `M` records).

Until the residual is explained, this harness is a diagnostic, not a proof of
correctness, and the README should not claim vendor-verified reconstruction.

Usage:
    python scripts/validate_mbp10_vs_vendor.py \
        --engine-book /tmp/engine_book.csv \
        --vendor data/databento/MSFT_2024-06-03_mbp10.dbn.zst \
        --sequences /tmp/msft_seq.csv
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_EXCHANGE_TZ = ZoneInfo("America/New_York")
_DBN_TO_LOBSTER_PRICE = 100_000


def _midnight_ns(ts_ns: int) -> int:
    local = datetime.fromtimestamp(ts_ns / 1e9, tz=_EXCHANGE_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1e9)


def load_vendor(path: Path, depth: int) -> pd.DataFrame:
    import databento as db

    store = db.DBNStore.from_file(str(path))
    frame = store.to_df()
    if frame.empty:
        raise SystemExit("vendor file produced no rows")

    ts_ns = (frame.index.astype("int64").to_numpy()
             if frame.index.name == "ts_recv"
             else frame["ts_recv"].astype("int64").to_numpy())
    midnight = _midnight_ns(int(ts_ns[0]))

    out = pd.DataFrame({"timestamp": (ts_ns - midnight) / 1e9,
                        "sequence": frame["sequence"].to_numpy()})
    for level in range(depth):
        for tag in ("bid", "ask"):
            px_col, sz_col = f"{tag}_px_{level:02d}", f"{tag}_sz_{level:02d}"
            if px_col not in frame.columns:
                out[f"{tag}_px_{level}"] = np.nan
                out[f"{tag}_sz_{level}"] = np.nan
                continue
            # DBNStore.to_df() already scales prices to dollars; the engine
            # emits LOBSTER-style 1e-4 integers, so normalise the vendor side
            # up rather than dividing the engine side and inviting float error.
            # .to_numpy() throughout: the vendor frame's ts_recv index has
            # duplicates, so Series assignment would try to reindex and raise.
            px = pd.to_numeric(frame[px_col], errors="coerce").to_numpy() * 10_000.0
            sz = pd.to_numeric(frame[sz_col], errors="coerce").to_numpy()
            # an absent level is reported as size 0 with a sentinel price
            absent = (np.nan_to_num(sz) == 0) | ~np.isfinite(px)
            out[f"{tag}_px_{level}"] = np.where(absent, np.nan, px)
            out[f"{tag}_sz_{level}"] = np.where(absent, np.nan, sz)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine-book", type=Path, required=True)
    parser.add_argument("--vendor", type=Path, required=True)
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--levels-reported", type=int, default=10)
    parser.add_argument("--sequences", type=Path, default=None,
                        help="sidecar row->sequence CSV from databento_to_lobster.py; "
                             "enables exact alignment instead of lossy timestamp matching")
    args = parser.parse_args()

    engine = pd.read_csv(args.engine_book)
    vendor = load_vendor(args.vendor, args.depth)

    # engine covers the full session; restrict the comparison to the overlap
    lo, hi = engine["timestamp"].min(), engine["timestamp"].max()
    vendor = vendor[(vendor["timestamp"] >= lo) & (vendor["timestamp"] <= hi)].copy()
    if vendor.empty:
        raise SystemExit("no timestamp overlap between engine output and vendor file")

    if args.sequences is not None:
        # Exact alignment. Many MBO events share a ts_recv, so timestamp
        # matching compares the engine at a different point in the stream than
        # the vendor snapshot and manufactures disagreement that is not there.
        seq = pd.read_csv(args.sequences)
        engine = engine.reset_index(drop=True)
        engine["sequence"] = seq["sequence"].reindex(engine.index).to_numpy()
        engine = engine.dropna(subset=["sequence"])
        # keep the LAST engine state per sequence: several MBO records can share
        # one sequence, and the vendor snapshot reflects all of them applied
        engine = engine.drop_duplicates(subset="sequence", keep="last")
        vendor = vendor.drop_duplicates(subset="sequence", keep="last")
        merged = vendor.merge(engine, on="sequence", suffixes=("_v", "_e"))
        alignment = "sequence (exact)"
    else:
        engine = engine.sort_values("timestamp")
        vendor = vendor.sort_values("timestamp")
        merged = pd.merge_asof(vendor, engine, on="timestamp", direction="backward",
                               suffixes=("_v", "_e"))
        alignment = "timestamp (lossy where timestamps repeat)"

    print(f"engine rows: {len(engine):,}   vendor rows in overlap: {len(vendor):,}")
    print(f"aligned on: {alignment}")
    print(f"compared:   {len(merged):,}   levels: {args.levels_reported}\n")

    print(f"{'level':>6}  {'bid px match':>13}  {'bid sz match':>13}  "
          f"{'ask px match':>13}  {'ask sz match':>13}")
    total_cells = matched_cells = 0
    for level in range(args.levels_reported):
        row = [f"{level:>6}"]
        for tag in ("bid", "ask"):
            for field in ("px", "sz"):
                col = f"{tag}_{field}_{level}"
                v, e = merged[f"{col}_v"], merged[f"{col}_e"]
                both_present = v.notna() & e.notna()
                agree = both_present & np.isclose(v, e, rtol=0, atol=0.5)
                n = int(both_present.sum())
                ok = int(agree.sum())
                total_cells += n
                matched_cells += ok
                pct = 100.0 * ok / n if n else float("nan")
                row.append(f"{pct:12.4f}%")
        print("  ".join(row))

    overall = 100.0 * matched_cells / total_cells if total_cells else float("nan")
    print(f"\noverall agreement across compared cells: {overall:.4f}% "
          f"({matched_cells:,}/{total_cells:,})")
    if overall >= 99.99:
        print("VERDICT: engine-derived MBP-10 matches the vendor's own derivation.")
    else:
        print("VERDICT: divergence present -- inspect the first differing level above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
