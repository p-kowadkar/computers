import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from computers.bench import inject as I
from computers.bench.inject import FAMILIES, HostSpan, Signal, inject, jd_to_year, plan_injections

JD0 = 2415020.5  # 1900
JD1 = 2447000.5  # 1987


@st.composite
def curves(draw, min_n=3, max_n=40):
    n = draw(st.integers(min_n, max_n))
    t = np.sort(np.array(draw(st.lists(st.floats(JD0, JD1), min_size=n, max_size=n))))
    mag = np.array(draw(st.lists(st.floats(8.0, 15.0), min_size=n, max_size=n)))
    limit = np.array(draw(st.lists(st.floats(10.0, 17.0), min_size=n, max_size=n)))
    nondet = np.array(draw(st.lists(st.booleans(), min_size=n, max_size=n)))
    # a real detection is never fainter than its own plate limit
    mag = np.minimum(mag, limit - 0.01)
    mag[nondet] = np.nan
    return t, mag, limit


@st.composite
def signals(draw):
    fam = draw(st.sampled_from(FAMILIES))
    amp = draw(st.floats(0.0, 8.0))
    t0 = draw(st.floats(1890.0, 1985.0))
    scale = {"linear": 0.0, "step": 0.0, "dip": draw(st.floats(1, 20)),
             "outburst": draw(st.floats(3, 120)), "wave": draw(st.floats(10, 100))}[fam]
    return Signal(fam, amp, t0, scale, draw(st.sampled_from([-1, 1])))


@given(curves(), signals())
@settings(max_examples=200, deadline=None)
def test_zero_amplitude_changes_nothing(c, s):
    t, mag, limit = c
    zero = Signal(s.family, 0.0, s.t0, s.scale, s.sign)
    out = inject(t, mag, limit, zero)
    assert np.array_equal(out.mag, mag, equal_nan=True)
    assert out.n_lost == 0 and out.n_converted == 0


@given(curves(), signals())
@settings(max_examples=200, deadline=None)
def test_inputs_are_never_changed(c, s):
    t, mag, limit = c
    before = (t.copy(), mag.copy(), limit.copy())
    inject(t, mag, limit, s)
    for a, b in zip((t, mag, limit), before, strict=True):
        assert np.array_equal(a, b, equal_nan=True)


@given(curves(), signals())
@settings(max_examples=200, deadline=None)
def test_a_point_pushed_below_its_limit_is_a_non_detection(c, s):
    t, mag, limit = c
    out = inject(t, mag, limit, s)
    dm = s.dm(jd_to_year(t))
    det = np.isfinite(mag)
    pushed = det & (dm > 0) & (mag + dm > limit + 1e-9)
    assert np.all(np.isnan(out.mag[pushed]))
    assert np.all(out.lost == (det & np.isnan(out.mag)))


@given(curves(), signals())
@settings(max_examples=200, deadline=None)
def test_a_detection_that_stays_one_is_the_host_plus_the_offset(c, s):
    t, mag, limit = c
    out = inject(t, mag, limit, s)
    keep = np.isfinite(mag) & np.isfinite(out.mag)
    expect = mag[keep] + s.dm(jd_to_year(t))[keep]
    assert np.allclose(out.mag[keep], expect, atol=1e-9, rtol=0)  # flux round trip


@given(curves(), signals())
@settings(max_examples=200, deadline=None)
def test_a_signal_that_only_brightens_never_loses_a_detection(c, s):
    t, mag, limit = c
    bright = Signal(s.family, s.amplitude, s.t0, s.scale, -1)
    if s.family != "outburst":
        out = inject(t, mag, limit, bright)
        dm = bright.dm(jd_to_year(t))
        assert out.n_lost == 0 or np.any(dm > 0)  # wave and linear can fade on one side
    out = inject(t, mag, limit, Signal("outburst", s.amplitude, s.t0, 30.0))
    assert out.n_lost == 0
    det = np.isfinite(mag)
    assert np.all(out.mag[det] <= mag[det] + 1e-9)  # brighter means a smaller number


@given(curves(), signals())
@settings(max_examples=100, deadline=None)
def test_converted_points_were_non_detections_and_are_now_inside_their_limit(c, s):
    t, mag, limit = c
    out = inject(t, mag, limit, s)
    assert np.all(np.isnan(mag[out.converted]))
    assert np.all(out.mag[out.converted] < limit[out.converted])
    assert np.all(np.isnan(out.mag[~np.isfinite(mag) & ~out.converted]))


@given(st.integers(0, 2**31), st.integers(0, 10**9))
@settings(max_examples=30, deadline=None)
def test_the_plan_is_deterministic_and_independent_of_other_hosts(seed, host_id):
    h = HostSpan(host_id, 1890.0, 1985.0)
    other = HostSpan(host_id + 1, 1895.0, 1980.0)
    a = plan_injections([h], seed=seed)
    b = plan_injections([other, h], seed=seed)
    assert a == [s for s in b if s.host_id == host_id]
    assert a == plan_injections([h], seed=seed)


@given(st.integers(0, 2**31))
@settings(max_examples=20, deadline=None)
def test_planned_signals_stay_inside_the_host_span_and_the_plan_ranges(seed):
    h = HostSpan(1, 1895.0, 1975.0)
    for s in plan_injections([h], seed=seed, n_amplitudes=3, replicates=3):
        lo, hi = I.AMPLITUDE_RANGE[s.family]
        assert lo - 1e-9 <= s.signal.amplitude <= hi + 1e-9
        if s.family != "linear":
            assert h.first_year <= s.signal.t0 <= h.last_year
        if s.family == "dip":
            assert s.signal.t0 + s.signal.scale <= h.last_year + 1e-9
        if s.family == "outburst":
            assert s.signal.sign == -1


def test_forbidden_hosts_raise():
    with pytest.raises(ValueError, match="known variables"):
        plan_injections([HostSpan(5, 1890, 1980)], seed=1, forbidden=[5])
