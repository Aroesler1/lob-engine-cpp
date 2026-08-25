#!/usr/bin/env python3
"""OFI vs depth imbalance: predictive power on a LOBSTER sample day.

Computes, directly from a LOBSTER orderbook_N file (ground truth, no
reconstruction error):

- L1 order flow imbalance (Cont, Kukanov & Stoikov 2014), same transition
  formula as the C++ engine's `ofi_event`, rolling-summed over event windows
- static depth imbalance at levels 1 and 5 (the engine's `order_imbalance`)

and reports Pearson/Spearman correlations of each signal against forward
mid-price log returns at multiple event horizons. This is the empirical
justification for exporting OFI next to depth imbalance.

Usage:
    python scripts/ofi_predictive_power.py MSFT_..._orderbook_10.csv \
        --window 1000 --horizons 10 100 1000
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd


def load_orderbook(path: str, levels: int = 10) -> pd.DataFrame:
    usecols = list(range(4 * levels))
    names = []
    for lvl in range(1, levels + 1):
        names += [f"ask_px_{lvl}", f"ask_sz_{lvl}", f"bid_px_{lvl}", f"bid_sz_{lvl}"]
    return pd.read_csv(path, header=None, usecols=usecols, names=names)


def l1_ofi(ob: pd.DataFrame) -> np.ndarray:
    """Per-event L1 OFI, identical to the engine's transition formula."""
    bp = ob["bid_px_1"].to_numpy(float)
    bq = ob["bid_sz_1"].to_numpy(float)
    ap = ob["ask_px_1"].to_numpy(float)
    aq = ob["ask_sz_1"].to_numpy(float)

    e = np.zeros(len(ob))
    e[1:] = (
        (bp[1:] >= bp[:-1]) * bq[1:]
        - (bp[1:] <= bp[:-1]) * bq[:-1]
        - (ap[1:] <= ap[:-1]) * aq[1:]
        + (ap[1:] >= ap[:-1]) * aq[:-1]
    )
    return e


def depth_imbalance(ob: pd.DataFrame, levels: int) -> np.ndarray:
    bid = sum(ob[f"bid_sz_{i}"].to_numpy(float) for i in range(1, levels + 1))
    ask = sum(ob[f"ask_sz_{i}"].to_numpy(float) for i in range(1, levels + 1))
    denom = bid + ask
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denom > 0, (bid - ask) / denom, np.nan)


def rolling_sum(x: np.ndarray, window: int) -> np.ndarray:
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full(len(x), np.nan)
    out[window - 1:] = c[window:] - c[:-window]
    return out


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 100:
        return np.nan
    ar = pd.Series(a[mask]).rank().to_numpy()
    br = pd.Series(b[mask]).rank().to_numpy()
    return float(np.corrcoef(ar, br)[0, 1])


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 100:
        return np.nan
    return float(np.corrcoef(a[mask], b[mask])[0, 1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("orderbook_csv")
    parser.add_argument("--levels", type=int, default=10)
    parser.add_argument("--window", type=int, default=1000, help="rolling OFI window (events)")
    parser.add_argument("--horizons", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--out", default=None, help="optional CSV output path")
    args = parser.parse_args()

    ob = load_orderbook(args.orderbook_csv, args.levels)
    mid = (ob["bid_px_1"].to_numpy(float) + ob["ask_px_1"].to_numpy(float)) / 2.0
    log_mid = np.log(mid)

    signals = {
        f"rolling_ofi_{args.window}": rolling_sum(l1_ofi(ob), args.window),
        "depth_imbalance_L1": depth_imbalance(ob, 1),
        "depth_imbalance_L5": depth_imbalance(ob, min(5, args.levels)),
    }

    rows = []
    for horizon in args.horizons:
        fwd = np.full(len(mid), np.nan)
        fwd[:-horizon] = log_mid[horizon:] - log_mid[:-horizon]
        for name, sig in signals.items():
            rows.append(
                {
                    "relation": "forward",
                    "horizon_events": horizon,
                    "signal": name,
                    "pearson": pearson(sig, fwd),
                    "spearman": spearman(sig, fwd),
                }
            )

    # Contemporaneous relation (the actual Cont-Kukanov-Stoikov claim): OFI
    # summed over a window vs the mid change over that SAME window.
    win = args.window
    concurrent = np.full(len(mid), np.nan)
    concurrent[win - 1:] = log_mid[win - 1:] - np.concatenate(([np.nan], log_mid))[: len(mid) - win + 1]
    for name, sig in signals.items():
        rows.append(
            {
                "relation": "contemporaneous",
                "horizon_events": win,
                "signal": name,
                "pearson": pearson(sig, concurrent),
                "spearman": spearman(sig, concurrent),
            }
        )

    table = pd.DataFrame(rows)
    print(f"events={len(ob)}  window={args.window}")
    print(table.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))
    if args.out:
        table.to_csv(args.out, index=False)
        print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
