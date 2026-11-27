import numpy as np
import pytest
from trig_helpers import DAY, STD_HARD, STD_SOFT, baseline_of, burst, jd, make_curve, quiet

from computers.l1 import triggers as T
from computers.thresholds import THRESHOLDS

# ----------------------------------------------------------------- the type and the inputs


def test_a_trigger_knows_its_kind_survival_and_kill_reasons():
    t = T.Trigger("outburst", 1946.1, 1946.2, 4.0, False, (1, 2), True)
    assert t.lower_limit and not t.needs_cutouts and t.survives
    k = t.killed_by("single_series_only").killed_by("single_series_only", "non_blue_only")
    assert k.kill_reasons == ("single_series_only", "non_blue_only") and not k.survives  # no duplicates, order kept
    assert t.kill_reasons == ()  # the original is unchanged
    assert t.killed_by("known_variable").survives  # known_variable alone does not kill
    assert not t.killed_by("known_variable", "seam_only").survives
    assert T.Trigger("disappearance", 1960.0, 1990.0, 0.0, False, (), True).needs_cutouts
    with pytest.raises(ValueError, match="unknown trigger kind"):
        T.Trigger("vanished", 0.0, 0.0, 0.0, False, (), True)


def test_the_parameters_start_from_the_registry_and_one_can_change():
    p = T.TriggerParams.from_registry()
    assert (p.outburst_min_points, p.outburst_window_days, p.outburst_sigma) == (2, 60.0, 5.0)
    assert p.outburst_amplitude_mag == THRESHOLDS["amplitude_floor_single_epoch_mag"].value == 1.0
    assert p.dip_amplitude_mag == p.step_shift_mag == p.long_term_span_mag == 0.5
    assert p.step_min_points == p.decade_min_points == 20 and p.trigger_min_series == 2
    q = p.but(outburst_min_points=3)
    assert q.outburst_min_points == 3 and p.outburst_min_points == 2 and q.dip_min_points == p.dip_min_points


def test_prepare_gives_the_baseline_and_a_noise_that_ignores_outliers():
    years, mag = quiet(noise=0.1)
    inp = T.prepare(make_curve(years, mag))
    assert inp.baseline == pytest.approx(12.0, abs=0.03) and inp.noise == pytest.approx(0.1, abs=0.03)
    spiked = T.prepare(make_curve(np.r_[years, [1920.0, 1920.1]], np.r_[mag, [7.0, 7.1]]))
    assert spiked.baseline == pytest.approx(inp.baseline, abs=0.01) and spiked.noise == pytest.approx(inp.noise, abs=0.01)
    assert not inp.detrended and T.prepare(make_curve(years, mag), detrended=True).detrended


def test_prepare_reads_the_magnitudes_it_is_given_and_rejects_a_wrong_length():
    years, mag = quiet()
    c = make_curve(years, mag)
    shifted = T.prepare(c, c.mag + 1.0, detrended=True)
    assert shifted.baseline == pytest.approx(T.prepare(c).baseline + 1.0)
    with pytest.raises(ValueError, match="one entry per row"):
        T.prepare(c, c.mag[:-1])


def test_a_curve_with_no_good_detection_has_no_baseline_and_no_triggers():
    c = make_curve([1900.0, 1910.0], [np.nan, np.nan])
    inp = T.prepare(c)
    assert np.isnan(inp.baseline) and np.isnan(inp.noise)
    assert T.outburst_triggers(inp) == [] and T.dip_triggers(inp) == []


def test_the_noise_falls_back_to_the_mad_when_there_are_too_few_blocks():
    years = np.linspace(1900.0, 1903.0, 40)  # inside one 5-year block: no between-block statistics
    mag = 12.0 + np.where(np.arange(40) % 2, 0.1, -0.1)
    inp = T.prepare(make_curve(years, mag))
    assert inp.noise == pytest.approx(1.4826 * 0.1, rel=0.05)


# ------------------------------------------------------------------------------ outburst


def plan(**p):
    """The registry parameters with the return check off, so a test exercises one rule at a time."""
    return T.TriggerParams.from_registry().but(**{"return_points": 0, **p})


def run(c, **p):
    return T.outburst_triggers(T.prepare(c), plan(**p))


def test_two_points_a_magnitude_brighter_within_sixty_days_fire_on_both_sides_of_the_amplitude():
    fires = run(burst([1.02, 1.02], [0, 30]))
    assert len(fires) == 1 and not fires[0].single_plate and len(fires[0].evidence) == 2
    assert fires[0].kind == "outburst" and fires[0].amplitude_mag == pytest.approx(1.02, abs=0.03)
    assert fires[0].epoch_start == pytest.approx(1920.0, abs=1e-3) and fires[0].epoch_end == pytest.approx(1920.0 + 30 * DAY, abs=1e-3)
    assert run(burst([0.98, 0.98], [0, 30])) == []  # a hair too faint, and neither is 2 mag


def test_the_sixty_day_window_boundary_on_both_sides():
    assert len(run(burst([1.5, 1.5], [0, 59.9]))) == 1
    assert run(burst([1.5, 1.5], [0, 60.1])) == []  # two lone points of 1.5 mag: not 2 mag, not within the window


def test_one_point_two_magnitudes_brighter_is_a_single_plate_trigger_and_less_is_not():
    t = run(burst([2.05], [0]))
    assert len(t) == 1 and t[0].single_plate and len(t[0].evidence) == 1
    assert run(burst([1.95], [0])) == []


def test_the_minimum_number_of_points_is_a_parameter():
    assert run(burst([1.5, 1.5], [0, 20]), outburst_min_points=3) == []
    assert len(run(burst([1.5, 1.5, 1.5], [0, 20, 40]), outburst_min_points=3)) == 1
    assert len(run(burst([1.5], [0]), outburst_min_points=1)) == 1


def test_the_noise_limit_binds_for_a_noisy_star():
    noisy = {"noise": 0.4}  # five sigma is about 2.0 mag
    assert run(burst([1.5, 1.5], [0, 20], **noisy)) == []
    assert len(run(burst([2.4, 2.4], [0, 20], **noisy))) == 1
    assert len(run(burst([1.5, 1.5], [0, 20], **noisy), outburst_sigma=2.0)) == 1  # the multiplier is a parameter


def test_a_decay_over_months_is_one_trigger_and_separate_events_are_separate():
    one = run(burst([3.0, 2.5, 2.0, 1.8, 1.5, 1.3, 1.1], [0, 50, 100, 150, 200, 250, 300]))
    assert len(one) == 1 and len(one[0].evidence) == 7 and one[0].amplitude_mag == pytest.approx(3.0, abs=0.03)
    two = run(burst([1.5, 1.5, 1.5, 1.5], [0, 20, 500, 520]))
    assert [len(t.evidence) for t in two] == [2, 2] and two[0].epoch_end < two[1].epoch_start


def test_a_bright_point_beside_a_cluster_is_part_of_it_not_a_second_trigger():
    t = run(burst([3.0, 1.5], [0, 20]))
    assert len(t) == 1 and not t[0].single_plate and len(t[0].evidence) == 2


def test_soft_rejected_points_count_and_hard_rejected_ones_do_not():
    assert len(run(burst([1.5, 1.5], [0, 20], tags=[STD_SOFT, STD_SOFT]))) == 1  # good_for_outburst keeps them
    assert run(burst([1.5, 1.5], [0, 20], tags=[STD_HARD, 0])) == []
    assert run(burst([2.5], [0], tags=[STD_HARD])) == []


def test_fainter_points_and_non_detections_are_never_evidence():
    assert run(burst([-1.5, -1.5], [0, 20])) == []  # fainter than the baseline by 1.5 mag
    assert run(burst([np.nan, np.nan], [0, 20])) == []


def test_the_trigger_reads_the_magnitudes_it_is_given_and_says_so():
    years, mag = quiet()
    c = make_curve(np.r_[years, [1920.0, 1920.05]], np.r_[mag, [11.8, 11.8]])  # not bright in the raw curve
    assert T.outburst_triggers(T.prepare(c)) == []
    adjusted = c.mag.copy()
    adjusted[(c.time >= jd(1919.99)) & (c.time <= jd(1920.1))] -= 1.5  # a detrending that reveals just these two
    t = T.outburst_triggers(T.prepare(c, adjusted, detrended=True))
    assert t and all(x.detrended for x in t)


def test_a_clean_flat_curve_never_fires_an_outburst():
    for seed in range(20):
        years, mag = quiet(noise=0.1, seed=seed)
        assert T.outburst_triggers(T.prepare(make_curve(years, mag))) == []


# ---------------------------------------------------------------------------------- dip


def dip(depth, n_points=3, span_years=2.0, noise=0.05, middle=None):
    """A quiet star with nothing between 1920 and 1950 except the dip points."""
    years, mag = quiet(noise=noise, hole=(1919.9, 1950.1))
    b = baseline_of(years, mag)
    dy = np.linspace(1920.0, 1920.0 + span_years, n_points)
    dm = np.full(n_points, b + depth)
    if middle is not None:
        dm[1] = middle
    return make_curve(np.r_[years, dy], np.r_[mag, dm]), b


def dips(c, **p):
    return T.dip_triggers(T.prepare(c), plan(**p))


def test_three_consecutive_faint_points_are_a_dip_on_both_sides_of_the_depth():
    c, _ = dip(0.52)
    (d,) = dips(c)
    assert d.kind == "dip" and len(d.evidence) == 3 and d.amplitude_mag == pytest.approx(0.52, abs=0.02)
    assert d.epoch_start == pytest.approx(1920.0, abs=1e-3) and d.epoch_end == pytest.approx(1922.0, abs=1e-3)
    assert dips(dip(0.48)[0]) == []


def test_two_points_are_not_a_dip_and_the_count_is_a_parameter():
    assert dips(dip(0.8, n_points=2)[0]) == []
    assert len(dips(dip(0.8, n_points=2)[0], dip_min_points=2)) == 1
    assert dips(dip(0.8, n_points=3)[0], dip_min_points=4) == []


def test_a_point_back_at_the_baseline_breaks_the_run():
    c, _ = dip(0.8, middle=12.0)  # the middle point is normal
    assert dips(c) == []


def test_a_non_detection_between_dip_points_is_ignored():
    years, mag = quiet(hole=(1919.9, 1950.1))
    b = baseline_of(years, mag)
    c = make_curve(np.r_[years, [1920.0, 1920.5, 1921.0, 1921.5]], np.r_[mag, [b + 0.8, np.nan, b + 0.8, b + 0.8]])
    (d,) = dips(c)
    assert len(d.evidence) == 3


def test_the_twenty_year_span_boundary_on_both_sides():
    assert len(dips(dip(0.8, span_years=19.9)[0])) == 1
    assert dips(dip(0.8, span_years=20.1)[0]) == []  # a longer fading is for the step and long-term triggers


def test_a_dip_is_not_fired_by_brightenings_and_hard_rejected_points_do_not_count():
    assert dips(dip(-0.8)[0]) == []
    years, mag = quiet(hole=(1919.9, 1950.1))
    b = baseline_of(years, mag)
    c = make_curve(np.r_[years, [1920.0, 1921.0, 1922.0]], np.r_[mag, [b + 0.8] * 3],
                   tags=np.r_[np.zeros(len(years)), [STD_HARD, 0, 0]].astype(np.uint64))
    assert dips(c) == []


def test_a_flat_curve_never_dips():
    for seed in range(20):
        years, mag = quiet(noise=0.1, seed=seed)
        assert T.dip_triggers(T.prepare(make_curve(years, mag))) == []


# ------------------------------------------------------------------- the recovery check


def pair_with_tail(tail_depth, n_tail=6):
    """A two-point outburst 1.8 mag bright, followed by `n_tail` points `tail_depth` mag brighter than baseline.

    The quiet star is cut out from 1919 to 1925, so the points after the outburst are the tail.
    Recovered share = (1.8 - tail_depth) / 1.8: 1.0 back at baseline, 0 if the star stays at the event's level.
    """
    years, mag = quiet(hole=(1919.0, 1925.0))
    b = baseline_of(years, mag)
    ey = np.r_[1920.0, 1920.0 + 30 * DAY]
    tail = 1920.5 + np.arange(n_tail) * 100 * DAY
    return make_curve(np.r_[years, ey, tail], np.r_[mag, [b - 1.8, b - 1.8], np.full(n_tail, b - tail_depth)]), b


def test_an_outburst_that_recovers_passes_and_a_persistent_change_does_not():
    assert len(run(pair_with_tail(0.0)[0], return_points=5)) == 1  # back at the baseline
    assert run(pair_with_tail(1.8)[0], return_points=5) == []  # the star stays at the event's level
    assert len(run(pair_with_tail(1.8)[0])) >= 1  # without the check it is an outburst, as in the plan


def test_a_slow_decay_counts_as_recovering():
    """A nova is still well above the baseline when the next points arrive, but it is fading: it passes."""
    c, _ = pair_with_tail(1.0)  # the tail is 1.0 mag bright against an event of 1.8: 44% of the way back
    assert len(run(c, return_points=5)) == 1


def test_the_recovery_share_boundary_on_both_sides():
    recovered = lambda depth: (1.8 - depth) / 1.8
    assert recovered(1.30) == pytest.approx(0.278, abs=0.001) and recovered(1.40) == pytest.approx(0.222, abs=0.001)
    assert len(run(pair_with_tail(1.30)[0], return_points=5, return_recovery=0.25)) == 1
    assert run(pair_with_tail(1.40)[0], return_points=5, return_recovery=0.25) == []
    assert len(run(pair_with_tail(1.40)[0], return_points=5, return_recovery=0.2)) == 1  # the share is a parameter


def test_too_few_points_after_the_event_means_the_recovery_cannot_be_seen_and_it_does_not_count():
    years, mag = quiet(hole=(1919.5, 1950.1))  # the data stop right after the outburst
    b = baseline_of(years, mag)
    ends = make_curve(np.r_[years, [1920.0, 1920.05]], np.r_[mag, [b - 1.8, b - 1.8]])
    assert run(ends, return_points=5) == []
    assert len(run(ends)) == 1
    # four points after the outburst and nothing else: enough for a check of 4, not of 5
    tail = 1920.5 + np.arange(4) * 100 * DAY
    four = make_curve(np.r_[years, [1920.0, 1920.05], tail], np.r_[mag, [b - 1.8, b - 1.8], np.full(4, b)])
    assert run(four, return_points=5) == [] and len(run(four, return_points=4)) == 1


def test_a_dip_that_recovers_passes_and_a_persistent_fade_chopped_by_noise_does_not():
    years, mag = quiet(hole=(1919.9, 1930.1))
    b = baseline_of(years, mag)
    back = make_curve(np.r_[years, [1920.0, 1922.0, 1924.0], 1925.0 + np.arange(6)], np.r_[mag, [b + 0.8] * 3, [b] * 6])
    assert len(dips(back, return_points=5)) == 1  # it comes back to the baseline
    # a fade that stays: one point at +0.45 falls under the 0.5 cut and splits it into two runs
    faint = [b + 0.8, b + 0.8, b + 0.8, b + 0.45, b + 0.8, b + 0.8, b + 0.8, b + 0.8, b + 0.8]
    persistent = make_curve(np.r_[years, 1920.0 + np.arange(9)], np.r_[mag, faint])
    starts_at_1920 = lambda found: [d for d in found if d.epoch_start < 1923.0]
    assert len(starts_at_1920(dips(persistent))) == 1  # the plan's rule reads the first run as a dip
    assert starts_at_1920(dips(persistent, return_points=5)) == []  # the star is still faint after it: not a dip
    assert len(dips(persistent, return_points=5)) == 1  # the later run is followed by baseline data and stays


def test_the_check_is_on_by_default_with_the_chosen_values():
    p = T.TriggerParams.from_registry()
    assert p.return_points == 5 and p.return_recovery == 0.25
    assert T.TriggerParams.from_registry().but(return_points=0).return_points == 0
