"""The evaluation harness (spec S6 R4)."""
import numpy as np
import pytest
from test_sequences import star_from
from trig_helpers import flat

from computers.ml import baselines as B
from computers.ml import evaluate as E
from computers.ml import masking as M


def stars(n=12, noise=0.1, nan_every=0):
    out = []
    for i in range(n):
        years, mag = flat(150, noise=noise, seed=i)
        if nan_every:
            mag = mag.copy()
            mag[::nan_every] = np.nan
        out.append(star_from(years, mag, limit=15.0))
    return out


def test_the_eval_set_is_fixed_by_its_seed_and_covers_both_modes_for_every_star():
    a, b, c = (E.build_eval_set(stars(4), k_per_mode=2, seed=s) for s in (1, 1, 2))
    assert all(np.array_equal(x[1].hidden, y[1].hidden) for x, y in zip(a.items, b.items, strict=True))
    assert any(not np.array_equal(x[1].hidden, y[1].hidden) for x, y in zip(a.items, c.items, strict=True))
    assert len(a.items) == 4 * 2 * 2 and {m.mode for _, m in a.items} == set(M.MODES)
    assert {i for i, _ in a.items} == {0, 1, 2, 3}


def test_a_perfect_predictor_has_zero_error_and_a_known_bad_one_has_a_known_error():
    es = E.build_eval_set(stars(), k_per_mode=2)
    perfect = E.score_predictions(es, [m.seq.value for _, m in es.items])
    assert np.allclose(perfect.err, 0.0) and len(perfect.err) > 100
    off = E.score_predictions(es, [m.seq.value + 0.3 for _, m in es.items])
    s = E.summary(off, n_boot=50)
    assert s["by_mode"]["random"]["rmse"] == pytest.approx(0.3) and s["by_mode"]["decade"]["median_abs"] == pytest.approx(0.3)


def test_the_bootstrap_interval_contains_the_estimate_and_is_zero_width_for_a_constant_error():
    es = E.build_eval_set(stars(), k_per_mode=2)
    off = E.score_predictions(es, [m.seq.value + 0.3 for _, m in es.items])
    c = E.summary(off, n_boot=100)["by_mode"]["random"]
    assert c["low"] == pytest.approx(0.3) and c["high"] == pytest.approx(0.3)
    noisy = E.score_predictions(es, [m.seq.value + np.random.default_rng(i).normal(0, 0.2, 200) for i, (_, m) in enumerate(es.items)])
    c = E.summary(noisy, n_boot=200)["by_mode"]["random"]
    assert c["low"] < c["rmse"] < c["high"]


def test_limit_violations_count_only_predictions_brighter_than_the_limit():
    es = E.build_eval_set(stars(nan_every=5), k_per_mode=2)
    n_lim = sum(int((m.hidden & m.seq.is_limit).sum()) for _, m in es.items)
    assert n_lim > 20
    fainter = E.score_predictions(es, [m.seq.value + 1.0 for _, m in es.items])  # at or beyond the limit: never an error
    at = E.score_predictions(es, [m.seq.value for _, m in es.items])  # exactly at the limit: not brighter
    brighter = E.score_predictions(es, [m.seq.value - 1.0 for _, m in es.items])
    assert fainter.limit_brighter.sum() == 0 and at.limit_brighter.sum() == 0
    assert brighter.limit_brighter.sum() == n_lim and len(brighter.limit_brighter) == n_lim


def test_the_difference_is_negative_when_the_first_method_is_better_and_needs_the_same_tokens():
    es = E.build_eval_set(stars(), k_per_mode=2)
    good = E.score_predictions(es, [m.seq.value + 0.05 for _, m in es.items])
    bad = E.score_predictions(es, [m.seq.value + 0.4 for _, m in es.items])
    d = E.difference(good, bad, n_boot=100)
    assert d["difference"] == pytest.approx(-0.35) and d["high"] < 0
    flipped = E.difference(bad, good, n_boot=100)
    assert flipped["low"] > 0
    other = E.score_predictions(E.build_eval_set(stars(5), k_per_mode=2), [m.seq.value for _, m in E.build_eval_set(stars(5), k_per_mode=2).items])
    with pytest.raises(ValueError, match="same tokens"):
        E.difference(good, other)


def test_the_error_is_split_by_era():
    years = np.r_[np.linspace(1895, 1950, 80), np.linspace(1972, 1988, 80)]
    mag = 12.0 + 0.01 * np.sin(years)
    s = [star_from(years, mag) for _ in range(4)]
    es = E.build_eval_set(s, k_per_mode=2)
    preds = [m.seq.value + np.where(m.seq.t + 1880 > 1969, 0.5, 0.0) for _, m in es.items]
    by = E.summary(E.score_predictions(es, preds), n_boot=50)["by_era"]
    assert by["before 1953"]["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert by["after 1969"]["rmse"] == pytest.approx(0.5) and by["1953 to 1969"]["n"] == 0


def test_the_baselines_run_over_the_whole_set_and_the_median_is_worse_than_interpolation_on_a_ramp():
    years = np.linspace(1900, 1980, 150)
    s = [star_from(years, 12.0 + 0.03 * (years - 1940) + np.random.default_rng(i).normal(0, 0.02, 150)) for i in range(6)]
    es = E.build_eval_set(s, k_per_mode=2)
    med = E.score_predictions(es, E.predict_all(es, B.median))
    lin = E.score_predictions(es, E.predict_all(es, B.interpolate))
    assert E.difference(lin, med, mode="random", n_boot=100)["high"] < 0


def test_a_wrong_number_of_predictions_is_refused():
    es = E.build_eval_set(stars(2), k_per_mode=1)
    with pytest.raises(ValueError, match="predictions"):
        E.score_predictions(es, [])
