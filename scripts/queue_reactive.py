#!/usr/bin/env python3
"""Calibrate the queue-reactive model of Huang, Lehalle and Rosenbaum on a session.

Huang, Lehalle and Rosenbaum, "Simulating and analyzing order book data: The
queue-reactive model" (JASA 2015, arXiv:1312.0563) treat the book as a Markov
queueing system: 2K queues sit at fixed offsets from a reference price, and the
intensity of limit-order arrival, cancellation and market-order flow at each
queue depends on the size of that queue. This is the first fitted model in a
repository that has otherwise only reconstructed books, so the question it
answers is how much of a real session a deliberately simple state-dependent
model can carry.

MODEL GEOMETRY (the part that is easy to get wrong)
---------------------------------------------------
Queue Q_i sits at distance (|i| - 0.5) ticks from p_ref, on the ask side for
i > 0 and the bid side for i < 0. The modelled queues are therefore at FIXED
prices relative to p_ref, NOT at fixed offsets from the best quote. That
distinction is the whole model: when the spread widens, Q_1 is genuinely empty,
and "market orders reach Q_2 only once Q_1 has emptied" becomes a statement the
geometry can express. Indexing from the best quote instead would make Q_1
non-empty by construction and destroy the mechanism. Event records carry both
indices so the difference stays visible, but the calibration is p_ref-relative,
as the paper is.

Because limit prices sit on the tick grid and the queues sit half a tick off
p_ref, p_ref is always an odd multiple of half a tick. That makes the geometry
exact in integers and needs no floating point at all:

    spread odd in ticks  -> p_ref = midprice (already on the half-tick grid)
    spread even in ticks -> p_ref = midprice +/- half a tick, whichever is
                            closer to the previous p_ref

ESTIMATOR
---------
For queue i at normalised size n, with N events observed while the queue held
that size and T seconds of exposure to it, the MLE for a Markov jump process is
N / T, split by event type. Sizes are normalised by AES_i, the average event
size at Q_i, as in the paper. Confidence bands are exact Garwood Poisson
intervals on the count, which stay honest in the sparse large-queue tail where a
Wald interval would happily dip below zero.

Usage:
    python scripts/queue_reactive.py --session INTC_2024-08-02
    python scripts/queue_reactive.py --session MSFT_2024-06-03 --no-simulate
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

TICK = 100                    # LOBSTER prices are 1e-4 dollars, so a cent is 100
HALF_TICK = TICK // 2
RTH_OPEN, RTH_CLOSE = 34_200, 57_600
K = 3                         # queues per side; the paper's choice

EVENT_ADD, EVENT_CANCEL, EVENT_EXEC = 1, 2, 4
EVENT_NAMES = {EVENT_ADD: "add", EVENT_CANCEL: "cancel", EVENT_EXEC: "exec"}
KINDS = ("add", "cancel", "exec")
QUEUES = [i for i in range(-K, 0)] + [i for i in range(1, K + 1)]
_MESSAGE_COLS = ["time", "event_type", "order_id", "size", "price", "direction"]

DATA_DIR = Path("data/databento")
SESSIONS = ("MSFT_2024-06-03", "INTC_2024-08-02")


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def reference_prices(best_bid: np.ndarray, best_ask: np.ndarray) -> np.ndarray:
    """p_ref per row, as an integer in LOBSTER price units, -1 before it is defined.

    p_ref must be an odd multiple of half a tick, because the queues sit half a
    tick off it and limit prices are whole ticks. When the spread is even in
    ticks the midprice lands ON the tick grid and is not a legal p_ref, so it is
    nudged half a tick toward the previous value. That tie-break is inherently
    sequential; everything else is vectorised around it.

    Rows with a one-sided book carry the previous value forward: the queues are
    still where they were, and re-deriving them from a half-empty book would
    invent a jump.
    """
    n = len(best_bid)
    out = np.full(n, -1, dtype=np.int64)
    two_sided = np.isfinite(best_bid) & np.isfinite(best_ask)
    if not two_sided.any():
        return out

    bid = np.where(two_sided, best_bid, 0).astype(np.int64)
    ask = np.where(two_sided, best_ask, 0).astype(np.int64)
    mid = (bid + ask) // 2
    odd = (((ask - bid) // TICK) % 2) == 1
    low, high = mid - HALF_TICK, mid + HALF_TICK

    previous = -1
    for r in range(n):
        if not two_sided[r]:
            out[r] = previous
            continue
        if odd[r]:
            value = mid[r]
        elif previous < 0:
            value = high[r]
        else:
            value = low[r] if abs(low[r] - previous) <= abs(high[r] - previous) else high[r]
        out[r] = value
        previous = value
    return out


def queue_index(price: np.ndarray, p_ref: np.ndarray) -> np.ndarray:
    """Signed queue index of a limit price; 0 when the price is off the queue grid.

    price = p_ref +/- (|i| - 0.5) * TICK, so the offset from p_ref is an odd
    multiple of half a tick and the index falls out by integer division. A price
    that is not an odd half-tick away is off-grid -- sub-penny sentinels, or any
    row where p_ref is still undefined -- and is reported rather than forced
    into a queue.
    """
    price = np.asarray(price, dtype=np.int64)
    p_ref = np.asarray(p_ref, dtype=np.int64)
    offset = price - p_ref
    index = np.where(offset > 0, (offset + HALF_TICK) // TICK,
                     -((-offset + HALF_TICK) // TICK))
    on_grid = (np.abs(offset) % TICK == HALF_TICK) & (p_ref > 0)
    return np.where(on_grid, index, 0).astype(np.int64)


def queue_prices(p_ref: np.ndarray) -> dict[int, np.ndarray]:
    """Price of each modelled queue given p_ref: Q_i is (|i| - 0.5) ticks away."""
    return {i: p_ref + i * TICK - (HALF_TICK if i > 0 else -HALF_TICK) for i in QUEUES}


# --------------------------------------------------------------------------
# building per-event records
# --------------------------------------------------------------------------

@dataclass
class SessionRecords:
    """Aggregated session statistics. Nothing here scales with the event count."""
    events: pd.DataFrame
    exposure: dict[tuple[int, int], float] = field(default_factory=dict)
    n_book_rows: int = 0
    n_messages: int = 0
    uncovered: int = 0            # queue slots deeper than the 10th reported level
    off_grid: int = 0             # messages priced off the tick grid
    depletions: int = 0           # best queue going from non-empty to empty
    pref_moves: int = 0           # rows where p_ref actually changed
    # p_ref moves keyed by (direction, raw size of the touch queue before the
    # move). This is the transition the three order-flow rates cannot express.
    moves: dict[tuple[str, int], int] = field(default_factory=dict)
    move_steps: dict[int, int] = field(default_factory=dict)   # |step| in ticks -> count
    # Model IIb: the same quantities at the touch queues, additionally keyed by
    # a coarse class of the OPPOSITE touch queue. Queue independence is what
    # breaks on this data, so the coupled tables are what the simulator uses.
    joint_exposure: dict[tuple[int, int, int], float] = field(default_factory=dict)
    joint_counts: dict[tuple[int, int, int, int], int] = field(default_factory=dict)
    joint_moves: dict[tuple[str, int, int], int] = field(default_factory=dict)
    class_edges: tuple[float, ...] = ()
    spread_ticks: np.ndarray = field(default_factory=lambda: np.empty(0))
    inside_window: float = 0.0    # share of exposure with the best quote inside +/-K
    mid_grid: np.ndarray = field(default_factory=lambda: np.empty(0))  # p_ref, 1s grid, ticks

    @property
    def theta(self) -> float:
        """Reference-price moves per best-queue depletion.

        Reported for comparison with the paper's Model III, which parameterises
        price motion by a single probability theta. It is NOT what the simulator
        uses -- see `estimate_intensities` for why a scalar is not enough here.
        """
        return self.pref_moves / self.depletions if self.depletions else 0.0

    @property
    def one_tick_move_share(self) -> float:
        total = sum(self.move_steps.values())
        return self.move_steps.get(1, 0) / total if total else float("nan")


def _side_sizes(px: np.ndarray, sz: np.ndarray, target: np.ndarray,
                best: np.ndarray, is_ask: bool) -> tuple[np.ndarray, np.ndarray]:
    """Size at `target` on one side's ladder, plus a coverage mask.

    A target inside the spread is genuinely empty and counts as covered. So does
    a target past the deepest reported level when the ladder is NOT full: the
    book listed everything it had, so the rest of the side really is empty. Only
    when all ten levels are occupied is a deeper target genuinely unknown, and
    those are counted so the calibration can state what it could not see.
    """
    match = px == target[:, None]
    found = match.any(axis=1)
    size = np.where(found, sz[np.arange(len(target)), match.argmax(axis=1)], 0.0)

    finite = np.isfinite(px)
    full = finite.all(axis=1)          # every one of the ten slots is occupied
    if is_ask:
        deepest = np.max(np.where(finite, px, -np.inf), axis=1)
        inside, beyond = target < best, target > deepest
    else:
        deepest = np.min(np.where(finite, px, np.inf), axis=1)
        inside, beyond = target > best, target < deepest
    return size, found | inside | ~(beyond & full)


N_CLASSES = 4          # empty / small / usual / large, as in the paper's Model IIb


def touch_classes(size: np.ndarray, edges: tuple[float, ...]) -> np.ndarray:
    """Coarse class of a queue: 0 empty, then small / usual / large."""
    return np.digitize(np.nan_to_num(size, nan=0.0), edges)


def build_records(book_path: Path, message_path: Path, *, chunk: int = 250_000,
                  verbose: bool = True) -> SessionRecords:
    """Walk book and message files in lockstep and aggregate per-queue statistics.

    The engine's --book-out writes the book AFTER each message, so the state a
    message acted on is the PREVIOUS row. Everything below is in those terms:
    the interval (t_r, t_{r+1}] is spent in the state of row r, and the message
    at t_{r+1} is the event that ends it. Getting this off by one would bias
    every intensity, because a queue's size is most likely to have just changed
    at exactly the moment an event lands on it.
    """
    messages = pd.read_csv(message_path, names=_MESSAGE_COLS, header=None,
                           usecols=["time", "event_type", "size", "price"])
    n_messages = len(messages)

    # Pass 1: p_ref for the whole session. It only needs the touch, and doing it
    # in one pass avoids having to stitch the sequential tie-break across chunks.
    if verbose:
        print("  pass 1/2: reference price", file=sys.stderr)
    touch = pd.read_csv(book_path, usecols=["timestamp", "bid_px_0", "ask_px_0",
                                            "bid_sz_0", "ask_sz_0"])
    p_ref_all = reference_prices(touch["bid_px_0"].to_numpy(), touch["ask_px_0"].to_numpy())
    times_all = touch["timestamp"].to_numpy()
    spread_ticks = ((touch["ask_px_0"].to_numpy() - touch["bid_px_0"].to_numpy()) / TICK)
    n_rows = len(touch)
    # class boundaries in raw shares, scaled off this symbol's own touch depth so
    # the same code works on a 65-share book and a 2,500-share one
    at_touch = np.concatenate([touch["bid_sz_0"].to_numpy(), touch["ask_sz_0"].to_numpy()])
    at_touch = at_touch[np.isfinite(at_touch) & (at_touch > 0)]
    median_touch = float(np.median(at_touch)) if at_touch.size else 1.0
    class_edges = (1.0, 0.5 * median_touch, 2.0 * median_touch)
    del touch

    if n_rows != n_messages:
        raise SystemExit(f"book has {n_rows:,} rows but the message file has "
                         f"{n_messages:,}; they must be the same conversion")

    exposure: dict[tuple[int, int], float] = {}
    moves: dict[tuple[str, int], int] = {}
    move_steps: dict[int, int] = {}
    joint_exposure: dict[tuple[int, int, int], float] = {}
    joint_counts: dict[tuple[int, int, int, int], int] = {}
    joint_moves: dict[tuple[str, int, int], int] = {}
    event_frames: list[pd.DataFrame] = []
    uncovered = off_grid = depletions = 0
    inside_time = total_time = 0.0

    px_cols = [f"{tag}_px_{lvl}" for tag in ("bid", "ask") for lvl in range(10)]
    sz_cols = [f"{tag}_sz_{lvl}" for tag in ("bid", "ask") for lvl in range(10)]

    prev_sizes: dict[int, float] | None = None
    prev_time: float | None = None

    if verbose:
        print("  pass 2/2: queues and events", file=sys.stderr)
    reader = pd.read_csv(book_path, usecols=["timestamp"] + px_cols + sz_cols,
                         chunksize=chunk)
    processed = 0
    for block in reader:
        lo, hi = processed, processed + len(block)
        processed = hi
        p_ref = p_ref_all[lo:hi]
        time = block["timestamp"].to_numpy()

        bid_px = block[[f"bid_px_{i}" for i in range(10)]].to_numpy()
        ask_px = block[[f"ask_px_{i}" for i in range(10)]].to_numpy()
        bid_sz = block[[f"bid_sz_{i}" for i in range(10)]].to_numpy()
        ask_sz = block[[f"ask_sz_{i}" for i in range(10)]].to_numpy()
        best_bid, best_ask = bid_px[:, 0], ask_px[:, 0]

        defined = p_ref > 0
        sizes: dict[int, np.ndarray] = {}
        for i, target in queue_prices(p_ref).items():
            is_ask = i > 0
            px, sz, best = ((ask_px, ask_sz, best_ask) if is_ask
                            else (bid_px, bid_sz, best_bid))
            value, covered = _side_sizes(px, sz, target, best, is_ask)
            uncovered += int((~covered & defined).sum())
            sizes[i] = np.where(defined, value, np.nan)

        # ---- exposure: the state of row r is held over (t_r, t_{r+1}] -------
        dt = np.empty(len(time))
        dt[:-1] = np.diff(time)
        dt[-1] = np.nan                      # closed by the first row of the next chunk
        in_window = (time >= RTH_OPEN) & (time < RTH_CLOSE)

        if prev_sizes is not None and prev_time is not None:
            gap = float(time[0]) - prev_time
            if gap > 0 and RTH_OPEN <= prev_time < RTH_CLOSE:
                for i, value in prev_sizes.items():
                    if np.isfinite(value):
                        key = (i, int(value))
                        exposure[key] = exposure.get(key, 0.0) + gap

        for i, value in sizes.items():
            usable = in_window & np.isfinite(value) & np.isfinite(dt)
            if not usable.any():
                continue
            keys = value[usable].astype(np.int64)
            weights = dt[usable]
            order = np.argsort(keys, kind="stable")
            keys, weights = keys[order], weights[order]
            starts = np.flatnonzero(np.r_[True, np.diff(keys) != 0])
            for key, total in zip(keys[starts], np.add.reduceat(weights, starts)):
                exposure[(i, int(key))] = exposure.get((i, int(key)), 0.0) + float(total)

        # ---- Model IIb: touch exposure keyed by the opposite queue's class --
        for own, opposite in ((1, -1), (-1, 1)):
            cls = touch_classes(sizes[opposite], class_edges)
            value = sizes[own]
            usable = in_window & np.isfinite(value) & np.isfinite(dt) & np.isfinite(sizes[opposite])
            if usable.any():
                keys = value[usable].astype(np.int64) * N_CLASSES + cls[usable]
                weights = dt[usable]
                order_ = np.argsort(keys, kind="stable")
                keys, weights = keys[order_], weights[order_]
                starts = np.flatnonzero(np.r_[True, np.diff(keys) != 0])
                for key, total in zip(keys[starts], np.add.reduceat(weights, starts)):
                    jk = (own, int(key) // N_CLASSES, int(key) % N_CLASSES)
                    joint_exposure[jk] = joint_exposure.get(jk, 0.0) + float(total)

        # how much of the session has its best quote inside the modelled window
        usable = in_window & np.isfinite(dt) & defined
        if usable.any():
            reach = np.abs(queue_index(np.nan_to_num(best_ask, nan=0).astype(np.int64), p_ref))
            inside_time += float(dt[usable & (reach <= K) & (reach > 0)].sum())
            total_time += float(dt[usable].sum())

        # ---- events: the message at t_{r+1} acts on the state of row r ------
        msg_lo, msg_hi = lo + 1, min(hi + 1, n_messages)
        if msg_hi > msg_lo:
            acting = messages.iloc[msg_lo:msg_hi]
            at = np.arange(msg_lo - lo - 1, msg_hi - lo - 1)   # row index within chunk
            price = acting["price"].to_numpy().astype(np.int64)
            etype = acting["event_type"].to_numpy()
            index = queue_index(price, p_ref[at])

            book_event = np.isin(etype, [EVENT_ADD, EVENT_CANCEL, EVENT_EXEC])
            off_grid += int((book_event & (index == 0) & (p_ref[at] > 0)).sum())

            keep = book_event & (index != 0) & (np.abs(index) <= K)
            if keep.any():
                rows_at = at[keep]
                idx = index[keep]
                q_before = np.array([sizes[int(i)][r] for i, r in zip(idx, rows_at)])
                best_ref = np.where(idx > 0, best_ask[rows_at], best_bid[rows_at])
                level = np.abs(price[keep] - best_ref) // TICK + 1

                frame = pd.DataFrame({
                    "time": acting["time"].to_numpy()[keep],
                    "event_type": etype[keep],
                    "queue": idx,
                    "level_from_best": level,
                    "size": acting["size"].to_numpy()[keep],
                    "q_before": q_before,
                })
                touch_side = np.where(idx > 0, -1, 1)
                opposite_size = np.array([sizes[int(o)][r] for o, r in zip(touch_side, rows_at)])
                frame["opposite_class"] = touch_classes(opposite_size, class_edges)
                frame["is_touch"] = np.abs(idx) == 1
                frame = frame[(frame.time >= RTH_OPEN) & (frame.time < RTH_CLOSE)
                              & np.isfinite(frame.q_before)]
                if len(frame):
                    event_frames.append(frame)
                    touch_rows = frame[frame.is_touch]
                    for key, count in touch_rows.groupby(
                            ["queue", "q_before", "opposite_class", "event_type"]).size().items():
                        jk = (int(key[0]), int(key[1]), int(key[2]), int(key[3]))
                        joint_counts[jk] = joint_counts.get(jk, 0) + int(count)

        # ---- best-queue depletions, the denominator of theta ----------------
        for i in (1, -1):
            series = sizes[i]
            depletions += int(((series[:-1] > 0) & (series[1:] == 0)
                               & in_window[:-1]).sum())

        # ---- reference-price moves, attributed to the touch queue size ------
        # A move is a transition of the whole state, so it is keyed on the size
        # the relevant touch queue held just before it: p_ref rises when the ask
        # touch is cleared, so the up-move intensity is a function of q_+1.
        step = np.diff(p_ref) // TICK
        moved = (step != 0) & (p_ref[:-1] > 0) & (p_ref[1:] > 0) & in_window[:-1]
        for direction, sign, queue in (("up", 1, 1), ("down", -1, -1)):
            hit = moved & (np.sign(step) == sign)
            if not hit.any():
                continue
            own = sizes[queue][:-1][hit]
            other = sizes[-queue][:-1][hit]
            good = np.isfinite(own) & np.isfinite(other)
            own_i = own[good].astype(np.int64)
            cls = touch_classes(other[good], class_edges)
            for value, count in zip(*np.unique(own_i, return_counts=True)):
                key = (direction, int(value))
                moves[key] = moves.get(key, 0) + int(count)
            packed = own_i * N_CLASSES + cls
            for value, count in zip(*np.unique(packed, return_counts=True)):
                jk = (direction, int(value) // N_CLASSES, int(value) % N_CLASSES)
                joint_moves[jk] = joint_moves.get(jk, 0) + int(count)
        for value, count in zip(*np.unique(np.abs(step[moved]), return_counts=True)):
            move_steps[int(value)] = move_steps.get(int(value), 0) + int(count)

        prev_sizes = {i: value[-1] for i, value in sizes.items()}
        prev_time = float(time[-1])
        if verbose:
            print(f"\r    {processed:,} / {n_rows:,} rows", end="", file=sys.stderr)

    if verbose:
        print(file=sys.stderr)

    events = (pd.concat(event_frames, ignore_index=True) if event_frames
              else pd.DataFrame(columns=["time", "event_type", "queue",
                                         "level_from_best", "size", "q_before"]))
    if len(events):
        events["q_before"] = events["q_before"].astype(np.int64)

    rth = (times_all >= RTH_OPEN) & (times_all < RTH_CLOSE) & (p_ref_all > 0)
    # p_ref on a one-second grid, in ticks, so real and simulated volatility are
    # measured the same way rather than one in ticks and the other in dollars
    grid = np.arange(RTH_OPEN, RTH_CLOSE, 1.0)
    at = np.searchsorted(times_all, grid, side="right") - 1
    sampled = np.where(at >= 0, p_ref_all[np.maximum(at, 0)], -1).astype(float)
    sampled[sampled < 0] = np.nan
    return SessionRecords(
        events=events, exposure=exposure, n_book_rows=n_rows, n_messages=n_messages,
        uncovered=uncovered, off_grid=off_grid, depletions=depletions,
        pref_moves=int((np.diff(p_ref_all[rth]) != 0).sum()),
        moves=moves, move_steps=move_steps, joint_exposure=joint_exposure,
        joint_counts=joint_counts, joint_moves=joint_moves, class_edges=class_edges,
        spread_ticks=spread_ticks[rth & np.isfinite(spread_ticks)],
        inside_window=inside_time / total_time if total_time else 0.0,
        mid_grid=sampled / TICK)


# --------------------------------------------------------------------------
# intensity estimation
# --------------------------------------------------------------------------

def normalise(shares, scale: float):
    """Queue size in AES units, keeping the EMPTY state exact.

    Plain rounding would map everything below half an average event size to
    n = 0, which lumps a genuinely empty queue in with a merely thin one. That
    distinction carries the whole price-move mechanism -- p_ref moves when a
    touch queue empties, not when it gets small -- so n = 0 is reserved for an
    empty queue and any non-empty queue lands at n >= 1.
    """
    value = np.asarray(shares, dtype=float)
    n = np.maximum(np.rint(value / scale), 1.0)
    return np.where(value <= 0, 0, n).astype(np.int64)


def average_event_sizes(events: pd.DataFrame) -> dict[int, float]:
    """AES_i: mean size over all event types at Q_i, the paper's normalising unit."""
    return {int(q): float(g["size"].mean()) for q, g in events.groupby("queue")}


def _garwood(count: np.ndarray, exposure: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Exact Poisson (Garwood) 95% interval for the rate count / exposure."""
    from scipy.stats import chi2
    lower = np.where(count > 0, chi2.ppf(0.025, 2 * np.maximum(count, 1)) / 2.0, 0.0)
    upper = chi2.ppf(0.975, 2 * count + 2) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        return lower / exposure, upper / exposure


def estimate_intensities(records: SessionRecords, aes: dict[int, float],
                         min_exposure: float = 1.0) -> pd.DataFrame:
    """Intensity table per (queue, normalised size), with exact Poisson bands.

    Exposure is binned through the same size -> n map as the events, so the
    numerator and denominator can never disagree about which bin a state is in.

    A FOURTH rate is estimated alongside the paper's three: lambda_move, the
    intensity of a reference-price move as a function of the touch queue size.
    The paper folds price motion into a single probability theta, and on this
    data a scalar will not do. Measured on INTC, Q+1 receives 283,010 adds
    against 260,606 cancels and executions -- a +22,404 surplus, 7.9% of
    arrivals. A closed birth-death chain cannot run that surplus indefinitely,
    and the queue does not grow in reality, so the missing outflow is real:
    it is queue content leaving by RE-INDEXING when p_ref moves, which is not
    an order event and therefore appears in none of the three order-flow rates.
    Simulating the three alone is not merely inaccurate, it is non-stationary --
    Q1 ran to 6,010 AES against a real 17.7 before this term was added.
    Estimating the move intensity as a function of queue size closes the
    generator, and it is state-dependent for an obvious reason: a touch holding
    thousands of shares is far harder to clear than an empty one.
    """
    if records.events.empty:
        return pd.DataFrame()

    cells: dict[tuple[int, int], dict[str, float]] = {}

    def cell(queue: int, n: int) -> dict[str, float]:
        return cells.setdefault((queue, n), {"exposure": 0.0, "add": 0.0,
                                             "cancel": 0.0, "exec": 0.0, "move": 0.0})

    for (queue, size), total in records.exposure.items():
        scale = aes.get(queue)
        if scale and np.isfinite(scale) and scale > 0:
            cell(queue, int(normalise(size, scale)))["exposure"] += total

    for (direction, size), count in records.moves.items():
        queue = 1 if direction == "up" else -1
        scale = aes.get(queue)
        if scale and np.isfinite(scale) and scale > 0:
            cell(queue, int(normalise(size, scale)))["move"] += float(count)

    events = records.events
    scales = events["queue"].map(aes)
    n_bin = normalise(events["q_before"].to_numpy(), scales.to_numpy())
    for (queue, n, etype), count in events.assign(n=n_bin).groupby(
            ["queue", "n", "event_type"]).size().items():
        cell(int(queue), int(n))[EVENT_NAMES[int(etype)]] += float(count)

    frame = pd.DataFrame([{"queue": q, "n": n, **v} for (q, n), v in sorted(cells.items())])
    frame = frame[frame.exposure >= min_exposure].reset_index(drop=True)
    frame["lambda_move"] = frame["move"] / frame.exposure
    for kind in KINDS:
        frame[f"lambda_{kind}"] = frame[kind] / frame.exposure
        low, high = _garwood(frame[kind].to_numpy(), frame.exposure.to_numpy())
        frame[f"lambda_{kind}_lo"], frame[f"lambda_{kind}_hi"] = low, high
    frame["lambda_total"] = frame[list(KINDS)].sum(axis=1) / frame.exposure
    return frame


def estimate_coupled(records: SessionRecords, aes: dict[int, float],
                     min_exposure: float = 1.0) -> pd.DataFrame:
    """Model IIb tables at the touch: rates given (own size, opposite class).

    The paper introduces this because queue independence is an approximation.
    On this data it is not a small one. Measured on INTC, the real one-second
    drift of Q+1 at a given own size swings from -3.3 AES/s when the opposite
    touch is empty to +1.2 AES/s when it is large -- a sign change driven
    entirely by a queue the Model I rates never look at.
    """
    cells: dict[tuple[int, int, int], dict[str, float]] = {}

    def cell(queue: int, n: int, cls: int) -> dict[str, float]:
        return cells.setdefault((queue, n, cls), {"exposure": 0.0, "add": 0.0,
                                                  "cancel": 0.0, "exec": 0.0, "move": 0.0})

    for (queue, size, cls), total in records.joint_exposure.items():
        scale = aes.get(queue)
        if scale and scale > 0:
            cell(queue, int(normalise(size, scale)), cls)["exposure"] += total
    for (queue, size, cls, etype), count in records.joint_counts.items():
        scale = aes.get(queue)
        if scale and scale > 0:
            cell(queue, int(normalise(size, scale)), cls)[EVENT_NAMES[etype]] += float(count)
    for (direction, size, cls), count in records.joint_moves.items():
        queue = 1 if direction == "up" else -1
        scale = aes.get(queue)
        if scale and scale > 0:
            cell(queue, int(normalise(size, scale)), cls)["move"] += float(count)

    frame = pd.DataFrame([{"queue": q, "n": n, "cls": c, **v}
                          for (q, n, c), v in sorted(cells.items())])
    if frame.empty:
        return frame
    frame = frame[frame.exposure >= min_exposure].reset_index(drop=True)
    for kind in KINDS + ("move",):
        frame[f"lambda_{kind}"] = frame[kind] / frame.exposure
    return frame


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------

@dataclass
class FittedModel:
    """Dense intensity lookups, including the reference-price move intensity."""
    rate: dict[int, np.ndarray]        # queue -> (max_n+1, 3) of add/cancel/exec
    move: dict[int, np.ndarray]        # queue +/-1 -> price-move rate by size
    invariant: dict[int, np.ndarray]   # queue -> distribution over n
    theta: float                       # reported for comparison, not simulated
    aes: dict[int, float]
    max_n: int
    # Model IIb tables at the touch, indexed [n, opposite class]
    coupled_rate: dict[int, np.ndarray] = field(default_factory=dict)
    coupled_move: dict[int, np.ndarray] = field(default_factory=dict)
    class_edges_n: dict[int, np.ndarray] = field(default_factory=dict)
    coupled: bool = False


def fit_model(frame: pd.DataFrame, records: SessionRecords, aes: dict[int, float],
              max_n: int | None = None, coupled: pd.DataFrame | None = None) -> FittedModel:
    """Turn the estimated table into arrays the simulator can index in O(1).

    Bins past the last observed one hold the last estimate rather than zero. A
    queue that wanders past the calibrated range still has to be able to shrink,
    or the simulation absorbs there and the run is worthless.
    """
    if max_n is None:
        # cover the whole observed range: freezing the tables below the largest
        # queue actually seen would cap the simulation somewhere the real book
        # demonstrably goes
        max_n = int(min(frame["n"].max(), 400)) if len(frame) else 60
    rate, move, invariant = {}, {}, {}
    for queue in QUEUES:
        sub = frame[frame.queue == queue].set_index("n").sort_index()
        table = np.zeros((max_n + 1, 3))
        for column, kind in enumerate(KINDS):
            series = sub[f"lambda_{kind}"].reindex(range(max_n + 1))
            table[:, column] = series.ffill().bfill().fillna(0.0).to_numpy()
        rate[queue] = table
        if abs(queue) == 1:
            series = sub["lambda_move"].reindex(range(max_n + 1))
            move[queue] = series.ffill().bfill().fillna(0.0).to_numpy()
        weight = sub["exposure"].reindex(range(max_n + 1)).fillna(0.0).to_numpy()
        invariant[queue] = weight / weight.sum() if weight.sum() > 0 else None
    model = FittedModel(rate=rate, move=move, invariant=invariant,
                        theta=records.theta, aes=aes, max_n=max_n)
    if coupled is None or coupled.empty:
        return model

    # Model IIb tables at the touch. Bins the session never visited fall back to
    # the uncoupled estimate for that size rather than to zero, so a sparse
    # (n, class) corner cannot silently freeze a queue.
    for queue in (1, -1):
        table = np.zeros((max_n + 1, N_CLASSES, 3))
        move_table = np.zeros((max_n + 1, N_CLASSES))
        sub = coupled[coupled.queue == queue]
        for cls in range(N_CLASSES):
            block = sub[sub.cls == cls].set_index("n").sort_index()
            for column, kind in enumerate(KINDS):
                series = block[f"lambda_{kind}"].reindex(range(max_n + 1))
                filled = series.ffill().bfill()
                table[:, cls, column] = np.where(np.isfinite(filled), filled,
                                                 rate[queue][:, column])
            series = block["lambda_move"].reindex(range(max_n + 1))
            filled = series.ffill().bfill()
            move_table[:, cls] = np.where(np.isfinite(filled), filled, move[queue])
        model.coupled_rate[queue] = table
        model.coupled_move[queue] = move_table
        # class edges converted from raw shares into this queue's AES units
        model.class_edges_n[queue] = np.asarray(records.class_edges) / aes[queue]
    model.coupled = True
    return model


def simulate(model: FittedModel, duration: float, seed: int = 0,
             warmup: float = 600.0, max_events: int = 4_000_000,
             cap: dict[int, int] | None = None) -> pd.DataFrame:
    """Run the fitted model forward and record the book path.

    Model I dynamics -- each queue reacts to its own size -- plus a fitted
    reference-price move intensity in place of the paper's scalar theta. Price
    moves are a competing transition alongside the order flow: p_ref rises at
    rate lambda_move(q_+1) and falls at rate lambda_move(q_-1), the queue vector
    slides one position, and the queue that falls off the far end is redrawn
    from its invariant measure.

    `cap` reflects queue sizes at a ceiling. It is an ADMISSION, not a fix. The
    fitted generator is not stationary on this data: net order-flow drift at the
    touch is negative only for n <= 4 and positive at every larger size, so the
    chain has an unstable equilibrium and escapes upward. A price move is the
    only thing that drains a large queue, and a price move requires the opposite
    touch to empty, which a runaway queue prevents -- a mutual deadlock the real
    book avoids and an independent-queue model cannot. Capping bounds the
    divergence so the other statistics can be compared at all; it does not make
    the model right, and the queue-size row of the comparison stays wrong on
    purpose so the failure is visible rather than tuned away.
    """
    rng = np.random.default_rng(seed)
    order = list(QUEUES)

    def draw(queue: int) -> int:
        weights = model.invariant.get(queue)
        return 0 if weights is None else int(rng.choice(len(weights), p=weights))

    def touch_rates(queue: int, own: int, opposite: int) -> tuple[np.ndarray, float]:
        """Order-flow rates and price-move rate at a touch queue.

        Under Model IIb these depend on the opposite touch as well as this one,
        which is the whole reason the simulation is stationary: it is what lets
        one side clear while the other is heavy.
        """
        i = min(own, model.max_n)
        if not model.coupled:
            return model.rate[queue][i], float(model.move[queue][i])
        cls = int(np.digitize(opposite, model.class_edges_n[queue]))
        return model.coupled_rate[queue][i, cls], float(model.coupled_move[queue][i, cls])

    state = {q: draw(q) for q in order}
    p_ref, t = 0, -warmup
    rows: list[tuple] = []

    def step_up() -> dict[int, int]:
        return {1: state[2], 2: state[3], 3: draw(3),
                -1: 0, -2: state[-1], -3: state[-2]}

    def step_down() -> dict[int, int]:
        return {-1: state[-2], -2: state[-3], -3: draw(-3),
                1: 0, 2: state[1], 3: state[2]}

    while t < duration and len(rows) < max_events:
        ask_rates, up = touch_rates(1, state[1], state[-1])
        bid_rates, down = touch_rates(-1, state[-1], state[1])
        blocks = {1: ask_rates, -1: bid_rates}
        per_queue = np.array([
            blocks[q].sum() if abs(q) == 1 else model.rate[q][min(state[q], model.max_n)].sum()
            for q in order])
        total = per_queue.sum() + up + down
        if total <= 0:
            break
        t += float(rng.exponential(1.0 / total))

        # price moves compete with order flow for the next transition
        u = rng.random() * total
        if u < up:
            state, p_ref = step_up(), p_ref + 1
            pick, kind = 1, 3
        elif u < up + down:
            state, p_ref = step_down(), p_ref - 1
            pick, kind = -1, 3
        else:
            pick = order[int(rng.choice(len(order), p=per_queue / per_queue.sum()))]
            row = (blocks[pick] if abs(pick) == 1
                   else model.rate[pick][min(state[pick], model.max_n)])
            kind = int(rng.choice(3, p=row / row.sum()))
            if kind == 0:
                state[pick] = state[pick] + 1
                if cap is not None:
                    state[pick] = min(state[pick], cap.get(pick, model.max_n))
            else:
                state[pick] = max(0, state[pick] - 1)

        if t >= 0:
            rows.append((t, pick, kind, p_ref, *(state[q] for q in order)))

    return pd.DataFrame(rows, columns=["time", "queue", "kind", "p_ref"]
                        + [f"q{q}" for q in order])


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------

def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    if cumulative[-1] <= 0:
        return float("nan")
    return float(np.interp(q * cumulative[-1], cumulative, values))


def _sim_spread(path: pd.DataFrame) -> np.ndarray:
    """Spread in ticks implied by the simulated queues.

    The innermost non-empty queue on each side sets the touch; with only K
    queues a side the spread is censored at 2K when a side is entirely empty,
    which is reported rather than hidden.
    """
    ask = np.stack([path[f"q{i}"].to_numpy() > 0 for i in range(1, K + 1)], axis=1)
    bid = np.stack([path[f"q{-i}"].to_numpy() > 0 for i in range(1, K + 1)], axis=1)
    ai = np.where(ask.any(axis=1), ask.argmax(axis=1) + 1, K + 1)
    bi = np.where(bid.any(axis=1), bid.argmax(axis=1) + 1, K + 1)
    return (ai + bi - 1).astype(float)


def _distances(real: np.ndarray, sim: np.ndarray, real_w: np.ndarray | None = None
               ) -> tuple[float, float]:
    """Wasserstein-1 and total-variation distance between two distributions.

    These are the two metrics LOB-Bench reports, computed directly here rather
    than through its file loader. The reason is substantive: LOB-Bench consumes
    ten-level LOBSTER books, and this model produces three levels a side by
    construction, so feeding it a padded book would score seven levels of
    fabricated emptiness against the real thing. Same yardstick, applied where
    it means something.
    """
    from scipy.stats import wasserstein_distance
    real = np.asarray(real, dtype=float)
    sim = np.asarray(sim, dtype=float)
    real, sim = real[np.isfinite(real)], sim[np.isfinite(sim)]
    if real.size < 2 or sim.size < 2:
        return float("nan"), float("nan")
    if real_w is not None:
        real_w = np.asarray(real_w, dtype=float)[np.isfinite(np.asarray(real, dtype=float))]
    w1 = float(wasserstein_distance(real, sim, u_weights=real_w))

    lo, hi = min(real.min(), sim.min()), max(real.max(), sim.max())
    edges = np.histogram_bin_edges(np.r_[real, sim], bins=60, range=(lo, hi))
    pr, _ = np.histogram(real, bins=edges, weights=real_w, density=False)
    ps, _ = np.histogram(sim, bins=edges, density=False)
    pr = pr / pr.sum() if pr.sum() else pr
    ps = ps / ps.sum() if ps.sum() else ps
    return w1, float(0.5 * np.abs(pr - ps).sum())


def compare(records: SessionRecords, path: pd.DataFrame,
            aes: dict[int, float]) -> pd.DataFrame:
    """Simulated versus real: point summaries plus distributional distances."""
    events = records.events
    real_inter = np.diff(np.sort(events["time"].to_numpy()))
    real_inter = real_inter[real_inter > 0]
    sim_inter = np.diff(path["time"].to_numpy())
    sim_inter = sim_inter[sim_inter > 0]

    # queue size at the touch, exposure-weighted so it is a time average
    q1 = [(int(normalise(size, aes[1])), total)
          for (queue, size), total in records.exposure.items()
          if queue == 1 and aes.get(1)]
    values = np.array([v for v, _ in q1], dtype=float)
    weights = np.array([w for _, w in q1], dtype=float)
    real_q1_mean = float((values * weights).sum() / weights.sum()) if weights.sum() else np.nan
    real_q1_med = _weighted_quantile(values, weights, 0.5)

    real_spread = records.spread_ticks
    sim_spread = _sim_spread(path)

    # mid-price volatility: standard deviation of one-second moves, in ticks on
    # both sides so the two numbers are directly comparable
    def tick_vol(times: np.ndarray, level: np.ndarray) -> float:
        if len(times) < 10:
            return float("nan")
        grid = np.arange(times[0], times[-1], 1.0)
        sampled = level[np.searchsorted(times, grid, side="right") - 1]
        return float(np.nanstd(np.diff(sampled)))

    real_mid = records.mid_grid
    real_vol = float(np.nanstd(np.diff(real_mid))) if len(real_mid) > 10 else float("nan")
    sim_vol = tick_vol(path["time"].to_numpy(), path["p_ref"].to_numpy().astype(float))

    sim_q1 = path["q1"].to_numpy().astype(float)
    real_mid_moves = np.diff(real_mid)
    grid = np.arange(path["time"].to_numpy()[0], path["time"].to_numpy()[-1], 1.0)
    sim_mid = path["p_ref"].to_numpy().astype(float)[
        np.searchsorted(path["time"].to_numpy(), grid, side="right") - 1]
    sim_mid_moves = np.diff(sim_mid)

    w_inter, tv_inter = _distances(np.log10(real_inter * 1000), np.log10(sim_inter * 1000))
    w_q1, tv_q1 = _distances(values, sim_q1, real_w=weights)
    w_spread, tv_spread = _distances(real_spread, sim_spread)
    w_mid, tv_mid = _distances(real_mid_moves, sim_mid_moves)

    return pd.DataFrame([
        {"statistic": "mean inter-arrival (ms)",
         "real": float(np.mean(real_inter) * 1000), "simulated": float(np.mean(sim_inter) * 1000),
         "wasserstein": w_inter, "total_variation": tv_inter},
        {"statistic": "median inter-arrival (ms)",
         "real": float(np.median(real_inter) * 1000), "simulated": float(np.median(sim_inter) * 1000),
         "wasserstein": np.nan, "total_variation": np.nan},
        {"statistic": "mean queue at Q1 (AES)", "real": real_q1_mean,
         "simulated": float(sim_q1.mean()), "wasserstein": w_q1, "total_variation": tv_q1},
        {"statistic": "median queue at Q1 (AES)", "real": real_q1_med,
         "simulated": float(np.median(sim_q1)), "wasserstein": np.nan, "total_variation": np.nan},
        {"statistic": "mean spread (ticks)", "real": float(np.mean(real_spread)),
         "simulated": float(np.mean(sim_spread)),
         "wasserstein": w_spread, "total_variation": tv_spread},
        {"statistic": "median spread (ticks)", "real": float(np.median(real_spread)),
         "simulated": float(np.median(sim_spread)), "wasserstein": np.nan, "total_variation": np.nan},
        {"statistic": "1s mid move sd (ticks)", "real": real_vol, "simulated": sim_vol,
         "wasserstein": w_mid, "total_variation": tv_mid},
    ])


# --------------------------------------------------------------------------
# plotting
# --------------------------------------------------------------------------

def plot_intensities(frame: pd.DataFrame, session: str, out_path: Path,
                     max_n: int = 25) -> None:
    """Grid of intensity curves: rows are event type, columns are |i|."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, K, figsize=(4.2 * K, 9.5), sharex=True)
    titles = {"add": r"limit arrival  $\lambda^L$", "cancel": r"cancellation  $\lambda^C$",
              "exec": r"market order  $\lambda^M$"}
    for row, kind in enumerate(KINDS):
        for column in range(K):
            ax = axes[row, column]
            level = column + 1
            for queue, colour, label in ((level, "#1f77b4", f"$Q_{{+{level}}}$ (ask)"),
                                         (-level, "#d62728", f"$Q_{{-{level}}}$ (bid)")):
                sub = frame[(frame.queue == queue) & (frame.n <= max_n)].sort_values("n")
                if sub.empty:
                    continue
                ax.plot(sub.n, sub[f"lambda_{kind}"], color=colour, lw=1.6, label=label)
                ax.fill_between(sub.n, sub[f"lambda_{kind}_lo"], sub[f"lambda_{kind}_hi"],
                                color=colour, alpha=0.18, lw=0)
            if row == 0:
                ax.set_title(f"$|i| = {level}$", fontsize=11)
            if column == 0:
                ax.set_ylabel(titles[kind] + "\n(events / s)", fontsize=10)
            if row == 2:
                ax.set_xlabel("queue size (units of AES)", fontsize=10)
            ax.grid(alpha=0.25, lw=0.5)
            ax.legend(fontsize=8, frameon=False)
    fig.suptitle(f"Queue-reactive intensities, {session} (95% Garwood bands)", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, choices=SESSIONS)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=Path("report/queue_reactive"))
    ap.add_argument("--no-simulate", action="store_true")
    ap.add_argument("--model-i", action="store_true",
                    help="simulate with independent queues (Model I) instead of the "
                         "opposite-queue coupling; diverges, and that is the point")
    ap.add_argument("--duration", type=float, default=float(RTH_CLOSE - RTH_OPEN))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-cap", action="store_true",
                    help="let queues run unbounded, showing the raw divergence")
    args = ap.parse_args()

    book = args.data_dir / f"{args.session}_book10.csv"
    messages = args.data_dir / f"{args.session}_message.csv"
    for path in (book, messages):
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1

    print(f"{args.session}")
    records = build_records(book, messages)
    aes = average_event_sizes(records.events)

    print(f"  {records.n_messages:,} messages, {len(records.events):,} inside the "
          f"{2 * K} modelled queues")
    print(f"  best quote inside the +/-{K} window for {records.inside_window * 100:.1f}% "
          f"of session time (median spread {np.median(records.spread_ticks):.0f} ticks)")
    print(f"  AES  " + "  ".join(f"Q{q:+d}={aes[q]:.0f}" for q in sorted(aes)))
    print(f"  best-queue depletions {records.depletions:,}, p_ref moves "
          f"{records.pref_moves:,}  ->  theta = {records.theta:.3f}")
    if records.uncovered:
        print(f"  note: {records.uncovered:,} queue slots sat deeper than the 10th "
              f"reported level and were treated as unknown, not empty")

    frame = estimate_intensities(records, aes)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out_dir / f"intensities_{args.session}.csv", index=False)
    plot_intensities(frame, args.session, args.out_dir / f"intensities_{args.session}.png")
    print(f"  wrote intensities + plot -> {args.out_dir}")

    if args.no_simulate:
        return 0

    coupled = estimate_coupled(records, aes)
    coupled.to_csv(args.out_dir / f"coupled_{args.session}.csv", index=False)
    model = fit_model(frame, records, aes, coupled=None if args.model_i else coupled)
    print(f"  simulating {args.duration:,.0f}s "
          f"({'Model I, independent queues' if args.model_i else 'Model IIb, coupled touch'}, "
          f"seed={args.seed})")
    cap = None
    if not args.no_cap:
        cap = {int(q): int(g["n"].max()) for q, g in frame.groupby("queue")}
        print("  queues reflected at the largest size each one reached: "
              + ", ".join(f"Q{q:+d}={cap[q]}" for q in sorted(cap)))
    path = simulate(model, args.duration, seed=args.seed, cap=cap)
    print(f"  {len(path):,} simulated events")

    table = compare(records, path, aes)
    table.to_csv(args.out_dir / f"compare_{args.session}.csv", index=False)
    print()
    print(table.to_string(index=False, float_format=lambda v: f"{v:0.3f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
