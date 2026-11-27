import numpy as np
import pytest
from synth_tile import make_tile

from computers.bench import metrics as M
from computers.bench import recovery as R
from computers.bench.inject import Signal, SignalSpec
from computers.bench.l1_adapter import l1_hosts
from computers.l1 import triggers as T
from computers.l1.triggers import Trigger, TriggerParams

DAY = 1 / 365.25


def trig(kind, start, end, survives=True):
    return Trigger(kind, start, end, 1.0, False, (1,), True, () if survives else ("single_series_only",))


# ------------------------------------------------------------------------------- spans


def test_signal_spans_by_family():
    assert R.signal_span(Signal("outburst", 3.0, 1920.0, 30.0, -1), 1890, 1985) == pytest.approx((1920.0, 1920.0 + 90 * DAY))
    assert R.signal_span(Signal("dip", 1.0, 1930.0, 8.0), 1890, 1985) == (1930.0, 1938.0)
    assert R.signal_span(Signal("step", 1.0, 1940.0), 1890, 1985) == (1940.0, 1940.0)
    assert R.signal_span(Signal("linear", 1.0, 1890.0), 1893.5, 1981.2) == (1893.5, 1981.2)
    assert R.signal_span(Signal("wave", 1.0, 1900.0, 40.0), 1893.5, 1981.2) == (1893.5, 1981.2)


def test_the_tolerances_come_from_the_registry_and_whole_curve_signals_have_none():
    assert R.tolerance_years("outburst") == pytest.approx(60 * DAY)
    assert R.tolerance_years("dip") == 1.0 and R.tolerance_years("step") == 5.0
    assert R.tolerance_years("linear") == float("inf") and R.tolerance_years("wave") == float("inf")


# ------------------------------------------------------------------------------ matching


@pytest.mark.parametrize(
    ("family", "span", "tol", "kind"),
    [("outburst", (1920.0, 1920.2), 60 * DAY, "outburst"), ("dip", (1930.0, 1938.0), 1.0, "dip"), ("step", (1940.0, 1940.0), 5.0, "step")],
)
def test_the_epoch_tolerance_on_both_sides_for_every_family(family, span, tol, kind):
    eps = 0.001
    after = span[1] + tol  # a trigger that starts exactly at the widened end
    assert R.matches([trig(kind, after - eps, after + 1)], [], span, family)
    assert not R.matches([trig(kind, after + eps, after + 1)], [], span, family)
    before = span[0] - tol  # a trigger that ends exactly at the widened start
    assert R.matches([trig(kind, before - 1, before + eps)], [], span, family)
    assert not R.matches([trig(kind, before - 1, before - eps)], [], span, family)


def test_whole_curve_signals_match_any_epoch():
    assert R.matches([trig("long_term", 1890.0, 1985.0)], [], (1893.0, 1981.0), "linear")
    assert R.matches([trig("step", 1900.0, 1900.1)], [], (1893.0, 1981.0), "wave")


def test_killed_triggers_do_not_count_and_a_host_trigger_already_there_does_not_count_either():
    new = trig("step", 1940.0, 1940.1)
    assert R.matches([new], [], (1940.0, 1940.0), "step") == [new]
    assert R.matches([trig("step", 1940.0, 1940.1, survives=False)], [], (1940.0, 1940.0), "step") == []
    assert R.matches([new], [trig("step", 1939.9, 1940.5)], (1940.0, 1940.0), "step") == []  # the host had it
    assert R.matches([new], [trig("step", 1900.0, 1900.1)], (1940.0, 1940.0), "step") == [new]  # a different epoch
    assert R.matches([new], [trig("dip", 1939.9, 1940.5)], (1940.0, 1940.0), "step") == [new]  # a different kind
    assert R.matches([new], [trig("step", 1939.9, 1940.5, survives=False)], (1940.0, 1940.0), "step") == [new]


# --------------------------------------------------------------------------- the run


def small_tile():
    ctx = make_tile(n_star=40, n_plate=600, noise=0.04, plate_sigma=0.05, seed=3,
                    series=lambda j: "ac" if j % 2 else "i")  # fmt: skip
    return ctx, l1_hosts(ctx, [1, 2, 3])


def spec(host, family, amp, t0, scale=0.0, sign=1, missed=False, idx=0):
    return SignalSpec(host, family, idx, 0, Signal(family, amp, t0, scale, sign), missed, "")


def run(specs, **kw):
    ctx, hosts = small_tile()
    return R.run_recovery({h: ctx for h in hosts}, hosts, specs, **kw)


def test_a_large_step_is_recovered_with_the_right_type_and_a_tiny_one_is_not():
    out = run([spec(1, "step", 1.0, 1940.0), spec(2, "step", 0.2, 1940.0)]).outcomes
    big, small = out
    assert big.recovered and big.right_type and big.match_kind == "step"
    assert not small.recovered and not small.right_type and small.match_kind is None


def test_an_outburst_across_two_series_is_recovered_as_an_outburst():
    sp = spec(1, "outburst", 4.0, 1920.0, scale=100.0, sign=-1)
    (o,) = run([sp]).outcomes
    # plates are 58 days apart and alternate series; a 4 mag outburst decaying over 100 days stays
    # above 1 mag for 138 days, so it lies on two or three plates of both series
    assert o.recovered and o.right_type and o.match_kind == "outburst"


def test_an_outburst_that_the_plate_spacing_cannot_resolve_is_not_recovered():
    """2.5 mag decaying over 60 days is above 1 mag for 55 days: at most one plate, so it cannot fire."""
    (o,) = run([spec(1, "outburst", 2.5, 1920.0, scale=60.0, sign=-1)]).outcomes
    assert not o.recovered


def test_a_long_fade_is_recovered_as_long_term_variation():
    (o,) = run([spec(1, "linear", 1.5, 1890.0)]).outcomes
    assert o.recovered and o.right_type and o.match_kind == "long_term"


def test_a_step_in_the_gap_is_missed_by_the_seam_rule_and_found_when_the_rule_is_switched_off():
    gap = spec(1, "step", 1.2, 1960.0, missed=True)
    (with_rule,) = run([gap]).outcomes
    assert not with_rule.recovered  # the seam rule kills it
    (without,) = run([gap], ignore_reasons=("seam_only",)).outcomes
    assert without.recovered and without.expected_missed  # which flags a detector with no seam rule as suspect
    assert M.suspect_recoveries([without]) == (1, 1) and M.suspect_recoveries([with_rule]) == (0, 1)


def test_the_run_does_not_inject_into_the_comparison_stars_or_the_hosts_cached_offsets():
    ctx, hosts = small_tile()
    before = ctx.matrix.copy()
    R.run_recovery({h: ctx for h in hosts}, hosts, [spec(1, "step", 1.0, 1940.0)])
    assert np.array_equal(ctx.matrix, before, equal_nan=True)


def test_the_confusion_counts_cover_every_spec_and_the_matrix_names_the_matched_kind():
    specs = [spec(1, "step", 1.0, 1940.0), spec(2, "step", 0.2, 1940.0), spec(3, "linear", 1.5, 1890.0)]
    r = run(specs)
    assert sum(r.confusion.values()) == 3
    table = R.confusion(r.confusion)
    assert table["step"] == {"none": 1, "step": 1} and table["linear"] == {"long_term": 1}


def test_completeness_can_be_read_by_any_match_or_by_right_type():
    out = run([spec(1, "step", 1.0, 1940.0), spec(2, "step", 0.2, 1940.0)]).outcomes
    any_bins = M.completeness([o for o in out], field="recovered")
    right_bins = M.completeness(out, field="right_type")
    assert (any_bins[0].k, right_bins[0].k) == (1, 1) and any_bins[0].n == 2


def test_without_detrending_the_run_still_works_and_a_host_with_too_few_points_matches_nothing():
    (o,) = run([spec(1, "step", 1.0, 1940.0)], detrend=False).outcomes
    assert o.recovered
    ctx, hosts = small_tile()
    h = hosts[1]
    nothing = np.full(len(h.mag), np.nan)
    assert R.analyse_host(ctx, h, nothing, TriggerParams.from_registry()) == ()


def test_parameters_reach_the_triggers_in_a_run():
    sp = spec(1, "step", 1.0, 1940.0)
    strict = TriggerParams.from_registry().but(step_shift_mag=1.5, long_term_span_mag=1.5)
    assert not run([sp], params=strict).outcomes[0].recovered
    assert T.TriggerParams.from_registry().step_shift_mag == 0.5
