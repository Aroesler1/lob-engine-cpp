#!/usr/bin/env python3
"""What latency costs a passive quoter, measured on real order flow.

The rule is deliberately the simplest thing that is still a strategy: keep one
share resting at the best bid and one at the best ask, and whenever the touch
moves, cancel and re-quote at the new touch. It has no signal and no inventory
management, so whatever the latency curve shows is the cost of being late
rather than the decay of an edge.

Every action is delayed by a fixed latency d. The rule SEES the book without
delay and its orders ARRIVE d later, which is the standard action-latency model
and the one that isolates the effect being measured. Three consequences, all of
them the point:

* The order joins the queue at the position observed at t+d, not at t. In a
  busy queue that is further back.
* If the touch moved through the quoted price during d, the order arrives
  marketable and trades immediately at its own limit. That is being picked off,
  and it is the mechanism by which latency turns a spread capture into a loss.
* The cancel is late too, so the order stays exposed for d longer than intended.

TWO ASSUMPTIONS, STATED BECAUSE THEY BOUND EVERYTHING BELOW
-----------------------------------------------------------
1. The own orders are too small to move the book. One share against a touch
   that holds tens to thousands makes this close to exact for the queue
   arithmetic, but it also means no market impact is charged anywhere.
2. Other participants do not react to the own orders. The counterfactual book
   is the real one, replayed unchanged. A real quoter at the touch would change
   what others do, and nothing here models that.

A third, narrower one: queue position advances only on TRADES at that price, not
on cancellations ahead of the order. That is the brief's definition and it is
conservative, understating fill rates by ignoring the queue that evaporates.

Usage:
    python scripts/latency_cost_curve.py
    python scripts/latency_cost_curve.py --session MSFT_2024-06-03
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import sessions
from sessions import TICK

LATENCIES = (0.0, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1)   # 0, 10us, 100us, 1ms, 10ms, 100ms
MARK_HORIZON = 10.0
FILL = 4
OWN_SIZE = 1.0


def latency_label(seconds: float) -> str:
    if seconds == 0:
        return "0"
    if seconds < 1e-3:
        return f"{seconds * 1e6:g}us"
    if seconds < 1.0:
        return f"{seconds * 1e3:g}ms"
    return f"{seconds:g}s"


def _episodes(times: np.ndarray, touch: np.ndarray, end: float) -> pd.DataFrame:
    """One row per quote the rule places on this side.

    A new episode starts whenever the touch price changes, which is exactly when
    the rule cancels and re-quotes. The decision time is the observation; the
    latency is applied by the caller.
    """
    valid = np.isfinite(touch)
    idx = np.flatnonzero(valid)
    if idx.size == 0:
        return pd.DataFrame()
    price = touch[idx]
    changed = np.r_[True, price[1:] != price[:-1]]
    starts = idx[changed]
    decided = times[starts]
    return pd.DataFrame({
        "decided": decided,
        "price": price[changed],
        # the rule holds the quote until it next observes the touch move
        "until": np.r_[decided[1:], end],
    })


def _size_at_price(book: dict[str, np.ndarray], rows: np.ndarray,
                   price: np.ndarray, is_bid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shares resting at `price` on the order's own side, and whether it is known.

    The book cache carries three levels a side. A price inside the spread is
    genuinely empty and counts as known-zero; a price deeper than the third
    level is unknown and the episode is dropped rather than assumed empty.
    """
    found = np.zeros(rows.size, dtype=bool)
    size = np.zeros(rows.size, dtype=np.float64)
    deepest = np.full(rows.size, np.nan)
    for level in range(sessions.BOOK_DEPTH):
        px = np.where(is_bid, book[f"bid_px_{level}"][rows], book[f"ask_px_{level}"][rows])
        sz = np.where(is_bid, book[f"bid_sz_{level}"][rows], book[f"ask_sz_{level}"][rows])
        hit = (px == price) & (px > 0) & ~found
        size[hit] = sz[hit]
        found |= hit
        present = px > 0
        deepest = np.where(present, px, deepest)

    best = np.where(is_bid, book["bid_px_0"][rows], book["ask_px_0"][rows]).astype(np.float64)
    best = np.where(best > 0, best, np.nan)
    # inside the spread: better than the best on our own side, so nothing rests there
    inside = np.where(is_bid, price > best, price < best)
    # beyond the deepest reported level on our side
    beyond = np.where(is_bid, price < deepest, price > deepest)
    known = found | inside | ~beyond
    return size, known


def _fill_times(trades: pd.DataFrame, episodes: pd.DataFrame) -> np.ndarray:
    """First time cumulative traded volume at the price exceeds the shares ahead.

    Grouped by price so each lookup is a vectorised searchsorted inside one
    monotone cumulative series. A session touches a few hundred distinct prices,
    so the Python loop is over groups, never over orders.
    """
    out = np.full(len(episodes), np.nan)
    if trades.empty or episodes.empty:
        return out
    by_price = {p: g for p, g in trades.groupby("price", sort=False)}
    for price, group in episodes.groupby("price", sort=False):
        book = by_price.get(price)
        if book is None:
            continue
        stamps = book["time"].to_numpy()
        cumulative = book["size"].to_numpy().cumsum()
        rows = group.index.to_numpy()
        entry = group["entry"].to_numpy()
        ahead = group["ahead"].to_numpy()

        start = np.searchsorted(stamps, entry, side="right")
        base = np.where(start > 0, cumulative[np.maximum(start - 1, 0)], 0.0)
        # strictly exceed: the order is behind `ahead` shares, so it needs one
        # more share to trade than the queue in front of it
        target = base + ahead
        hit = np.searchsorted(cumulative, target, side="right")
        hit = np.maximum(hit, start)
        ok = hit < stamps.size
        filled = np.full(rows.size, np.nan)
        filled[ok] = stamps[hit[ok]]
        out[rows] = filled
    return out


def simulate(messages: pd.DataFrame, book: dict[str, np.ndarray],
             latency: float) -> pd.DataFrame:
    """Replay the quoting rule at one latency and return one row per quote."""
    time = messages["time"].to_numpy()
    best_bid = np.where(book["bid_px_0"] > 0, book["bid_px_0"], np.nan).astype(np.float64)
    best_ask = np.where(book["ask_px_0"] > 0, book["ask_px_0"], np.nan).astype(np.float64)
    mid = (best_bid + best_ask) / 2.0

    inside = sessions.rth_mask(time)
    if not inside.any():
        return pd.DataFrame()
    lo, hi = time[inside][0], time[inside][-1]

    frames = []
    for is_bid, touch in ((True, best_bid), (False, best_ask)):
        window = np.where(inside, touch, np.nan)
        episode = _episodes(time, window, hi)
        if episode.empty:
            continue
        episode = episode[(episode["decided"] >= lo) & (episode["decided"] < hi)].copy()
        episode["is_bid"] = is_bid
        episode["entry"] = episode["decided"] + latency
        episode["expiry"] = episode["until"] + latency
        frames.append(episode)
    if not frames:
        return pd.DataFrame()

    episodes = pd.concat(frames, ignore_index=True)
    episodes = episodes[episodes["entry"] < hi].reset_index(drop=True)

    # state of the book at the moment the order actually arrives
    rows = np.searchsorted(time, episodes["entry"].to_numpy(), side="right") - 1
    rows = np.clip(rows, 0, time.size - 1)
    is_bid = episodes["is_bid"].to_numpy()
    price = episodes["price"].to_numpy()

    ahead, known = _size_at_price(book, rows, price, is_bid)
    episodes["ahead"] = ahead
    episodes["known"] = known

    # arrived marketable: the touch moved through the quoted price during the
    # latency, so the order trades at once instead of resting
    opposite = np.where(is_bid, best_ask[rows], best_bid[rows])
    crossed = np.where(is_bid, price >= opposite, price <= opposite) & np.isfinite(opposite)
    episodes["crossed"] = crossed

    episodes = episodes[episodes["known"]].reset_index(drop=True)
    if episodes.empty:
        return pd.DataFrame()
    # re-derive the per-row arrays from the FILTERED frame. Reusing the
    # pre-filter arrays silently misaligns every price and side with its
    # episode as soon as one row is dropped.
    is_bid = episodes["is_bid"].to_numpy()
    price = episodes["price"].to_numpy()
    rows = np.searchsorted(time, episodes["entry"].to_numpy(), side="right") - 1
    rows = np.clip(rows, 0, time.size - 1)

    trades = messages[(messages.event_type == FILL)].copy()
    # a fill's `direction` is the side of the RESTING order it consumed, so
    # trades that consume the bid queue are the ones with direction +1
    trades["is_bid"] = trades["direction"] > 0
    resting = episodes[~episodes["crossed"]]
    fill_time = np.full(len(episodes), np.nan)
    for side in (True, False):
        part = resting[resting["is_bid"] == side]
        if part.empty:
            continue
        side_trades = trades[trades["is_bid"] == side][["time", "price", "size"]]
        found = _fill_times(side_trades, part.reset_index(drop=True))
        fill_time[part.index.to_numpy()] = found
    # a crossed order trades the instant it lands
    fill_time[episodes["crossed"].to_numpy()] = episodes["entry"].to_numpy()[
        episodes["crossed"].to_numpy()]

    # the quote is pulled at expiry, so a fill after that never happened
    alive = fill_time < np.minimum(episodes["expiry"].to_numpy(), hi)
    fill_time = np.where(alive, fill_time, np.nan)
    episodes["fill_time"] = fill_time
    episodes["filled"] = np.isfinite(fill_time)

    finite = np.isfinite(mid)
    grid_t, grid_mid = time[finite], mid[finite]

    def mid_at(stamps: np.ndarray) -> np.ndarray:
        idx = np.searchsorted(grid_t, stamps, side="right") - 1
        out = np.full(stamps.shape, np.nan)
        ok = idx >= 0
        out[ok] = grid_mid[idx[ok]]
        return out

    filled = episodes["filled"].to_numpy()
    stamps = episodes["fill_time"].to_numpy()
    mid0 = np.full(len(episodes), np.nan)
    mid1 = np.full(len(episodes), np.nan)
    mid0[filled] = mid_at(stamps[filled])
    mid1[filled] = mid_at(stamps[filled] + MARK_HORIZON)
    past_end = np.zeros(len(episodes), dtype=bool)
    past_end[filled] = stamps[filled] + MARK_HORIZON > grid_t[-1]
    mid1[past_end] = np.nan

    sign = np.where(is_bid, 1.0, -1.0)     # +1 long after a bid fill
    episodes["gross_ticks"] = sign * (mid0 - price) / TICK
    episodes["adverse_ticks"] = -sign * (mid1 - mid0) / TICK
    episodes["net_ticks"] = sign * (mid1 - price) / TICK
    return episodes


def summarise(episodes: pd.DataFrame, session: str, latency: float) -> dict:
    if episodes.empty:
        return {"session": session, "latency_s": latency,
                "latency": latency_label(latency), "quotes": 0}
    filled = episodes[episodes["filled"]]
    marked = filled[np.isfinite(filled["net_ticks"])]
    return {
        "session": session,
        "latency_s": latency,
        "latency": latency_label(latency),
        "quotes": len(episodes),
        "fills": len(filled),
        "fill_rate": len(filled) / len(episodes),
        "crossed_share": float(episodes["crossed"].mean()),
        # the quote landed at a price the book had already left, so it rests
        # alone and is the new touch. Favourable, and a direct consequence of
        # being late: at zero latency it is essentially never possible.
        "alone_share": float((episodes.loc[~episodes["crossed"], "ahead"] == 0).mean()),
        "median_ahead": float(episodes.loc[~episodes["crossed"], "ahead"].median()),
        "gross_ticks": float(marked["gross_ticks"].mean()) if len(marked) else np.nan,
        "adverse_ticks": float(marked["adverse_ticks"].mean()) if len(marked) else np.nan,
        "net_ticks": float(marked["net_ticks"].mean()) if len(marked) else np.nan,
        "net_total_ticks": float(marked["net_ticks"].sum()) if len(marked) else np.nan,
    }


def run(session: str, root: Path | None = None,
        latencies: tuple[float, ...] = LATENCIES) -> pd.DataFrame:
    messages = sessions.load_messages(session, root)
    book = sessions.load_book(session, root)
    rows = [summarise(simulate(messages, book, d), session, d) for d in latencies]
    return pd.DataFrame(rows)


def plot(table: pd.DataFrame, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    # colour by stock, line style by session. Fifteen lines exceed the default
    # colour cycle, and the split that matters here is between the names.
    palette = {"MSFT": "#1f77b4", "INTC": "#d62728", "AAPL": "#2ca02c"}
    styles = ["-", "--", "-.", ":", (0, (3, 1, 1, 1))]
    seen: dict[str, int] = {}
    for session, group in table.groupby("session"):
        group = group.sort_values("latency_s")
        symbol = session.split("_")[0]
        index = seen.setdefault(session, len(seen))
        style = styles[sum(1 for s_ in seen if s_.startswith(symbol) and seen[s_] < index) % len(styles)]
        colour = palette.get(symbol, "0.4")
        positive = group[group.latency_s > 0]
        ax.plot(positive["latency_s"] * 1e6, positive["net_ticks"],
                marker="o", markersize=3.5, linewidth=1.4, linestyle=style,
                color=colour, label=session)
        zero = group[group.latency_s == 0]
        if not zero.empty:
            ax.scatter([positive["latency_s"].min() * 1e6 / 3], zero["net_ticks"],
                       marker="*", s=90, zorder=5, color=colour)
    ax.set_xscale("log")
    ax.axhline(0, color="0.4", linewidth=0.8, linestyle="--")
    ax.set_xlabel("action latency (microseconds, log scale; star = zero latency)")
    ax.set_ylabel("net P&L per fill (ticks, marked to mid +10s)")
    ax.set_title("Cost of latency for a one-share quoter at the touch")
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="append", choices=sessions.SESSIONS)
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=Path("report/latency"))
    args = ap.parse_args()

    wanted = tuple(args.session) if args.session else sessions.SESSIONS
    args.out_dir.mkdir(parents=True, exist_ok=True)

    tables = []
    for session in wanted:
        table = run(session, args.work_dir)
        tables.append(table)
        print(f"{session}")
        print(table[["latency", "quotes", "fills", "fill_rate", "crossed_share",
                     "alone_share", "median_ahead", "gross_ticks", "adverse_ticks",
                     "net_ticks"]]
              .to_string(index=False, float_format=lambda v: f"{v:0.4f}"))
        print()

    combined = pd.concat(tables, ignore_index=True)
    combined.to_csv(args.out_dir / "latency_curve.csv", index=False)
    plot(combined, args.out_dir / "latency_curve.png")
    print(f"saved -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
