#!/usr/bin/env python3
"""Direct displayed-fill markout, keeping the historical queue score separate.

Each execution contributes quantity * side * (future mid - execution price).
An order's result divides the sum by its original submitted quantity; unfilled
shares contribute zero within the observed session. Deciles average these
per-order results, so a partially filled order never earns a full-order payoff.
This is gross marked inventory value, not liquidation P&L or causal queue value.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import sessions
from queue_position_value import _bootstrap_mean, _decile, resting_orders


def direct_payoff(messages, book, orders, *, horizon=10.0, fee_ticks=0.0):
    """Per-order, per-submitted-share payoff from all regular-session fills.

    Orders with any unobservable fill mark are excluded from the payoff mean,
    never assigned zero. The table reports their count and executed quantity.
    Remainders resting at close are counted separately, not treated as lifetime
    nonfills. The horizon uses the last book state at or before its timestamp,
    including invalid quotes, rather than carrying an earlier valid quote over
    a missing side. At a tied timestamp all messages at that timestamp apply.
    """
    if not np.isfinite(horizon) or horizon <= 0 or not np.isfinite(fee_ticks):
        raise ValueError("finite positive horizon and finite signed fee required")
    time = messages.time.to_numpy(dtype=float)
    if len(time) != len(book["timestamp"]) or not np.array_equal(time, book["timestamp"]):
        raise ValueError("message and book timestamps must align row for row")
    if len(time) == 0 or np.any(np.diff(time) < 0):
        raise ValueError("nonempty time-ordered message stream required")
    out = orders.copy().set_index("order_id", drop=False)
    if out.index.has_duplicates or (out.own_size <= 0).any():
        raise ValueError("unique orders with positive original size required")
    fills = messages[(messages.event_type == 4) & sessions.rth_mask(time)].copy()
    fills = fills[fills.order_id.isin(out.index)]
    fills = fills.join(out[["direction", "arrival"]], on="order_id", rsuffix="_order")
    if ((fills.time < fills.arrival) | (fills.direction != fills.direction_order)
            | (fills["size"] <= 0)).any():
        raise ValueError("execution violates order lifecycle")
    qty = fills.groupby("order_id")["size"].sum().reindex(out.index, fill_value=0)
    if (qty > out.own_size).any():
        raise ValueError("executed quantity exceeds original order size")
    targets = fills.time.to_numpy() + horizon
    idx = np.searchsorted(time, targets, side="right") - 1
    bid = book["bid_px_0"][idx].astype(float)
    ask = book["ask_px_0"][idx].astype(float)
    valid = ((targets < sessions.RTH_CLOSE) & (targets <= time[-1])
             & (bid > 0) & (ask > bid))
    gross = fills.direction.to_numpy() * ((bid + ask) / 2 - fills.price.to_numpy()) / sessions.TICK
    fills["gross_total"] = np.where(valid, gross * fills["size"], 0.0)
    fills["missing_qty"] = np.where(valid, 0.0, fills["size"])
    fills["missing_fill"] = ~valid
    totals = fills.groupby("order_id")[["gross_total", "missing_qty", "missing_fill"]].sum()
    totals = totals.reindex(out.index, fill_value=0)
    out["executed_qty"] = qty
    out["missing_mark_qty"] = totals.missing_qty
    out["payoff_observed"] = totals.missing_fill == 0
    out["gross_payoff_ticks"] = (totals.gross_total / out.own_size).where(out.payoff_observed)
    out["net_payoff_ticks"] = out.gross_payoff_ticks - fee_ticks * qty / out.own_size
    cancels = messages[(messages.event_type == 2) & sessions.rth_mask(time)]
    cancelled = cancels.groupby("order_id")["size"].sum().reindex(out.index, fill_value=0)
    out["remaining_qty_at_close"] = (out.own_size - qty - cancelled).clip(lower=0)
    out["decile"] = _decile(out.position.to_numpy())
    return out.reset_index(drop=True)


def aggregate(orders, session, *, horizon=10.0, fee_ticks=0.0):
    rows = []
    for decile, group in orders.groupby("decile"):
        observed = group[group.payoff_observed]
        lo, hi = _bootstrap_mean(group.net_payoff_ticks.to_numpy(), group.minute.to_numpy())
        rows.append(dict(
            session=session, decile=int(decile), horizon_s=horizon, fee_ticks=fee_ticks,
            orders=len(group), observed_orders=len(observed),
            missing_mark_orders=int((~group.payoff_observed).sum()),
            original_qty=float(group.own_size.sum()), executed_qty=float(group.executed_qty.sum()),
            missing_mark_qty=float(group.missing_mark_qty.sum()),
            remaining_qty_at_close=float(group.remaining_qty_at_close.sum()),
            partial_fill_orders=int(((group.executed_qty > 0) & (group.executed_qty < group.own_size)).sum()),
            gross_payoff_ticks=float(observed.gross_payoff_ticks.mean()),
            net_payoff_ticks=float(observed.net_payoff_ticks.mean()),
            net_payoff_lo=lo, net_payoff_hi=hi,
        ))
    return pd.DataFrame(rows)


def compare(table, legacy):
    """Paired session summaries, with estimands explicitly named."""
    rows = []
    for session, group in table.groupby("session"):
        indexed = group.set_index("decile")
        front = indexed.loc[0] if 0 in indexed.index else None
        back = indexed.loc[9] if 9 in indexed.index else None
        f = float(front.net_payoff_ticks) if front is not None else np.nan
        b = float(back.net_payoff_ticks) if back is not None else np.nan
        old = legacy[legacy.session == session]
        if len(old) != 1:
            raise ValueError(f"one historical score required for {session}")
        rows.append(dict(session=session,
                         historical_front_minus_back_score=float(old.iloc[0].front_minus_back_ticks),
                         direct_front_payoff_ticks=f, direct_back_payoff_ticks=b,
                         direct_front_minus_back_ticks=f-b,
                         orders=int(group.orders.sum()), observed_orders=int(group.observed_orders.sum()),
                         missing_mark_orders=int(group.missing_mark_orders.sum()),
                         partial_fill_orders=int(group.partial_fill_orders.sum()),
                         remaining_qty_at_close=float(group.remaining_qty_at_close.sum())))
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", action="append", choices=sessions.SESSIONS)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("report/queue_payoff"))
    parser.add_argument("--horizon", type=float, default=10.0)
    parser.add_argument("--fee-ticks", type=float, default=0.0)
    args = parser.parse_args()
    tables = []
    for session in args.session or sessions.SESSIONS:
        messages = sessions.load_messages(session, args.work_dir)
        book = sessions.load_book(session, args.work_dir)
        orders = resting_orders(messages, book)
        payoff = direct_payoff(messages, book, orders, horizon=args.horizon, fee_ticks=args.fee_ticks)
        tables.append(aggregate(payoff, session, horizon=args.horizon, fee_ticks=args.fee_ticks))
        print(f"{session}: {len(orders)} orders, {(~payoff.payoff_observed).sum()} missing marks", flush=True)
        del messages, book, orders, payoff
    args.out_dir.mkdir(parents=True, exist_ok=True)
    table = pd.concat(tables, ignore_index=True)
    table.to_csv(args.out_dir / "deciles.csv", index=False)
    root = Path(__file__).resolve().parents[1]
    comparison = compare(table, pd.read_csv(root / "report/queue_position/summary.csv"))
    comparison.to_csv(args.out_dir / "comparison.csv", index=False)
    print(comparison.to_string(index=False))


if __name__ == "__main__":
    main()
