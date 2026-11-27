"""The ranker and the L2 benchmark arms (spec S5 R3, R4)."""
import numpy as np
import pytest

from computers.bench import l2_bench as B
from computers.l2 import ranker as R
from computers.l2.features import FEATURES

ROW = {"kind": "outburst", "amplitude_mag": 2.0, "n_evidence": 4, "single_plate": False, "detrended": True,
       "n_series": 2, "anomaly": 0.5}  # fmt: skip


def test_design_reads_each_input_and_flips_detrended():
    x = R.design([ROW, {**ROW, "detrended": False, "kind": "dip"}], R.ALL_COLUMNS)
    cols = {c: i for i, c in enumerate(R.ALL_COLUMNS)}
    assert x[0, cols["not_detrended"]] == 0 and x[1, cols["not_detrended"]] == 1
    assert x[0, cols["kind_outburst"]] == 1 and x[1, cols["kind_outburst"]] == 0 and x[1, cols["kind_dip"]] == 1
    assert x[0, cols["log_n_evidence"]] == pytest.approx(np.log(4))


def test_an_unknown_input_is_refused():
    with pytest.raises(KeyError):
        R.design([ROW], ["amplitude_mag", "magic"])


def test_group_folds_keep_every_group_in_one_fold_and_use_all_folds():
    groups = [i % 17 for i in range(200)]
    f = R.group_folds(groups, 5, seed=1)
    assert set(f) == set(groups) and set(f.values()) == {0, 1, 2, 3, 4}
    assert f == R.group_folds(groups, 5, seed=1)  # reproducible


def toy(n_pos=300, n_neg=30, seed=0):
    rng = np.random.default_rng(seed)
    pos = [{**ROW, "amplitude_mag": float(rng.normal(2.5, 0.4)), "n_series": int(rng.integers(2, 4))} for _ in range(n_pos)]
    neg = [{**ROW, "amplitude_mag": float(rng.normal(1.2, 0.2)), "n_series": 1} for _ in range(n_neg)]
    x = R.design(pos + neg, R.ALL_COLUMNS)
    return x, np.r_[np.ones(n_pos), np.zeros(n_neg)]


def test_the_ranker_scores_real_above_false_and_a_fainter_amplitude_scores_lower():
    x, y = toy()
    m = R.fit(x, y, R.ALL_COLUMNS)
    p = R.predict(m, x)
    assert p[y == 1].mean() > p[y == 0].mean() + 0.3
    weak, strong = {**ROW, "amplitude_mag": 1.0}, {**ROW, "amplitude_mag": 3.0}
    a, b = R.predict(m, R.design([weak, strong], R.ALL_COLUMNS))
    assert a < b  # the weaker event must not outrank the stronger


def test_the_ranker_needs_both_classes():
    x, y = toy(n_neg=1)
    with pytest.raises(ValueError, match="at least 2"):
        R.fit(x, y, R.ALL_COLUMNS)


def test_with_two_negatives_it_falls_back_without_calibration_or_with_it_but_never_fails():
    x, y = toy(n_neg=2)
    assert R.fit(x, y, R.ALL_COLUMNS).n_neg == 2


# --------------------------------------------------------------------- the arms


def sig(host, family, strict_rows, relaxed_rows, missed=False, amp=1.0):
    return {"host_id": host, "family": family, "amplitude_index": 0, "amplitude": amp, "expected_missed": missed,
            "features": np.zeros(len(FEATURES)), "arms": {"strict": strict_rows, "relaxed": relaxed_rows}}  # fmt: skip


def synthetic(seed=0):
    rng = np.random.default_rng(seed)
    c = B.Collected()
    for h in range(60):
        f = rng.normal(0, 1, len(FEATURES))
        fa = [{**ROW, "amplitude_mag": float(rng.normal(1.1, 0.1)), "n_series": 1}] if h % 6 == 0 else []
        c.hosts[h] = {"features": f, "arms": {"strict": [], "relaxed": fa}}
    for i in range(400):
        h = i % 60
        strong = {**ROW, "amplitude_mag": float(rng.normal(2.5, 0.3)), "n_series": 1 + int(i % 2)}
        two = strong["n_series"] >= 2
        c.signals.append(sig(h, "outburst", [strong] if two else [], [strong]))
    return c


def test_arms_count_relaxed_false_alarms_and_l2_removes_them_without_losing_the_strong_signals():
    c = synthetic()
    res = B.arms(c, "plain")
    a = res["arms"]
    assert a["strict"]["false_alarms"]["any"]["survive"] == 0
    assert a["relaxed"]["false_alarms"]["any"]["survive"] == 10  # every sixth host of 60
    assert a["relaxed+l2"]["false_alarms"]["any"]["survive"] <= a["strict"]["false_alarms"]["any"]["survive"]
    assert a["relaxed"]["recovered"]["outburst"][0] == 400 > a["strict"]["recovered"]["outburst"][0] == 200
    assert a["relaxed+l2"]["recovered"]["outburst"][0] > a["strict"]["recovered"]["outburst"][0]  # L2 buys recovery back
    assert res["auc"] > 0.9 and res["gained_vs_strict"] > 0


def test_a_threshold_above_every_score_recovers_nothing_so_the_cap_can_always_be_met():
    c = synthetic()
    res = B.arms(c, "amplitude", strict_fa_cap=0)
    assert res["arms"]["relaxed+l2"]["false_alarms"]["any"]["survive"] == 0


def test_the_cross_fit_never_scores_a_host_with_a_model_that_saw_it():
    c = synthetic()
    cf = B.cross_fit(c, "plain+anomaly", k=5, seed=2)
    assert all(np.isfinite(r["score"]) for r in cf["neg"] + cf["pos"])
    folds = R.group_folds(sorted(c.hosts), 5, 2)
    assert set(folds) == set(c.hosts)  # every host belongs to exactly one fold


def test_expected_missed_signals_are_not_training_labels():
    c = synthetic()
    for s in c.signals[:100]:
        s["expected_missed"] = True
    cf = B.cross_fit(c, "plain")
    assert cf["n_pos"] == 300


def test_calibration_bins_cover_every_row():
    y = np.r_[np.zeros(50), np.ones(50)]
    p = np.linspace(0, 1, 100)
    bins = B.calibration(y, p, bins=5)
    assert sum(b["n"] for b in bins) == 100 and bins[0]["share_real"] < bins[-1]["share_real"]


def test_the_gain_interval_is_positive_when_the_ranker_separates_and_zero_at_a_tie():
    c = synthetic()
    cf = B.cross_fit(c, "plain")
    g = B.outburst_gain_interval(c, cf, 0, n_boot=50)
    assert g["low"] <= g["median"] <= g["high"] and g["median"] > 0  # relaxed+L2 recovers the one-series signals too
    for r in cf["neg"] + cf["pos"]:
        r["score"] = 0.5  # no information
    flat = B.outburst_gain_interval(c, cf, 0, n_boot=50)
    assert flat["median"] <= 0  # a tie scores everything above the false alarms or nothing, never a gain
