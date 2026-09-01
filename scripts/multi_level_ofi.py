#!/usr/bin/env python3
"""Multi-level integrated OFI versus best-level OFI.

Cont, Cucuringu and Zhang (Quantitative Finance 2023) show that combining order
flow imbalance across the top levels of the book into a single integrated
variable explains contemporaneous price impact better than best-level OFI alone.
Their integration is a principal component across the per-level OFIs, which is
the natural choice because the levels are strongly correlated and a plain sum
would let deep, thin levels dominate through sheer count.

This reproduces that comparison on Databento MBP-10 for one session. Vendor
depth is used rather than the engine's reconstruction so the result is a
statement about the market, not about the book-building code.

Per level i, the OFI increment between consecutive book states follows the
Cont-Kukanov-Stoikov transition form:

    e_i = 1{Pb_i >= Pb_i'} qb_i - 1{Pb_i <= Pb_i'} qb_i'
        - 1{Pa_i <= Pa_i'} qa_i + 1{Pa_i >= Pa_i'} qa_i'

The script reports, at several horizons:
  - contemporaneous R^2 of price change on OFI (the impact relation)
  - predictive R^2 on the NEXT interval (what is actually tradeable)
for best-level OFI, a naive sum across levels, and the PCA-integrated variable.

Usage:
    python scripts/multi_level_ofi.py --vendor data/databento/MSFT_..._mbp10.dbn.zst
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd


def level_ofi(bid_px, bid_sz, ask_px, ask_sz) -> np.ndarray:
    """Per-event OFI for one level, from consecutive book states."""
    e = np.zeros(len(bid_px))
    e[1:] = (
        (bid_px[1:] >= bid_px[:-1]) * bid_sz[1:]
        - (bid_px[1:] <= bid_px[:-1]) * bid_sz[:-1]
        - (ask_px[1:] <= ask_px[:-1]) * ask_sz[1:]
        + (ask_px[1:] >= ask_px[:-1]) * ask_sz[:-1]
    )
    return np.nan_to_num(e)


def build(path: Path, depth: int) -> pd.DataFrame:
    import databento as db

    frame = db.DBNStore.from_file(str(path)).to_df()
    out = {}
    for level in range(depth):
        bp = pd.to_numeric(frame[f"bid_px_{level:02d}"], errors="coerce").to_numpy(float)
        bs = pd.to_numeric(frame[f"bid_sz_{level:02d}"], errors="coerce").to_numpy(float)
        ap = pd.to_numeric(frame[f"ask_px_{level:02d}"], errors="coerce").to_numpy(float)
        asz = pd.to_numeric(frame[f"ask_sz_{level:02d}"], errors="coerce").to_numpy(float)
        out[f"ofi_{level}"] = level_ofi(np.nan_to_num(bp), np.nan_to_num(bs),
                                        np.nan_to_num(ap), np.nan_to_num(asz))
    mid = (pd.to_numeric(frame["bid_px_00"], errors="coerce").to_numpy(float)
           + pd.to_numeric(frame["ask_px_00"], errors="coerce").to_numpy(float)) / 2.0
    out["mid"] = mid
    return pd.DataFrame(out).replace([np.inf, -np.inf], np.nan).dropna()


def integrate_pca(ofi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """First principal component of the per-level OFIs, sign-aligned to level 0.

    Standardising first matters: level sizes differ by an order of magnitude
    across the book, and without it the component is dominated by whichever
    level happens to be deepest rather than by common flow.
    """
    z = (ofi - ofi.mean(axis=0)) / (ofi.std(axis=0) + 1e-12)
    _, _, vt = np.linalg.svd(z - z.mean(axis=0), full_matrices=False)
    w = vt[0]
    if w[0] < 0:
        w = -w
    return z @ w, w


def _r2(y: np.ndarray, x: np.ndarray) -> float:
    if len(y) < 30 or np.allclose(x.std(), 0):
        return float("nan")
    beta = np.polyfit(x, y, 1)
    resid = y - np.polyval(beta, x)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return 1.0 - float((resid ** 2).sum()) / ss_tot if ss_tot > 0 else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendor", type=Path, required=True)
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument("--horizons", type=int, nargs="+", default=[10, 50, 100, 500])
    args = ap.parse_args()

    data = build(args.vendor, args.depth)
    cols = [f"ofi_{i}" for i in range(args.depth)]
    ofi = data[cols].to_numpy(float)
    mid = data["mid"].to_numpy(float)

    integrated, weights = integrate_pca(ofi)
    print(f"events: {len(data):,}   depth: {args.depth}")
    print("PCA weights across levels: " + " ".join(f"{w:+.2f}" for w in weights))
    print()

    rows = []
    for h in args.horizons:
        contemp = np.full(len(mid), np.nan)
        contemp[h:] = mid[h:] - mid[:-h]
        fwd = np.full(len(mid), np.nan)
        fwd[:-h] = mid[h:] - mid[:-h]

        def roll(x: np.ndarray) -> np.ndarray:
            """Trailing sum over h events, aligned so out[i] covers x[i-h+1..i]."""
            c = np.cumsum(np.insert(np.asarray(x, dtype=float), 0, 0.0))
            out = np.full(len(x), np.nan)
            out[h - 1:] = c[h:] - c[: len(c) - h]
            return out

        variants = {
            "best level (L1)": roll(ofi[:, 0]),
            "naive sum": roll(ofi.sum(axis=1)),
            "PCA integrated": roll(integrated),
        }
        for name, series in variants.items():
            for label, target in (("contemporaneous", contemp), ("predictive", fwd)):
                m = np.isfinite(series) & np.isfinite(target)
                rows.append({"horizon": h, "ofi": name, "relation": label,
                             "r2": _r2(target[m], series[m]), "n": int(m.sum())})

    table = pd.DataFrame(rows)
    for rel in ("contemporaneous", "predictive"):
        sub = table[table["relation"] == rel].pivot(index="horizon", columns="ofi", values="r2")
        print(f"{rel} R^2")
        print(sub.to_string(float_format=lambda v: f"{v:0.4f}"))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
