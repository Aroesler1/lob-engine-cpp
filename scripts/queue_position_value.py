#!/usr/bin/env python3
"""Historical queue score, retained for reproducibility, not direct payoff.

Use queue_payoff.py for execution-price markouts weighted by actual fills.
The score below mixes unconditional arrival spreads with conditional first-fill
markouts, omits arrival-to-fill movement and ignores partial-fill sizing.
It must not be interpreted as expected realized payoff or causal queue value.

A limit order at the best quote earns the spread if it fills and loses to
adverse selection if the price moves through it. Queue position governs both:
an order at the front fills more often, and it fills on the trades that arrive
first, which are not the same trades that reach the back of the queue.

Moallemi and Yuan, "A model for queue position valuation in a limit order book"
(2017, https://doi.org/10.2139/ssrn.2996221), price that position directly. This
measures the two inputs their model trades off, on real order flow, and reports
the difference between front and back of queue as a single number in ticks.

DEFINITIONS
-----------
For every new limit order resting at the best bid or best ask on arrival:

* `position` = shares ahead / (shares ahead + own size), in [0, 1). 0 is the
  front, so an order that improves the touch and stands alone scores 0. Shares
  ahead is recovered as (size at that price after arrival) - (own size), which
  is correct whether or not the price level already existed.
* `filled` = the order received at least one execution during its life. An order
  that is cancelled without ever trading did not fill; one still resting at the
  close is censored and counted as not filled, with the censoring rate reported.
* `adverse selection` = the mid move after the fill, signed so a LOSS to the
  resting order is POSITIVE. A resting bid that fills is long, so it loses when
  the mid falls; a resting ask is short and loses when the mid rises. In LOBSTER
  `direction` is +1 for a bid and -1 for an ask, so the signed loss is
  `-direction * (mid(t+h) - mid(t_fill))` for horizons h of 1s, 10s and 60s.
* `edge` = fill probability * (half spread at arrival - adverse selection), in
  ticks. This is the quantity the front-versus-back comparison reports.

The bootstrap resamples whole MINUTES rather than individual orders. Orders
arriving close together share a book state and a price path, so resampling them
independently would treat one queue's worth of correlated outcomes as many
independent observations and produce a band several times too narrow.

Usage:
    python scripts/queue_position_value.py
    python scripts/queue_position_value.py --session MSFT_2024-06-03
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import sessions
from sessions import TICK

N_DECILES = 10
HORIZONS = (1.0, 10.0, 60.0)
ADD, CANCEL, FILL = 1, 2, 4


def _touch(book: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Best bid and ask after each message, as float with 0 meaning absent."""
    bid = book["bid_px_0"].astype(np.float64)
    ask = book["ask_px_0"].astype(np.float64)
    return np.where(bid > 0, bid, np.nan), np.where(ask > 0, ask, np.nan)


def resting_orders(messages: pd.DataFrame, book: dict[str, np.ndarray]) -> pd.DataFrame:
    """One row per new limit order that arrived at the touch, with its outcome."""
    time = messages["time"].to_numpy()
    etype = messages["event_type"].to_numpy()
    oid = messages["order_id"].to_numpy()
    size = messages["size"].to_numpy().astype(np.float64)
    price = messages["price"].to_numpy().astype(np.float64)
    direction = messages["direction"].to_numpy()

    best_bid, best_ask = _touch(book)
    mid = (best_bid + best_ask) / 2.0
    half_spread = (best_ask - best_bid) / 2.0

    # an add is "at the touch" when, in the book AFTER it is applied, its own
    # price is the best on its side
    is_add = etype == ADD
    at_touch = is_add & np.where(direction > 0, price == best_bid, price == best_ask)
    at_touch &= sessions.rth_mask(time) & np.isfinite(half_spread)
    rows = np.flatnonzero(at_touch)
    if rows.size == 0:
        return pd.DataFrame()

    # size at the order's own price after arrival: level 0 on its own side
    own_side_size = np.where(direction[rows] > 0,
                             book["bid_sz_0"][rows], book["ask_sz_0"][rows])
    ahead = own_side_size.astype(np.float64) - size[rows]
    # a negative value would mean the level held less than the order just added,
    # which the engine cannot produce; guard rather than silently clip
    if np.any(ahead < -1e-9):
        raise SystemExit("shares ahead came out negative; book and messages disagree")
    ahead = np.maximum(ahead, 0.0)

    orders = pd.DataFrame({
        "order_id": oid[rows],
        "arrival": time[rows],
        "direction": direction[rows],
        "own_size": size[rows],
        "ahead": ahead,
        "total": ahead + size[rows],
        "half_spread_ticks": half_spread[rows] / TICK,
    })
    orders["position"] = orders["ahead"] / orders["total"]

    # an order id is unique per session in this feed; if it were not, the joins
    # below would silently attribute one order's fills to another
    duplicated = int(orders["order_id"].duplicated().sum())
    if duplicated:
        raise SystemExit(f"{duplicated:,} order ids were added more than once; "
                         "the lifecycle join would be wrong")

    # outcome: first execution on that order, and total quantity removed
    executions = messages[messages.event_type == FILL]
    first_fill = executions.groupby("order_id")["time"].min()
    cancels = messages[messages.event_type == CANCEL]
    removed = (executions.groupby("order_id")["size"].sum()
               .add(cancels.groupby("order_id")["size"].sum(), fill_value=0.0))

    first_cancel = cancels.groupby("order_id")["time"].min()
    orders = orders.join(first_cancel.rename("cancel_time"), on="order_id")
    orders = orders.join(first_fill.rename("fill_time"), on="order_id")
    orders = orders.join(removed.rename("removed"), on="order_id")
    orders["removed"] = orders["removed"].fillna(0.0)
    orders["filled"] = orders["fill_time"].notna()
    # still resting at the close: neither filled nor fully removed
    orders["censored"] = ~orders["filled"] & (orders["removed"] < orders["own_size"])
    orders["time_to_fill"] = orders["fill_time"] - orders["arrival"]
    # how long the order was actually exposed. Fill probability is "filled
    # before cancel", so it mixes queue position with how long the order was
    # left alone; without this column a decile that simply rests longer looks
    # like a decile that fills better.
    leaves = np.fmin(orders["fill_time"].to_numpy(), orders["cancel_time"].to_numpy())
    orders["life"] = leaves - orders["arrival"].to_numpy()

    # adverse selection at each horizon, in ticks, positive = loss
    finite = np.isfinite(mid)
    grid_t, grid_mid = time[finite], mid[finite]
    fill_time = orders["fill_time"].to_numpy()
    has_fill = np.isfinite(fill_time)

    def mid_at(stamps: np.ndarray) -> np.ndarray:
        idx = np.searchsorted(grid_t, stamps, side="right") - 1
        out = np.full(stamps.shape, np.nan)
        ok = idx >= 0
        out[ok] = grid_mid[idx[ok]]
        return out

    base = np.full(len(orders), np.nan)
    base[has_fill] = mid_at(fill_time[has_fill])
    for horizon in HORIZONS:
        later = np.full(len(orders), np.nan)
        later[has_fill] = mid_at(fill_time[has_fill] + horizon)
        # beyond the last observation the mid is unknown, not unchanged
        past_end = np.full(len(orders), False)
        past_end[has_fill] = fill_time[has_fill] + horizon > grid_t[-1]
        loss = -orders["direction"].to_numpy() * (later - base) / TICK
        loss[past_end] = np.nan
        orders[f"adverse_{horizon:g}s"] = loss

    orders["minute"] = (orders["arrival"] // 60).astype(np.int64)
    return orders


def _decile(position: np.ndarray) -> np.ndarray:
    """Fixed [0,1) deciles, not sample quantiles.

    Sample quantiles would make the buckets mean different things on different
    sessions, which is exactly the comparison this table is for.
    """
    return np.clip((position * N_DECILES).astype(int), 0, N_DECILES - 1)


def _bootstrap_mean(values: np.ndarray, minutes: np.ndarray, *,
                    draws: int = 1000, seed: int = 0) -> tuple[float, float]:
    """Percentile band for a mean, resampling whole minutes."""
    ok = np.isfinite(values)
    values, minutes = values[ok], minutes[ok]
    if values.size == 0:
        return float("nan"), float("nan")
    unique, inverse = np.unique(minutes, return_inverse=True)
    if unique.size < 2:
        return float("nan"), float("nan")
    order = np.argsort(inverse, kind="stable")
    sorted_values = values[order]
    bounds = np.searchsorted(inverse[order], np.arange(unique.size + 1))
    totals = np.add.reduceat(sorted_values, bounds[:-1])
    counts = np.diff(bounds).astype(np.float64)

    rng = np.random.default_rng(seed)
    picks = rng.integers(0, unique.size, size=(draws, unique.size))
    means = totals[picks].sum(axis=1) / np.maximum(counts[picks].sum(axis=1), 1e-12)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _nanmean(values) -> float:
    """nanmean over an all-NaN slice is undefined, not an error to warn about."""
    values = np.asarray(values, dtype=np.float64)
    return float(np.nanmean(values)) if np.isfinite(values).any() else float("nan")


def summarise(orders: pd.DataFrame, session: str) -> pd.DataFrame:
    orders = orders.assign(decile=_decile(orders["position"].to_numpy()))
    rows = []
    for decile, group in orders.groupby("decile"):
        adverse = group["adverse_10s"].to_numpy()
        low, high = _bootstrap_mean(adverse, group["minute"].to_numpy())
        filled = group[group["filled"]]
        mean_adverse = _nanmean(adverse)
        fill_prob = float(group["filled"].mean())
        half_spread = float(group["half_spread_ticks"].mean())
        # an order that never fills captures no spread and suffers no adverse
        # selection, so its edge is exactly zero. Letting the undefined mean
        # propagate would report NaN and quietly drop the decile from any
        # front-versus-back comparison, which is the case most worth seeing.
        edge = 0.0 if fill_prob == 0.0 else fill_prob * (half_spread - mean_adverse)
        rows.append({
            "session": session,
            "decile": int(decile),
            "orders": len(group),
            "fill_prob": fill_prob,
            "median_ttf_s": float(filled["time_to_fill"].median()) if len(filled) else np.nan,
            "median_life_s": float(group["life"].median()),
            "median_own_size": float(group["own_size"].median()),
            "adverse_1s": _nanmean(group["adverse_1s"]),
            "adverse_10s": mean_adverse,
            "adverse_60s": _nanmean(group["adverse_60s"]),
            "adverse_10s_lo": low,
            "adverse_10s_hi": high,
            "half_spread_ticks": half_spread,
            "edge_ticks": edge,
        })
    return pd.DataFrame(rows)


def headline(table: pd.DataFrame) -> dict[str, float]:
    """Front minus back of queue, in ticks of expected edge."""
    front = table[table.decile == 0].iloc[0]
    back = table[table.decile == N_DECILES - 1].iloc[0]
    return {
        "front_edge_ticks": float(front.edge_ticks),
        "back_edge_ticks": float(back.edge_ticks),
        "front_minus_back_ticks": float(front.edge_ticks - back.edge_ticks),
        "front_fill_prob": float(front.fill_prob),
        "back_fill_prob": float(back.fill_prob),
        "front_adverse_10s": float(front.adverse_10s),
        "back_adverse_10s": float(back.adverse_10s),
    }


def run(session: str, root: Path | None = None) -> tuple[pd.DataFrame, dict[str, float], dict]:
    messages = sessions.load_messages(session, root)
    book = sessions.load_book(session, root)
    if len(messages) != len(book["timestamp"]):
        raise SystemExit(f"{session}: {len(messages):,} messages against "
                         f"{len(book['timestamp']):,} book rows")
    orders = resting_orders(messages, book)
    if orders.empty:
        raise SystemExit(f"{session}: no orders rested at the touch")
    table = summarise(orders, session)
    stats = {
        "orders": len(orders),
        "censored": float(orders["censored"].mean()),
        "fill_prob": float(orders["filled"].mean()),
    }
    return table, headline(table), stats


def plot(deciles: pd.DataFrame, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.9), sharex=True)
    for session, group in deciles.groupby("session"):
        group = group.sort_values("decile")
        style = dict(linewidth=1.2, marker="o", markersize=3, alpha=0.85)
        axes[0].plot(group["decile"], group["fill_prob"], **style)
        axes[1].plot(group["decile"], group["adverse_10s"], **style)
        axes[2].plot(group["decile"], group["edge_ticks"], label=session, **style)
    axes[0].set_ylabel("fill probability")
    axes[1].set_ylabel("adverse selection at 10s (ticks, loss positive)")
    axes[2].set_ylabel("edge (ticks)")
    axes[2].axhline(0, color="0.4", linewidth=0.8, linestyle="--")
    for ax in axes:
        ax.set_xlabel("queue position decile (0 = front)")
        ax.grid(alpha=0.25, linewidth=0.5)
    axes[2].legend(fontsize=6, ncol=2)
    fig.suptitle("What a place in line is worth, by queue position at arrival", y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="append", choices=sessions.SESSIONS)
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=Path("report/queue_position"))
    args = ap.parse_args()

    wanted = tuple(args.session) if args.session else sessions.SESSIONS
    args.out_dir.mkdir(parents=True, exist_ok=True)

    tables, heads = [], []
    for session in wanted:
        table, head, stats = run(session, args.work_dir)
        tables.append(table)
        heads.append({"session": session, **head, **stats})
        print(f"{session}: {stats['orders']:,} orders at the touch, "
              f"fill {stats['fill_prob'] * 100:.1f}%, censored {stats['censored'] * 100:.1f}%")
        print(f"  front edge {head['front_edge_ticks']:+.4f} ticks, "
              f"back {head['back_edge_ticks']:+.4f}, "
              f"front-minus-back {head['front_minus_back_ticks']:+.4f}")

    deciles = pd.concat(tables)
    deciles.to_csv(args.out_dir / "deciles.csv", index=False)
    plot(deciles, args.out_dir / "queue_position.png")
    summary = pd.DataFrame(heads)
    summary.to_csv(args.out_dir / "summary.csv", index=False)
    print()
    print(summary.to_string(index=False, float_format=lambda v: f"{v:0.4f}"))
    print(f"\nsaved -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
