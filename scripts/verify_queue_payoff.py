#!/usr/bin/env python3
"""Reconcile published queue markouts using aggregate data only."""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from queue_payoff import compare
from sessions import SESSIONS


def build(root):
    deciles = pd.read_csv(root / "report/queue_payoff/deciles.csv")
    if set(deciles.session) != set(SESSIONS):
        raise ValueError("the fixed fifteen-session sample is required")
    for _, group in deciles.groupby("session"):
        if sorted(group.decile) != list(range(10)):
            raise ValueError("each session must contain ten unique deciles")
    if not (deciles.orders == deciles.observed_orders + deciles.missing_mark_orders).all():
        raise ValueError("observed and missing order counts must reconcile")
    if not deciles.horizon_s.eq(10).all() or not deciles.fee_ticks.eq(0).all():
        raise ValueError("published comparison uses ten seconds and zero fees")
    if (deciles.executed_qty > deciles.original_qty).any():
        raise ValueError("executed shares cannot exceed submitted shares")
    if not np.isfinite(deciles.net_payoff_ticks).all():
        raise ValueError("all published decile payoffs must be observed")
    legacy = pd.read_csv(root / "report/queue_position/summary.csv")
    paired = compare(deciles, legacy)
    pd.testing.assert_frame_equal(paired, pd.read_csv(root / "report/queue_payoff/comparison.csv"),
                                  check_exact=False, rtol=1e-10, atol=1e-10)
    paired["symbol"] = paired.session.str.split("_").str[0]
    rows = []
    for symbol, group in [("all", paired), *paired.groupby("symbol")]:
        rows.append(dict(
            symbol=symbol, sessions=len(group), orders=int(group.orders.sum()),
            observed_orders=int(group.observed_orders.sum()),
            missing_mark_orders=int(group.missing_mark_orders.sum()),
            partial_fill_orders=int(group.partial_fill_orders.sum()),
            positive_front_gap_sessions=int((group.direct_front_minus_back_ticks > 0).sum()),
            negative_front_payoff_sessions=int((group.direct_front_payoff_ticks < 0).sum()),
            gap_min=float(group.direct_front_minus_back_ticks.min()),
            gap_median=float(group.direct_front_minus_back_ticks.median()),
            gap_max=float(group.direct_front_minus_back_ticks.max()),
        ))
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = build(root)
    path = root / "report/queue_payoff/summary.csv"
    if args.check:
        pd.testing.assert_frame_equal(summary, pd.read_csv(path), check_exact=False,
                                      rtol=1e-10, atol=1e-10)
    else:
        summary.to_csv(path, index=False)
    print("Verified fifteen sessions, decile arithmetic, counts and payoff summary")


if __name__ == "__main__":
    main()
