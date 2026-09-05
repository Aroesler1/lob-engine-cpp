"""Tests for queue position valuation.

The centrepiece is a toy book of three orders queued at one price where every
answer is hand-computable: who is ahead of whom, which one trades, which one is
cancelled, which is still resting at the end, and what the mid did afterwards.
Queue position is a ratio of two book quantities and adverse selection is a
signed difference of two mids, so both are easy to get subtly backwards and
neither would look wrong in aggregate.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from queue_position_value import (  # noqa: E402
    _bootstrap_mean,
    _decile,
    headline,
    resting_orders,
    summarise,
)

OPEN = 34_200.0
BID, ASK = 1_000_000, 1_000_500      # $100.00 / $100.05, a five-tick spread


def toy() -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Three bid orders at one price, plus a resting ask so the mid exists.

    order 1 (100 sh) arrives first  -> 0 ahead of it,   position 0
    order 2 (200 sh) arrives second -> 100 ahead of it, position 100/300
    order 3 (300 sh) arrives third  -> 300 ahead of it, position 300/600
    then order 1 trades, order 2 cancels, order 3 is left resting at the end.
    """
    rows = [
        # time,        type, oid, size, price, dir
        (OPEN + 0.0,   1, 10, 500, ASK, -1),
        (OPEN + 0.1,   1,  1, 100, BID,  1),
        (OPEN + 0.2,   1,  2, 200, BID,  1),
        (OPEN + 0.3,   1,  3, 300, BID,  1),
        (OPEN + 1.0,   4,  1, 100, BID,  1),   # order 1 fills
        (OPEN + 2.0,   2,  2, 200, BID,  1),   # order 2 cancels in full
        (OPEN + 11.0,  1, 11, 100, BID + 200, 1),   # bid improves by two ticks
    ]
    messages = pd.DataFrame(
        rows, columns=["time", "event_type", "order_id", "size", "price", "direction"])

    # book AFTER each message: (bid_px, bid_sz, ask_px, ask_sz)
    states = [
        (0, 0, ASK, 500),
        (BID, 100, ASK, 500),
        (BID, 300, ASK, 500),
        (BID, 600, ASK, 500),
        (BID, 500, ASK, 500),
        (BID, 300, ASK, 500),
        (BID + 200, 100, ASK, 500),
    ]
    book = {
        "timestamp": messages["time"].to_numpy(dtype=np.float64),
        "bid_px_0": np.array([s[0] for s in states], dtype=np.int32),
        "bid_sz_0": np.array([s[1] for s in states], dtype=np.int32),
        "ask_px_0": np.array([s[2] for s in states], dtype=np.int32),
        "ask_sz_0": np.array([s[3] for s in states], dtype=np.int32),
    }
    return messages, book


@pytest.fixture(scope="module")
def orders():
    return resting_orders(*toy()).set_index("order_id")


def test_only_touch_orders_are_recorded(orders):
    """Order 10 is excluded even though it is the best ask.

    It arrives into a book with no bid at all, so there is no spread to capture
    and no mid to measure adverse selection against. Counting it would put an
    order with an undefined payoff into the front-of-queue bucket.
    """
    assert set(orders.index) == {1, 2, 3, 11}


def test_shares_ahead_is_the_queue_in_front_not_the_whole_level(orders):
    assert orders.loc[1, "ahead"] == 0
    assert orders.loc[2, "ahead"] == 100
    assert orders.loc[3, "ahead"] == 300


def test_position_is_zero_at_the_front_and_rises_down_the_queue(orders):
    assert orders.loc[1, "position"] == pytest.approx(0.0)
    assert orders.loc[2, "position"] == pytest.approx(100 / 300)
    assert orders.loc[3, "position"] == pytest.approx(300 / 600)


def test_an_order_that_improves_the_touch_stands_alone(orders):
    # order 11 creates a new best price, so nothing can be ahead of it
    assert orders.loc[11, "ahead"] == 0
    assert orders.loc[11, "position"] == pytest.approx(0.0)


def test_fill_cancel_and_censoring_are_distinguished(orders):
    assert bool(orders.loc[1, "filled"]) is True
    assert bool(orders.loc[2, "filled"]) is False
    assert bool(orders.loc[3, "filled"]) is False
    # order 2 left the book in full, order 3 never did
    assert bool(orders.loc[2, "censored"]) is False
    assert bool(orders.loc[3, "censored"]) is True


def test_time_to_fill_measures_from_arrival(orders):
    assert orders.loc[1, "time_to_fill"] == pytest.approx(0.9)
    assert np.isnan(orders.loc[2, "time_to_fill"])


def test_half_spread_is_in_ticks(orders):
    # (1_000_500 - 1_000_000) / 2 = 250 units = 2.5 ticks
    assert orders.loc[1, "half_spread_ticks"] == pytest.approx(2.5)


def test_adverse_selection_is_signed_so_a_loss_is_positive(orders):
    """Order 1 is a resting BID, so it is long after the fill.

    The mid goes from 1,000,250 at the fill to 1,000,350 ten seconds later, one
    tick UP. A long position gains, so the adverse-selection number must be
    NEGATIVE one tick. Getting this backwards would invert every conclusion.
    """
    assert orders.loc[1, "adverse_10s"] == pytest.approx(-1.0)
    # one second after the fill the mid has not moved yet
    assert orders.loc[1, "adverse_1s"] == pytest.approx(0.0)


def test_a_horizon_past_the_end_of_the_session_is_unknown_not_flat(orders):
    # the stream ends 11s after the open, so a 60s horizon has no observation
    assert np.isnan(orders.loc[1, "adverse_60s"])


def test_adverse_selection_flips_sign_for_a_resting_ask():
    """Same price path, opposite side: a short loses when the mid rises."""
    messages, book = toy()
    # make the filled order an ask sitting at the touch instead of a bid
    messages.loc[1, ["price", "direction"]] = [ASK, -1]
    messages.loc[4, ["price", "direction"]] = [ASK, -1]
    book["ask_sz_0"] = np.array([500, 600, 600, 600, 500, 500, 500], dtype=np.int32)
    out = resting_orders(messages, book).set_index("order_id")
    assert out.loc[1, "adverse_10s"] == pytest.approx(+1.0)


def test_orders_outside_regular_hours_are_dropped():
    messages, book = toy()
    messages["time"] = messages["time"] - 3_600.0     # push the whole toy pre-open
    book["timestamp"] = messages["time"].to_numpy(dtype=np.float64)
    assert resting_orders(messages, book).empty


def test_a_reused_order_id_is_refused_rather_than_mis_joined():
    messages, book = toy()
    messages.loc[3, "order_id"] = 1        # order 3 now collides with order 1
    with pytest.raises(SystemExit, match="added more than once"):
        resting_orders(messages, book)


def test_deciles_are_fixed_not_sample_quantiles():
    # a session where every order is near the front must not have its top
    # decile relabelled as "back of queue"
    position = np.array([0.0, 0.01, 0.02, 0.03])
    assert list(_decile(position)) == [0, 0, 0, 0]
    assert _decile(np.array([0.999]))[0] == 9
    assert _decile(np.array([1.0]))[0] == 9        # clipped, not out of range


def test_bootstrap_band_brackets_the_mean_and_widens_with_noise():
    rng = np.random.default_rng(0)
    minutes = np.repeat(np.arange(200), 5)
    quiet = rng.normal(2.0, 0.1, size=minutes.size)
    noisy = rng.normal(2.0, 3.0, size=minutes.size)
    lo_q, hi_q = _bootstrap_mean(quiet, minutes, draws=400)
    lo_n, hi_n = _bootstrap_mean(noisy, minutes, draws=400)
    assert lo_q < quiet.mean() < hi_q
    assert (hi_n - lo_n) > (hi_q - lo_q)


def test_bootstrap_resamples_minutes_not_orders():
    """A single minute cannot produce a band: there is one independent draw.

    If the bootstrap resampled orders it would happily return a tight interval
    here, which is the failure this guards against.
    """
    values = np.random.default_rng(1).normal(size=500)
    lo, hi = _bootstrap_mean(values, np.zeros(500, dtype=np.int64))
    assert np.isnan(lo) and np.isnan(hi)


def test_edge_combines_fill_probability_and_adverse_selection():
    orders = pd.DataFrame({
        "position": [0.0, 0.95],
        "filled": [True, False],
        "time_to_fill": [1.0, np.nan],
        "adverse_1s": [0.5, np.nan],
        "adverse_10s": [0.5, np.nan],
        "adverse_60s": [0.5, np.nan],
        "half_spread_ticks": [2.5, 2.5],
        "minute": [0, 1],
        "life": [1.0, 5.0],
        "own_size": [50.0, 10.0],
    })
    table = summarise(orders, "TOY")
    front = table[table.decile == 0].iloc[0]
    # fill probability 1, half spread 2.5, adverse selection 0.5 -> edge 2.0
    assert front.edge_ticks == pytest.approx(2.0)
    back = table[table.decile == 9].iloc[0]
    assert back.fill_prob == 0.0 and back.edge_ticks == pytest.approx(0.0)
    head = headline(table)
    assert head["front_minus_back_ticks"] == pytest.approx(2.0)
