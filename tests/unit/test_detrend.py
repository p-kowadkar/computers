import warnings

import numpy as np
import pytest
from synth_tile import make_tile

from computers.l1 import detrend as D
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

NAN = np.nan


# ------------------------------------------------------------ the arithmetic, by hand


def hand_residuals():
    return np.array([
        [0.1, -0.2, 0.0, 0.3, NAN, 0.0],
        [0.2, -0.1, 0.1, 0.2, NAN, NAN],
        [0.0, -0.3, 0.2, 0.4, NAN, 0.1],
        [0.3, -0.2, -0.1, 0.1, 0.5, 0.2],
        [0.1, -0.2, 0.0, 0.3, 0.6, NAN],
    ])


def test_plate_offsets_are_the_median_residual_per_plate():
    offset, count = D.plate_offsets(hand_residuals(), 5)
    assert count.tolist() == [5, 5, 5, 5, 2, 3]
    assert offset[:4] == pytest.approx([0.1, -0.2, 0.0, 0.3])  # worked out by hand
    assert np.isnan(offset[4:]).all()  # plates seen by fewer than 5 stars have no offset


def test_the_per_plate_minimum_is_inclusive_on_both_sides():
    off3, _ = D.plate_offsets(hand_residuals(), 3)
    assert off3[5] == pytest.approx(0.1) and np.isnan(off3[4])  # 3 stars on plate 5 meet a minimum of 3
    off4, _ = D.plate_offsets(hand_residuals(), 4)
    assert np.isnan(off4[5])  # but not a minimum of 4
    off2, _ = D.plate_offsets(hand_residuals(), 2)
    assert off2[4] == pytest.approx(0.55)


def test_an_empty_plate_has_no_offset_and_does_not_warn():
    r = np.full((5, 2), NAN)
    r[:, 0] = [0.1, 0.2, 0.3, 0.4, 0.5]
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an all-NaN plate must not raise a RuntimeWarning
        offset, count = D.plate_offsets(r, 5)
    assert offset[0] == pytest.approx(0.3) and np.isnan(offset[1]) and count.tolist() == [5, 0]


# ------------------------------------------------------------------- the pool


def pool_tile(mags, colours, quiet=None, **kw):
    ctx = make_tile(n_star=len(mags), mags=mags, colours=colours, n_plate=30, **kw)
    if quiet is not None:
        ctx.quiet = np.array(quiet, dtype=bool)
    return ctx


def pool_refs(ctx, ref):
    return sorted(ctx.refs[i] for i in D.select_pool(ctx, ref).indices)


def test_the_magnitude_window_is_inclusive_and_both_sides_are_tested():
    # star 1 is the target at B 12.0; the window is 0.5 mag on each side
    ctx = pool_tile([12.0, 12.5, 12.51, 11.5, 11.49], [0.6] * 5)
    assert pool_refs(ctx, 1) == [2, 4]  # 12.5 and 11.5 are inside; 12.51 and 11.49 are out


def test_the_colour_window_is_inclusive_and_both_sides_are_tested():
    ctx = pool_tile([12.0] * 5, [0.5, 0.75, 0.76, 0.25, 0.24])
    pool = D.select_pool(ctx, 1)
    assert pool.colour_cut and sorted(ctx.refs[i] for i in pool.indices) == [2, 4]


def test_a_target_without_colour_uses_magnitude_only_and_says_so():
    ctx = pool_tile([12.0, 12.0, 12.0], [NAN, 0.1, 1.4])
    pool = D.select_pool(ctx, 1)
    assert not pool.colour_cut and sorted(ctx.refs[i] for i in pool.indices) == [2, 3]


def test_a_comparison_without_colour_is_left_out_when_the_target_has_one():
    ctx = pool_tile([12.0, 12.0, 12.0], [0.6, NAN, 0.6])
    assert pool_refs(ctx, 1) == [3]


def test_the_pool_never_holds_the_target_or_a_star_that_is_not_quiet():
    ctx = pool_tile([12.0] * 4, [0.6] * 4, quiet=[True, True, False, True])
    assert pool_refs(ctx, 1) == [2, 4] and pool_refs(ctx, 3) == [1, 2, 4]


def test_the_pool_is_capped_and_takes_the_nearest_with_ties_by_reference():
    n = 60
    mags = [12.0] + [12.0 + 0.4 * (i % 2) * (i / n) for i in range(1, n)]  # a spread of distances
    ctx = pool_tile(mags, [0.6] * n)
    pool = D.select_pool(ctx, 1)
    assert len(pool.indices) == int(THRESHOLDS["comparison_max"].value) == 40
    d = np.abs(ctx.mag - ctx.mag[0])
    chosen = set(pool.indices.tolist())
    farthest_in = max(d[i] for i in chosen)
    assert all(d[i] >= farthest_in - 1e-9 for i in range(1, n) if i not in chosen)
    assert np.all(np.diff(d[pool.indices]) >= -1e-12)  # nearest first


# ---------------------------------------------------------------- detrending a star


def test_the_target_loses_the_plate_systematic_and_keeps_its_own_signal():
    step = lambda i, j: 1.0 if (i == 0 and j >= 60) else 0.0
    ctx = make_tile(star_offset=step, plate_sigma=0.15, noise=0.02, seed=1)
    d = D.detrend(ctx, ctx.curve(1))
    raw = ctx.curve(1).mag
    assert d.detrended and d.kill_reason is None and d.offsets.pool_size == 40
    early, late = slice(0, 60), slice(60, 120)
    assert np.std(d.mag[early]) < 0.3 * np.std(raw[early])  # the plate noise is gone
    assert np.mean(d.mag[late]) - np.mean(d.mag[early]) == pytest.approx(1.0, abs=0.03)  # the step stays


def test_the_detrended_value_is_the_magnitude_minus_the_plate_offset():
    ctx = make_tile(seed=2)
    c = ctx.curve(5)
    d = D.detrend(ctx, c)
    cols = ctx.columns_of(c)
    assert np.allclose(d.mag, c.mag - d.offsets.offset[cols], atol=1e-12)
    assert np.allclose(d.limit, c.limit - d.offsets.offset[cols], atol=1e-12)


def test_too_few_comparisons_gives_the_raw_curve_and_the_kill_reason():
    ctx = make_tile(n_star=10, n_plate=30)  # a pool of 9 is under the minimum of 10
    d = D.detrend(ctx, ctx.curve(1))
    assert d.kill_reason == "few_comparisons" and not d.detrended and d.offsets.pool_size == 9
    assert np.array_equal(d.mag, ctx.curve(1).mag, equal_nan=True)
    assert d.curve.tag_counts()["NO_OFFSET"] == 0
    ok = D.detrend(make_tile(n_star=11, n_plate=30), make_tile(n_star=11, n_plate=30).curve(1))
    assert ok.kill_reason is None and ok.offsets.pool_size == 10  # exactly the minimum is enough


def test_a_plate_with_too_few_comparisons_has_no_offset_and_its_points_are_tagged():
    sparse = lambda i, j: j == 7 and i >= 4
    ctx = make_tile(missing=sparse, n_star=60, n_plate=120)
    d = D.detrend(ctx, ctx.curve(1))  # four comparisons on plate 7 is under the minimum of 5
    c = d.curve
    col = int(np.flatnonzero(c.exposure_id == "ac00007e1")[0])
    assert c.has(Tag.NO_OFFSET)[col] and np.isnan(d.mag[col]) and np.isnan(d.limit[col])
    assert c.tag_counts()["NO_OFFSET"] == 1 and np.isfinite(d.mag).sum() == len(c) - 1
    assert d.offsets.count[7] == 3  # stars 2 to 4: the target itself is not a comparison


def test_the_offsets_are_cached_and_an_injected_curve_reuses_them():
    ctx = make_tile(seed=3)
    off = D.offsets_for(ctx, 3)
    assert D.offsets_for(ctx, 3) is off and ctx.offsets_cache[3] is off
    c = ctx.curve(3)
    injected = c.with_magnitudes(c.mag + np.where(np.arange(len(c)) >= 60, 0.8, 0.0))
    d = D.detrend(ctx, injected)
    assert d.offsets is off
    plain = D.detrend(ctx, c)
    assert np.allclose(d.mag - plain.mag, np.where(np.arange(len(c)) >= 60, 0.8, 0.0))


def test_an_injection_in_the_target_does_not_change_the_comparison_offsets():
    ctx = make_tile(seed=4)
    before = D.offsets_for(ctx, 2).offset.copy()
    c = ctx.curve(2)
    D.detrend(ctx, c.with_magnitudes(c.mag + 3.0))
    assert np.array_equal(D.offsets_for(ctx, 2).offset, before, equal_nan=True)
    # but the same star as a comparison for another target keeps its original curve
    assert np.isfinite(ctx.matrix[ctx.index[2]]).all()


def test_series_zero_points_are_reported_but_not_subtracted():
    ser = lambda j: "ac" if j < 60 else "i"
    shift = lambda i, j: 0.5 if (i == 0 and j >= 60) else 0.0
    ctx = make_tile(series=ser, star_offset=shift, plate_sigma=0.02, noise=0.01, seed=5)
    d = D.detrend(ctx, ctx.curve(1))
    zp = d.zero_points
    assert set(zp) == {"ac", "i"} and zp["ac"][1] == 60 == zp["i"][1]  # ac good points are 60, i are 60
    assert zp["i"][0] - zp["ac"][0] == pytest.approx(0.5, abs=0.05)
    assert np.mean(d.mag[60:]) - np.mean(d.mag[:60]) == pytest.approx(0.5, abs=0.05)  # the step is still there


def test_non_detections_get_the_same_shift_on_their_limit():
    ctx = make_tile(seed=6)
    c = ctx.curve(4)
    nd = c.with_magnitudes(np.where(np.arange(len(c)) % 10 == 0, np.nan, c.mag))
    d = D.detrend(ctx, nd)
    off = d.offsets.offset[ctx.columns_of(nd)]
    assert np.isnan(d.mag[::10]).all() and np.allclose(d.limit, nd.limit - off, equal_nan=True)
