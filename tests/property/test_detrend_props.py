import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from synth_tile import make_tile

from computers.l1 import detrend as D
from computers.thresholds import THRESHOLDS

N_PLATE = 120  # the requirement is stated for curves of 100 or more plates


def detrended(ctx, ref=1):
    return D.detrend(ctx, ctx.curve(ref)).mag


@given(st.integers(0, 10_000), st.integers(0, N_PLATE - 1), st.floats(-3.0, 3.0), st.floats(0.01, 0.2))
@settings(max_examples=40, deadline=None)
def test_a_constant_added_to_every_star_on_one_plate_changes_no_detrended_value_by_more_than_the_median_sensitivity(
    seed, plate, c, noise
):
    """The shift moves each star's median by about half the gap between neighbouring points,
    which is 6 to 10 times sigma/N (measured over 6 settings of N and sigma); 15 sigma/N is
    the bound. For sigma 0.1 and 120 plates that is 0.0125 mag, near the 0.01 the plan quotes."""
    shift = lambda i, j: c if j == plate else 0.0
    a = make_tile(n_plate=N_PLATE, seed=seed, noise=noise)
    b = make_tile(n_plate=N_PLATE, seed=seed, noise=noise, star_offset=shift)
    sigma = np.hypot(noise, 0.1)  # the stars' scatter about their medians: noise and the plate offsets
    assert np.max(np.abs(detrended(a) - detrended(b))) <= 15 * sigma / N_PLATE


@given(st.integers(0, 10_000), st.floats(-5.0, 5.0))
@settings(max_examples=30, deadline=None)
def test_a_constant_added_to_every_point_of_every_star_shifts_every_detrended_value_by_it(seed, c):
    a = make_tile(n_plate=N_PLATE, seed=seed)
    b = make_tile(n_plate=N_PLATE, seed=seed, star_offset=lambda i, j: c)
    assert np.allclose(detrended(b) - detrended(a), c, atol=1e-4)


@given(st.integers(0, 10_000), st.floats(0.0, 0.5))
@settings(max_examples=30, deadline=None)
def test_detrending_against_exact_copies_of_the_target_gives_a_constant(seed, plate_sigma):
    ctx = make_tile(n_plate=N_PLATE, seed=seed, noise=0.0, plate_sigma=plate_sigma, mags=[12.0] * 60)
    d = detrended(ctx)
    assert np.ptp(d) < 1e-5


@given(
    st.lists(st.floats(10.0, 14.0), min_size=2, max_size=70),
    st.data(),
)
@settings(max_examples=40, deadline=None)
def test_the_pool_is_within_the_windows_capped_nearest_first_and_never_the_target(mags, data):
    n = len(mags)
    colours = data.draw(st.lists(st.floats(0.0, 1.5), min_size=n, max_size=n))
    ctx = make_tile(n_star=n, n_plate=12, mags=mags, colours=colours)
    pool = D.select_pool(ctx, 1)
    mag_w = THRESHOLDS["comparison_mag_window"].value
    col_w = THRESHOLDS["comparison_colour_window"].value
    idx = pool.indices
    assert len(idx) <= int(THRESHOLDS["comparison_max"].value) and 0 not in idx
    assert np.all(np.abs(ctx.mag[idx] - ctx.mag[0]) <= mag_w + 1e-9)
    assert np.all(np.abs(ctx.colour[idx] - ctx.colour[0]) <= col_w + 1e-6)
    d = np.maximum(np.abs(ctx.mag[idx] - ctx.mag[0]) / mag_w, np.abs(ctx.colour[idx] - ctx.colour[0]) / col_w)
    assert np.all(np.diff(d) >= -1e-9)
