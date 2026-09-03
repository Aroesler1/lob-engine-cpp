#!/usr/bin/env python3
"""Score this engine's book against the vendor's with LOB-Bench.

LOB-Bench (Nagy et al., ICML 2025, github.com/peernagy/lob_bench) is the
standard yardstick for LOB *generative* models: it takes "real" and "generated"
LOBSTER sequences and reports L1 / Wasserstein-1 distances between their
distributions of spread, depth, imbalance, inter-arrival time and so on.

The mapping used here is deliberate and worth stating plainly, because it is not
the use the tool was written for:

    generated = this engine's book, reconstructed from Databento MBO
    real      = Databento's own MBP-10 for the same session

Both sides consume the SAME message stream, so this is not a test of whether the
engine invents realistic markets -- it cannot be. It is a distributional
cross-check of the book logic against an outside implementation of the metrics,
and it is a second thing besides: it proves the engine's LOBSTER output loads
and scores in the standard academic toolchain unmodified, through someone else's
parser rather than ours.

What that means for the result: where cell-level agreement is already exact, the
divergences are zero BY CONSTRUCTION, and a zero here is a regression check, not
independent evidence of realism. It is informative in the direction of failure
-- any non-zero value would mean the two books differ somewhere the cell diff
did not look, or that our LOBSTER export is malformed.

Alignment follows scripts/validate_mbp10_vs_vendor.py: vendor A/C/F rows carry
the book after their event, so they align with the engine at the end of that
sequence's block. Vendor T prints carry the PRE-trade book and are excluded here
rather than mixed in, which would compare two different snapshot conventions.

Usage:
    python scripts/run_lob_bench.py \
        --engine-book data/databento/INTC_2024-08-02_book10.csv \
        --vendor data/databento/INTC_2024-08-02_mbp10.dbn.zst \
        --sequences data/databento/INTC_2024-08-02_seq.csv \
        --messages data/databento/INTC_2024-08-02_message.csv \
        --symbol INTC --date 2024-08-02 \
        --lob-bench /path/to/lob_bench --work-dir /tmp/lobbench_intc
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_mbp10_vs_vendor import block_bounds, load_vendor  # noqa: E402

RTH_OPEN = 34_200
RTH_CLOSE = 57_600
# LOBSTER's sentinels for a level that is not present.
ABSENT_ASK_PX, ABSENT_BID_PX, ABSENT_SZ = 9_999_999_999, -9_999_999_999, 0

_MESSAGE_COLS = ["time", "event_type", "order_id", "size", "price", "direction"]


def aligned_rows(engine: pd.DataFrame, vendor: pd.DataFrame,
                 sequences: np.ndarray) -> tuple[np.ndarray, pd.DataFrame]:
    """Engine row indices that have a vendor book row, and those vendor rows.

    Returned in engine order, one row per matched sequence.
    """
    first, last = block_bounds(sequences)
    block = pd.DataFrame({"sequence": sequences[first], "engine_row": last})
    book_rows = vendor[vendor["action"] != "T"].drop_duplicates("sequence", keep="last")
    merged = book_rows.merge(block, on="sequence", how="inner")

    inside = ((engine["timestamp"].to_numpy()[merged["engine_row"].to_numpy()] >= RTH_OPEN)
              & (engine["timestamp"].to_numpy()[merged["engine_row"].to_numpy()] < RTH_CLOSE))
    merged = merged[inside].sort_values("engine_row")
    return merged["engine_row"].to_numpy(), merged.reset_index(drop=True)


def to_lobster_book(frame: pd.DataFrame, depth: int) -> np.ndarray:
    """Columns in LOBSTER order: ask_px, ask_sz, bid_px, bid_sz per level.

    The vendor frame carries NaN for an absent level and the engine writes an
    empty field for one, so both arrive as NaN and get the same sentinel.

    Prices are ROUNDED, not truncated. Databento reports prices in dollars and
    load_vendor scales them by 1e4, so a level at $21.59 arrives as
    215899.99999999997; truncating turns that into 215899 and manufactures a
    one-tick disagreement with the engine on roughly every price cell. The
    vendor diff tolerates it with atol=0.5, an integer export has to round.
    """
    out = np.empty((len(frame), 4 * depth), dtype=np.int64)
    for level in range(depth):
        suffix = f"_{level}"
        cols = {tag: frame[f"{tag}_px{suffix}"].to_numpy(dtype=float) for tag in ("ask", "bid")}
        sizes = {tag: frame[f"{tag}_sz{suffix}"].to_numpy(dtype=float) for tag in ("ask", "bid")}
        for offset, tag, absent_px in ((0, "ask", ABSENT_ASK_PX), (2, "bid", ABSENT_BID_PX)):
            px, sz = cols[tag], sizes[tag]
            missing = ~np.isfinite(px) | ~np.isfinite(sz)
            out[:, 4 * level + offset] = np.rint(
                np.where(missing, absent_px, np.nan_to_num(px))).astype(np.int64)
            out[:, 4 * level + offset + 1] = np.rint(
                np.where(missing, ABSENT_SZ, np.nan_to_num(sz))).astype(np.int64)
    return out


def write_sequences(work_dir: Path, symbol: str, date: str, messages: pd.DataFrame,
                    gen_book: np.ndarray, real_book: np.ndarray,
                    n_windows: int, window: int) -> int:
    """Cut the aligned stream into equal windows and write the three folders."""
    for folder in ("real", "gen", "cond"):
        (work_dir / folder).mkdir(parents=True, exist_ok=True)

    total = len(messages)
    if total < n_windows * window:
        n_windows = max(1, total // window)
    # spread the windows across the session rather than taking a contiguous
    # block, so open, midday and close are all represented
    starts = np.linspace(0, total - window, n_windows).astype(int)

    for real_id, start in enumerate(starts):
        stop = start + window
        chunk = messages.iloc[start:stop]
        stem = f"{symbol}_{date}_{{kind}}_real_id_{real_id}{{suffix}}.csv"

        for folder, book, suffix in (("real", real_book, ""),
                                     ("gen", gen_book, "_gen_id_0")):
            chunk.to_csv(work_dir / folder / stem.format(kind="message", suffix=suffix),
                         header=False, index=False)
            np.savetxt(work_dir / folder / stem.format(kind="orderbook", suffix=suffix),
                       book[start:stop], fmt="%d", delimiter=",")

        # LOB-Bench expects a conditioning prefix per sequence; one row is
        # enough to satisfy the loader without feeding any extra information
        # into the unconditional scores.
        head = messages.iloc[max(0, start - 1):start] if start else messages.iloc[:1]
        head.to_csv(work_dir / "cond" / stem.format(kind="message", suffix=""),
                    header=False, index=False)
        rows = real_book[max(0, start - 1):start] if start else real_book[:1]
        np.savetxt(work_dir / "cond" / stem.format(kind="orderbook", suffix=""),
                   rows, fmt="%d", delimiter=",")
    return len(starts)


def score(work_dir: Path, lob_bench: Path) -> pd.DataFrame:
    """Run LOB-Bench's own scoring on the folders just written."""
    sys.path.insert(0, str(lob_bench))
    import data_loading as lb_loading
    import eval as lb_eval
    import metrics as lb_metrics
    import scoring as lb_scoring

    loader = lb_loading.Simple_Loader(
        str(work_dir / "real"), str(work_dir / "gen"), str(work_dir / "cond"))

    # the battery the paper leads with, plus the two message-derived timings
    config = {
        "spread": {"fn": lambda m, b: lb_eval.spread(m, b).values, "discrete": True},
        "orderbook_imbalance": {"fn": lambda m, b: lb_eval.orderbook_imbalance(m, b).values},
        "ask_volume_touch": {"fn": lambda m, b: lb_eval.l1_volume(m, b).ask_vol.values},
        "bid_volume_touch": {"fn": lambda m, b: lb_eval.l1_volume(m, b).bid_vol.values},
        "ask_volume_10": {"fn": lambda m, b: lb_eval.total_volume(m, b, 10).ask_vol_10.values},
        "bid_volume_10": {"fn": lambda m, b: lb_eval.total_volume(m, b, 10).bid_vol_10.values},
        "limit_ask_order_depth": {"fn": lambda m, b: lb_eval.limit_order_depth(m, b)[0].values},
        "limit_bid_order_depth": {"fn": lambda m, b: lb_eval.limit_order_depth(m, b)[1].values},
        "ask_cancellation_depth": {"fn": lambda m, b: lb_eval.cancellation_depth(m, b)[0].values},
        "bid_cancellation_depth": {"fn": lambda m, b: lb_eval.cancellation_depth(m, b)[1].values},
        "log_inter_arrival_time": {
            "fn": lambda m, b: np.log(
                lb_eval.inter_arrival_time(m).replace({0: 1e-9}).values.astype(float))},
        "log_time_to_cancel": {
            "fn": lambda m, b: np.log(
                lb_eval.time_to_cancel(m).dt.total_seconds()
                .replace({0: 1e-9}).values.astype(float))},
    }
    metric_fns = {"l1": lb_metrics.l1_by_group, "wasserstein": lb_metrics.wasserstein}

    scores, _, _ = lb_scoring.run_benchmark(loader, config, metric_fns)
    rows = [{"statistic": name,
             "l1": float(value["l1"][0]),
             "wasserstein": float(value["wasserstein"][0])}
            for name, value in scores.items()]
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine-book", type=Path, required=True)
    ap.add_argument("--vendor", type=Path, required=True)
    ap.add_argument("--sequences", type=Path, required=True)
    ap.add_argument("--messages", type=Path, required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--lob-bench", type=Path, required=True,
                    help="clone of github.com/peernagy/lob_bench")
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument("--windows", type=int, default=100)
    ap.add_argument("--window-size", type=int, default=4096)
    ap.add_argument("--out", type=Path, default=None, help="CSV to write the score table to")
    args = ap.parse_args()

    engine = pd.read_csv(args.engine_book)
    sequences = pd.read_csv(args.sequences)["sequence"].to_numpy()
    if len(sequences) != len(engine):
        raise SystemExit(f"sidecar has {len(sequences):,} rows but the engine book has "
                         f"{len(engine):,}; they must come from the same conversion")
    vendor = load_vendor(args.vendor, args.depth)

    rows, vendor_rows = aligned_rows(engine, vendor, sequences)
    print(f"{args.symbol} {args.date}: {len(rows):,} RTH rows aligned "
          f"to a vendor book row (of {len(engine):,} engine rows)")

    messages = pd.read_csv(args.messages, names=_MESSAGE_COLS, header=None).iloc[rows]
    gen_book = to_lobster_book(engine.iloc[rows].reset_index(drop=True), args.depth)
    real_book = to_lobster_book(vendor_rows, args.depth)

    identical = np.array_equal(gen_book, real_book)
    print(f"engine vs vendor book cells identical on these rows: {identical} "
          f"({gen_book.size:,} cells)")

    n = write_sequences(args.work_dir, args.symbol, args.date, messages,
                        gen_book, real_book, args.windows, args.window_size)
    print(f"wrote {n} sequences of {args.window_size:,} messages -> {args.work_dir}\n")

    table = score(args.work_dir, args.lob_bench)
    print(table.to_string(index=False, float_format=lambda v: f"{v:0.6f}"))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.out, index=False)
        print(f"\nsaved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
