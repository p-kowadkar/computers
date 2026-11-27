import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from computers.l1 import seams as S
from computers.l1.curve import Curve
from computers.thresholds import THRESHOLDS

J2000 = 2451545.0


def make(years, series):
    n = len(years)
    return Curve(1, J2000 + (years - 2000.0) * 365.25, np.full(n, 12.0), np.full(n, 15.0),
                 np.array([f"e{i}" for i in range(n)]), series, np.full(n, 2, dtype=np.uint8),
                 np.zeros(n, dtype=np.uint64))


@st.composite
def curves(draw):
    n = draw(st.integers(0, 500))
    years = np.sort(np.array(draw(st.lists(st.floats(1885, 1990), min_size=n, max_size=n))))
    series = np.array(draw(st.lists(st.sampled_from(["ac", "ai", "i"]), min_size=n, max_size=n)), dtype=str)
    return make(years, series)


@given(curves(), st.floats(THRESHOLDS["menzel_gap_start_year"].value, THRESHOLDS["menzel_gap_end_year"].value))
@settings(max_examples=200, deadline=None)
def test_a_change_inside_the_menzel_gap_is_always_seam_only(c, epoch):
    r = S.seam_only(c, epoch)
    assert r.seam_only and r.reason == "in_gap"


@given(curves())
@settings(max_examples=150, deadline=None)
def test_stretch_ids_never_decrease_with_time_and_the_gap_always_cuts(c):
    s = S.stretches(c)
    assert np.all(np.diff(s.stretch) >= 0)
    y = s.year
    before, inside, after = y < 1953.0, (y >= 1953.0) & (y <= 1969.0), y > 1969.0
    for a, b in ((before, inside), (inside, after), (before, after)):
        if a.any() and b.any():
            assert s.stretch[a].max() < s.stretch[b].min()


# 600 points over 95 years is 6.3 a year: from 1905 to 1943 each side of the epoch inside the first
# stretch holds at least 50 points on average, so 20 is missed only with negligible probability.
@given(st.integers(0, 10_000), st.floats(1905.0, 1943.0))
@settings(max_examples=100, deadline=None)
def test_a_dense_single_series_curve_has_no_seam_effect_before_the_gap(seed, epoch):
    rng = np.random.default_rng(seed)
    years = np.sort(rng.uniform(1890, 1985, 600))
    c = make(years, np.array(["ac"] * 600))
    assert not S.seam_only(c, epoch).seam_only
