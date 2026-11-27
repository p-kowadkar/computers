import numpy as np
import pytest
from synth_tile import make_tile
from trig_helpers import DAY, make_curve, quiet

from computers.l1 import analyse as A
from computers.l1 import kill as K
from computers.l1 import triggers as T
from computers.l1.tags import Tag


def eruption(series=("ac", "i"), tags=(0, 0), depth=1.8, days=(0, 30)):
    """The quiet star plus two bright points, with the series and tags of those points as given."""
    years, mag = quiet(noise=0.05)
    n = len(years)
    ey = 1920.0 + np.asarray(days) * DAY
    b = T.prepare(make_curve(years, mag)).baseline
    allseries = np.r_[np.array(["ac"] * n), np.array(series)]
    alltags = np.r_[np.zeros(n), np.asarray(tags)].astype(np.uint64)
    return make_curve(np.r_[years, ey], np.r_[mag, [b - depth] * len(ey)], series=allseries, tags=alltags)


def prepared(c):
    inp = T.prepare(c)
    return inp, T.outburst_triggers(inp)


def reasons(c, sep=None, **p):
    inp, found = prepared(c)
    assert len(found) == 1
    return K.kill_reasons(found[0], inp, T.TriggerParams.from_registry().but(**p), vsx_sep_arcsec=sep)


def test_evidence_from_two_series_survives_and_from_one_is_killed():
    assert reasons(eruption(series=("ac", "i"))) == ()
    assert reasons(eruption(series=("ac", "ac"))) == ("single_series_only",)


def test_the_minimum_number_of_series_is_a_parameter_with_a_per_kind_override():
    assert reasons(eruption(series=("ac", "ac")), trigger_min_series=1) == ()
    assert reasons(eruption(series=("ac", "i")), trigger_min_series=3) == ("single_series_only",)
    only_outbursts = {"min_series_overrides": (("outburst", 1),)}
    assert reasons(eruption(series=("ac", "ac")), **only_outbursts) == ()
    p = T.TriggerParams.from_registry().but(**only_outbursts)
    assert p.min_series("outburst") == 1 and p.min_series("dip") == 2


def test_non_blue_only_needs_every_evidence_point_to_be_non_blue_and_unknown_does_not_count():
    nb, unk = int(Tag.NON_BLUE), int(Tag.EMULSION_UNKNOWN)
    assert reasons(eruption(tags=(nb, nb))) == ("non_blue_only",)
    assert reasons(eruption(tags=(nb, 0))) == ()  # one blue plate keeps it
    assert reasons(eruption(tags=(unk, unk))) == ()  # an unknown emulsion is not a non-blue one
    assert reasons(eruption(tags=(nb, unk))) == ()


def test_blend_suspect_needs_every_evidence_point_to_be_blended():
    bl = int(Tag.BLEND_SUSPECT)
    assert reasons(eruption(tags=(bl, bl))) == ("blend_suspect",)
    assert reasons(eruption(tags=(bl, 0))) == ()


def test_known_variable_is_flagged_inside_the_match_radius_and_kept():
    assert reasons(eruption(), sep=2.9) == ("known_variable",)
    assert reasons(eruption(), sep=3.0) == ("known_variable",)  # the radius is inclusive
    assert reasons(eruption(), sep=3.1) == () and reasons(eruption(), sep=None) == ()
    inp, found = prepared(eruption())
    (t,) = K.apply_kill_reasons(found, inp, vsx_sep_arcsec=2.0)
    assert t.survives and t.kill_reasons == ("known_variable",)


def test_several_reasons_come_in_a_fixed_order_without_duplicates():
    nb = int(Tag.NON_BLUE)
    assert reasons(eruption(series=("ac", "ac"), tags=(nb, nb)), sep=1.0) == (
        "single_series_only", "non_blue_only", "known_variable",
    )  # fmt: skip
    assert list(K.KILL_REASONS) == ["too_few_points", "seam_only", "single_series_only", "non_blue_only", "blend_suspect", "known_variable"]


def test_a_killed_trigger_is_never_dropped():
    inp, found = prepared(eruption(series=("ac", "ac")))
    out = K.apply_kill_reasons(found, inp)
    assert len(out) == len(found) == 1 and not out[0].survives and found[0].kill_reasons == ()


def test_a_step_that_crosses_a_seam_or_sits_in_the_gap_is_killed_as_seam_only():
    rng = np.random.default_rng(2)
    y = np.r_[np.sort(rng.uniform(1890, 1950, 200)), np.sort(rng.uniform(1970, 1985, 100))]  # nothing in the gap
    m = 12.0 + np.where(y >= 1970.0, 0.8, 0.0) + rng.normal(0, 0.03, 300)
    inp = T.prepare(make_curve(y, m, series=np.where(y < 1960, "ac", "i")))
    (step,) = T.step_triggers(inp)
    assert "seam_only" in K.kill_reasons(step, inp)
    # a step in the middle of a stretch is not a seam effect
    y2 = np.sort(rng.uniform(1895, 1950, 300))
    inp2 = T.prepare(make_curve(y2, 12.0 + np.where(y2 >= 1925, 0.8, 0) + rng.normal(0, 0.03, 300), series=np.where(np.arange(300) % 2, "ac", "i")))
    (step2,) = T.step_triggers(inp2)
    assert "seam_only" not in K.kill_reasons(step2, inp2) and K.kill_reasons(step2, inp2) == ()


def test_seam_only_applies_to_steps_only():
    inp, found = prepared(eruption())
    assert "seam_only" not in K.kill_reasons(found[0], inp)


# ----------------------------------------------------------------------- analyse_star


def tile_with_eruption(series_of_the_two=("ac", "i")):
    """40 stars on 600 plates (58 days apart); star 1 brightens by 1.8 mag on plates 300 and 301."""
    a, b = series_of_the_two
    plates = {300: a, 301: b}
    return make_tile(
        n_star=40, n_plate=600, noise=0.04, plate_sigma=0.05, seed=3,
        star_offset=lambda i, j: -1.8 if (i == 0 and j in (300, 301)) else 0.0,
        series=lambda j: plates.get(j, "ac" if j % 2 else "i"),
    )  # fmt: skip


def test_a_two_series_eruption_survives_and_passes_to_l2():
    ctx = tile_with_eruption()
    r = A.analyse_star(ctx, 1)
    outbursts = [t for t in r.triggers if t.kind == "outburst"]
    assert len(outbursts) == 1 and outbursts[0].survives and not outbursts[0].single_plate
    assert outbursts[0].detrended and outbursts[0].amplitude_mag == pytest.approx(1.8, abs=0.2)
    assert r.stop_reason == "passed" and r.stop_layer == "l2" and r.survivors == (outbursts[0],)
    assert r.seconds > 0 and r.detrended is not None and r.detrended.detrended


def test_the_same_eruption_seen_by_one_series_is_killed_and_logged_not_dropped():
    r = A.analyse_star(tile_with_eruption(("ac", "ac")), 1)
    killed = [t for t in r.triggers if t.kind == "outburst"]
    assert len(killed) == 1 and killed[0].kill_reasons == ("single_series_only",)
    assert r.survivors == () and r.stop_reason == "killed_by_single_series_only" and r.stop_layer == "l1"
    assert r.kill_counts == {"single_series_only": 1}


def test_a_quiet_star_stops_with_no_trigger():
    r = A.analyse_star(tile_with_eruption(), 5)
    assert r.triggers == () and r.stop_reason == "no_trigger" and r.stop_layer == "l1" and r.survivors == ()


def test_a_curve_with_too_few_good_points_stops_before_any_trigger():
    ctx = make_tile(n_star=20, n_plate=40)  # 40 good points are under the minimum of 50
    r = A.analyse_star(ctx, 1)
    assert r.stop_reason == "too_few_points" and r.stop_layer == "l1" and r.triggers == ()
    assert r.detrended is None and r.inp is None and r.curve.kill_reason == "too_few_points"


def test_a_known_variable_keeps_its_trigger_and_is_flagged():
    r = A.analyse_star(tile_with_eruption(), 1, vsx_sep_arcsec=1.0)
    (t,) = [t for t in r.triggers if t.kind == "outburst"]
    assert t.survives and t.kill_reasons == ("known_variable",) and r.stop_reason == "passed"
    assert r.kill_counts == {}  # known_variable is not counted as something that stopped it


def test_parameters_reach_the_triggers():
    ctx = tile_with_eruption()
    strict = T.TriggerParams.from_registry().but(outburst_amplitude_mag=2.5, outburst_single_point_mag=3.0)
    assert A.analyse_star(ctx, 1, strict).triggers == ()
    lenient = T.TriggerParams.from_registry().but(min_series_overrides=(("outburst", 1),))
    assert A.analyse_star(tile_with_eruption(("ac", "ac")), 1, lenient).stop_reason == "passed"
