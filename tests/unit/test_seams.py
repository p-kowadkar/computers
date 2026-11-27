import numpy as np

from computers.l1 import seams as S
from computers.l1.curve import Curve
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

J2000 = 2451545.0


def jd(year):
    return J2000 + (np.asarray(year, dtype=float) - 2000.0) * 365.25


def curve(years, series="ac", nondet=(), tags=None):
    years = np.sort(np.asarray(years, dtype=float))
    n = len(years)
    mag = np.full(n, 12.0)
    for i in nondet:
        mag[i] = np.nan
    ser = np.array([series] * n) if isinstance(series, str) else np.asarray(series)
    return Curve(1, jd(years), mag, np.full(n, 15.0), np.array([f"e{i}" for i in range(n)]), ser,
                 np.full(n, 2, dtype=np.uint8), np.zeros(n, dtype=np.uint64) if tags is None else tags)


GAP0, GAP1 = 1953.0, 1969.0


# --------------------------------------------------------------------- stretches


def test_the_gap_cuts_a_curve_into_three_stretches_with_inclusive_edges():
    c = curve([1900, 1952.99, 1953.0, 1960, 1969.0, 1969.01, 1980])
    st = S.stretches(c)
    assert st.stretch.tolist() == [0, 0, 1, 1, 1, 2, 2]  # 1953.0 and 1969.0 are inside the gap
    assert [r for _, r in st.breaks] == ["gap_start", "gap_end"] and st.n == 3


def test_a_curve_entirely_before_the_gap_is_one_stretch():
    assert S.stretches(curve(np.linspace(1900, 1940, 50))).n == 1
    assert S.stretches(curve([])).n == 0


def mix(frac_b_late, n=300):
    """Series 'a' alone to 1940, then a share `frac_b_late` of 'b' from 1940 to 1950."""
    years = np.linspace(1900.0, 1950.0, n)
    rng = np.random.default_rng(0)
    ser = np.where((years >= 1940.0) & (rng.random(n) < frac_b_late), "b", "a")
    return years, ser


def test_a_series_that_takes_over_breaks_the_curve_at_the_window_edge():
    years = np.linspace(1900.0, 1950.0, 300)
    ser = np.where(years < 1940.0, "a", "b")
    st = S.stretches(curve(years, series=ser))
    assert (1940.0, "series") in st.breaks and st.n == 2
    assert st.stretch[np.searchsorted(years, 1939.9)] == 0 and st.stretch[np.searchsorted(years, 1940.1)] == 1


def _two_windows(prev_b, next_b, n=100):
    """Windows 1930-1935 and 1935-1940 with the given share of series b in each."""
    years = np.concatenate([np.linspace(1930, 1934.99, n), np.linspace(1935, 1939.99, n)])
    ser = np.array(["b" if i < round(n * prev_b) else "a" for i in range(n)]
                   + ["b" if i < round(n * next_b) else "a" for i in range(n)])
    return curve(years, series=ser)


def test_a_series_that_rises_from_under_a_quarter_to_over_a_half_breaks_the_curve_on_both_sides_of_the_quarter():
    # b holds 75% of the second window. It counts as new when it held under half of 0.5 (0.25) before.
    assert any(r == "series" for _, r in S.stretches(_two_windows(0.24, 0.75)).breaks)
    assert not any(r == "series" for _, r in S.stretches(_two_windows(0.25, 0.75)).breaks)
    assert not any(r == "series" for _, r in S.stretches(_two_windows(0.30, 0.75)).breaks)


def test_a_series_that_reaches_the_half_share_from_nothing_breaks_the_curve_at_exactly_half():
    assert not any(r == "series" for _, r in S.stretches(_two_windows(0.0, 0.49)).breaks)
    assert any(r == "series" for _, r in S.stretches(_two_windows(0.0, 0.50)).breaks)


def test_the_break_is_symmetric_a_series_that_vanishes_breaks_the_curve_too():
    assert any(r == "series" for _, r in S.stretches(_two_windows(0.75, 0.24)).breaks)
    assert not any(r == "series" for _, r in S.stretches(_two_windows(0.75, 0.25)).breaks)


def test_a_stable_mix_does_not_break_the_curve():
    years = np.linspace(1900.0, 1950.0, 400)
    ser = np.array(["a", "b"] * 200)  # half and half throughout
    assert S.stretches(curve(years, series=ser)).n == 1


def test_windows_with_fewer_than_ten_good_points_take_no_part():
    years = np.linspace(1930, 1945, 18)  # 3 windows of 6 points each
    ser = np.array(["a"] * 6 + ["b"] * 12)
    assert S.stretches(curve(years, series=ser)).n == 1
    assert S.stretches(curve(np.linspace(1930, 1950, 400), series=np.array(["a"] * 200 + ["b"] * 200),
                             tags=np.r_[np.full(200, int(Tag.STD_HARD)), np.zeros(200)].astype(np.uint64))).n == 1


# ----------------------------------------------------------------------- seam_only


def dense(n=400):
    return curve(np.linspace(1890.0, 1985.0, n))


def test_an_epoch_in_the_gap_is_always_seam_only_even_where_plates_exist():
    c = dense(2000)  # many plates inside the gap
    for epoch in (1953.0, 1960.0, 1969.0):
        r = S.seam_only(c, epoch)
        assert r.seam_only and r.reason == "in_gap"


def test_the_gap_edges_are_inclusive_and_just_outside_is_judged_on_the_data():
    c = dense(2000)
    assert S.seam_only(c, 1952.999999).reason != "in_gap" and S.seam_only(c, 1969.000001).reason != "in_gap"
    assert S.seam_only(c, 1953.0).reason == "in_gap" and S.seam_only(c, 1969.0).reason == "in_gap"


def test_a_clear_mid_stretch_change_is_not_a_seam_effect():
    r = S.seam_only(dense(), 1920.0)
    assert not r.seam_only and r.reason == "visible"


def test_the_minimum_points_boundary_on_both_sides():
    need = int(THRESHOLDS["seam_min_points"].value)
    assert need == 20
    for after, expected in ((20, False), (19, True)):
        years = np.r_[np.linspace(1900, 1939.9, 100), np.linspace(1941, 1952, after)]
        r = S.seam_only(curve(years), 1940.0)
        assert r.seam_only is expected, after
        assert r.reason == ("visible" if not expected else "too_few_points")
    for before, expected in ((20, False), (19, True)):
        years = np.r_[np.linspace(1900, 1939.9, before), np.linspace(1941, 1952, 100)]
        assert S.seam_only(curve(years), 1940.0).seam_only is expected, before


def test_a_change_across_a_break_is_a_seam_effect_even_with_plenty_of_points():
    years = np.r_[np.linspace(1900, 1952.9, 200), np.linspace(1953.5, 1968, 200)]
    r = S.seam_only(curve(years), 1952.95)  # the next point is in the gap stretch
    assert r.seam_only and r.reason == "across_break"


def test_a_series_change_makes_a_change_at_the_boundary_a_seam_effect():
    years = np.linspace(1900.0, 1950.0, 400)
    ser = np.where(years < 1940.0, "a", "b")
    c = curve(years, series=ser)
    assert S.seam_only(c, 1940.0).reason == "across_break"
    assert not S.seam_only(c, 1920.0).seam_only  # inside the first stretch, with points on both sides
    assert not S.seam_only(c, 1945.0).seam_only  # inside the second


def test_no_data_on_one_side():
    c = curve(np.linspace(1900, 1940, 100))
    assert S.seam_only(c, 1890.0).reason == "no_data_one_side"
    assert S.seam_only(c, 1950.0).reason == "no_data_one_side"
    assert S.seam_only(curve([]), 1920.0).seam_only


def test_non_detections_and_rejected_points_do_not_count():
    years = np.linspace(1900, 1952, 200)
    c = curve(years, nondet=range(100, 200))  # everything after 1926 is a non-detection
    assert S.seam_only(c, 1940.0).reason == "no_data_one_side"
    tags = np.zeros(200, dtype=np.uint64)
    tags[100:] = int(Tag.METEOR_REJECTED)
    assert S.seam_only(curve(years, tags=tags), 1940.0).seam_only


def test_stretches_can_be_passed_in_to_avoid_recomputing():
    c = dense()
    st = S.stretches(c)
    assert S.seam_only(c, 1920.0, st) == S.seam_only(c, 1920.0)
