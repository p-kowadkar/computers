"""The anomaly score (spec S5 R2, R9.2)."""
import numpy as np
import pytest

from computers.l2 import anomaly as A
from computers.l2.features import FEATURES

D = len(FEATURES)


def quiet_matrix(n=200, seed=0):
    return np.random.default_rng(seed).normal(0, 1, (n, D))


def test_a_strange_row_scores_higher_than_a_typical_one():
    m = A.fit(quiet_matrix())
    typical, strange = np.zeros(D), np.full(D, 8.0)
    s = A.score(m, np.vstack([typical, strange]))
    assert s[1] > s[0]


def test_the_same_model_and_features_give_the_same_score_and_a_refit_with_a_seed_does_too():
    x = quiet_matrix()
    a, b = A.fit(x, seed=3), A.fit(x, seed=3)
    assert np.array_equal(A.score(a, x[:20]), A.score(b, x[:20]))


def test_nan_is_filled_with_the_training_median_and_never_zero():
    x = quiet_matrix()
    m = A.fit(x)
    row = x[0].copy()
    row[2] = np.nan
    filled = row.copy()
    filled[2] = np.nanmedian(x[:, 2])
    assert A.score(m, row)[0] == pytest.approx(A.score(m, filled)[0])
    assert np.all(np.isfinite(m.fill))


def test_an_all_nan_column_still_fits_and_scores():
    x = quiet_matrix()
    x[:, 4] = np.nan
    m = A.fit(x)
    assert np.all(np.isfinite(A.score(m, x[:5])))


def test_save_and_load_give_the_same_scores(tmp_path):
    x = quiet_matrix()
    m = A.fit(x, snapshot_digest="abc")
    A.save(m, tmp_path / "m" / "a.joblib")
    m2 = A.load(tmp_path / "m" / "a.joblib")
    assert np.array_equal(A.score(m, x), A.score(m2, x)) and m2.snapshot_digest == "abc"


def test_a_model_with_another_feature_list_is_refused(tmp_path):
    import dataclasses

    m = dataclasses.replace(A.fit(quiet_matrix()), features=("a", "b"))
    A.save(m, tmp_path / "bad.joblib")
    with pytest.raises(ValueError, match="feature list"):
        A.load(tmp_path / "bad.joblib")


def test_wrong_shapes_and_tiny_training_sets_are_refused():
    with pytest.raises(ValueError):
        A.fit(np.zeros((50, D - 1)))
    with pytest.raises(ValueError):
        A.fit(np.zeros((5, D)))
    with pytest.raises(ValueError):
        A.score(A.fit(quiet_matrix()), np.zeros((2, D + 1)))
