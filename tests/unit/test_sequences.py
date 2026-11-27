"""Tokens, sampling and the dataset builder (spec S6 R1)."""
import json

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from trig_helpers import flat, make_curve

from computers.l1.tags import Tag
from computers.ml import dataset as D
from computers.ml import sequences as S


def star_from(years, mag, *, limit=15.0, tags=None, series="ac", err=0.1, shift=0.0, scale=1.0):
    curve = make_curve(years, mag, series=series, limit=limit, tags=tags)
    m = (curve.mag - 12.0) * scale + 12.0 + shift
    lim = np.full(len(curve), limit + shift)
    errs = {e: err for e in curve.exposure_id}
    return D.star_from_curve("t", curve, m, lim, errs, detrended=True, min_good=5)


def test_a_star_is_the_median_subtracted_and_a_constant_shift_changes_nothing():
    years, mag = flat(120, base=12.0, noise=0.1, seed=1)
    a, b = star_from(years, mag), star_from(years, mag, shift=2.5)
    assert np.allclose(a.value, b.value) and a.median_mag + 2.5 == pytest.approx(b.median_mag)
    assert abs(np.median(a.value[~a.is_limit])) < 1e-9


def test_amplitude_is_kept_scaling_the_star_scales_the_tokens():
    years, mag = flat(120, noise=0.1, seed=2)
    a, b = star_from(years, mag), star_from(years, mag, scale=3.0)
    det = ~a.is_limit
    assert np.allclose(b.value[det], 3.0 * a.value[det])  # not divided by scatter


def test_a_non_detection_carries_its_limit_depth_and_no_magnitude():
    years, mag = flat(100, base=12.0, noise=0.05, seed=3)
    mag = mag.copy()
    mag[::10] = np.nan
    s = star_from(years, mag, limit=14.0)
    lim = s.is_limit
    assert lim.sum() == 10 and np.allclose(s.value[lim], 14.0 - s.median_mag)  # fainter than the median: positive depth
    assert (s.value[lim] > 0).all()


def test_rejected_rows_and_non_detections_with_a_rejecting_tag_are_not_in_the_star():
    years, mag = flat(100, noise=0.05, seed=4)
    mag = mag.copy()
    mag[5] = np.nan
    tags = np.zeros(100, dtype=np.uint64)
    tags[10] = int(Tag.STD_HARD)  # a detection that cleaning rejected
    tags[5] = int(Tag.STD_HARD)  # a non-detection that cleaning rejected
    s = star_from(years, mag, tags=tags)
    assert len(s) == 98 and s.is_limit.sum() == 0


def test_too_few_good_detections_gives_none():
    years, mag = flat(4)
    assert star_from(years, mag) is None


def test_missing_errors_are_filled_with_the_stars_median_and_floored():
    years, mag = flat(60, noise=0.05, seed=5)
    curve = make_curve(years, mag)
    errs = {e: (0.2 if i % 2 else 0.001) for i, e in enumerate(curve.exposure_id)}
    del errs[curve.exposure_id[0]]
    s = D.star_from_curve("t", curve, curve.mag, curve.limit, errs, detrended=False, min_good=5)
    assert s.err.min() >= S.ERR_FLOOR and np.isfinite(s.err).all()


def test_series_and_kind_are_integer_codes_and_an_unknown_series_is_zero():
    assert S.series_id("zzzzz") == 0 and S.series_id("ac") > 0
    assert S.series_id("ac") == S.series_id("ac") and S.series_id("ac") != S.series_id("ee")
    years, mag = flat(50)
    s = star_from(years, mag, series="ac")
    assert (s.series == S.series_id("ac")).all() and (s.kind == 2).all()


def test_time_features_have_two_per_scale_and_are_bounded():
    f = S.time_features(np.array([0.0, 10.0, 100.0]))
    assert f.shape == (3, 2 * len(S.TIME_SCALES)) and np.abs(f).max() <= 1.0
    assert np.allclose(f[0, : len(S.TIME_SCALES)], 0.0) and np.allclose(f[0, len(S.TIME_SCALES) :], 1.0)


# ------------------------------------------------------------------------ sampling


def test_a_short_star_keeps_every_row_and_is_padded_at_the_end():
    years, mag = flat(60, seed=6)
    s = star_from(years, mag)
    q = S.make_sequence(s, np.random.default_rng(0), length=80)
    assert (~q.pad).sum() == 60 and q.pad[60:].all() and not q.pad[:60].any()
    assert (q.index[:60] == np.arange(60)).all() and (q.index[60:] == -1).all()
    assert np.all(np.diff(q.t[:60]) >= 0)


def test_a_long_star_is_sampled_across_the_whole_century_not_the_dense_decade():
    rng = np.random.default_rng(1)
    dense = rng.uniform(1930, 1935, 3000)  # most rows in five years
    sparse = np.r_[rng.uniform(1890, 1925, 60), rng.uniform(1960, 1985, 60)]
    year = np.sort(np.r_[dense, sparse])
    idx = S.sample_indices(year, np.random.default_rng(2), 200)
    assert len(idx) == 200 and len(set(idx.tolist())) == 200 and np.all(np.diff(idx) > 0)
    picked = year[idx]
    # 120 sparse rows fall in about 65 distinct bins and each bin is represented; a uniform draw would give about 8
    assert ((picked < 1926) | (picked > 1959)).sum() >= 55
    assert ((picked >= 1930) & (picked <= 1935)).sum() <= 145  # the dense five years are thinned from 3,000 rows


def test_fresh_draws_differ_and_the_same_seed_repeats():
    year = np.sort(np.random.default_rng(3).uniform(1890, 1980, 2000))
    a = S.sample_indices(year, np.random.default_rng(10))
    assert np.array_equal(a, S.sample_indices(year, np.random.default_rng(10)))
    assert not np.array_equal(a, S.sample_indices(year, np.random.default_rng(11)))


@settings(max_examples=40, deadline=None)
@given(n=st.integers(1, 800), length=st.integers(5, 120), seed=st.integers(0, 1000))
def test_a_sample_never_exceeds_the_length_never_repeats_a_row_and_stays_in_time_order(n, length, seed):
    year = np.sort(np.random.default_rng(seed).uniform(1885, 1989, n))
    idx = S.sample_indices(year, np.random.default_rng(seed), length)
    assert len(idx) == min(n, length) and len(set(idx.tolist())) == len(idx)
    assert np.all(np.diff(idx) > 0) and idx.min() >= 0 and idx.max() < n


# ------------------------------------------------------------------------- the spec


def test_the_spec_is_versioned_hashed_and_stable():
    assert S.SPEC["version"] == S.SPEC_VERSION and S.SPEC["length"] == 200
    assert json.loads(S.spec_json()) == json.loads(json.dumps(S.SPEC))
    assert S.spec_hash() == S.spec_hash() and len(S.spec_hash()) == 16


def test_saved_stars_round_trip_and_a_spec_mismatch_is_refused(tmp_path, monkeypatch):
    years, mag = flat(80, seed=7)
    stars = [star_from(years, mag), star_from(years, mag + 0.3, series="ee")]
    D.save_stars(stars, tmp_path / "s.npz")
    back = D.load_stars(tmp_path / "s.npz")
    assert len(back) == 2 and np.array_equal(back[1].value, stars[1].value) and back[1].series[0] == stars[1].series[0]
    monkeypatch.setattr(S, "SPEC", {**S.SPEC, "length": 123})
    with pytest.raises(ValueError, match="rebuild"):
        D.load_stars(tmp_path / "s.npz")


def test_star_rows_must_be_in_time_order():
    z = np.zeros(3)
    with pytest.raises(ValueError, match="time order"):
        S.Star("t", 1, np.array([1900.0, 1899.0, 1901.0]), z, z, z.astype(bool), z.astype(np.int16), z.astype(np.uint8), 12.0, True)
