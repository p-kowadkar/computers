import numpy as np
import pytest

from computers.bench.inject import FAMILIES, HostSpan, Signal, inject, jd_to_year, plan_injections
from computers.thresholds import THRESHOLDS

J2000 = 2451545.0


def yr(y):  # a Julian date for a decimal year
    return J2000 + (y - 2000.0) * 365.25


def test_jd_to_year_anchor():
    assert jd_to_year(J2000) == 2000.0
    assert jd_to_year(yr(1946.5)) == pytest.approx(1946.5)


# ------------------------------------------------------------- hand-computed cases


def test_step_golden_numbers():
    t = np.array([yr(1900), yr(1920), yr(1940)])
    mag = np.array([12.0, 12.0, 12.0])
    lim = np.array([15.0, 15.0, 12.5])
    out = inject(t, mag, lim, Signal("step", 1.0, t0=1930.0))
    # 1900 and 1920 unchanged; 1940 is 13.0, below its 12.5 limit, so it is lost
    assert out.mag[0] == pytest.approx(12.0) and out.mag[1] == pytest.approx(12.0)
    assert np.isnan(out.mag[2]) and out.n_lost == 1
    ok = inject(t, mag, np.array([15.0] * 3), Signal("step", 1.0, t0=1930.0))
    assert ok.mag[2] == pytest.approx(13.0)


def test_linear_golden_numbers():
    t = np.array([yr(1900), yr(1950), yr(2000)])
    out = inject(t, np.full(3, 11.0), np.full(3, 20.0), Signal("linear", 0.5, t0=1900.0))
    assert out.mag == pytest.approx([11.0, 11.25, 11.5])  # 0.5 mag per century, fading


def test_outburst_two_magnitudes_is_a_factor_of_6_3_in_flux():
    t = np.array([yr(1946.0)])
    out = inject(t, np.array([11.0]), np.array([14.0]), Signal("outburst", 2.0, t0=1946.0, scale=30.0))
    assert out.mag[0] == pytest.approx(9.0)
    assert 10 ** (-0.4 * (out.mag[0] - 11.0)) == pytest.approx(6.3096, rel=1e-4)


def test_outburst_decays_and_is_zero_before_the_start():
    t = np.array([yr(1945.9), yr(1946.0), yr(1946.0) + 30.0])  # 30 days later
    s = Signal("outburst", 4.0, t0=1946.0, scale=30.0)
    dm = s.dm(jd_to_year(t))
    assert dm[0] == 0.0 and dm[1] == pytest.approx(-4.0)
    assert dm[2] == pytest.approx(-4.0 * np.exp(-1.0), rel=1e-3)


def test_dip_has_flat_bottom_and_zero_outside():
    s = Signal("dip", 1.0, t0=1930.0, scale=10.0)
    dm = s.dm(np.array([1929.9, 1930.0, 1935.0, 1940.0, 1940.1]))
    assert dm[0] == 0 and dm[1] == pytest.approx(0) and dm[2] == pytest.approx(1.0)
    assert dm[3] == pytest.approx(0, abs=1e-12) and dm[4] == 0


def test_wave_amplitude_and_period():
    s = Signal("wave", 0.5, t0=1900.0, scale=40.0)
    assert s.dm(np.array([1910.0]))[0] == pytest.approx(0.5)
    assert s.dm(np.array([1940.0]))[0] == pytest.approx(0.0, abs=1e-12)


# ------------------------------------------------------- the limit rules, both sides


def test_fainter_than_the_limit_is_lost_and_brighter_than_it_stays():
    t = np.array([yr(1950)] * 2)
    lim = np.array([13.0, 13.0])
    out = inject(t, np.array([12.0, 12.0]), lim, Signal("step", 0.99, t0=1900.0))
    assert out.mag == pytest.approx([12.99, 12.99])  # just inside the limit
    out = inject(t, np.array([12.0, 12.0]), lim, Signal("step", 1.01, t0=1900.0))
    assert np.isnan(out.mag).all()  # just outside


def test_a_brightening_never_loses_a_point_even_one_already_below_its_limit():
    t = np.array([yr(1950)])
    out = inject(t, np.array([13.5]), np.array([13.0]), Signal("step", 0.5, t0=1900.0, sign=-1))
    assert out.mag[0] == pytest.approx(13.0) and out.n_lost == 0


def test_non_detection_stays_one_unless_the_signal_lifts_the_host_above_the_limit():
    t = np.array([yr(1900), yr(1950), yr(1950)])
    mag = np.array([12.0, np.nan, np.nan])
    lim = np.array([15.0, 11.0, 9.0])  # host median is 12.0
    out = inject(t, mag, lim, Signal("step", 2.0, t0=1940.0, sign=-1))
    # at 1950 the host would read 10.0: above the 11.0 limit, below the 9.0 limit
    assert np.isnan(out.mag[2]) and out.mag[1] == pytest.approx(10.0)
    assert out.converted.tolist() == [False, True, False] and out.n_converted == 1
    fainter = inject(t, mag, lim, Signal("step", 2.0, t0=1940.0, sign=1))
    assert np.isnan(fainter.mag[1:]).all() and fainter.n_converted == 0


def test_a_star_with_no_detections_is_returned_unchanged():
    t = np.array([yr(1900), yr(1910)])
    out = inject(t, np.full(2, np.nan), np.array([12.0, 12.0]), Signal("step", 5.0, 1890.0, sign=-1))
    assert np.isnan(out.mag).all() and out.n_converted == 0


# ------------------------------------------------------------------------ errors


def test_bad_inputs():
    with pytest.raises(ValueError, match="same length"):
        inject(np.zeros(3), np.zeros(2), np.zeros(3), Signal("step", 1.0, 1900.0))
    with pytest.raises(ValueError, match="unknown family"):
        Signal("spike", 1.0, 1900.0)
    with pytest.raises(ValueError, match="scale"):
        Signal("dip", 1.0, 1900.0)
    with pytest.raises(ValueError, match="amplitude"):
        Signal("step", -1.0, 1900.0)


# ------------------------------------------------------------------------- plan


def test_plan_size_and_keys():
    hosts = [HostSpan(1, 1890.0, 1985.0), HostSpan(2, 1895.0, 1975.0)]
    specs = plan_injections(hosts, seed=7)
    assert len(specs) == 2 * 5 * 6 * 2
    keys = {(s.host_id, s.family, s.amplitude_index, s.replicate) for s in specs}
    assert len(keys) == len(specs)
    assert {s.family for s in specs} == set(FAMILIES)


def test_plan_places_some_steps_in_the_gap_and_marks_them_expected_missed():
    gap = (THRESHOLDS["menzel_gap_start_year"].value, THRESHOLDS["menzel_gap_end_year"].value)
    specs = plan_injections([HostSpan(i, 1890.0, 1985.0) for i in range(40)], seed=3,
                            families=["step"])
    in_gap = [s for s in specs if gap[0] <= s.signal.t0 <= gap[1]]
    assert 0.2 < len(in_gap) / len(specs) < 0.6
    assert all(s.expected_missed and "Gap" in s.reason for s in in_gap)
    assert not any(s.expected_missed for s in specs if s not in in_gap)


def test_weak_fades_are_expected_missed():
    specs = plan_injections([HostSpan(1, 1890.0, 1985.0)], seed=1, families=["linear"])
    floor = THRESHOLDS["fade_floor_mag_per_century"].value
    assert any(s.expected_missed for s in specs)
    for s in specs:
        assert s.expected_missed == (s.signal.amplitude < floor)


def test_step_falls_back_when_the_host_has_no_data_in_the_gap():
    specs = plan_injections([HostSpan(1, 1890.0, 1940.0)], seed=1, families=["step"], replicates=20)
    assert all(not s.expected_missed for s in specs)
    assert all(1890.0 <= s.signal.t0 <= 1940.0 for s in specs)


def test_different_seeds_give_different_plans():
    h = [HostSpan(1, 1890.0, 1985.0)]
    assert plan_injections(h, seed=1) != plan_injections(h, seed=2)
