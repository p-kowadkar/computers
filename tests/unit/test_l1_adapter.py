import numpy as np
import pytest
from synth_tile import make_tile

from computers.bench import metrics as M
from computers.bench.harness import false_alarms, run_injections
from computers.bench.inject import HostSpan, Signal, SignalSpec, plan_injections
from computers.bench.l1_adapter import L1Detector, l1_hosts
from computers.bench.trivial import TrivialDetector
from computers.l1.detrend import detrend
from computers.l1.tags import Tag

N_PLATE = 120


def stepped_plates():
    """Plate systematics that fake a century fade: +0.3 mag on the early plates, -0.3 on the late."""
    off = np.zeros(N_PLATE)
    off[:12], off[-12:] = 0.3, -0.3
    return off


def tile(**kw):
    return make_tile(n_plate=N_PLATE, plate_offset=stepped_plates(), noise=0.03, **kw)


def test_hosts_are_cleaned_curves_with_rejected_rows_dropped_and_the_curve_attached():
    ctx = make_tile(n_plate=N_PLATE, series=lambda j: "ai" if j < 20 else "ac")  # the first 20 plates are meteor
    hosts = l1_hosts(ctx, [1, 2])
    h = hosts[1]
    assert set(hosts) == {1, 2} and h.curve is not None and len(h.time) == N_PLATE - 20
    assert np.array_equal(h.time, h.curve.time) and not h.curve.rejected.any()
    assert h.curve.n_good == N_PLATE - 20 and np.all(np.diff(h.time) >= 0)


def test_without_detrending_the_detector_sees_the_plate_systematic_as_a_fade():
    ctx = tile()
    hosts = l1_hosts(ctx, range(1, 30))
    raw = L1Detector(ctx, detrended=False, threshold_mag=0.3)
    assert false_alarms(hosts, raw).k == 29  # the false fade of 0.6 mag is flagged on every host


def test_detrending_removes_the_systematic_so_no_constant_is_flagged():
    ctx = tile()
    hosts = l1_hosts(ctx, range(1, 30))
    assert false_alarms(hosts, L1Detector(ctx, detrended=True, threshold_mag=0.3)).k == 0


def test_a_real_step_survives_detrending_and_is_recovered():
    ctx = tile()
    hosts = l1_hosts(ctx, [1, 2, 3])
    specs = [SignalSpec(h, "step", 0, 0, Signal("step", 1.0, 1940.0), False, "") for h in (1, 2, 3)]
    out = run_injections(hosts, specs, L1Detector(ctx, detrended=True, threshold_mag=0.3))
    assert all(o.recovered for o in out)


def test_a_host_that_already_alarms_is_not_credited_with_recoveries():
    ctx = tile()
    hosts = l1_hosts(ctx, [1])
    specs = plan_injections([HostSpan(1, 1890, 1985)], seed=1, families=["step"])
    out = run_injections(hosts, specs, L1Detector(ctx, detrended=False, threshold_mag=0.3))
    assert not any(o.recovered for o in out)


def test_the_comparison_pool_is_never_injected():
    ctx = tile()
    before = ctx.matrix.copy()
    hosts = l1_hosts(ctx, [1])
    spec = SignalSpec(1, "step", 0, 0, Signal("step", 2.0, 1900.0), False, "")
    run_injections(hosts, [spec], L1Detector(ctx, threshold_mag=0.3))
    assert np.array_equal(ctx.matrix, before, equal_nan=True)
    assert np.isfinite(ctx.matrix[ctx.index[1]]).all()  # the host's own row is its original curve


def test_the_adapter_agrees_with_the_trivial_detector_when_it_does_nothing():
    ctx = make_tile(n_plate=N_PLATE, seed=7)
    hosts = l1_hosts(ctx, [1, 2, 3])
    plain = TrivialDetector(0.5)
    for h in hosts.values():
        assert L1Detector(ctx, detrended=False, threshold_mag=0.5)(h, h.mag, h.limit) == plain(h.time, h.mag, h.limit)


def test_a_detrended_detector_needs_hosts_from_l1_hosts():
    from computers.bench.harness import HostCurve

    ctx = make_tile(n_plate=N_PLATE)
    bare = HostCurve(1, np.arange(3.0), np.full(3, 12.0), np.full(3, 15.0))
    with pytest.raises(ValueError, match="l1_hosts"):
        L1Detector(ctx)(bare, bare.mag, bare.limit)


def test_the_completeness_curve_of_the_detrended_detector_rises_with_amplitude():
    ctx = tile()
    hosts = l1_hosts(ctx, range(1, 21))
    specs = plan_injections([HostSpan(h, 1890, 1985) for h in hosts], seed=2, families=["step"], replicates=2)
    out = run_injections(hosts, specs, L1Detector(ctx, threshold_mag=0.5))
    fr = [b.fraction for b in M.completeness(out)]
    assert fr[-1] > fr[0] and fr[-1] > 0.7
    assert detrend(ctx, hosts[1].curve).curve.tag_counts()[Tag.NO_OFFSET.name] == 0  # every plate has enough comparisons
