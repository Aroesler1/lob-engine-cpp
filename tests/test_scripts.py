"""Tests for the Python tooling around the engine.

These cover the parts where a silent wrong answer is plausible: an integer cast
that quietly loses a tick, a session window that shifts by an hour across a DST
boundary, and a cell-accounting identity that keeps the headline agreement
percentage honest.
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetch_databento_session import session_window  # noqa: E402
from run_lob_bench import (  # noqa: E402
    ABSENT_ASK_PX,
    ABSENT_BID_PX,
    ABSENT_SZ,
    to_lobster_book,
)
from validate_mbp10_vs_vendor import Counts, compare  # noqa: E402


def _book_frame(rows, depth=1):
    """Frame shaped like load_vendor / the engine's --book-out, one level."""
    data = {}
    for level in range(depth):
        for tag in ("bid", "ask"):
            for field in ("px", "sz"):
                data[f"{tag}_{field}_{level}"] = [r[f"{tag}_{field}_{level}"] for r in rows]
    return pd.DataFrame(data)


def test_price_cast_rounds_rather_than_truncates():
    """Regression: Databento reports dollars, so scaling by 1e4 lands just below
    the integer. Truncating turned $21.59 into 215899 and manufactured a one-tick
    disagreement on nearly every price cell while leaving sizes untouched."""
    frame = _book_frame([{
        "bid_px_0": 215899.99999999997, "bid_sz_0": 2415.0,
        "ask_px_0": 215999.99999999997, "ask_sz_0": 3000.0,
    }])
    out = to_lobster_book(frame, depth=1)
    # LOBSTER column order per level: ask_px, ask_sz, bid_px, bid_sz
    assert out[0, 0] == 216000
    assert out[0, 2] == 215900


def test_absent_levels_use_lobster_sentinels():
    frame = _book_frame([{
        "bid_px_0": np.nan, "bid_sz_0": np.nan,
        "ask_px_0": np.nan, "ask_sz_0": np.nan,
    }])
    out = to_lobster_book(frame, depth=1)
    assert out[0, 0] == ABSENT_ASK_PX and out[0, 1] == ABSENT_SZ
    assert out[0, 2] == ABSENT_BID_PX and out[0, 3] == ABSENT_SZ


def test_column_order_is_ask_then_bid_per_level():
    frame = _book_frame([
        {"bid_px_0": 100.0, "bid_sz_0": 1.0, "ask_px_0": 200.0, "ask_sz_0": 2.0,
         "bid_px_1": 90.0, "bid_sz_1": 3.0, "ask_px_1": 210.0, "ask_sz_1": 4.0},
    ], depth=2)
    out = to_lobster_book(frame, depth=2)
    assert list(out[0]) == [200, 2, 100, 1, 210, 4, 90, 3]


@pytest.mark.parametrize("date,offset", [("2024-08-02", "EDT"), ("2024-01-02", "EST")])
def test_session_window_is_exchange_local_across_dst(date, offset):
    """04:00-20:00 must be exchange-local on both sides of the DST boundary; a
    UTC-fixed window would shift by an hour in winter and clip the session."""
    start, end = session_window(date)
    assert (start.hour, end.hour) == (4, 20)
    assert start.tzname() == offset and end.tzname() == offset
    assert (end - start).total_seconds() == 16 * 3600


def test_slot_accounting_partitions_every_cell():
    """compared + both-absent + one-sided must equal the total slot count, or a
    disagreement about whether a level exists could hide behind the percentage."""
    vendor = pd.DataFrame({
        "sequence": [1, 2, 3],
        "bid_px_0": [100.0, np.nan, 100.0], "bid_sz_0": [5.0, np.nan, 5.0],
        "ask_px_0": [200.0, np.nan, np.nan], "ask_sz_0": [6.0, np.nan, np.nan],
    })
    engine = pd.DataFrame({
        "bid_px_0": [100.0, np.nan, np.nan], "bid_sz_0": [5.0, np.nan, np.nan],
        "ask_px_0": [200.0, np.nan, np.nan], "ask_sz_0": [6.0, np.nan, np.nan],
    })
    sequences = np.array([1, 2, 3])
    first = np.array([0, 1, 2])
    counts = compare(vendor, engine, sequences, first, first, depth=1)

    assert isinstance(counts, Counts)
    slots = 3 * 4  # 3 sequences x (bid px, bid sz, ask px, ask sz)
    total = int(counts.compared.sum() + counts.both_absent.sum() + counts.one_sided.sum())
    assert total == slots
    # sequence 1 fully present and matching; 2 fully absent; 3 vendor-only on bid
    assert int(counts.matched.sum()) == 4
    assert int(counts.compared.sum()) == 4
    assert int(counts.both_absent.sum()) == 6
    assert int(counts.one_sided.sum()) == 2
