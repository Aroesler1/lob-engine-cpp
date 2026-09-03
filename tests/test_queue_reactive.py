"""Tests for the queue-reactive calibration.

The centrepiece is a synthetic book generated from intensities that are KNOWN in
closed form, written out in exactly the format the engine emits, and then run
through the real calibration path. That is the only way to tell an estimator
that works from one that merely produces plausible-looking curves: on real data
there is nothing to check the answer against.

The geometry tests matter just as much. Every intensity in this module is
conditioned on a queue index derived from the reference price, so an off-by-half
a tick in p_ref, or an off-by-one between "book after message r" and "state the
message acted on", would silently bias every curve while leaving the output
looking perfectly reasonable.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from queue_reactive import (  # noqa: E402
    HALF_TICK,
    normalise,
    K,
    RTH_OPEN,
    TICK,
    average_event_sizes,
    build_records,
    estimate_intensities,
    queue_index,
    queue_prices,
    reference_prices,
)

QUEUES = [-3, -2, -1, 1, 2, 3]


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def test_odd_spread_puts_pref_at_the_midprice():
    # 1-tick spread: the midprice is already half a tick off the grid
    p_ref = reference_prices(np.array([215800.0]), np.array([215900.0]))
    assert p_ref[0] == 215850


def test_even_spread_breaks_the_tie_toward_the_previous_pref():
    # rows: 1-tick spread fixes p_ref low, then a 2-tick spread whose midprice
    # sits ON the grid and must therefore be nudged to the nearer legal value
    bid = np.array([215800.0, 215800.0])
    ask = np.array([215900.0, 216000.0])
    p_ref = reference_prices(bid, ask)
    assert p_ref[0] == 215850
    # midprice 215900 -> candidates 215850 and 215950; 215850 is nearer to 215850
    assert p_ref[1] == 215850


def test_pref_is_always_an_odd_multiple_of_half_a_tick():
    rng = np.random.default_rng(0)
    bid = 200000 + rng.integers(0, 50, 500) * TICK
    ask = bid + rng.integers(1, 9, 500) * TICK
    p_ref = reference_prices(bid.astype(float), ask.astype(float))
    assert np.all(p_ref % TICK == HALF_TICK)


def test_one_sided_book_carries_the_previous_pref_forward():
    bid = np.array([215800.0, np.nan, 215800.0])
    ask = np.array([215900.0, 215900.0, 215900.0])
    p_ref = reference_prices(bid, ask)
    assert p_ref[1] == p_ref[0] == 215850


def test_queue_index_round_trips_with_queue_prices():
    p_ref = np.array([215850] * len(QUEUES))
    prices = queue_prices(p_ref)
    for i in QUEUES:
        recovered = queue_index(np.array([prices[i][0]]), np.array([215850]))
        assert recovered[0] == i, f"Q{i} did not round-trip"


def test_queue_one_is_half_a_tick_from_pref_not_a_whole_tick():
    # the classic error: putting Q_1 a full tick away, which shifts every queue
    prices = queue_prices(np.array([215850]))
    assert prices[1][0] == 215900 and prices[-1][0] == 215800
    assert prices[2][0] == 216000 and prices[-2][0] == 215700


def test_off_grid_prices_get_no_queue():
    # a sub-penny sentinel is not on the queue grid and must not be forced onto it
    assert queue_index(np.array([1]), np.array([215850]))[0] == 0
    # nor is anything at all valid before p_ref is defined
    assert queue_index(np.array([215900]), np.array([-1]))[0] == 0


# --------------------------------------------------------------------------
# synthetic book with known intensities
# --------------------------------------------------------------------------

def true_rates(queue: int, n: int) -> tuple[float, float, float]:
    """Ground-truth (add, cancel, exec) intensities for the synthetic book.

    Shaped like the paper's stylized facts so the test also exercises the
    regime the estimator is meant to resolve: flat arrivals at the touch,
    decreasing arrivals behind it, cancellation proportional to queue size,
    and execution concentrated at the touch and decaying in size.

    The best queues are reflected at n = 1 rather than 0, which pins the spread
    at one tick and therefore holds p_ref still. That keeps the ground truth
    exactly knowable: no reference-price moves means no queue re-indexing.
    """
    level = abs(queue)
    floor = 1 if level == 1 else 0
    add = 2.0 if level == 1 else 3.0 / (1.0 + n)
    cancel = 0.0 if n <= floor else 0.12 * n
    execute = (0.0 if n <= floor else 1.5 * np.exp(-n / 6.0)) if level == 1 else 0.0
    return add, cancel, execute


def generate_synthetic_book(path: Path, n_events: int = 160_000, seed: int = 7,
                            p_ref: int = 215_850) -> tuple[Path, Path]:
    """Gillespie-simulate the six queues and write engine-format CSVs.

    Every event has size 1, so AES = 1 and a normalised queue size is just a
    share count -- which makes the recovered intensities directly comparable to
    `true_rates` with no unit conversion in the way.
    """
    rng = np.random.default_rng(seed)
    prices = {i: int(queue_prices(np.array([p_ref]))[i][0]) for i in QUEUES}
    state = {i: (4 if abs(i) == 1 else 2) for i in QUEUES}

    t = float(RTH_OPEN)
    messages, books = [], []
    order_id = 1

    for _ in range(n_events):
        rates = {i: true_rates(i, state[i]) for i in QUEUES}
        totals = np.array([sum(rates[i]) for i in QUEUES])
        total = totals.sum()
        t += float(rng.exponential(1.0 / total))

        pick = QUEUES[int(rng.choice(len(QUEUES), p=totals / total))]
        row = np.array(rates[pick])
        kind = int(rng.choice(3, p=row / row.sum()))
        event_type = (1, 2, 4)[kind]
        state[pick] = state[pick] + 1 if kind == 0 else state[pick] - 1

        messages.append((t, event_type, order_id, 1, prices[pick],
                         1 if pick < 0 else -1))
        order_id += 1

        # book AFTER the event, in the engine's --book-out layout: non-empty
        # levels only, ascending on the ask and descending on the bid
        asks = sorted((prices[i], state[i]) for i in (1, 2, 3) if state[i] > 0)
        bids = sorted(((prices[i], state[i]) for i in (-1, -2, -3) if state[i] > 0),
                      reverse=True)
        row_out = [f"{t:.9f}"]
        for level in range(10):
            for ladder in (bids, asks):
                if level < len(ladder):
                    row_out.append(str(ladder[level][0]))
                    row_out.append(str(ladder[level][1]))
                else:
                    row_out.extend(["", ""])
        books.append(",".join(row_out))

    message_path = path / "SYN_message.csv"
    book_path = path / "SYN_book10.csv"
    pd.DataFrame(messages).to_csv(message_path, header=False, index=False,
                                  float_format="%.9f")
    header = ["timestamp"]
    for level in range(10):
        header += [f"bid_px_{level}", f"bid_sz_{level}",
                   f"ask_px_{level}", f"ask_sz_{level}"]
    book_path.write_text(",".join(header) + "\n" + "\n".join(books) + "\n")
    return book_path, message_path


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    path = tmp_path_factory.mktemp("qr")
    book, messages = generate_synthetic_book(path)
    records = build_records(book, messages, chunk=50_000, verbose=False)
    aes = average_event_sizes(records.events)
    frame = estimate_intensities(records, aes)
    return records, aes, frame


def test_synthetic_aes_is_one_by_construction(synthetic):
    _, aes, _ = synthetic
    for queue in QUEUES:
        assert aes[queue] == pytest.approx(1.0)


def test_every_event_is_attributed_to_a_queue(synthetic):
    records, _, _ = synthetic
    # the synthetic book only ever trades on the six modelled queues, so nothing
    # should be dropped as off-grid or out of window
    assert records.off_grid == 0
    assert records.uncovered == 0
    assert len(records.events) == records.n_messages - 1   # first message has no prior state


def test_reference_price_never_moves_in_the_synthetic_book(synthetic):
    records, _, _ = synthetic
    # the best queues are reflected at 1, so the spread is pinned and p_ref is still
    assert records.depletions == 0
    assert np.nanmax(records.mid_grid) == pytest.approx(np.nanmin(records.mid_grid))


def _coverage(frame, min_exposure=20.0):
    """(covered, total, ratios) for the truth against each bin's 95% band."""
    covered = total = 0
    ratios = []
    for _, row in frame[frame.exposure >= min_exposure].iterrows():
        truth = dict(zip(("add", "cancel", "exec"),
                         true_rates(int(row.queue), int(row.n))))
        for kind, value in truth.items():
            lo, hi = row[f"lambda_{kind}_lo"], row[f"lambda_{kind}_hi"]
            total += 1
            covered += int(lo - 1e-12 <= value <= hi + 1e-12)
            if value > 0.05 and row["exposure"] >= 100:
                ratios.append(row[f"lambda_{kind}"] / value)
    return covered, total, np.array(ratios)


def test_confidence_bands_have_their_nominal_coverage(synthetic):
    """The honest way to test an interval estimator over hundreds of bins.

    Checking every bin individually against its own 95% band would fail by
    construction -- roughly one bin in twenty is *supposed* to exclude the
    truth, so that test would just be measuring the multiple-comparisons rate.
    Coverage is the real property, and it is strictly stronger: it fails if the
    point estimates are biased OR if the bands are the wrong width.
    """
    _, _, frame = synthetic
    covered, total, _ = _coverage(frame)
    assert total >= 150, "too few well-observed bins for coverage to mean anything"
    rate = covered / total
    # binomial sd at p=0.95 over ~280 bins is ~1.3pp; 0.90-0.98 is a wide net
    # that still catches a broken estimator or a mis-scaled interval
    assert 0.90 <= rate <= 0.98, f"95% bands covered the truth {rate:.1%} of the time"


def test_point_estimates_are_unbiased(synthetic):
    """Separately from coverage: the estimates must not drift systematically.

    N/T for a jump process is biased high by a factor N/(N-1), so a small
    positive offset is expected; anything beyond a few percent is not.
    """
    _, _, frame = synthetic
    _, _, ratios = _coverage(frame)
    assert len(ratios) >= 100
    assert 0.97 <= ratios.mean() <= 1.05, f"mean estimate/truth {ratios.mean():.4f}"
    assert 0.97 <= np.median(ratios) <= 1.05


@pytest.mark.parametrize("queue", QUEUES)
def test_every_queue_is_recovered_to_within_a_few_percent(synthetic, queue):
    """Per-queue guard, on well-observed bins only, so no queue can be silently
    wrong while the pooled coverage statistic still looks healthy."""
    _, _, frame = synthetic
    _, _, ratios = _coverage(frame[frame.queue == queue], min_exposure=100.0)
    assert len(ratios) >= 5, f"Q{queue} has too few well-observed bins to test"
    assert abs(np.median(ratios) - 1.0) < 0.08, (
        f"Q{queue} median estimate/truth {np.median(ratios):.4f}")


def test_stylized_facts_are_recovered(synthetic):
    """The three shapes the paper reports, checked on data that really has them."""
    _, _, frame = synthetic

    touch = frame[(frame.queue == 1) & (frame.exposure >= 50.0)].sort_values("n")
    deep = frame[(frame.queue == 2) & (frame.exposure >= 50.0)].sort_values("n")

    # cancellation grows with queue size
    assert np.corrcoef(touch.n, touch.lambda_cancel)[0, 1] > 0.9
    # limit arrivals fall away from the touch
    assert np.corrcoef(deep.n, deep.lambda_add)[0, 1] < -0.8
    # execution happens at the touch and essentially nowhere else
    assert touch.lambda_exec.max() > 0.5
    assert deep.lambda_exec.max() == pytest.approx(0.0)


def test_estimator_is_exposure_weighted_not_event_weighted():
    """A rate is events over TIME. Averaging per-event would give a different,
    wrong answer whenever the queue spends unequal time in each state."""
    from queue_reactive import SessionRecords

    events = pd.DataFrame({
        "time": [1.0, 2.0, 3.0], "event_type": [1, 1, 2], "queue": [1, 1, 1],
        "level_from_best": [1, 1, 1], "size": [1, 1, 1], "q_before": [0, 0, 5],
    })
    records = SessionRecords(events=events, exposure={(1, 0): 4.0, (1, 5): 1.0})
    frame = estimate_intensities(records, {1: 1.0})
    at_zero = frame[(frame.queue == 1) & (frame.n == 0)].iloc[0]
    at_five = frame[(frame.queue == 1) & (frame.n == 5)].iloc[0]
    assert at_zero.lambda_add == pytest.approx(2 / 4.0)
    assert at_five.lambda_cancel == pytest.approx(1 / 1.0)


def test_queue_size_is_measured_before_the_event_not_after(tmp_path):
    """The engine writes the book AFTER each message, so the state an event acted
    on is the previous row. Reading the same row would attribute every event to
    the size it produced rather than the size that produced it."""
    header = ["timestamp"]
    for level in range(10):
        header += [f"bid_px_{level}", f"bid_sz_{level}",
                   f"ask_px_{level}", f"ask_sz_{level}"]

    def book_row(t, bid_sz, ask_sz):
        cells = [f"{t:.9f}"]
        for level in range(10):
            if level == 0:
                cells += ["215800", str(bid_sz), "215900", str(ask_sz)]
            else:
                cells += ["", "", "", ""]
        return ",".join(cells)

    # three adds on the ask: sizes after each message are 10, 20, 30
    rows = [book_row(RTH_OPEN + 1, 10, 10), book_row(RTH_OPEN + 2, 10, 20),
            book_row(RTH_OPEN + 3, 10, 30)]
    book = tmp_path / "B_book10.csv"
    book.write_text(",".join(header) + "\n" + "\n".join(rows) + "\n")

    messages = pd.DataFrame([
        (RTH_OPEN + 1, 1, 1, 10, 215900, -1),
        (RTH_OPEN + 2, 1, 2, 10, 215900, -1),
        (RTH_OPEN + 3, 1, 3, 10, 215900, -1),
    ])
    message_path = tmp_path / "B_message.csv"
    messages.to_csv(message_path, header=False, index=False, float_format="%.9f")

    records = build_records(book, message_path, chunk=10, verbose=False)
    got = records.events.sort_values("time")
    # message 2 acted on the book after message 1 (ask 10), message 3 on ask 20
    assert list(got.q_before) == [10, 20]
    assert list(got.queue) == [1, 1]


def test_level_from_best_is_recorded_alongside_the_pref_index(synthetic):
    """Both indices are kept so the difference between them stays visible."""
    records, _, _ = synthetic
    events = records.events
    assert "level_from_best" in events.columns
    # in the synthetic book Q_1 is always the touch, so the two agree there
    touch = events[events.queue == 1]
    assert (touch.level_from_best == 1).all()


def test_event_record_carries_side_and_per_queue_inter_arrival(synthetic):
    """The per-event record is a stated deliverable, so its fields are pinned."""
    records, _, _ = synthetic
    events = records.events
    for column in ("event_type", "side", "queue", "level_from_best",
                   "q_before", "dt_queue"):
        assert column in events.columns, f"missing {column}"

    # side must agree with the sign of the p_ref-relative queue index
    assert (events[events.queue > 0].side == "ask").all()
    assert (events[events.queue < 0].side == "bid").all()

    # dt_queue is the gap since the previous event on the SAME queue, so it is
    # at least as large as the gap since the previous event anywhere
    for queue, group in events.groupby("queue"):
        stamps = group.time.to_numpy()
        expected = np.diff(stamps)
        assert np.allclose(group.dt_queue.to_numpy()[1:], expected, atol=1e-9), queue
        assert np.isnan(group.dt_queue.to_numpy()[0]), "first event has no predecessor"


def test_rate_estimator_matches_the_papers_waiting_time_form(synthetic):
    """N / T must agree with the paper's own estimator, [mean waiting time]^-1.

    Huang, Lehalle and Rosenbaum define the total event intensity at a queue as
    the reciprocal of the mean waiting time between events there, conditional on
    queue size. This module instead uses events over exposure, which is the
    standard MLE for a Markov jump process and is what makes the Poisson
    confidence bands exact. The two are the same estimator written differently,
    and this checks that on data where both are computable -- if they diverged,
    one of the two accountings would be wrong.
    """
    records, aes, frame = synthetic
    events = records.events.dropna(subset=["dt_queue"])
    binned = events.assign(
        n=[int(normalise(q, aes[qu])) for q, qu in zip(events.q_before, events.queue)])

    compared = 0
    for (queue, n), group in binned.groupby(["queue", "n"]):
        if len(group) < 400:
            continue
        row = frame[(frame.queue == queue) & (frame.n == n)]
        if row.empty:
            continue
        paper = 1.0 / group.dt_queue.mean()
        mine = float(row.lambda_total.iloc[0])
        assert abs(paper - mine) / mine < 0.10, (
            f"Q{queue} n={n}: waiting-time form {paper:.3f} vs N/T {mine:.3f}")
        compared += 1
    assert compared >= 8, f"only {compared} bins had enough events to compare"
