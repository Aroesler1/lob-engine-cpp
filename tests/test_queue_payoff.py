"""Cash-flow counterexamples, including partial fills and missing marks."""
import numpy as np
import pytest

from test_queue_position_value import BID, OPEN, toy
from queue_position_value import resting_orders
from queue_payoff import direct_payoff, aggregate


def test_published_payoff_verifier_rejects_changed_order_count(tmp_path):
    import shutil
    from pathlib import Path
    import pandas as pd
    from verify_queue_payoff import build
    root = Path(__file__).resolve().parents[1]
    for relative in ("report/queue_payoff", "report/queue_position"):
        shutil.copytree(root / relative, tmp_path / relative)
    path = tmp_path / "report/queue_payoff/deciles.csv"
    frame = pd.read_csv(path)
    frame.loc[0, "orders"] += 1
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="counts"):
        build(tmp_path)


def evaluate(messages, book, **kwargs):
    return direct_payoff(messages, book, resting_orders(messages, book), **kwargs).set_index("order_id")


def test_direct_execution_price_captures_move_before_fill():
    messages, book = toy()
    # Raise the midpoint before execution, keeping the execution price fixed.
    book["ask_px_0"][4:] += 400
    orders = evaluate(messages, book)
    assert orders.loc[1, "gross_payoff_ticks"] == pytest.approx(5.5)
    # Legacy spread minus post-fill adverse selection misses the earlier move.
    assert orders.loc[1, "half_spread_ticks"] - orders.loc[1, "adverse_10s"] == pytest.approx(3.5)


def test_partial_fill_receives_only_executed_fraction_and_fees():
    messages, book = toy()
    messages.loc[4, "size"] = 25
    orders = evaluate(messages, book, fee_ticks=0.2)
    assert orders.loc[1, "gross_payoff_ticks"] == pytest.approx(0.25 * 3.5)
    assert orders.loc[1, "net_payoff_ticks"] == pytest.approx(0.25 * 3.3)
    assert orders.loc[1, "remaining_qty_at_close"] == 75
    assert orders.loc[2, "net_payoff_ticks"] == 0


def test_all_executions_contribute_at_their_own_horizon():
    messages, book = toy()
    messages.loc[4, "size"] = 25
    messages.loc[5, ["event_type", "order_id", "size", "price", "direction"]] = [4, 1, 75, BID, 1]
    orders = evaluate(messages, book, horizon=9)
    # First mark at t=10 sees 2.5 ticks, second at t=11 sees 3.5.
    assert orders.loc[1, "gross_payoff_ticks"] == pytest.approx(0.25 * 2.5 + 0.75 * 3.5)
    assert orders.loc[1, "executed_qty"] == 100


def test_incomplete_second_fill_does_not_become_zero_profit():
    messages, book = toy()
    messages.loc[4, "size"] = 25
    messages.loc[5, ["event_type", "order_id", "size", "price", "direction"]] = [4, 1, 75, BID, 1]
    orders = evaluate(messages, book)
    assert np.isnan(orders.loc[1, "net_payoff_ticks"])
    assert orders.loc[1, "missing_mark_qty"] == 75
    table = aggregate(orders.reset_index(drop=True), "TOY")
    assert table.missing_mark_orders.sum() == 1


def test_missing_book_side_is_not_filled_from_an_earlier_quote():
    messages, book = toy()
    book["ask_px_0"][-1] = 0
    orders = evaluate(messages, book)
    assert not orders.loc[1, "payoff_observed"]
    assert np.isnan(orders.loc[1, "gross_payoff_ticks"])


def test_after_hours_mark_is_not_used_for_regular_session_payoff():
    messages, book = toy()
    shift = 57_595 - OPEN
    messages["time"] += shift
    book["timestamp"] = messages.time.to_numpy()
    orders = evaluate(messages, book)
    assert not orders.loc[1, "payoff_observed"]


def test_misaligned_cache_is_refused():
    messages, book = toy()
    book["timestamp"] = book["timestamp"].copy()
    book["timestamp"][1] += 0.01
    with pytest.raises(ValueError, match="align row for row"):
        evaluate(messages, book)


def test_overfill_is_refused():
    messages, book = toy()
    messages.loc[4, "size"] = 101
    with pytest.raises(ValueError, match="exceeds original"):
        evaluate(messages, book)
