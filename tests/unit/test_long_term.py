import numpy as np
import pytest
from trig_helpers import STD_HARD, make_curve

from computers.l1 import triggers as T
from computers.l1.tags import Tag


def decades(levels, points=25, start=1890, series="ac"):
    """`points` evenly spaced points in each decade from `start`, each decade at its own level."""
    years, mag = [], []
    for i, level in enumerate(levels):
        p = points[i] if isinstance(points, (list, tuple)) else points
        years += list(np.linspace(start + 10 * i + 0.5, start + 10 * i + 9.5, p))
        mag += [12.0 + level] * p
    return make_curve(years, mag, series=series)


def lt(c, **p):
    return T.long_term_triggers(T.prepare(c), T.TriggerParams.from_registry().but(**p))


def test_a_steady_fade_over_four_decades_fires_with_its_span_and_direction():
    (t,) = lt(decades([0.0, 0.2, 0.4, 0.6]))
    assert t.kind == "long_term" and t.amplitude_mag == pytest.approx(0.6) and t.direction == 1
    assert t.epoch_start == pytest.approx(1890.5) and t.epoch_end == pytest.approx(1929.5) and len(t.evidence) == 100
    (b,) = lt(decades([0.0, -0.2, -0.4, -0.6]))
    assert b.direction == -1 and b.amplitude_mag == pytest.approx(0.6)


def test_the_span_boundary_on_both_sides():
    assert len(lt(decades([0.0, 0.1, 0.3, 0.51]))) == 1
    assert lt(decades([0.0, 0.1, 0.3, 0.49])) == []
    assert len(lt(decades([0.0, 0.1, 0.3, 0.49]), long_term_span_mag=0.4)) == 1


def test_the_decade_count_boundary_on_both_sides():
    assert lt(decades([0.0, 0.3, 0.6])) == []  # three populated decades are not enough
    assert len(lt(decades([0.0, 0.3, 0.6, 0.9]))) == 1
    assert lt(decades([0.0, 0.3, 0.6, 0.9]), long_term_min_decades=5) == []
    assert len(lt(decades([0.0, 0.3, 0.6, 0.9, 1.2]), long_term_min_decades=5)) == 1


def test_a_decade_with_nineteen_points_is_dropped_and_twenty_is_kept():
    assert lt(decades([0.0, 0.3, 0.6, 0.9], points=[25, 25, 25, 19])) == []
    assert len(lt(decades([0.0, 0.3, 0.6, 0.9], points=[25, 25, 25, 20]))) == 1


def test_a_variation_that_is_not_monotonic_still_counts_by_its_span():
    (t,) = lt(decades([0.0, 0.6, 0.0, 0.6]))
    assert t.amplitude_mag == pytest.approx(0.6) and t.direction == 1


def test_a_flat_star_does_not_fire_and_neither_does_a_dip_of_one_decade_smaller_than_the_floor():
    assert lt(decades([0.0, 0.0, 0.0, 0.0, 0.0])) == []
    assert lt(decades([0.0, 0.0, 0.3, 0.0, 0.0])) == []


def test_a_change_visible_only_across_the_gap_does_not_fire():
    # 1890 to 1949 flat, 1970 to 1989 shifted by 1 mag: the two sides are different stretches
    years = np.r_[np.linspace(1890.5, 1949.5, 150), np.linspace(1970.5, 1989.5, 80)]
    mag = np.r_[np.full(150, 12.0), np.full(80, 13.0)]
    assert lt(make_curve(years, mag)) == []


def test_hard_rejected_points_do_not_count_toward_a_decade():
    c = decades([0.0, 0.3, 0.6, 0.9], points=[25, 25, 25, 25])
    tags = np.zeros(len(c), dtype=np.uint64)
    tags[-6:] = STD_HARD  # the last decade drops to 19 good points
    assert lt(make_curve(2000 + (c.time - 2451545.0) / 365.25, c.mag, tags=tags)) == []


def test_the_trigger_reads_the_magnitudes_it_is_given():
    c = decades([0.0, 0.0, 0.0, 0.0])
    shifted = c.mag + np.repeat([0.0, 0.2, 0.4, 0.6], 25)
    (t,) = T.long_term_triggers(T.prepare(c, shifted, detrended=True))
    assert t.detrended and t.amplitude_mag == pytest.approx(0.6)


# ------------------------------------------------------------------------ disappearance


def fading_to_nothing(n_deep, *, limit=14.0, series=("ac", "ai"), tags=None):
    """100 good detections to 1940, then `n_deep` non-detections with the given limit and series."""
    y1 = np.linspace(1890.0, 1940.0, 100)
    y2 = np.linspace(1941.0, 1985.0, n_deep) if n_deep else np.array([])
    ser = np.r_[np.array(["ac"] * 100), np.array([series[i % len(series)] for i in range(n_deep)], dtype=str)]
    mag = np.r_[np.full(100, 12.0), np.full(n_deep, np.nan)]
    tg = np.zeros(100 + n_deep, dtype=np.uint64) if tags is None else np.asarray(tags, dtype=np.uint64)
    return make_curve(np.r_[y1, y2], mag, series=ser, limit=limit, tags=tg)


def vanish(c, **p):
    return T.disappearance_triggers(T.prepare(c), T.TriggerParams.from_registry().but(**p))


def test_ten_deep_non_detections_from_two_series_fire_and_nine_do_not():
    (t,) = vanish(fading_to_nothing(10))
    assert t.kind == "disappearance" and t.needs_cutouts and len(t.evidence) == 10
    assert t.amplitude_mag == pytest.approx(2.0) and t.epoch_start == pytest.approx(1940.0)
    assert vanish(fading_to_nothing(9)) == []


def test_one_series_is_not_enough_and_the_requirement_is_a_parameter():
    assert vanish(fading_to_nothing(12, series=("ac",))) == []
    assert len(vanish(fading_to_nothing(12, series=("ac",)), disappearance_min_series=1)) == 1
    assert vanish(fading_to_nothing(12), disappearance_min_plates=13) == []


def test_the_limit_must_be_deeper_than_the_baseline_by_the_margin_on_both_sides():
    assert vanish(fading_to_nothing(12, limit=12.9)) == []  # only 0.9 mag deeper
    assert len(vanish(fading_to_nothing(12, limit=13.1))) == 1
    assert vanish(fading_to_nothing(12, limit=11.0)) == []  # a shallow plate says nothing about the star


def test_rejected_plates_do_not_count():
    tags = np.r_[np.zeros(100), np.full(5, int(Tag.METEOR_REJECTED)), np.zeros(7)]
    assert vanish(fading_to_nothing(12, tags=tags)) == []  # 7 usable deep plates are left


def test_a_star_detected_to_the_end_or_seen_again_does_not_fire():
    assert vanish(fading_to_nothing(0)) == []
    y = np.r_[np.linspace(1890, 1940, 100), np.linspace(1941, 1960, 12), [1985.0]]
    mag = np.r_[np.full(100, 12.0), np.full(12, np.nan), [12.0]]  # it comes back
    assert vanish(make_curve(y, mag, series=np.array(["ac"] * 100 + ["ac", "ai"] * 6 + ["ac"]), limit=14.0)) == []


def test_the_word_vanished_appears_in_no_output():
    (t,) = vanish(fading_to_nothing(12))
    assert "vanish" not in repr(t).lower() and "vanish" not in t.kind
    assert not any("vanish" in kind for kind in T.KINDS)


def test_the_detrended_limit_is_what_is_compared_with_the_detrended_baseline():
    c = fading_to_nothing(12, limit=14.0)
    shifted = c.limit - 1.5  # a detrending that makes the limits only 0.5 mag deeper
    assert T.disappearance_triggers(T.prepare(c, limit=shifted, detrended=True)) == []
    assert len(T.disappearance_triggers(T.prepare(c, limit=c.limit + 0.0, detrended=True))) == 1

