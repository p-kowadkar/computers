import json

import numpy as np
import pytest
from synth_leavitt import J2000, write_all

from computers.bench import l1_report as R
from computers.bench import leavitt as L
from computers.bench import tabby_check as T
from computers.cli import main


def jd(year):
    return J2000 + (np.asarray(year, dtype=float) - 2000.0) * 365.25


# ------------------------------------------------------------------ trend_slope


def curve_years():
    return np.concatenate([np.linspace(1890 + 10 * d, 1899.9 + 10 * d, 30) for d in range(9)])  # nine decades


def test_a_linear_trend_gives_its_slope_in_mag_per_century():
    y = curve_years()
    for slope in (0.5, -0.3, 0.0):
        assert T.trend_slope(jd(y), 12.0 + slope * (y - 1940.0) / 100.0) == pytest.approx(slope, abs=0.02)


def test_the_slope_is_robust_to_one_bad_decade_and_ignores_sparse_decades():
    y = curve_years()
    m = 12.0 + 0.4 * (y - 1940.0) / 100.0
    bad = m.copy()
    bad[(y >= 1930) & (y < 1940)] += 3.0  # one decade wildly off
    assert T.trend_slope(jd(y), bad) == pytest.approx(0.4, abs=0.1)
    sparse = np.where((y >= 1890) & (y < 1900), np.nan, m)  # a decade with no points is skipped
    assert T.trend_slope(jd(y), sparse) == pytest.approx(0.4, abs=0.02)


def test_the_decade_point_boundary_and_the_decade_count_boundary():
    y = np.concatenate([np.linspace(1890 + 10 * d, 1899.9 + 10 * d, 20) for d in range(4)])
    assert T.trend_slope(jd(y), 12 + (y - 1890) / 100) is not None  # 20 points a decade, 4 decades
    assert T.trend_slope(jd(y[:-1]), 12 + (y[:-1] - 1890) / 100) is None  # the last decade drops to 19
    assert T.trend_slope(jd(y[:60]), 12 + (y[:60] - 1890) / 100) is None  # three decades


def test_the_sign_follows_the_magnitude_scale_a_fade_is_positive():
    y = curve_years()
    assert T.trend_slope(jd(y), 12.0 + (y - 1890) / 100) > 0  # the star gets fainter


def test_the_tabby_report_says_inside_or_outside():
    base = {"meteor_policy": "reject", "look_alike_set": "quiet", "look_alikes": 10,
            "kic": {"B": 12.4, "colour": 0.5, "pool": 40}}
    s = {"n": 10, "median": 0.0, "low": -0.1, "high": 0.1, "star": 0.2, "inside": False, "rank": 0.98}
    text = T.format_tabby_report({**base, "raw": s, "detrended": {**s, "star": 0.05, "inside": True, "rank": 0.6}})
    assert "OUTSIDE the central 95%" in text and "inside the central 95%" in text
    with pytest.raises(ValueError, match="look_alikes"):
        T.run_tabby_check("nowhere", look_alikes="all")


def test_the_summary_places_a_star_in_a_distribution():
    s = T._summary(list(np.linspace(-1, 1, 101)), 0.0)
    assert s["inside"] and s["rank"] == pytest.approx(0.5, abs=0.02)
    assert not T._summary(list(np.linspace(-1, 1, 101)), 1.5)["inside"]
    assert T._summary([], 0.1)["inside"] is None and T._summary([1.0], None)["star"] is None


# ----------------------------------------------------------------------- the report


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("l1rep") / "data"
    write_all(d)
    L.build_set(d, say=lambda _: None)
    return d


def test_the_l1_report_has_every_table_and_a_gap_that_is_always_seam_only(built):
    r = R.run_l1_report(built, say=lambda _: None)
    cfgs = {"raw", "l1_reject_clean", "l1_reject_detrended", "l1_keep_clean", "l1_keep_detrended"}
    assert cfgs <= set(r["configs"])
    for name in cfgs:
        c = r["configs"][name]
        assert set(c["false_alarms"]) == {"0.2", "0.3", "0.5"} and c["scatter"]["hosts"] > 0
        assert {x["family"] for x in c["crossings"]} == {"dip", "linear", "outburst", "step", "wave"}
    assert r["seams"]["gap"]["share"] == 1.0 and r["seams"]["gap"]["n"] > 0
    assert "reasons_for_non_gap_steps" in r["seams"]
    text = R.format_l1_report(r)
    for title in ("1. Scatter", "2. False alarms", "3. Completeness", "4. Steps judged seam_only"):
        assert title in text


def test_the_l1_command_writes_its_report(built, capsys):
    assert main(["bench", "l1", "--data-dir", str(built)]) == 0
    out = capsys.readouterr().out
    assert "L1 report on the dev split" in out
    assert json.loads((built / "reports" / "bench-l1.json").read_text())["seams"]["gap"]["share"] == 1.0
