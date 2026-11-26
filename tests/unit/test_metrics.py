import math

import numpy as np
import pytest

from computers.bench import metrics as M
from computers.bench.harness import HostCurve, false_alarms, run_injections
from computers.bench.inject import HostSpan, Signal, SignalSpec, plan_injections
from computers.bench.trivial import TrivialDetector

J2000 = 2451545.0


def jd(year):
    return J2000 + (np.asarray(year) - 2000.0) * 365.25


# ------------------------------------------------------------------------ wilson


@pytest.mark.parametrize(
    ("k", "n", "lo", "hi"),
    [(0, 10, 0.0, 0.2775), (10, 10, 0.7225, 1.0), (5, 10, 0.2366, 0.7634), (50, 100, 0.4038, 0.5962)],
)
def test_wilson_matches_published_values(k, n, lo, hi):
    got = M.wilson(k, n)
    assert got[0] == pytest.approx(lo, abs=2e-4) and got[1] == pytest.approx(hi, abs=2e-4)


def test_wilson_edges():
    assert M.wilson(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        M.wilson(5, 4)


def test_wilson_interval_narrows_with_n_and_contains_the_fraction():
    a, b = M.wilson(30, 100), M.wilson(300, 1000)
    assert a[0] < 0.3 < a[1] and b[0] < 0.3 < b[1] and (b[1] - b[0]) < (a[1] - a[0])


# ----------------------------------------------------------------- amplitude_at


def test_crossing_interpolates_in_log_amplitude():
    # 0.25 at amplitude 1 and 0.75 at amplitude 4: half way in log is amplitude 2
    assert M.amplitude_at([1.0, 4.0], [0.25, 0.75], 0.5) == pytest.approx(2.0)


def test_crossing_edges():
    assert M.amplitude_at([1, 2, 3], [0.1, 0.3, 0.4], 0.5) is None  # never reached
    assert M.amplitude_at([1, 2, 3], [0.6, 0.7, 0.8], 0.5) == 1.0  # reached by the first bin
    assert M.amplitude_at([1, 2, 3], [0.5, 0.5, 0.9], 0.5) == 1.0  # exactly at the level
    assert M.amplitude_at([1, 2, 3], [0.499, 0.5, 0.9], 0.5) == 2.0


def test_crossing_uses_a_monotone_envelope_and_sorts_by_amplitude():
    assert M.amplitude_at([1, 2, 4], [0.2, 0.9, 0.4], 0.9) == pytest.approx(2.0)
    assert M.amplitude_at([4, 1, 2], [0.9, 0.2, 0.6], 0.5) == pytest.approx(
        math.exp(math.log(1) + (0.3 / 0.4) * math.log(2))
    )
    with pytest.raises(ValueError):
        M.amplitude_at([1, 2], [0.1], 0.5)


# ------------------------------------------------------------------ completeness


def outcomes(family, index, amp, k, n, missed=False):
    return [M.Outcome(i, family, index, amp, missed, i < k) for i in range(n)]


def test_completeness_bins_counts_and_reliability():
    o = outcomes("step", 0, 0.2, 2, 20) + outcomes("step", 1, 0.5, 18, 20) + outcomes("step", 2, 1.0, 9, 9)
    bins = M.completeness(o)
    assert [(b.amplitude_index, b.n, b.k) for b in bins] == [(0, 20, 2), (1, 20, 18), (2, 9, 9)]
    assert [b.reliable for b in bins] == [True, True, False]  # 9 is under the minimum of 10
    assert bins[1].fraction == 0.9 and bins[1].low < 0.9 < bins[1].high


def test_expected_missed_signals_are_left_out_of_completeness_and_counted_apart():
    o = outcomes("step", 0, 0.2, 10, 10) + outcomes("step", 0, 0.2, 3, 5, missed=True)
    assert M.completeness(o)[0].n == 10
    assert M.suspect_recoveries(o) == (3, 5)
    assert M.suspect_recoveries(outcomes("step", 0, 0.2, 10, 10)) == (0, 0)


def test_crossings_skip_unreliable_bins_and_report_not_reached():
    o = (outcomes("step", 0, 0.2, 1, 20) + outcomes("step", 1, 0.4, 19, 20)
         + outcomes("step", 2, 0.8, 5, 5) + outcomes("dip", 0, 0.3, 0, 20))
    c = {(x.family, x.level): x for x in M.crossings(M.completeness(o))}
    assert 0.2 < c[("step", 0.5)].estimate < 0.4
    assert c[("step", 0.9)].estimate == pytest.approx(0.4, rel=0.15)  # 0.95 at 0.4; the 5-case bin is ignored
    assert c[("dip", 0.5)].estimate is None
    low, est, high = c[("step", 0.5)].low, c[("step", 0.5)].estimate, c[("step", 0.5)].high
    assert low <= est <= high


# ----------------------------------------------------------------- false alarms


def test_false_alarm_rate_per_thousand():
    r = M.false_alarm_rate(3, 300)
    assert r.per_1000 == pytest.approx(10.0) and r.low_per_1000 < 10.0 < r.high_per_1000
    z = M.false_alarm_rate(0, 0)
    assert z.per_1000 == 0.0 and z.k == 0


# ------------------------------------------------------------------- the trivial detector


def two_decades(early, late, n=20):
    t = np.r_[jd(np.linspace(1900, 1908, n)), jd(np.linspace(1970, 1978, n))]
    return t, np.r_[np.full(n, early), np.full(n, late)], np.full(2 * n, 20.0)


def test_trivial_detector_threshold_on_both_sides_and_both_directions():
    d = TrivialDetector(threshold_mag=0.5)
    assert not d(*two_decades(12.0, 12.49))  # just under
    assert d(*two_decades(12.0, 12.51))  # just over: fainter
    assert d(*two_decades(12.51, 12.0))  # just over: brighter
    assert not d(*two_decades(12.0, 12.0))


def test_trivial_detector_ignores_non_detections_and_short_curves():
    t, m, lim = two_decades(12.0, 13.0)
    m_nd = m.copy()
    m_nd[-20:] = np.nan  # the whole late decade is non-detections
    assert not TrivialDetector()(t, m_nd, lim)
    assert not TrivialDetector()(t[:6], m[:6], lim[:6])


def test_trivial_detector_defaults_to_the_long_term_amplitude_floor():
    assert TrivialDetector().threshold_mag == 0.5


# ------------------------------------------------------------------------ harness


def host(host_id=1, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    year = np.sort(rng.uniform(1890, 1985, 300))
    return HostCurve(host_id, jd(year), 12.0 + rng.normal(0, noise, 300), np.full(300, 16.0))


def test_harness_counts_a_large_step_as_recovered_and_a_small_one_as_missed():
    hosts = {1: host()}
    mk = lambda amp, i: SignalSpec(1, "step", i, 0, Signal("step", amp, 1940.0), False, "")
    out = run_injections(hosts, [mk(1.0, 0), mk(0.2, 1)], TrivialDetector())
    assert [o.recovered for o in out] == [True, False]
    assert out[0].amplitude == 1.0 and out[0].family == "step"


def test_a_host_that_already_alarms_is_not_credited_with_recoveries():
    always = lambda t, m, lim: True
    out = run_injections({1: host()}, plan_injections([HostSpan(1, 1890, 1985)], seed=1, families=["step"]), always)
    assert out and not any(o.recovered for o in out)


def test_a_detector_that_never_fires_recovers_nothing():
    out = run_injections({1: host()}, plan_injections([HostSpan(1, 1890, 1985)], seed=1), lambda t, m, lim: False)
    assert not any(o.recovered for o in out)


def test_false_alarms_counts_flagged_hosts():
    hosts = {i: host(i, seed=i) for i in range(1, 6)}
    seen = iter([True, False, False, True, False])
    r = false_alarms(hosts, lambda t, m, lim: next(seen))
    assert (r.k, r.n) == (2, 5) and r.per_1000 == pytest.approx(400.0)


def test_the_trivial_detector_draws_a_rising_curve_for_steps_and_is_suspect():
    """The exit test of Sprint 2, on synthetic hosts: completeness rises with amplitude."""
    hosts = {i: host(i, noise=0.12, seed=i) for i in range(1, 31)}
    specs = plan_injections([HostSpan(i, 1890, 1985) for i in hosts], seed=5,
                            families=["step", "linear"], replicates=2)
    out = run_injections(hosts, specs, TrivialDetector())
    bins = M.completeness(out)
    for family in ("step", "linear"):
        fr = [b.fraction for b in bins if b.family == family]
        assert fr[-1] > fr[0] and fr[-1] > 0.8, (family, fr)
        assert fr == sorted(fr) or max(np.diff(fr)) > 0  # rises overall
    recovered, total = M.suspect_recoveries(out)
    assert total > 0 and recovered > 0  # it recovers steps placed inside the Menzel Gap


def test_crossing_marks_when_the_lowest_amplitude_already_reaches_the_level():
    o = outcomes("step", 0, 0.5, 20, 20) + outcomes("step", 1, 1.0, 20, 20)
    c = {x.level: x for x in M.crossings(M.completeness(o))}
    assert c[0.5].at_or_below and c[0.5].estimate == 0.5
    o = outcomes("step", 0, 0.5, 2, 20) + outcomes("step", 1, 1.0, 20, 20)
    assert not {x.level: x for x in M.crossings(M.completeness(o))}[0.5].at_or_below
