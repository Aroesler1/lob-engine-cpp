"""Tests for the queue-reactive Hawkes extension.

The one that matters is recovery: a stream simulated from KNOWN alpha and beta
has to come back out of the maximum-likelihood fit. Everything about a Hawkes
likelihood is easy to get plausibly wrong, because a mis-specified compensator
or an off-by-one in the decay recursion still produces a smooth surface with a
comfortable-looking optimum somewhere near the truth.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from hawkes import (  # noqa: E402
    N_DIMS,
    Sample,
    _decayed,
    baseline_only_loglik,
    dim_index,
    fit,
    negative_loglik,
    _pack,
)


def simulate_ground_truth(mu, alpha, beta, horizon, seed=0):
    """Exact multivariate Hawkes by Ogata thinning, constant baseline.

    Deliberately a separate implementation from the one under test: if the
    simulator and the estimator shared a bug the recovery test would pass while
    both were wrong.
    """
    rng = np.random.default_rng(seed)
    dims = mu.size
    excite = np.zeros(dims)
    times, marks = [], []
    t = 0.0
    while t < horizon:
        ceiling = float((mu + alpha @ excite).sum())
        if ceiling <= 0:
            break
        t += float(rng.exponential(1.0 / ceiling))
        if t >= horizon:
            break
        decayed = excite * np.exp(-beta * (t - (times[-1] if times else 0.0)))
        intensity = mu + alpha @ decayed
        total = float(intensity.sum())
        if rng.random() * ceiling <= total:
            mark = int(rng.choice(dims, p=intensity / total))
            times.append(t)
            marks.append(mark)
            excite = decayed
            excite[mark] += 1.0
        else:
            excite = decayed
    return np.array(times), np.array(marks)


def sample_from(times, marks, mu, horizon):
    """Wrap a simulated stream in the Sample the fitter consumes."""
    baseline = np.tile(mu, (times.size, 1))
    dwell = np.r_[np.diff(times), horizon - times[-1]]
    return Sample(time=times, dim=marks, baseline=baseline, dwell=dwell,
                  window_start=np.array([0]), window_end=np.array([horizon]),
                  coverage=1.0)


@pytest.fixture(scope="module")
def ground_truth():
    """One decay time and one excitation channel, strongly identified."""
    mu = np.full(N_DIMS, 4.0)
    alpha = np.zeros((N_DIMS, N_DIMS))
    beta = np.full(N_DIMS, 50.0)
    # each dimension excites itself at a norm of 0.4
    np.fill_diagonal(alpha, 0.4 * 50.0)
    horizon = 900.0
    times, marks = simulate_ground_truth(mu, alpha, beta, horizon, seed=3)
    return mu, alpha, beta, horizon, times, marks


def test_the_simulated_stream_is_actually_clustered(ground_truth):
    """Sanity on the fixture: a Hawkes stream must be burstier than Poisson.

    If the generator produced something Poisson the recovery test below would
    be measuring nothing.
    """
    mu, alpha, beta, horizon, times, marks = ground_truth
    gaps = np.diff(times)
    # coefficient of variation is 1 for a Poisson process and above it when
    # events cluster
    cv = gaps.std() / gaps.mean()
    assert cv > 1.15, f"stream is not clustered (cv {cv:.3f})"
    assert times.size > 20_000


def test_known_parameters_are_recovered(ground_truth):
    mu, alpha, beta, horizon, times, marks = ground_truth
    sample = sample_from(times, marks, mu, horizon)
    fitted = fit(sample, standard_errors=False, maxiter=400)

    true_norm = np.diag(alpha / beta[None, :])
    got_norm = np.diag(fitted.norm)
    assert np.allclose(got_norm, true_norm, atol=0.12), (
        f"self-excitation norms {got_norm.round(3)} against {true_norm.round(3)}")

    true_decay = 1.0 / beta
    got_decay = fitted.decay_s
    assert np.allclose(got_decay, true_decay, rtol=0.60), (
        f"decay times {got_decay.round(4)} against {true_decay.round(4)}")


def test_the_fit_beats_the_baseline_on_clustered_data(ground_truth):
    mu, alpha, beta, horizon, times, marks = ground_truth
    sample = sample_from(times, marks, mu, horizon)
    fitted = fit(sample, standard_errors=False, maxiter=400)
    assert fitted.loglik > fitted.baseline_loglik
    assert fitted.loglik > baseline_only_loglik(sample)


def test_a_poisson_stream_fits_near_zero_excitation():
    """The estimator must not manufacture clustering that is not there."""
    rng = np.random.default_rng(7)
    horizon, rate = 600.0, 30.0
    n = rng.poisson(rate * horizon)
    times = np.sort(rng.uniform(0, horizon, n))
    marks = rng.integers(0, N_DIMS, n)
    mu = np.full(N_DIMS, rate / N_DIMS)
    sample = sample_from(times, marks, mu, horizon)
    fitted = fit(sample, standard_errors=False, maxiter=300)
    assert fitted.spectral_radius < 0.25, (
        f"found excitation of {fitted.spectral_radius:.3f} in a Poisson stream")


def test_decay_recursion_matches_a_direct_sum():
    """The recursion is the hot path and the easiest thing to get off by one."""
    times = np.array([0.0, 0.5, 0.9, 2.0, 2.1])
    is_source = np.array([True, False, True, True, False])
    beta = 1.7
    got = _decayed(times, is_source, beta, np.array([0]))
    for k in range(times.size):
        expected = sum(np.exp(-beta * (times[k] - times[j]))
                       for j in range(k) if is_source[j])
        assert got[k] == pytest.approx(expected, abs=1e-12), f"index {k}"


def test_decay_resets_at_a_window_boundary():
    """Windows are disjoint slices of the session. Excitation must not leak
    across a gap the fit never observed, or the second window inherits a
    phantom parent from minutes earlier."""
    times = np.array([0.0, 0.1, 100.0, 100.1])
    is_source = np.array([True, True, False, False])
    got = _decayed(times, is_source, 1.0, np.array([0, 2]))
    assert got[2] == 0.0
    assert got[3] == 0.0


def test_dimension_index_separates_side_and_kind():
    # ask side occupies 0..2, bid side 3..5, so a cross-side kernel entry is
    # distinguishable from a same-side one
    assert dim_index(0, 1) == 0 and dim_index(2, 1) == 2
    assert dim_index(0, -1) == 3 and dim_index(2, -1) == 5


def test_likelihood_rejects_a_negative_intensity():
    """A parameter vector that drives the intensity non-positive is infeasible,
    not merely unlikely; it must be refused rather than returning a NaN the
    optimiser will happily walk toward."""
    times = np.array([0.0, 1.0, 2.0])
    sample = Sample(time=times, dim=np.array([0, 0, 0]),
                    baseline=np.full((3, N_DIMS), -1.0),
                    dwell=np.array([1.0, 1.0, 1.0]),
                    window_start=np.array([0]), window_end=np.array([3.0]),
                    coverage=1.0)
    theta = _pack(np.full((N_DIMS, N_DIMS), 1e-9), np.full(N_DIMS, 1.0))
    assert negative_loglik(theta, sample) >= 1e17
