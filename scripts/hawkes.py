#!/usr/bin/env python3
"""Hawkes self-excitation on top of the queue-reactive baseline.

The queue-reactive model gets the book's shape roughly right and its TIMING
badly wrong: it is a Markov chain whose waiting times are exponential given the
state, so it cannot produce bursts. On this repo's sessions the real median
inter-arrival is tens of microseconds against a simulated few milliseconds,
which is the failure the README documents.

Wu, Rambaldi, Muzy and Bacry, "Queue-reactive Hawkes models for the order flow"
(arXiv:1901.08938), fix it by ADDING a Hawkes term to the queue-reactive
arrival rates. The baseline keeps the state dependence, the Hawkes term supplies
the clustering:

    lambda_d(t) = mu_d(q(t)) + sum_s alpha[d, s] * sum_{t_j in s, t_j < t}
                                             exp(-beta[s] * (t - t_j))

`d` and `s` run over the six touch dimensions: {limit, cancel, market} at the
best bid and at the best ask, which is the set the paper models jointly. Queues
behind the touch keep the pure queue-reactive rates: they carry far less of the
flow and adding thirty-six more parameters for them would not be identified.

"Touch" means the innermost NON-EMPTY queue on each side, not the fixed index
Q+1/Q-1. The two differ whenever the spread is wider than one tick, and on a
five-tick book they differ almost always: MSFT sits at Q+1 for about 1% of the
session, so selecting on the index rather than on the book would fit forty-two
parameters to a few thousand unrepresentative events. Selecting on the actual
touch gives four to five times as many events on the same session.

Kernel norm and decay time are the two reported quantities:

    norm[d, s]  = alpha[d, s] / beta[s]      expected offspring of type d per
                                             parent of type s
    decay[s]    = 1 / beta[s]                seconds

A norm matrix with spectral radius at or above 1 is an explosive process. It is
reported rather than constrained, because a fitted radius near 1 is a finding
about the data, not a number to clamp.

TIED TIMESTAMPS BOUND THE DECAY
-------------------------------
The likelihood of a simple point process assumes no two events share a time.
This feed breaks that: 6 to 15% of consecutive touch events on these sessions
carry an identical `ts_recv`. At a gap of exactly zero the kernel is
exp(0) = 1 whatever beta is, so the optimiser can drive beta to infinity, put
all the excitation on coincident events and run the likelihood up without bound.
Left unbounded it does exactly that, returning decay times of 1e-300 seconds and
log-likelihoods an order of magnitude too large on 7 of 15 sessions.

The decay is therefore bounded below at one microsecond. That is not a
convenience: a decay faster than the interval over which the feed reports
distinct timestamps is not identifiable from this data, and a fit that claims
one is reading the tie structure rather than the clustering. The tie share is
reported per session so the size of the problem stays visible.

WHY THE FIT USES WINDOWS
------------------------
The log-likelihood needs the decayed sum at every event, which is a sequential
recursion that numpy cannot vectorise without overflowing: with a decay time of
a millisecond, exp(beta * t) over a six-hour session is exp(2e7). The recursion
is therefore an explicit loop, and running it over every event of every session
for every optimiser step is hours of work for twelve to forty-two parameters.

Instead the fit uses disjoint windows spread evenly across the session. Each
window is long relative to the kernel it is estimating, so the events lost at
each window edge are a small and unbiased fraction. Coverage and the edge share
are both reported so the cost is visible.

Usage:
    python scripts/hawkes.py --session MSFT_2024-06-03
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import sessions
from queue_reactive import (
    KINDS,
    estimate_coupled,
    simulate as base_simulate,
    QUEUES,
    RTH_CLOSE,
    RTH_OPEN,
    SessionRecords,
    FittedModel,
    average_event_sizes,
    build_records,
    estimate_intensities,
    fit_model,
    normalise,
)

# six touch dimensions: (kind index 0..2) x (side: 0 = ask/Q+1, 1 = bid/Q-1)
DIMS = tuple(f"{k}_{s}" for s in ("ask", "bid") for k in KINDS)
N_DIMS = len(DIMS)
EVENT_TO_KIND = {1: 0, 2: 1, 4: 2}

MIN_DECAY_S, MAX_DECAY_S = 1e-6, 10.0
MIN_ALPHA, MAX_ALPHA = 1e-9, 1e9


def dim_index(kind: int, queue: int) -> int:
    return kind + (0 if queue > 0 else 3)


@dataclass
class HawkesFit:
    alpha: np.ndarray                  # (6, 6) excitation, [target, source]
    beta: np.ndarray                   # (6,) decay rate per SOURCE dimension
    alpha_se: np.ndarray
    beta_se: np.ndarray
    loglik: float
    baseline_loglik: float             # same data, alpha = 0
    n_events: int
    n_windows: int
    coverage: float                    # share of session time inside the windows
    tie_share: float = 0.0
    converged: bool = True

    @property
    def norm(self) -> np.ndarray:
        return self.alpha / self.beta[None, :]

    @property
    def decay_s(self) -> np.ndarray:
        return 1.0 / self.beta

    @property
    def spectral_radius(self) -> float:
        return float(np.max(np.abs(np.linalg.eigvals(self.norm))))

    def table(self) -> pd.DataFrame:
        rows = []
        for target in range(N_DIMS):
            for source in range(N_DIMS):
                rows.append({
                    "target": DIMS[target],
                    "source": DIMS[source],
                    "alpha": self.alpha[target, source],
                    "alpha_se": self.alpha_se[target, source],
                    "norm": self.norm[target, source],
                    "decay_s": self.decay_s[source],
                    "decay_se_s": self.beta_se[source] / self.beta[source] ** 2,
                })
        return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# building the fitting sample
# --------------------------------------------------------------------------

def _decayed(times: np.ndarray, is_source: np.ndarray, beta: float,
             starts: np.ndarray) -> np.ndarray:
    """Sum of exp(-beta * (t - t_j)) over earlier source events, per event.

    An explicit loop on purpose. The closed form needs exp(beta * t), which
    overflows float64 within a second at the decay times this data produces, and
    the block-stable rewrites cost more than they save at these array sizes.
    The recursion resets at each window boundary: excitation does not carry
    across a gap the fit never observed.
    """
    n = times.size
    out = np.empty(n)
    resets = np.zeros(n, dtype=bool)
    resets[starts] = True
    running = 0.0
    previous = 0.0
    exp = np.exp
    for k in range(n):
        if resets[k]:
            running, previous = 0.0, times[k]
        running *= exp(-beta * (times[k] - previous))
        out[k] = running
        if is_source[k]:
            running += 1.0
        previous = times[k]
    return out


@dataclass
class Sample:
    """Touch events inside the fitting windows, with their baseline rates."""
    time: np.ndarray                   # event times
    dim: np.ndarray                    # 0..5
    baseline: np.ndarray               # (n, 6) queue-reactive rate at each event
    dwell: np.ndarray                  # time to the next event, for the integral
    window_start: np.ndarray           # index of the first event of each window
    window_end: np.ndarray             # end time of each window
    coverage: float
    tie_share: float = 0.0             # consecutive events sharing a timestamp

    @property
    def n(self) -> int:
        return self.time.size


def build_sample(records: SessionRecords, frame: pd.DataFrame,
                 aes: dict[int, float], *, n_windows: int = 120,
                 window_s: float = 10.0) -> Sample:
    events = records.events
    touch = events[events["level_from_best"] == 1]
    if touch.empty:
        raise SystemExit("no events at the touch")

    # dense baseline lookup: queue -> (max_n + 1, 3)
    model = fit_model(frame, records, aes)

    edges = np.linspace(RTH_OPEN, RTH_CLOSE - window_s, n_windows)
    time_all = touch["time"].to_numpy()
    queue_all = touch["queue"].to_numpy()
    etype_all = touch["event_type"].to_numpy()
    # the whole queue vector, so the touch queue on EACH side can be located at
    # every event; the compensator needs the baseline rate of all six
    # dimensions, not only the one that fired
    queue_vector = np.stack([touch[f"q{q}"].to_numpy() for q in QUEUES], axis=1)

    keep_blocks, starts, ends = [], [], []
    cursor = 0
    for left in edges:
        right = left + window_s
        lo = np.searchsorted(time_all, left, side="left")
        hi = np.searchsorted(time_all, right, side="left")
        if hi - lo < 2:
            continue
        starts.append(cursor)
        cursor += hi - lo
        keep_blocks.append(np.arange(lo, hi))
        ends.append(right)
    if not keep_blocks:
        raise SystemExit("no window held enough touch events to fit")
    keep = np.concatenate(keep_blocks)

    time = time_all[keep]
    kind = np.array([EVENT_TO_KIND.get(int(e), -1) for e in etype_all[keep]])
    dim = np.array([dim_index(k, q) for k, q in zip(kind, queue_all[keep])])
    valid = kind >= 0
    if not valid.all():
        raise SystemExit("an event at the touch had a type outside add/cancel/exec")

    # baseline rate of every dimension at every event time. The touch queue on
    # each side is the innermost non-empty one, which moves as the spread
    # changes, so the rate table is indexed per row by (queue, size).
    vector = queue_vector[keep]
    order = list(QUEUES)
    ask_cols = [order.index(q) for q in (1, 2, 3)]
    bid_cols = [order.index(q) for q in (-1, -2, -3)]

    def touch_rate(cols: list[int], queues: tuple[int, ...]) -> np.ndarray:
        sizes = np.nan_to_num(vector[:, cols])
        occupied = sizes > 0
        first = np.where(occupied.any(axis=1), occupied.argmax(axis=1), 0)
        out = np.empty((time.size, 3))
        for slot, queue in enumerate(queues):
            rows_here = first == slot
            if not rows_here.any():
                continue
            n = np.minimum(
                normalise(sizes[rows_here, slot], aes[queue]).astype(int), model.max_n)
            out[rows_here] = model.rate[queue][n]
        return out

    baseline = np.empty((time.size, N_DIMS))
    baseline[:, 0:3] = touch_rate(ask_cols, (1, 2, 3))
    baseline[:, 3:6] = touch_rate(bid_cols, (-1, -2, -3))
    baseline = np.maximum(baseline, 1e-12)

    # dwell time to the next event, truncated at the window end
    window_start = np.array(starts)
    window_end = np.array(ends)
    dwell = np.empty(time.size)
    bounds = np.r_[window_start, time.size]
    for w in range(window_start.size):
        lo, hi = bounds[w], bounds[w + 1]
        block = time[lo:hi]
        dwell[lo:hi] = np.r_[np.diff(block), window_end[w] - block[-1]]
    dwell = np.maximum(dwell, 0.0)

    span = RTH_CLOSE - RTH_OPEN
    coverage = float(window_start.size * window_s / span)
    gaps = np.diff(time)
    tie_share = float(np.mean(gaps == 0)) if gaps.size else 0.0
    return Sample(time=time, dim=dim, baseline=baseline, dwell=dwell,
                  window_start=window_start, window_end=window_end,
                  coverage=coverage, tie_share=tie_share)


# --------------------------------------------------------------------------
# likelihood
# --------------------------------------------------------------------------

def _pack(alpha: np.ndarray, beta: np.ndarray) -> np.ndarray:
    return np.r_[np.log(np.maximum(alpha, 1e-12)).ravel(), np.log(beta)]


def _unpack(theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    alpha = np.exp(theta[:N_DIMS * N_DIMS]).reshape(N_DIMS, N_DIMS)
    beta = np.exp(theta[N_DIMS * N_DIMS:])
    return alpha, beta


def negative_loglik(theta: np.ndarray, sample: Sample,
                    cache: dict | None = None) -> float:
    """Negative log-likelihood of the additive queue-reactive Hawkes model.

    Parameters are optimised in log space so alpha and beta stay positive
    without constrained optimisation, which also conditions the problem: the
    decays span several orders of magnitude.
    """
    alpha, beta = _unpack(theta)
    n = sample.n

    # decayed sums, one recursion per SOURCE dimension. The alpha matrix only
    # recombines them, so nine or thirty-six kernels still cost six recursions.
    key = tuple(np.round(beta, 10))
    if cache is not None and cache.get("key") == key:
        decayed = cache["decayed"]
    else:
        decayed = np.empty((n, N_DIMS))
        for source in range(N_DIMS):
            decayed[:, source] = _decayed(
                sample.time, sample.dim == source, beta[source], sample.window_start)
        if cache is not None:
            cache["key"], cache["decayed"] = key, decayed

    intensity = sample.baseline + decayed @ alpha.T          # (n, 6)
    at_event = intensity[np.arange(n), sample.dim]
    if not np.all(np.isfinite(at_event)) or np.any(at_event <= 0):
        return 1e18
    log_term = np.sum(np.log(at_event))

    # compensator: the baseline part is piecewise constant between events, and
    # the excitation part integrates in closed form over each window
    baseline_integral = float(np.sum(sample.baseline.sum(axis=1) * sample.dwell))
    bounds = np.r_[sample.window_start, n]
    tail = np.zeros(N_DIMS)
    for w in range(sample.window_start.size):
        lo, hi = bounds[w], bounds[w + 1]
        remaining = sample.window_end[w] - sample.time[lo:hi]
        for source in range(N_DIMS):
            here = sample.dim[lo:hi] == source
            if here.any():
                tail[source] += np.sum(1.0 - np.exp(-beta[source] * remaining[here]))
    excitation_integral = float(np.sum(alpha * (tail / beta)[None, :]))

    return -(log_term - baseline_integral - excitation_integral)


def baseline_only_loglik(sample: Sample) -> float:
    at_event = sample.baseline[np.arange(sample.n), sample.dim]
    return float(np.sum(np.log(at_event))
                 - np.sum(sample.baseline.sum(axis=1) * sample.dwell))


def fit(sample: Sample, *, seed: int = 0, maxiter: int = 300,
        standard_errors: bool = True) -> HawkesFit:
    """Maximum likelihood, warm-started from the baseline fit.

    The warm start is a weak excitation with decays spread over the range the
    data plausibly contains, rather than alpha = 0: at exactly zero the gradient
    in log-alpha vanishes and the optimiser cannot leave the baseline.
    """
    from scipy.optimize import minimize

    rng = np.random.default_rng(seed)
    rate = sample.n / max(np.sum(sample.dwell), 1e-9)
    beta0 = np.full(N_DIMS, 0.0)
    # decay times spanning a millisecond to a tenth of a second, the range the
    # inter-arrival failure lives in
    beta0 = np.array([1000.0, 300.0, 100.0, 1000.0, 300.0, 100.0])
    alpha0 = np.full((N_DIMS, N_DIMS), 0.05 * rate / N_DIMS) * (
        1.0 + 0.1 * rng.standard_normal((N_DIMS, N_DIMS)))
    alpha0 = np.maximum(alpha0, 1e-6)

    # decay bounded to [1 us, 10 s]. The lower bound is what stops the optimiser
    # exploiting tied timestamps; the upper one keeps a kernel that has not
    # decayed within a window from being fitted to the window edge.
    bounds = ([(np.log(MIN_ALPHA), np.log(MAX_ALPHA))] * (N_DIMS * N_DIMS)
              + [(np.log(1.0 / MAX_DECAY_S), np.log(1.0 / MIN_DECAY_S))] * N_DIMS)
    cache: dict = {}
    result = minimize(negative_loglik, _pack(alpha0, beta0), args=(sample, cache),
                      method="L-BFGS-B", bounds=bounds,
                      options={"maxiter": maxiter})
    alpha, beta = _unpack(result.x)

    # standard errors from a numerical Hessian of the negative log-likelihood
    # in the ORIGINAL parameters, via the log-space delta method. Perturbing an
    # alpha leaves every beta untouched, so the cached decay recursions are
    # reused for the whole alpha block and only the beta rows pay for them.
    if standard_errors:
        hessian = _numeric_hessian(result.x, sample, cache=cache)
        try:
            covariance = np.linalg.inv(hessian)
            variance = np.clip(np.diag(covariance), 0.0, None)
        except np.linalg.LinAlgError:
            variance = np.full(result.x.size, np.nan)
    else:
        variance = np.full(result.x.size, np.nan)
    scale = np.r_[alpha.ravel(), beta]
    se = np.sqrt(variance) * scale          # d(exp(x))/dx = exp(x)
    return HawkesFit(
        alpha=alpha, beta=beta,
        alpha_se=se[:N_DIMS * N_DIMS].reshape(N_DIMS, N_DIMS),
        beta_se=se[N_DIMS * N_DIMS:],
        loglik=-float(result.fun),
        baseline_loglik=baseline_only_loglik(sample),
        n_events=sample.n, n_windows=sample.window_start.size,
        coverage=sample.coverage, tie_share=sample.tie_share,
        converged=bool(result.success))


def _numeric_hessian(theta: np.ndarray, sample: Sample, step: float = 1e-4,
                     cache: dict | None = None) -> np.ndarray:
    n = theta.size
    hessian = np.zeros((n, n))
    base = negative_loglik(theta, sample, cache)
    single = np.empty(n)
    for i in range(n):
        bump = np.zeros(n)
        bump[i] = step
        single[i] = negative_loglik(theta + bump, sample, cache)
    for i in range(n):
        for j in range(i, n):
            bump = np.zeros(n)
            bump[i] += step
            bump[j] += step
            plus = negative_loglik(theta + bump, sample, cache)
            hessian[i, j] = hessian[j, i] = (
                plus - single[i] - single[j] + base) / step ** 2
    return hessian


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------

def simulate(model: FittedModel, fitted: HawkesFit, duration: float, *,
             seed: int = 0, warmup: float = 60.0, max_events: int = 4_000_000,
             cap: dict[int, int] | None = None) -> pd.DataFrame:
    """Queue-reactive dynamics with an additive Hawkes term at the touch.

    Ogata thinning. Between events the excitation only decays, so the intensity
    at the current instant bounds it over the whole waiting interval and a
    candidate can be accepted with probability lambda(t')/lambda*.
    """
    rng = np.random.default_rng(seed)
    order = list(QUEUES)
    alpha, beta = fitted.alpha, fitted.beta

    def draw(queue: int) -> int:
        weights = model.invariant.get(queue)
        return 0 if weights is None else int(rng.choice(len(weights), p=weights))

    def touch_rates(queue: int, own: int, opposite: int) -> tuple[np.ndarray, float]:
        i = min(own, model.max_n)
        if not model.coupled:
            return model.rate[queue][i], float(model.move[queue][i])
        cls = int(np.digitize(opposite, model.class_edges_n[queue]))
        return model.coupled_rate[queue][i, cls], float(model.coupled_move[queue][i, cls])

    state = {q: draw(q) for q in order}
    excite = np.zeros(N_DIMS)
    p_ref, t = 0, -warmup
    rows: list[tuple] = []
    deep_queues = [q for q in order if abs(q) != 1]

    def assemble(current: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
        """Touch rates (6), deep-queue totals (4), and the two move rates."""
        ask_rates, up = touch_rates(1, state[1], state[-1])
        bid_rates, down = touch_rates(-1, state[-1], state[1])
        touch = np.maximum(np.r_[ask_rates, bid_rates] + alpha @ current, 0.0)
        deep = np.array([model.rate[q][min(state[q], model.max_n)].sum()
                         for q in deep_queues])
        return touch, deep, up, down

    def step_up() -> dict[int, int]:
        return {1: state[2], 2: state[3], 3: draw(3),
                -1: 0, -2: state[-1], -3: state[-2]}

    def step_down() -> dict[int, int]:
        return {-1: state[-2], -2: state[-3], -3: draw(-3),
                1: 0, 2: state[1], 3: state[2]}

    while t < duration and len(rows) < max_events:
        # the excitation only decays between events, so the intensity right now
        # bounds it over the whole waiting interval: a valid thinning ceiling
        touch, deep, up, down = assemble(excite)
        ceiling = touch.sum() + deep.sum() + up + down
        if ceiling <= 0:
            break
        waited = float(rng.exponential(1.0 / ceiling))
        t += waited
        excite = excite * np.exp(-beta * waited)

        touch, deep, up, down = assemble(excite)
        total = touch.sum() + deep.sum() + up + down
        if rng.random() * ceiling > total:
            continue                      # thinned out; time advanced, no event

        u = rng.random() * total
        if u < up:
            state, p_ref = step_up(), p_ref + 1
            pick, kind = 1, 3
        elif u < up + down:
            state, p_ref = step_down(), p_ref - 1
            pick, kind = -1, 3
        else:
            weights = np.r_[touch, deep]
            choice = int(rng.choice(weights.size, p=weights / weights.sum()))
            if choice < N_DIMS:
                pick = 1 if choice < 3 else -1
                kind = choice % 3
                # only touch events feed the excitation, matching the fit
                excite[choice] += 1.0
            else:
                pick = deep_queues[choice - N_DIMS]
                row = model.rate[pick][min(state[pick], model.max_n)]
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
# driver
# --------------------------------------------------------------------------

def run(session: str, *, root: Path | None = None, n_windows: int = 120,
        window_s: float = 10.0, seed: int = 0, duration: float | None = None,
        standard_errors: bool = True, verbose: bool = True):
    """Fit and simulate one session. Returns (records, aes, model, fit, path)."""
    derived = sessions.derived(session, root)
    if not derived.exists():
        raise SystemExit(f"no cached artifacts for {session}; run build_sessions.py")

    records = build_records_from_cache(session, root)
    aes = average_event_sizes(records.events)
    frame = estimate_intensities(records, aes)
    sample = build_sample(records, frame, aes, n_windows=n_windows, window_s=window_s)
    if verbose:
        print(f"  fitting on {sample.n:,} touch events across "
              f"{sample.window_start.size} windows ({sample.coverage * 100:.1f}% of RTH)")
    fitted = fit(sample, seed=seed, standard_errors=standard_errors)
    model = fit_model(frame, records, aes,
                      coupled=estimate_coupled(records, aes))
    cap = {int(q): int(g["n"].max()) for q, g in frame.groupby("queue")}
    span = duration if duration is not None else float(RTH_CLOSE - RTH_OPEN)
    path = simulate(model, fitted, span, seed=seed, cap=cap)
    # the base-model path from the same fitted baseline and the same seed, so
    # the Turing test compares two simulators that differ only in the Hawkes
    # term rather than in their calibration or their randomness
    base_path = base_simulate(model, span, seed=seed, cap=cap)
    return records, aes, model, fitted, path, base_path


def build_records_from_cache(session: str, root: Path | None = None) -> SessionRecords:
    """Feed the npz book cache straight into the queue-reactive builder.

    `build_records` is the function the whole queue-reactive result rests on, so
    it is reused rather than reimplemented; it now takes the book in memory so
    the cache does not have to be written back out as CSV first.
    """
    return build_records(None, sessions.derived(session, root).messages,
                         verbose=False, depth=sessions.BOOK_DEPTH,
                         book=sessions.load_book(session, root))


def score_both_with_lob_bench(session: str, records: SessionRecords,
                              aes: dict[int, float], cache: Path, work_dir: Path,
                              lob_bench: Path) -> pd.DataFrame:
    """Run the LOB-Bench battery on the base and Hawkes simulators.

    Both are scored against the SAME real session with the same window count and
    the same exporter, so the two columns differ only in which simulator wrote
    the generated side. Paths come from the cached parquet the fit already
    wrote, so this does not refit anything.
    """
    from queue_reactive import export_for_lob_bench, score_with_lob_bench

    frames = {}
    for model in ("base", "hawkes"):
        path_file = cache / f"path_{model}_{session}.parquet"
        if not path_file.exists():
            raise SystemExit(f"no cached {model} path for {session}; run the fit first")
        target = work_dir / session / model
        export_for_lob_bench(records, pd.read_parquet(path_file), aes, session, target)
        frames[model] = score_with_lob_bench(target, lob_bench).set_index("statistic")

    out = pd.DataFrame({
        "statistic": frames["base"].index,
        "base_l1": frames["base"]["l1"].to_numpy(),
        "hawkes_l1": frames["hawkes"]["l1"].reindex(frames["base"].index).to_numpy(),
        "base_wasserstein": frames["base"]["wasserstein"].to_numpy(),
        "hawkes_wasserstein": frames["hawkes"]["wasserstein"].reindex(
            frames["base"].index).to_numpy(),
    })
    out.insert(0, "session", session)
    return out


def plot(out_dir: Path, sessions_done: list[str]) -> None:
    """Left: what the Hawkes term fixed. Right: the kernel it fitted."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = []
    for session in sessions_done:
        hawkes = out_dir / f"compare_{session}.csv"
        base = out_dir / f"compare_base_{session}.csv"
        if not (hawkes.exists() and base.exists()):
            continue
        key = "median inter-arrival (ms)"
        h = pd.read_csv(hawkes).set_index("statistic")
        b = pd.read_csv(base).set_index("statistic")
        rows.append({"session": session,
                     "base": b.loc[key, "simulated"] / b.loc[key, "real"],
                     "hawkes": h.loc[key, "simulated"] / h.loc[key, "real"]})
    if not rows:
        return
    table = pd.DataFrame(rows)

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8),
                             gridspec_kw={"width_ratios": [1.35, 1]})
    y = np.arange(len(table))
    axes[0].scatter(table["base"], y, marker="o", s=36, color="#8A9399", label="base")
    axes[0].scatter(table["hawkes"], y, marker="D", s=32, color="#0E5B63", label="Hawkes")
    for i, row in table.iterrows():
        axes[0].plot([row["base"], row["hawkes"]], [i, i], color="0.75",
                     linewidth=0.9, zorder=0)
    axes[0].axvline(1.0, color="0.4", linewidth=0.9, linestyle="--")
    axes[0].set_xscale("log")
    axes[0].set_yticks(y, table["session"], fontsize=7)
    axes[0].set_xlabel("simulated median inter-arrival / real (1.0 = match, log scale)")
    axes[0].set_title("What the Hawkes term fixed")
    axes[0].legend(fontsize=8, loc="lower right")
    axes[0].grid(alpha=0.25, linewidth=0.5, axis="x")

    # kernel norms, averaged over sessions: expected offspring per parent
    stack = []
    for session in sessions_done:
        path = out_dir / f"kernel_{session}.csv"
        if path.exists():
            stack.append(pd.read_csv(path).pivot(index="target", columns="source",
                                                 values="norm"))
    if stack:
        mean = sum(stack) / len(stack)
        mean = mean.reindex(index=list(DIMS), columns=list(DIMS))
        image = axes[1].imshow(mean.to_numpy(), cmap="BuPu", vmin=0)
        axes[1].set_xticks(range(N_DIMS), DIMS, rotation=45, ha="right", fontsize=7)
        axes[1].set_yticks(range(N_DIMS), DIMS, fontsize=7)
        axes[1].set_xlabel("source (the parent event)")
        axes[1].set_ylabel("target (the excited event)")
        axes[1].set_title(f"Kernel norm, mean of {len(stack)} sessions")
        for i in range(N_DIMS):
            for j in range(N_DIMS):
                value = mean.to_numpy()[i, j]
                axes[1].text(j, i, f"{value:.2f}", ha="center", va="center",
                             fontsize=6.5,
                             color="white" if value > mean.to_numpy().max() * 0.6 else "0.2")
        fig.colorbar(image, ax=axes[1], fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(out_dir / "hawkes.png", dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="append", choices=sessions.SESSIONS)
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=Path("report/hawkes"))
    ap.add_argument("--windows", type=int, default=120)
    ap.add_argument("--window-seconds", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-standard-errors", action="store_true")
    ap.add_argument("--lob-bench", type=Path, default=None,
                    help="clone of peernagy/lob_bench; scores the base and Hawkes "
                         "simulators with its own metric code")
    ap.add_argument("--lob-bench-work", type=Path, default=Path("/tmp/hawkes_lobbench"))
    ap.add_argument("--score-only", action="store_true",
                    help="reuse the cached simulated paths instead of refitting; "
                         "only useful together with --lob-bench")
    args = ap.parse_args()

    from queue_reactive import compare

    wanted = tuple(args.session) if args.session else sessions.SESSIONS
    args.out_dir.mkdir(parents=True, exist_ok=True)

    cache = args.work_dir or sessions.work_dir()

    if args.score_only:
        if not args.lob_bench:
            raise SystemExit("--score-only is only meaningful with --lob-bench")
        battery = []
        for session in wanted:
            print(session, flush=True)
            records = build_records_from_cache(session, args.work_dir)
            aes = average_event_sizes(records.events)
            table = score_both_with_lob_bench(session, records, aes, cache,
                                              args.lob_bench_work, args.lob_bench)
            table.to_csv(args.out_dir / f"lob_bench_{session}.csv", index=False)
            battery.append(table)
            print(table.to_string(index=False, float_format=lambda v: f"{v:0.4f}"),
                  flush=True)
        pd.concat(battery).to_csv(args.out_dir / "lob_bench.csv", index=False)
        print(f"saved -> {args.out_dir}")
        return 0

    summaries = []
    for session in wanted:
        print(session)
        records, aes, model, fitted, path, base_path = run(
            session, root=args.work_dir, n_windows=args.windows,
            window_s=args.window_seconds, seed=args.seed,
            standard_errors=not args.no_standard_errors)
        fitted.table().to_csv(args.out_dir / f"kernel_{session}.csv", index=False)
        table = compare(records, path, aes)
        table.to_csv(args.out_dir / f"compare_{session}.csv", index=False)
        base_table = compare(records, base_path, aes)
        base_table.to_csv(args.out_dir / f"compare_base_{session}.csv", index=False)
        # paths are large and reproducible, so they go to the gitignored work
        # directory for the Turing test to pick up rather than into report/
        path.to_parquet(cache / f"path_hawkes_{session}.parquet", index=False)
        base_path.to_parquet(cache / f"path_base_{session}.parquet", index=False)
        print(f"  tied timestamps {fitted.tie_share * 100:.1f}%")
        print(f"  spectral radius {fitted.spectral_radius:.3f}, "
              f"log-likelihood {fitted.loglik:,.0f} against baseline "
              f"{fitted.baseline_loglik:,.0f}")
        print("  decay times (s): "
              + ", ".join(f"{d}={v:.2e}" for d, v in zip(DIMS, fitted.decay_s)))
        print(table.to_string(index=False, float_format=lambda v: f"{v:0.3f}"))
        print()
        summaries.append({
            "session": session,
            "n_events": fitted.n_events,
            "coverage": fitted.coverage,
            "tie_share": fitted.tie_share,
            "spectral_radius": fitted.spectral_radius,
            "loglik": fitted.loglik,
            "baseline_loglik": fitted.baseline_loglik,
            "converged": fitted.converged,
            **{f"decay_{d}": v for d, v in zip(DIMS, fitted.decay_s)},
        })

    pd.DataFrame(summaries).to_csv(args.out_dir / "summary.csv", index=False)
    plot(args.out_dir, list(wanted))
    print(f"saved -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
