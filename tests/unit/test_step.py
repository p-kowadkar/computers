import time

import numpy as np
import pytest
from trig_helpers import make_curve

from computers.l1 import seams
from computers.l1 import triggers as T


def level_curve(n_before, n_after, shift, *, change=1925.0, first=1895.0, last=1950.0, noise=0.03, seed=0, series="ac"):
    """`n_before` points before the change at level 12 and `n_after` after it at 12 + shift."""
    rng = np.random.default_rng(seed)
    y1 = np.sort(rng.uniform(first, change - 0.01, n_before))
    y2 = np.sort(rng.uniform(change, last, n_after))
    m = np.r_[12.0 + rng.normal(0, noise, n_before), 12.0 + shift + rng.normal(0, noise, n_after)]
    return make_curve(np.r_[y1, y2], m, series=series)


def steps(c, **p):
    return T.step_triggers(T.prepare(c), T.TriggerParams.from_registry().but(**p))


def test_a_clean_step_is_found_at_its_epoch_with_its_size_and_direction():
    (s,) = steps(level_curve(150, 150, 0.8))
    assert s.kind == "step" and s.direction == 1 and s.amplitude_mag == pytest.approx(0.8, abs=0.03)
    assert s.epoch_start <= 1925.05 and s.epoch_end >= 1924.95 and s.epoch_end - s.epoch_start < 1.0  # between the neighbouring points
    (b,) = steps(level_curve(150, 150, -0.8))
    assert b.direction == -1 and b.amplitude_mag == pytest.approx(0.8, abs=0.03)


def test_the_shift_boundary_on_both_sides():
    assert len(steps(level_curve(150, 150, 0.53))) == 1
    assert steps(level_curve(150, 150, 0.47)) == []
    assert len(steps(level_curve(150, 150, 0.47), step_shift_mag=0.4)) == 1  # the threshold is a parameter


def test_the_points_per_side_boundary_on_both_sides():
    assert len(steps(level_curve(20, 20, 0.9))) == 1  # exactly 20 a side
    assert steps(level_curve(20, 19, 0.9)) == []  # 39 points cannot be split 20 and 20
    assert steps(level_curve(40, 10, 0.9)) == []  # the change has only 10 points after it
    assert len(steps(level_curve(25, 25, 0.9), step_min_points=25)) == 1
    assert steps(level_curve(25, 25, 0.9), step_min_points=26) == []


def test_the_evidence_is_the_points_next_to_the_change():
    (s,) = steps(level_curve(150, 150, 0.8))
    assert len(s.evidence) == 40 and list(s.evidence) == sorted(s.evidence)


def test_a_flat_curve_has_no_step_and_noise_alone_does_not_reach_half_a_magnitude():
    for seed in range(20):
        assert steps(level_curve(200, 200, 0.0, noise=0.1, seed=seed)) == []


def test_a_step_in_the_gap_is_found_and_the_seam_rule_names_it():
    rng = np.random.default_rng(1)
    years = np.sort(rng.uniform(1890, 1985, 800))
    mag = 12.0 + np.where(years >= 1960.0, 0.8, 0.0) + rng.normal(0, 0.03, 800)
    c = make_curve(years, mag)
    found = steps(c)
    inside = [s for s in found if 1953.0 <= s.epoch_end <= 1969.0 or s.epoch_start >= 1953.0 and s.epoch_start <= 1969.0]
    assert inside, [(s.epoch_start, s.epoch_end) for s in found]
    for s in inside:
        assert seams.seam_only(c, s.epoch_end).seam_only


def test_a_step_between_two_stretches_is_returned_so_it_can_be_logged_as_a_seam_effect():
    rng = np.random.default_rng(2)
    y = np.r_[np.sort(rng.uniform(1890, 1950, 200)), np.sort(rng.uniform(1970, 1985, 100))]  # nothing in the gap
    m = 12.0 + np.where(y >= 1970.0, 0.8, 0.0) + rng.normal(0, 0.03, 300)
    c = make_curve(y, m)
    found = steps(c)
    assert len(found) == 1 and found[0].epoch_start == pytest.approx(y[199], abs=1e-6) and found[0].epoch_end == pytest.approx(y[200], abs=1e-6)
    assert seams.seam_only(c, found[0].epoch_end).reason == "across_break"


def test_a_step_that_a_series_change_explains_is_a_seam_effect():
    y = np.sort(np.random.default_rng(3).uniform(1895, 1950, 400))
    series = np.where(y < 1925.0, "ai", "ac")
    m = 12.0 + np.where(y >= 1925.0, 0.8, 0.0)
    c = make_curve(y, m, series=series)
    found = steps(c)
    assert found and seams.seam_only(c, found[0].epoch_end).seam_only


def test_the_coarse_to_fine_search_agrees_with_trying_every_split():
    rng = np.random.default_rng(4)
    for n in (60, 200, 700, 1500):
        for k in range(4):
            shift = rng.uniform(0.4, 1.5)
            c = level_curve(n // 3, n - n // 3, shift, seed=int(rng.integers(0, 10**6)), noise=0.08)
            x = c.mag
            full = min(range(20, len(x) - 19), key=lambda i: (T._split_cost(x, i), i))
            found = T._best_split(x, 20)
            assert found is not None and found[0] == full  # the true minimum, found without trying every split
            assert found[1] == pytest.approx(np.median(x[full:]) - np.median(x[:full]))


def test_a_curve_of_fifteen_hundred_points_takes_under_ten_milliseconds():
    c = level_curve(500, 1000, 0.7, noise=0.1)
    inp = T.prepare(c)
    t0 = time.perf_counter()
    for _ in range(10):
        T.step_triggers(inp)
    assert (time.perf_counter() - t0) / 10 < 0.010


def test_steps_read_the_magnitudes_they_are_given_and_say_so():
    c = level_curve(100, 100, 0.0)
    adjusted = c.mag + np.where(np.arange(len(c)) >= 100, 0.9, 0.0)
    (s,) = T.step_triggers(T.prepare(c, adjusted, detrended=True))
    assert s.detrended and s.amplitude_mag == pytest.approx(0.9, abs=0.05)
