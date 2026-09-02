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

CURRENT RESULT
--------------
On MSFT 2024-06-03, with action-aware sequence alignment, engine-derived MBP-10
agrees with Databento's own MBP-10 on 100.0000% of compared cells -- all
53,551,466 of them, at every one of the ten levels:

    vendor book rows (A/C/F)  vs engine post-event    100.0000%
    vendor T prints           vs engine pre-fill      100.0000%

No cells are excluded to reach that: across 53,552,200 slots there are zero
where one side reports a level and the other calls it absent, and 734 where both
agree the level is absent.

Three bugs had to be fixed to get there, two in the engine's input and one in
this harness.

1. Execution double-count (98.23% -> 99.74%)
   Databento emits THREE MBO records for one displayed execution: a T print, an
   F fill against the resting order, and a C removing that same quantity from
   the book. Applying the F and the C both reduced the resting order twice, so
   engine depth ran systematically below the vendor's. databento_to_lobster.py
   now drops the execution-mirror cancel.

2. Mixed snapshot conventions in this harness (99.74% -> 99.9938%)
   The residual was NOT a book-logic error. Databento's MBP-10 contains
   essentially no F rows (3 in the whole session); it represents a displayed
   execution as a T print followed by a C removal, and the two carry DIFFERENT
   book states:

       vendor T row  -> the book BEFORE the execution is applied
       vendor C row  -> the book AFTER it is applied

   Trade sequences split 60,292 emitting only (T,) and 38,209 emitting (T, C).
   Keeping the last vendor row per sequence therefore compared against a
   post-trade snapshot on some sequences and a pre-trade snapshot on others,
   manufacturing disagreement on roughly half of all at-touch fills.

   The giveaway: on the 3,001 fills that fully consumed the touch, the vendor
   still showed the consumed price in 3,001 of 3,001 cases.

3. Dedup keyed on the wrong field (99.9938% -> 100.0000%)
   The mirror cancel was matched on (sequence, price, size), which catches
   70,254 of 70,510 fills. The 256 misses each left a cancel in the stream that
   double-decremented a resting order, and the book carried that error until the
   order left -- so a handful of events produced 3,321 mismatched cells spread
   over 1,886 sequences, 1,585 of them single-record sequences that were simply
   downstream of the damage. Keying on (sequence, order_id) matches 70,510 of
   70,510, because the F names the resting order and the mirror C removes
   quantity from that same order.

   What made this findable: mismatched sequences sat a median 15,055 sequences
   after the nearest dedup miss, against 4,683,657 for sequences that agreed.

Ruled out along the way: cancel semantics generally (Databento `C` carries the
delta cancelled and never exceeds the remaining size -- verified over 1,925,732
cancels with zero over-cancels), modify handling (no `M` records in this
session), price truncation (zero sub-penny prices, so the 1e-9 to 1e-4
conversion is lossless), and multi-record
sequence ordering (multi-record sequences turned out to be UNDER-represented
among the mismatches, 0.3x, which is what redirected the search to drift).

Usage:
    python scripts/validate_mbp10_vs_vendor.py \
        --engine-book /tmp/engine_book.csv \
        --vendor data/databento/MSFT_2024-06-03_mbp10.dbn.zst \
        --sequences /tmp/msft_seq.csv \
        --messages data/databento/MSFT_2024-06-03_message.csv
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
_LOBSTER_DISPLAYED_FILL = 4


def _midnight_ns(ts_ns: int) -> int:
    local = datetime.fromtimestamp(ts_ns / 1e9, tz=_EXCHANGE_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1e9)


def _as_char(value) -> str:
    """Databento exposes action as either a str or its integer code."""
    return chr(value) if isinstance(value, (int, np.integer)) else str(value)


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
    # the action of the source MBO record. It decides which book state the row
    # carries, so it is required for correct alignment -- see module docstring.
    out["action"] = np.array([_as_char(a) for a in frame["action"].to_numpy()])
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


def block_bounds(sequences: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Row index of the first and last engine row sharing each MBO sequence."""
    if not (np.diff(sequences) >= 0).all():
        raise SystemExit("sequences are not monotonically non-decreasing; "
                         "the sidecar does not match the engine book")
    first = np.flatnonzero(np.r_[True, np.diff(sequences) != 0])
    last = np.r_[first[1:] - 1, len(sequences) - 1]
    return first, last


def pre_fill_rows(sequences: np.ndarray, event_types: np.ndarray,
                  first: np.ndarray, last: np.ndarray) -> np.ndarray:
    """Row carrying the book state just before each block's first displayed fill.

    Blocks with no fill fall back to the block's last row: a hidden print is a
    book no-op, so its pre and post states are identical.
    """
    rows = np.arange(len(sequences))
    sentinel = len(sequences)
    first_fill = np.minimum.reduceat(
        np.where(event_types == _LOBSTER_DISPLAYED_FILL, rows, sentinel), first)
    has_fill = first_fill < sentinel
    return np.maximum(np.where(has_fill, first_fill - 1, last), 0)


def compare(vendor: pd.DataFrame, engine: pd.DataFrame, sequences: np.ndarray,
            pick: np.ndarray, first: np.ndarray, depth: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-level (matched, compared) cell counts for one alignment."""
    snapshot = engine.iloc[pick].reset_index(drop=True)
    snapshot.insert(0, "sequence", sequences[first])
    merged = vendor.merge(snapshot, on="sequence", suffixes=("_v", "_e"))

    matched = np.zeros(depth, dtype=np.int64)
    compared = np.zeros(depth, dtype=np.int64)
    for level in range(depth):
        for tag in ("bid", "ask"):
            for field in ("px", "sz"):
                col = f"{tag}_{field}_{level}"
                v, e = merged[f"{col}_v"], merged[f"{col}_e"]
                both = v.notna() & e.notna()
                compared[level] += int(both.sum())
                matched[level] += int((both & np.isclose(v, e, rtol=0, atol=0.5)).sum())
    return matched, compared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine-book", type=Path, required=True)
    parser.add_argument("--vendor", type=Path, required=True)
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--sequences", type=Path, default=None,
                        help="sidecar row->sequence CSV from databento_to_lobster.py; "
                             "enables exact alignment instead of lossy timestamp matching")
    parser.add_argument("--messages", type=Path, default=None,
                        help="the LOBSTER message CSV fed to the engine. Needed to locate "
                             "each displayed fill, which is what lets vendor T prints be "
                             "compared against the pre-fill book they actually carry")
    args = parser.parse_args()

    engine = pd.read_csv(args.engine_book)
    vendor = load_vendor(args.vendor, args.depth)

    # engine covers the full session; restrict the comparison to the overlap
    lo, hi = engine["timestamp"].min(), engine["timestamp"].max()
    vendor = vendor[(vendor["timestamp"] >= lo) & (vendor["timestamp"] <= hi)].copy()
    if vendor.empty:
        raise SystemExit("no timestamp overlap between engine output and vendor file")

    if args.sequences is None:
        # Lossy. Many MBO events share a ts_recv, so this compares the engine at
        # a different point in the stream than the vendor snapshot and
        # manufactures disagreement that is not there.
        engine = engine.sort_values("timestamp")
        vendor = vendor.sort_values("timestamp")
        merged = pd.merge_asof(vendor, engine, on="timestamp", direction="backward",
                               suffixes=("_v", "_e"))
        print("WARNING: no --sequences sidecar; falling back to timestamp alignment\n")
        matched = np.zeros(args.depth, dtype=np.int64)
        compared = np.zeros(args.depth, dtype=np.int64)
        for level in range(args.depth):
            for tag in ("bid", "ask"):
                for field in ("px", "sz"):
                    col = f"{tag}_{field}_{level}"
                    v, e = merged[f"{col}_v"], merged[f"{col}_e"]
                    both = v.notna() & e.notna()
                    compared[level] += int(both.sum())
                    matched[level] += int((both & np.isclose(v, e, rtol=0, atol=0.5)).sum())
        groups = {"timestamp-aligned": (matched, compared)}
    else:
        sequences = pd.read_csv(args.sequences)["sequence"].to_numpy()
        engine = engine.reset_index(drop=True)
        if len(sequences) != len(engine):
            raise SystemExit(f"sidecar has {len(sequences):,} rows but the engine book has "
                             f"{len(engine):,}; they must come from the same conversion")
        first, last = block_bounds(sequences)

        # Vendor A/C/F rows report the book after their event is applied, so
        # they align with the engine at the end of the sequence's block.
        book_rows = vendor[vendor["action"] != "T"].drop_duplicates("sequence", keep="last")
        groups = {"vendor book rows (A/C/F) vs engine post-event":
                  compare(book_rows, engine, sequences, last, first, args.depth)}

        # Vendor T rows are trade prints reporting the book BEFORE the execution.
        prints = vendor[vendor["action"] == "T"].drop_duplicates("sequence", keep="last")
        if args.messages is None:
            print("NOTE: no --messages, so vendor T prints are excluded. They carry the\n"
                  "      pre-trade book and would otherwise be compared against the wrong\n"
                  "      engine state.\n")
        elif not prints.empty:
            events = pd.read_csv(args.messages, header=None, usecols=[1]).iloc[:, 0].to_numpy()
            if len(events) != len(engine):
                raise SystemExit(f"message CSV has {len(events):,} rows but the engine book "
                                 f"has {len(engine):,}; they must be the same conversion")
            pre = pre_fill_rows(sequences, events, first, last)
            groups["vendor T prints          vs engine pre-fill"] = \
                compare(prints, engine, sequences, pre, first, args.depth)

    width = max(len(name) for name in groups)
    total_matched = total_compared = 0
    print(f"{'alignment':<{width}}  {'agreement':>10}  {'cells':>25}")
    for name, (matched, compared) in groups.items():
        m, c = int(matched.sum()), int(compared.sum())
        total_matched += m
        total_compared += c
        print(f"{name:<{width}}  {100 * m / c:9.4f}%  {m:>11,}/{c:<13,}")

    if len(groups) > 1:
        print(f"{'combined':<{width}}  {100 * total_matched / total_compared:9.4f}%  "
              f"{total_matched:>11,}/{total_compared:<13,}")

    print(f"\n{'level':>6}  " + "  ".join(f"{name.split(' vs ')[0].strip():>24}"
                                          for name in groups))
    for level in range(args.depth):
        cells = []
        for matched, compared in groups.values():
            c = compared[level]
            cells.append(f"{100 * matched[level] / c:23.4f}%" if c else f"{'n/a':>24}")
        print(f"{level:>6}  " + "  ".join(cells))

    overall = 100.0 * total_matched / total_compared if total_compared else float("nan")
    print()
    if overall >= 99.99:
        print("VERDICT: engine-derived MBP-10 matches the vendor's own derivation.")
    else:
        print("VERDICT: divergence present -- inspect the first differing level above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
