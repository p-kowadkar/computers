import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from computers.data import spike

NAN = float("nan")


def _curve(years, mags, reject=None):
    years = np.array(years, dtype=float)
    mags = np.array(mags, dtype=float)
    reject = np.zeros(len(years), dtype=np.int64) if reject is None else np.array(reject)
    return years, mags, reject


# ---------------------------------------------------------------- credentials


def test_api_mode_public_without_key():
    assert spike.api_mode_from_env({}) == ("public", None)
    assert spike.api_mode_from_env({"STARGLASS_API_KEY": "   "}) == ("public", None)


def test_api_mode_full_with_key_and_trims():
    assert spike.api_mode_from_env({"STARGLASS_API_KEY": " abc123 "}) == ("full", "abc123")


def test_daschlab_variable_alone_is_not_used():
    # The project variable is the only supported source of the key.
    assert spike.api_mode_from_env({"DASCHLAB_API_KEY": "abc"}) == ("public", None)


def test_redact_removes_every_occurrence():
    out = spike.redact("a SECRET b SECRET", "SECRET")
    assert "SECRET" not in out
    assert spike.redact("nothing here", None) == "nothing here"
    assert spike.redact("nothing here", "") == "nothing here"


# ---------------------------------------------------------------- T CrB check


def test_outburst_detected_when_point_is_much_brighter():
    # Quiescent near mag 10, one 1946 point at mag 6: brighter by 4 mag.
    y, m, r = _curve([1920, 1930, 1940, 1946.1], [10.0, 10.1, 9.9, 6.0])
    res = spike.tcrb_outburst_check(y, m, r)
    assert res["passed"]
    assert res["baseline_median"] == pytest.approx(10.0)
    assert res["points"][0]["brighter_by"] == pytest.approx(4.0)


def test_fainter_point_is_not_an_outburst():
    # Larger magnitude means fainter. A mag-14 point must not pass.
    y, m, r = _curve([1920, 1930, 1940, 1946.1], [10.0, 10.1, 9.9, 14.0])
    res = spike.tcrb_outburst_check(y, m, r)
    assert not res["passed"]
    assert res["points"][0]["brighter_by"] < 0


def test_margin_boundary_is_inclusive():
    y, m, r = _curve([1920, 1930, 1946.5], [10.0, 10.0, 8.0])
    assert spike.tcrb_outburst_check(y, m, r, margin=2.0)["passed"]
    assert not spike.tcrb_outburst_check(y, m, r, margin=2.01)["passed"]


@pytest.mark.parametrize(
    ("event_year", "expected"),
    [(1945.999, False), (1946.0, True), (1946.999, True), (1947.0, False)],
)
def test_only_1946_counts(event_year, expected):
    y, m, r = _curve([1920, 1930, event_year], [10.0, 10.0, 5.0])
    assert spike.tcrb_outburst_check(y, m, r)["passed"] is expected


def test_baseline_excludes_1946_and_pre_1900():
    # A bright 1946 point and a bright 1890 point must not pull the baseline up.
    y, m, r = _curve([1890, 1920, 1930, 1946.2], [5.0, 10.0, 10.0, 5.0])
    res = spike.tcrb_outburst_check(y, m, r)
    assert res["baseline_median"] == pytest.approx(10.0)
    assert res["n_baseline"] == 2


def test_non_detections_never_count():
    y, m, r = _curve([1920, 1930, 1946.2], [10.0, 10.0, NAN])
    res = spike.tcrb_outburst_check(y, m, r)
    assert not res["passed"]
    assert res["n_in_1946"] == 0


def test_rejected_points_are_excluded():
    y, m, r = _curve([1920, 1930, 1946.2], [10.0, 10.0, 5.0], reject=[0, 0, 1])
    res = spike.tcrb_outburst_check(y, m, r)
    assert not res["passed"]
    # The same curve with nothing rejected passes: this is the R5.2 raw-vs-cleaned case.
    assert spike.tcrb_outburst_check(y, m, np.zeros(3, dtype=int))["passed"]


def test_no_baseline_fails_with_reason():
    y, m, r = _curve([1946.2], [5.0])
    res = spike.tcrb_outburst_check(y, m, r)
    assert not res["passed"]
    assert "1900" in res["reason"]


def test_no_1946_points_fails_with_reason():
    y, m, r = _curve([1920, 1930], [10.0, 10.0])
    res = spike.tcrb_outburst_check(y, m, r)
    assert not res["passed"]
    assert "1946" in res["reason"]


@given(
    base=st.floats(min_value=5, max_value=15),
    delta=st.floats(min_value=-5, max_value=5),
)
def test_direction_property(base, delta):
    """A 1946 point `delta` mags brighter passes exactly when delta >= margin."""
    y, m, r = _curve([1920, 1930, 1946.5], [base, base, base - delta])
    res = spike.tcrb_outburst_check(y, m, r, margin=2.0)
    assert res["passed"] == (res["points"][0]["brighter_by"] >= 2.0)


# ------------------------------------------------------------------- summarize


def test_summarize_counts():
    y = np.array([1900, 1910, 1920, 1930.0])
    m = np.array([10.0, NAN, 11.0, 12.0])
    series = ["a", "a", "b", "a"]
    r_std = np.array([0, 0, 1, 0])
    r_ext = np.array([0, 0, 1, 2])
    s = spike.summarize(y, m, series, r_std, r_ext)
    assert s["rows"] == 4
    assert s["detections"] == 3
    assert s["non_detections"] == 1
    assert s["series"] == {"a": 3, "b": 1}
    assert s["rejected_standard"] == 1
    assert s["rejected_after_extra"] == 2
    assert s["good_detections_standard"] == 2
    assert s["good_detections_after_extra"] == 1
    assert s["median_detected_mag"] == pytest.approx(11.0)
    assert (s["first_year"], s["last_year"]) == (1900.0, 1930.0)


def test_summarize_rejects_inconsistent_stages():
    with pytest.raises(ValueError, match="stay rejected"):
        spike.summarize([1900.0], [10.0], ["a"], np.array([1]), np.array([0]))


def test_summarize_length_mismatch():
    with pytest.raises(ValueError, match="same length"):
        spike.summarize([1900.0, 1901.0], [10.0], ["a"], np.array([0]), np.array([0]))


def test_summarize_empty_and_all_non_detections():
    s = spike.summarize([], [], [], np.array([], dtype=int), np.array([], dtype=int))
    assert s["rows"] == 0
    assert s["median_detected_mag"] is None
    s2 = spike.summarize([1900.0], [NAN], ["a"], np.array([0]), np.array([0]))
    assert s2["detections"] == 0
    assert s2["median_detected_mag"] is None


# ------------------------------------------------------------------ separation


def test_separation_known_values():
    # One arcsecond in declination.
    sep = spike.angular_separation_arcsec(10.0, 20.0, 10.0, 20.0 + 1 / 3600)
    assert float(sep) == pytest.approx(1.0, rel=1e-6)
    # RA offsets shrink with cos(dec).
    sep = spike.angular_separation_arcsec(10.0, 60.0, 10.0 + 1 / 3600, 60.0)
    assert float(sep) == pytest.approx(0.5, rel=1e-4)


def test_separation_report_orders_and_warns():
    ra = np.array([100.0, 100.0, 100.0, 100.0])
    dec = np.array([20.01, 20.0005, 20.002, 20.0])
    rep = spike.separation_report(ra, dec, 100.0, 20.0)
    assert [n["index"] for n in rep["nearest"]] == [3, 1, 2]
    assert not rep["warn"]
    far = spike.separation_report(ra[:1], dec[:1], 100.0, 20.0)
    assert far["warn"]  # 36 arcsec away
    assert spike.separation_report([], [], 0.0, 0.0)["warn"]


def test_separation_report_ties_are_deterministic():
    ra = np.array([10.0, 10.0, 10.0])
    dec = np.array([5.001, 5.001, 5.001])
    rep = spike.separation_report(ra, dec, 10.0, 5.0)
    assert [n["index"] for n in rep["nearest"]] == [0, 1, 2]


# ------------------------------------------------------------------ quiet star


def _cand(ref, b, sep, n):
    return {"ref_number": ref, "b_mag": b, "sep_arcmin": sep, "n_detections": n}


def test_shortlist_filters_and_keeps_top_quartile():
    cands = [
        _cand(1, 12.0, 10, 100),
        _cand(2, 12.0, 10, 200),
        _cand(3, 12.0, 10, 300),
        _cand(4, 12.0, 10, 400),
        _cand(5, 10.0, 10, 999),  # too bright
        _cand(6, 14.0, 10, 999),  # too faint
        _cand(7, 12.0, 1.0, 999),  # too close to the target
        _cand(8, None, 10, 999),  # no B magnitude
    ]
    short = spike.quiet_star_shortlist(cands)
    # 75th percentile of 100,200,300,400 is 325, so only ref 4 stays.
    assert [c["ref_number"] for c in short] == [4]


def test_shortlist_empty():
    assert spike.quiet_star_shortlist([]) == []
    assert spike.quiet_star_shortlist([_cand(1, 20.0, 10, 5)]) == []


def test_pick_quiet_star_needs_plain_star_type():
    short = [_cand(30, 12, 10, 5), _cand(10, 12, 10, 5), _cand(20, 12, 10, 5), _cand(40, 12, 10, 5)]
    otypes = {10: "V*", 20: "*", 30: "PM*", 40: None}
    pick = spike.pick_quiet_star(short, otypes)
    assert pick["chosen"]["ref_number"] == 20
    assert [a["ref_number"] for a in pick["alternates"]] == [30]


def test_pick_quiet_star_none_when_all_variable():
    assert spike.pick_quiet_star([_cand(1, 12, 10, 5)], {1: "EB*"}) is None
    assert spike.pick_quiet_star([_cand(1, 12, 10, 5)], {}) is None


def test_pick_quiet_star_is_order_independent():
    short = [_cand(3, 12, 10, 5), _cand(1, 12, 10, 5), _cand(2, 12, 10, 5)]
    otypes = {1: "*", 2: "*", 3: "*"}
    a = spike.pick_quiet_star(short, otypes)
    b = spike.pick_quiet_star(list(reversed(short)), otypes)
    assert a == b
    assert a["chosen"]["ref_number"] == 1


# --------------------------------------------------------------- cutout offset


def test_cutout_offset_centre_of_835_pixels():
    # Pixel 417 is the exact centre of an 835-pixel axis.
    off = spike.cutout_offset(417, 417, (835, 835))
    assert off == {"dx": 0.0, "dy": 0.0, "radius": 0.0}


def test_cutout_offset_values_and_axes():
    off = spike.cutout_offset(420, 410, (835, 835))
    assert off["dx"] == 3 and off["dy"] == -7
    assert off["radius"] == pytest.approx(np.hypot(3, 7))
    # shape is (ny, nx): x pairs with the second entry.
    off = spike.cutout_offset(10, 0, (5, 21))
    assert off == {"dx": 0.0, "dy": -2.0, "radius": 2.0}


# --------------------------------------------------------------------- timing


def test_timing_stats_ignores_failed_requests_for_median():
    recs = [
        {"seconds": 1.0, "ok": True},
        {"seconds": 3.0, "ok": True},
        {"seconds": 0.1, "ok": False},
        {"seconds": 2.0, "ok": True},
    ]
    st_ = spike.timing_stats(recs)
    assert st_ == {"n": 4, "n_failed": 1, "median_seconds": 2.0, "max_seconds": 3.0}


def test_timing_stats_all_failed():
    st_ = spike.timing_stats([{"seconds": 0.5, "ok": False}])
    assert st_["median_seconds"] is None and st_["n_failed"] == 1
    assert spike.timing_stats([])["n"] == 0
