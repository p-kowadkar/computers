"""Golden tests for L1 cleaning on frozen excerpts of real lightcurves (plan 6.7, spec R7).

The five fixtures are committed excerpts of real DASCH responses (T CrB, RY Cnc and three
other stars). The expected counts are frozen in `expected/l1_clean.json`; changing them
needs a decision record. Excerpts have no exposure colour terms, so every row is
emulsion-unknown here, and the detrending golden is in `tests/unit/test_detrend.py`.
"""

import json

import numpy as np
import pytest
from _l1 import CATALOG_MAG, EXPECTED, clean, current, fixtures, load, oracle_tags

from computers.l1.tags import Tag

T_CRB = 415593021255513
RY_CNC = 408395461194919
ERUPTION_YEARS = (1946.0, 1946.3)  # the 1946 eruption in the T CrB excerpt


def by_ref():
    return {d["ref_number"]: (d, table) for d, table in (load(p) for p in fixtures())}


def test_the_five_fixtures_are_present():
    refs = by_ref()
    assert T_CRB in refs and RY_CNC in refs and len(refs) == 5
    assert refs[RY_CNC][0]["star"] == "RY Cnc"
    assert set(CATALOG_MAG) == set(refs)


@pytest.mark.parametrize("path", fixtures(), ids=lambda p: p.stem)
@pytest.mark.parametrize("policy", ["reject", "keep"])
def test_the_vectorised_cleaner_matches_a_row_by_row_oracle(path, policy):
    d, table = load(path)
    c = clean(table, d["ref_number"], policy)
    assert c.tags.tolist() == oracle_tags(table, d["ref_number"], policy)


def test_the_frozen_counts_are_unchanged():
    expected = json.loads(EXPECTED.read_text())
    assert current() == expected  # a deliberate change needs a decision record and scripts/make_l1_golden.py


def test_the_cleaner_does_not_change_the_row_count_or_drop_a_detection():
    for d, table in by_ref().values():
        c = clean(table, d["ref_number"])
        assert len(c) == table.num_rows
        assert int(c.detected.sum()) == sum(m is not None for m in table.column("magcal_magdep").to_pylist())


def test_ry_cnc_tags():
    _, table = by_ref()[RY_CNC]
    c = clean(table, RY_CNC)
    counts = c.tag_counts()
    assert counts["BLEND_SUSPECT"] == 2 and counts["EMULSION_UNKNOWN"] == len(c)
    assert counts["METEOR_REJECTED"] > 0 and counts["ASTROMETRY"] > 0 and counts["PLATE_EDGE"] > 0
    assert counts["UNKNOWN_SERIES"] == 0 and c.n_good == 12
    good = c.mag[c.good]
    assert 12.5 < np.median(good) < 14.5  # a star of B 14.08; the 12 survivors span 0.9 mag
    assert np.min(c.mag[c.detected]) < 9.0 and np.min(good) > 12.0  # the 8.07 point is a defect, and is rejected


# ---------------------------------------------------------------- the T CrB eruption


def eruption_rows(c, years=ERUPTION_YEARS, brighter_by=1.0):
    base = float(np.median(c.mag[c.good]))
    yr = 2000.0 + (c.time - 2451545.0) / 365.25
    return c.detected & (yr >= years[0]) & (yr <= years[1]) & (c.mag <= base - brighter_by)


def test_t_crb_keeps_two_eruption_points_within_sixty_days_for_the_outburst_trigger():
    _, table = by_ref()[T_CRB]
    c = clean(table, T_CRB)  # the default policy rejects meteor series
    rows = eruption_rows(c)
    assert rows.sum() == 5  # eight detections in the window, five of them 1 mag or more above the baseline
    kept = rows & c.good_for_outburst
    days = c.time[kept]
    assert kept.sum() >= 2 and days.max() - days.min() <= 60.0  # exactly two survive: no margin
    assert not (rows & c.good).any()  # no eruption point survives the full set of rejections


def test_in_the_excerpt_the_default_meteor_policy_costs_the_eruption_its_peak():
    """In this excerpt six of the eight 1946 points, including the 7.73 peak, are on meteor plates.

    The excerpt is enriched with awkward rows, so this overstates the loss. The full curve has
    dozens of eruption points on other series (see test_l1_real.py).
    """
    _, table = by_ref()[T_CRB]
    reject, keep = clean(table, T_CRB, "reject"), clean(table, T_CRB, "keep")
    peak = int(np.nanargmin(reject.mag))
    assert reject.mag[peak] == pytest.approx(7.73, abs=0.01)
    assert reject.has(Tag.METEOR_REJECTED)[peak] and not reject.good_for_outburst[peak]
    assert not keep.has(Tag.METEOR_REJECTED)[peak]
    n_reject = int((eruption_rows(reject) & reject.good_for_outburst).sum())
    n_keep = int((eruption_rows(keep) & keep.good_for_outburst).sum())
    assert n_keep > n_reject >= 2  # keeping meteor plates keeps more of the eruption
    assert keep.good_for_outburst[peak] or keep.has(Tag.ASTROMETRY)[peak]  # the peak is then held back by astrometry alone
