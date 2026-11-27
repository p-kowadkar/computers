import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from trig_helpers import make_curve

from computers.l1 import triggers as T


@st.composite
def curves(draw, spread=2.0, min_n=1, max_n=120):
    n = draw(st.integers(min_n, max_n))
    years = np.sort(np.array(draw(st.lists(st.floats(1885, 1990), min_size=n, max_size=n))))
    mag = np.array(draw(st.lists(st.one_of(st.floats(12 - spread, 12 + spread), st.just(np.nan)), min_size=n, max_size=n)))
    tags = np.array(draw(st.lists(st.sampled_from([0, 0, 0, 1, 2, 4, 8, 16]), min_size=n, max_size=n)), dtype=np.uint64)
    return make_curve(years, mag, tags=tags, series=np.array(draw(st.lists(st.sampled_from(["ac", "ai", "i"]), min_size=n, max_size=n))))


@given(curves(spread=0.4))
@settings(max_examples=200, deadline=None)
def test_a_curve_that_never_strays_half_a_magnitude_fires_nothing(c):
    inp = T.prepare(c)
    assert T.outburst_triggers(inp) == [] and T.dip_triggers(inp) == []


@given(curves())
@settings(max_examples=200, deadline=None)
def test_every_trigger_points_at_real_rows_in_time_order_with_ordered_epochs(c):
    inp = T.prepare(c)
    for t in T.outburst_triggers(inp) + T.dip_triggers(inp):
        rows = np.array(t.evidence)
        assert len(rows) >= 1 and rows.min() >= 0 and rows.max() < len(c)
        assert np.all(np.diff(rows) > 0) and np.all(np.isfinite(c.mag[rows]))
        assert t.epoch_start <= t.epoch_end and t.amplitude_mag > 0
        assert np.isclose(t.epoch_start, inp.year[rows[0]]) and np.isclose(t.epoch_end, inp.year[rows[-1]])


@given(curves())
@settings(max_examples=150, deadline=None)
def test_a_curve_that_only_fades_from_its_baseline_has_no_outburst_and_one_that_only_brightens_has_no_dip(c):
    base = T.prepare(c).baseline
    if not np.isfinite(base):
        return
    faded = c.with_magnitudes(np.where(np.isfinite(c.mag), np.maximum(c.mag, base), np.nan))
    assert T.outburst_triggers(T.prepare(faded)) == []  # nothing is brighter than a baseline the points sit above
    brightened = c.with_magnitudes(np.where(np.isfinite(c.mag), np.minimum(c.mag, base + 0.0), np.nan))
    b2 = T.prepare(brightened)
    assert T.dip_triggers(b2) == []


def described(triggers):
    return [(t.kind, round(t.epoch_start, 9), round(t.epoch_end, 9), round(t.amplitude_mag, 9), t.single_plate) for t in triggers]


@given(curves(), st.integers(1, 40))
@settings(max_examples=100, deadline=None)
def test_adding_non_detections_changes_neither_the_baseline_nor_the_triggers(c, extra):
    years = np.r_[2000 + (c.time - 2451545.0) / 365.25, np.linspace(1890, 1985, extra)]
    padded = make_curve(
        years, np.r_[c.mag, np.full(extra, np.nan)],
        tags=np.r_[c.tags, np.zeros(extra, dtype=np.uint64)], series=np.r_[c.series, np.array(["ac"] * extra)],
    )
    before, after = T.prepare(c), T.prepare(padded)
    assert np.array_equal([before.baseline, before.noise], [after.baseline, after.noise], equal_nan=True)
    assert described(T.outburst_triggers(before)) == described(T.outburst_triggers(after))
    assert described(T.dip_triggers(before)) == described(T.dip_triggers(after))
