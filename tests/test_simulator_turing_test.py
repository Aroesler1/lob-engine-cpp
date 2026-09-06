"""Tests for the simulator Turing test.

The null calibration is the one that matters: when the "fake" windows are drawn
from the real data itself there is nothing to find, and the whole apparatus has
to report AUC near 0.5. A discriminative test that reads above chance on
identical data would make every simulator look bad and the Hawkes comparison
meaningless.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from queue_reactive import QUEUES  # noqa: E402
from simulator_turing_test import (  # noqa: E402
    WINDOW,
    canonical_sim,
    chronological_split,
    discriminate,
    family,
    window_features,
    windows,
)


def synthetic_stream(n_events: int, seed: int = 0, rate: float = 50.0) -> pd.DataFrame:
    """A plausible order-event stream with a mean-reverting touch queue."""
    rng = np.random.default_rng(seed)
    gaps = rng.exponential(1.0 / rate, n_events)
    frame = pd.DataFrame({
        "time": 34_200.0 + np.cumsum(gaps),
        "kind": rng.choice(3, n_events, p=[0.45, 0.45, 0.10]),
    })
    for q in QUEUES:
        walk = np.abs(np.cumsum(rng.normal(0, 1, n_events))) + 5.0
        frame[f"q{q}"] = walk
    return frame


def test_identical_data_scores_at_chance():
    """Split one real stream into two halves that interleave in time.

    Both classes then span the same period and carry the same distribution, so
    any AUC materially above 0.5 is the apparatus finding structure that is not
    there.
    """
    stream = synthetic_stream(200 * 400, seed=1)
    table = windows(stream, label=0)
    assert len(table) >= 200
    # alternate windows into the two classes so both cover the whole session
    table = table.reset_index(drop=True)
    table.loc[table.index % 2 == 1, "label"] = 1
    real = table[table.label == 0].reset_index(drop=True)
    fake = table[table.label == 1].reset_index(drop=True)

    score, detail = discriminate(real, fake, seed=0)
    assert abs(score - 0.5) < 0.05, f"AUC {score:.3f} on identical data"
    assert not detail.empty


def test_an_obviously_different_stream_is_caught():
    """Sanity in the other direction: if the fake is ten times slower the test
    must notice, or a 0.5 elsewhere means nothing."""
    real = windows(synthetic_stream(200 * 200, seed=2, rate=500.0), label=0)
    fake = windows(synthetic_stream(200 * 200, seed=3, rate=50.0), label=1)
    score, detail = discriminate(real, fake, seed=0)
    assert score > 0.9, f"AUC {score:.3f} on a stream ten times slower"
    assert detail.iloc[0]["family"] == "timing"


def test_split_is_chronological_not_random():
    table = pd.DataFrame({
        "t0": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
        "label": [0] * 10,
        "x": range(10),
    })
    train, test = chronological_split(table)
    # every training window precedes every test window
    assert table.loc[train, "t0"].max() < table.loc[test, "t0"].min()
    assert train.sum() == 7 and test.sum() == 3


def test_split_is_applied_within_each_class():
    """Two classes covering different spans must each be cut at their own 70%.

    A single global cut would put one class entirely in training and the other
    entirely in test, which scores a perfect AUC on the time axis alone.
    """
    table = pd.DataFrame({
        "t0": list(range(10)) + list(range(100, 110)),
        "label": [0] * 10 + [1] * 10,
        "x": range(20),
    })
    train, test = chronological_split(table)
    assert set(table.loc[train, "label"]) == {0, 1}
    assert set(table.loc[test, "label"]) == {0, 1}


def test_price_moves_are_dropped_from_the_simulated_stream():
    """A reference-price move has no counterpart in the real event stream, so
    leaving it in would be a free tell."""
    path = pd.DataFrame({
        "time": [1.0, 2.0, 3.0, 4.0],
        "kind": [0, 3, 1, 2],
        "queue": [1, 1, -1, 1],
        "p_ref": [0, 1, 1, 1],
        **{f"q{q}": [1.0, 2.0, 3.0, 4.0] for q in QUEUES},
    })
    out = canonical_sim(path)
    assert len(out) == 3
    assert 3 not in set(out["kind"])


def test_every_feature_has_a_family():
    block = synthetic_stream(WINDOW, seed=4)
    for name in window_features(block):
        assert family(name) in {"timing", "event mix", "book shape", "order flow"}


def test_an_unknown_feature_name_is_refused():
    # a new feature added without a family would otherwise be silently dropped
    # from the attribution and the families would stop summing to the total
    with pytest.raises(KeyError):
        family("some_new_feature")


def test_window_features_are_finite_on_a_normal_block():
    block = synthetic_stream(WINDOW, seed=5)
    values = window_features(block)
    assert all(np.isfinite(v) for v in values.values()), values


def test_too_few_events_produce_no_windows():
    assert windows(synthetic_stream(WINDOW * 2, seed=6), label=0).empty
