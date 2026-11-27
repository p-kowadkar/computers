"""Features of a lightcurve (spec S5 R1)."""
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from trig_helpers import flat, make_curve

from computers.l1 import triggers as T
from computers.l2.features import FEATURES, feature_vector, features

SHAPE = [f for f in FEATURES if f not in ("n_good", "span_years", "n_decades")]


def feats(years, mag, **kw):
    return features(T.prepare(make_curve(years, mag, **kw)))


def test_every_feature_name_is_present_and_in_order():
    years, mag = flat(200)
    f = feats(years, mag)
    assert tuple(f) == FEATURES and feature_vector(f).shape == (len(FEATURES),)


def test_an_empty_curve_gives_nan_and_not_zero():
    f = feats([1900.0, 1901.0], [np.nan, np.nan])
    assert f["n_good"] == 0 and all(math.isnan(f[k]) for k in SHAPE)


def test_too_few_points_give_nan_shape_features_but_real_counts():
    f = feats([1900.0, 1901.0, 1902.0], [12.0, 12.1, 11.9])
    assert f["n_good"] == 3 and f["span_years"] == pytest.approx(2.0)
    assert math.isnan(f["amp_robust"]) and math.isnan(f["skew"])


def test_a_constant_star_has_chi_square_near_one_and_few_outliers():
    years, mag = flat(600, noise=0.08, seed=3)
    f = feats(years, mag)
    assert 0.5 < f["chi2_red"] < 2.0
    assert f["frac_3sigma"] < 0.05
    assert 0.5 < f["von_neumann"] < 1.6  # white noise


def test_a_smooth_ramp_has_a_small_von_neumann_ratio_and_a_long_run():
    years = np.linspace(1900, 1950, 300)
    f = feats(years, 12.0 + 0.02 * (years - 1900) + np.random.default_rng(1).normal(0, 0.02, 300))
    assert f["von_neumann"] < 0.2 and f["longest_run"] > 0.45


def test_a_fade_has_a_positive_slope_and_a_brightening_a_negative_one():
    years = np.linspace(1890, 1970, 400)
    fade = feats(years, 12.0 + 0.01 * (years - 1890))
    bright = feats(years, 12.0 - 0.01 * (years - 1890))
    assert fade["decade_slope"] > 0 > bright["decade_slope"]  # fainter must not read as brighter
    assert fade["decade_slope"] == pytest.approx(1.0, abs=0.1)  # 0.01 mag per year is 1 mag per century


def test_a_step_gives_a_large_max_step_and_a_quiet_star_a_small_one():
    years = np.linspace(1900, 1950, 400)
    rng = np.random.default_rng(2)
    quiet = feats(years, 12.0 + rng.normal(0, 0.05, 400))
    step = feats(years, 12.0 + np.where(years > 1925, 0.8, 0.0) + rng.normal(0, 0.05, 400))
    assert step["max_step"] > 0.6 > quiet["max_step"]


def test_a_tail_towards_fainter_gives_positive_skew_and_towards_brighter_negative():
    years, mag = flat(400, noise=0.05, seed=4)
    fainter = mag.copy()
    fainter[:20] += 1.5
    brighter = mag.copy()
    brighter[:20] -= 1.5
    assert feats(years, fainter)["skew"] > 1 > -1 > feats(years, brighter)["skew"]


def test_zero_point_spread_sees_series_that_disagree():
    years, mag = flat(300, noise=0.05, seed=5)
    series = np.where(np.arange(300) % 2 == 0, "ac", "ee")
    same = feats(years, mag, series=series)
    off = feats(years, mag + np.where(series == "ee", 0.4, 0.0), series=series)
    assert off["zp_spread"] > 0.15 > same["zp_spread"]


def test_one_series_gives_nan_spread():
    years, mag = flat(100)
    assert math.isnan(feats(years, mag)["zp_spread"])


def test_features_read_only_good_points():
    years, mag = flat(200, noise=0.05, seed=6)
    bad = mag.copy()
    bad[::7] += 3.0  # wild points, all tagged as rejected
    tags = np.zeros(200, dtype=np.uint64)
    tags[::7] = 1
    from computers.l1.tags import Tag

    tags[::7] = int(Tag.STD_HARD)
    clean = feats(years, mag)
    masked = feats(years, bad, tags=tags)
    assert masked["n_good"] < 200 and masked["amp_robust"] == pytest.approx(clean["amp_robust"], abs=0.1)


@settings(max_examples=40, deadline=None)
@given(shift=st.floats(-3, 3), seed=st.integers(0, 50))
def test_shape_features_do_not_change_when_every_magnitude_shifts(shift, seed):
    years, mag = flat(250, noise=0.07, seed=seed)
    a, b = feats(years, mag), feats(years, mag + shift)
    for name in FEATURES:
        if math.isnan(a[name]):
            assert math.isnan(b[name]), name
        else:
            assert b[name] == pytest.approx(a[name], abs=1e-6, rel=1e-6), name
