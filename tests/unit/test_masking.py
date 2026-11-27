"""Masking and the baselines (spec S6 R2.1, R4.2)."""
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_sequences import star_from
from trig_helpers import flat

from computers.ml import baselines as B
from computers.ml import masking as M
from computers.ml import sequences as S


def seq_of(n=150, length=200, seed=0, series="ac", noise=0.1):
    years, mag = flat(n, noise=noise, seed=seed)
    return S.make_sequence(star_from(years, mag, series=series), np.random.default_rng(seed), length)


def test_a_hidden_token_loses_its_value_and_error_and_keeps_time_series_and_kind():
    q = seq_of()
    m = M.apply_mask(q, np.random.default_rng(1), mode="random", fraction=0.4)
    assert (m.value_in[m.hidden] == 0).all() and (m.err_in[m.hidden] == 0).all()
    assert np.array_equal(m.seq.t, q.t) and np.array_equal(m.seq.series, q.series) and np.array_equal(m.seq.kind, q.kind)
    vis = ~m.hidden & ~q.pad
    assert np.array_equal(m.value_in[vis], q.value[vis]) and np.array_equal(m.err_in[vis], q.err[vis])


def test_padding_is_never_hidden_and_the_fraction_is_close_to_the_request():
    q = seq_of(n=120, length=200)
    m = M.apply_mask(q, np.random.default_rng(2), mode="random", fraction=0.4)
    assert not (m.hidden & q.pad).any() and m.hidden.sum() == round(0.4 * 120)


def test_the_decade_mode_hides_one_contiguous_stretch_of_at_most_a_decade_plus_random_extras():
    years = np.linspace(1890, 1980, 200)
    q = S.make_sequence(star_from(years, 12 + 0.05 * np.sin(years)), np.random.default_rng(0), 200)
    m = M.apply_mask(q, np.random.default_rng(3), mode="decade", fraction=0.3)
    assert m.hidden.sum() == 60
    # a decade holds about 22 of 200 evenly spaced tokens, so the block is about 22 and the other 38 are random
    assert m.mode == "decade"
    t = q.t[m.hidden]
    block_run = max(np.diff(np.flatnonzero(np.diff(np.r_[False, m.hidden, False].astype(int)) != 0))[::2])
    assert block_run >= 18 and (t.max() - t.min()) > 10  # a contiguous block plus scattered extras


def test_a_dense_decade_is_cut_to_the_target_so_the_fraction_never_exceeds_the_request():
    years = np.sort(np.r_[np.random.default_rng(0).uniform(1930, 1932, 150), np.linspace(1890, 1980, 50)])
    q = S.make_sequence(star_from(years, 12 + 0 * years + 0.05 * np.cos(years * 9)), np.random.default_rng(0), 200)
    m = M.apply_mask(q, np.random.default_rng(1), mode="decade", fraction=0.3)
    assert m.hidden.sum() == round(0.3 * (~q.pad).sum())


def test_the_same_seed_gives_the_same_mask_and_another_seed_a_different_one():
    q = seq_of()
    a = M.apply_mask(q, np.random.default_rng(5))
    b = M.apply_mask(q, np.random.default_rng(5))
    c = M.apply_mask(q, np.random.default_rng(6))
    assert np.array_equal(a.hidden, b.hidden) and not np.array_equal(a.hidden, c.hidden)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="mode"):
        M.apply_mask(seq_of(), np.random.default_rng(0), mode="stripes")


@settings(max_examples=60, deadline=None)
@given(n=st.integers(5, 200), seed=st.integers(0, 500), mode=st.sampled_from(M.MODES))
def test_the_masked_fraction_is_always_between_30_and_50_percent(n, seed, mode):
    q = seq_of(n=n, seed=seed % 7)
    m = M.apply_mask(q, np.random.default_rng(seed), mode=mode)
    real = int((~q.pad).sum())
    frac = m.hidden.sum() / real
    assert 0.3 - 1.0 / real <= frac <= 0.5 + 1.0 / real and not (m.hidden & q.pad).any()


# ------------------------------------------------------------------------ baselines


def test_every_baseline_is_exact_on_a_constant_star():
    years, mag = flat(120, base=12.0, noise=0.0, seed=1)
    q = S.make_sequence(star_from(years, mag), np.random.default_rng(0), 200)
    m = M.apply_mask(q, np.random.default_rng(1), mode="random", fraction=0.4)
    for name, f in B.BASELINES.items():
        assert np.allclose(f(m)[m.hidden], 0.0), name


def test_linear_interpolation_is_exact_on_a_line_and_the_median_is_not():
    years = np.linspace(1900, 1960, 120)
    q = S.make_sequence(star_from(years, 12.0 + 0.02 * (years - 1930)), np.random.default_rng(0), 200)
    m = M.apply_mask(q, np.random.default_rng(1), mode="random", fraction=0.3)
    inner = m.hidden & (q.t > q.t[~m.hidden & ~q.pad].min()) & (q.t < q.t[~m.hidden & ~q.pad].max())
    assert np.allclose(B.interpolate(m)[inner], q.value[inner], atol=1e-6)
    assert np.abs(B.median(m)[inner] - q.value[inner]).max() > 0.1


def test_nearest_in_series_uses_the_same_series_first():
    years = np.linspace(1900, 1950, 100)
    series = np.where(np.arange(100) % 2 == 0, "ac", "ee")
    mag = 12.0 + np.where(series == "ee", 0.5, 0.0)  # the two series disagree by 0.5 mag
    q = S.make_sequence(star_from(years, mag, series=series), np.random.default_rng(0), 200)
    m = M.apply_mask(q, np.random.default_rng(2), mode="random", fraction=0.3)
    pred = B.nearest_in_series(m)
    err_series = np.abs(pred[m.hidden] - q.value[m.hidden]).max()
    err_interp = np.abs(B.interpolate(m)[m.hidden] - q.value[m.hidden]).max()
    assert err_series < 0.05 < err_interp  # it follows its own series' zero point, interpolation does not


def test_a_sample_with_nothing_visible_predicts_zero_and_never_fails():
    q = seq_of(n=20, seed=3)
    m = M.Masked(q, ~q.pad, np.zeros(200), np.zeros(200), "random")  # every real token hidden
    for f in B.BASELINES.values():
        assert (f(m) == 0).all()


def test_limits_are_not_used_as_visible_detections():
    years, mag = flat(100, base=12.0, noise=0.02, seed=4)
    mag = mag.copy()
    mag[::5] = np.nan
    q = S.make_sequence(star_from(years, mag, limit=16.0), np.random.default_rng(0), 200)
    assert q.is_limit.any()
    m = M.apply_mask(q, np.random.default_rng(1), mode="random", fraction=0.3)
    assert np.abs(B.interpolate(m)[m.hidden & ~q.is_limit]).max() < 1.0  # a 4-mag-deep limit did not leak in
