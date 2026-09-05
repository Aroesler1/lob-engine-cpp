"""Tests for the latency cost curve.

The anchor is a synthetic stream with a stationary book where the rule's order
always trades on the next print, so the spread capture is known in closed form
and the simulator has to reproduce it exactly at zero latency. Everything else
here pins a sign or a boundary: which trades consume which side's queue, that
the queue must be strictly cleared rather than merely matched, and that a quote
which arrives marketable is recorded as picked off rather than as a fill from
the front of the queue.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from latency_cost_curve import latency_label, simulate  # noqa: E402

OPEN = 34_200.0
BID, ASK = 1_000_000, 1_000_400        # four-tick spread, mid 1_000_200
MID = (BID + ASK) / 2


def stream(rows, states):
    messages = pd.DataFrame(
        rows, columns=["time", "event_type", "order_id", "size", "price", "direction"])
    book = {"timestamp": messages["time"].to_numpy(dtype=np.float64)}
    for level in range(3):
        for tag in ("bid", "ask"):
            book[f"{tag}_px_{level}"] = np.array(
                [s.get(f"{tag}_px_{level}", 0) for s in states], dtype=np.int32)
            book[f"{tag}_sz_{level}"] = np.array(
                [s.get(f"{tag}_sz_{level}", 0) for s in states], dtype=np.int32)
    return messages, book


def flat_state(bid_sz=1, ask_sz=1):
    return {"bid_px_0": BID, "bid_sz_0": bid_sz, "ask_px_0": ASK, "ask_sz_0": ask_sz}


def stationary_book_with_one_trade():
    """Touch never moves; one print at the bid clears the single share ahead."""
    rows = [
        (OPEN + 0.0,  1, 1, 1, BID, 1),      # book exists from the open
        (OPEN + 1.0,  4, 2, 5, BID, 1),      # trade consuming the bid queue
        (OPEN + 20.0, 1, 3, 1, BID, 1),      # keeps the stream alive past the mark
    ]
    return stream(rows, [flat_state(), flat_state(), flat_state()])


def test_zero_latency_captures_the_known_spread():
    """One share ahead, a print of five, a book that never moves.

    The bid quote sits at 1,000,000 with a mid of 1,000,200, so the capture is
    exactly two ticks, and with a flat mid there is nothing to give back.
    """
    episodes = simulate(*stationary_book_with_one_trade(), latency=0.0)
    bid = episodes[episodes["is_bid"]].iloc[0]
    assert bool(bid["filled"]) is True
    assert bid["fill_time"] == pytest.approx(OPEN + 1.0)
    assert bid["gross_ticks"] == pytest.approx(2.0)
    assert bid["adverse_ticks"] == pytest.approx(0.0)
    assert bid["net_ticks"] == pytest.approx(2.0)


def test_the_untouched_side_does_not_fill():
    # the only print consumes the bid queue, so the ask quote must still be open
    episodes = simulate(*stationary_book_with_one_trade(), latency=0.0)
    ask = episodes[~episodes["is_bid"]].iloc[0]
    assert bool(ask["filled"]) is False


def test_a_trade_that_only_matches_the_queue_ahead_does_not_fill():
    """Strictly exceed, not reach. Five shares ahead and a five-share print
    leaves the order still first in line and unfilled."""
    rows = [
        (OPEN + 0.0,  1, 1, 1, BID, 1),
        (OPEN + 1.0,  4, 2, 5, BID, 1),
        (OPEN + 20.0, 1, 3, 1, BID, 1),
    ]
    states = [flat_state(bid_sz=5)] * 3
    episodes = simulate(*stream(rows, states), latency=0.0)
    assert bool(episodes[episodes["is_bid"]].iloc[0]["filled"]) is False


def test_one_more_share_than_the_queue_does_fill():
    rows = [
        (OPEN + 0.0,  1, 1, 1, BID, 1),
        (OPEN + 1.0,  4, 2, 6, BID, 1),
        (OPEN + 20.0, 1, 3, 1, BID, 1),
    ]
    states = [flat_state(bid_sz=5)] * 3
    episodes = simulate(*stream(rows, states), latency=0.0)
    assert bool(episodes[episodes["is_bid"]].iloc[0]["filled"]) is True


def test_adverse_selection_is_signed_so_a_loss_is_positive():
    """The bid fills, then the mid falls a tick. Long into a falling mid loses."""
    lower_ask = ASK - 200
    rows = [
        (OPEN + 0.0,  1, 1, 1, BID, 1),
        (OPEN + 1.0,  4, 2, 5, BID, 1),
        (OPEN + 11.0, 1, 3, 1, BID, 1),     # mid drops one tick at the mark
        (OPEN + 20.0, 1, 4, 1, BID, 1),
    ]
    moved = {"bid_px_0": BID, "bid_sz_0": 1, "ask_px_0": lower_ask, "ask_sz_0": 1}
    episodes = simulate(*stream(rows, [flat_state(), flat_state(), moved, moved]),
                        latency=0.0)
    bid = episodes[episodes["is_bid"]].iloc[0]
    # mid goes 1,000,200 -> 1,000,100, a one-tick fall against a long
    assert bid["adverse_ticks"] == pytest.approx(1.0)
    assert bid["net_ticks"] == pytest.approx(bid["gross_ticks"] - 1.0)


def test_a_quote_that_lands_marketable_is_recorded_as_crossed():
    """The ask collapses onto our bid during the latency window.

    The order arrives already tradeable, which is being picked off. It must be
    flagged, and it must fill at the moment it lands rather than waiting in a
    queue it never joined.
    """
    rows = [
        (OPEN + 0.0,   1, 1, 1, BID, 1),
        (OPEN + 0.001, 1, 2, 1, BID, 1),     # ask drops to the bid price
        (OPEN + 20.0,  1, 3, 1, BID, 1),
    ]
    collapsed = {"bid_px_0": BID, "bid_sz_0": 1, "ask_px_0": BID, "ask_sz_0": 1}
    episodes = simulate(*stream(rows, [flat_state(), collapsed, collapsed]),
                        latency=1e-2)
    bid = episodes[episodes["is_bid"]].iloc[0]
    assert bool(bid["crossed"]) is True
    assert bool(bid["filled"]) is True
    assert bid["fill_time"] == pytest.approx(OPEN + 1e-2)


def test_zero_latency_never_crosses():
    # the rule quotes the touch it just observed, so it cannot arrive marketable
    episodes = simulate(*stationary_book_with_one_trade(), latency=0.0)
    assert not episodes["crossed"].any()


def test_a_fill_after_the_quote_is_pulled_does_not_count():
    """The touch moves at t+2, so the rule cancels; a print at t+5 is too late."""
    rows = [
        (OPEN + 0.0,  1, 1, 1, BID, 1),
        (OPEN + 2.0,  1, 2, 1, BID + 100, 1),   # touch moves up one tick
        (OPEN + 5.0,  4, 3, 500, BID, 1),       # print back at the old price
        (OPEN + 20.0, 1, 4, 1, BID + 100, 1),
    ]
    moved = {"bid_px_0": BID + 100, "bid_sz_0": 1, "ask_px_0": ASK, "ask_sz_0": 1}
    episodes = simulate(*stream(rows, [flat_state(), moved, moved, moved]), latency=0.0)
    first = episodes[episodes["is_bid"]].sort_values("decided").iloc[0]
    assert first["price"] == BID
    assert bool(first["filled"]) is False


def test_a_price_deeper_than_the_cached_book_is_dropped_not_assumed_empty():
    """Three levels are cached. A quote price below all of them has an unknown
    queue ahead, and guessing zero would invent a front-of-queue fill."""
    deep = {
        "bid_px_0": BID, "bid_sz_0": 1,
        "bid_px_1": BID - 100, "bid_sz_1": 1,
        "bid_px_2": BID - 200, "bid_sz_2": 1,
        "ask_px_0": ASK, "ask_sz_0": 1,
    }
    rows = [
        (OPEN + 0.0,  1, 1, 1, BID - 500, 1),
        (OPEN + 1.0,  1, 2, 1, BID, 1),
        (OPEN + 20.0, 1, 3, 1, BID, 1),
    ]
    # the touch starts far below the cached ladder, so its queue is unknowable
    start = {"bid_px_0": BID - 500, "bid_sz_0": 1, "ask_px_0": ASK, "ask_sz_0": 1,
             "bid_px_1": BID - 600, "bid_sz_1": 1, "bid_px_2": BID - 700, "bid_sz_2": 1}
    episodes = simulate(*stream(rows, [start, deep, deep]), latency=0.0)
    # the deep quote survives (it is the touch at level 0); nothing is invented
    assert episodes["known"].all()


def test_latency_labels_are_readable():
    assert latency_label(0.0) == "0"
    assert latency_label(1e-5) == "10us"
    assert latency_label(1e-3) == "1ms"
    assert latency_label(1e-1) == "100ms"


def test_orders_outside_regular_hours_are_ignored():
    rows = [
        (OPEN - 100.0, 1, 1, 1, BID, 1),
        (OPEN - 99.0,  4, 2, 5, BID, 1),
    ]
    episodes = simulate(*stream(rows, [flat_state(), flat_state()]), latency=0.0)
    assert episodes.empty
