import numpy as np
import pytest

from computers.l1 import tags as T
from computers.l1.curve import Curve
from computers.l1.tags import Tag


def make(n=6, mag=None, tags=None, ref=7):
    mag = np.array([12.0, np.nan, 12.1, 12.2, np.nan, 12.3] if mag is None else mag)
    return Curve(
        ref, np.arange(n, dtype=float) + 2415000.0, mag, np.full(n, 14.0),
        np.array([f"ac{i:05d}e1" for i in range(n)]), np.array(["ac"] * n),
        np.full(n, T.KIND_PATROL, dtype=np.uint8),
        np.zeros(n, dtype=np.uint64) if tags is None else np.array(tags, dtype=np.uint64),
    )


def test_tag_bits_are_distinct_single_bits():
    values = [int(t) for t in Tag]
    assert len(values) == len(set(values))
    assert all(v & (v - 1) == 0 and v > 0 for v in values)


def test_rejecting_sets_are_made_of_known_tags_and_differ_by_the_soft_bit_only():
    assert T.REJECTING == int(Tag.STD_HARD | Tag.STD_SOFT | Tag.ASTROMETRY | Tag.METEOR_REJECTED)
    assert T.REJECTING & ~T.REJECTING_FOR_OUTBURST == int(Tag.STD_SOFT)
    for flag_only in (Tag.NON_BLUE, Tag.EMULSION_UNKNOWN, Tag.BLEND_SUSPECT, Tag.SHAPE_OUTLIER,
                      Tag.PLATE_EDGE, Tag.DEEP_LIMIT_SUSPECT, Tag.UNKNOWN_SERIES, Tag.SOURCE_MERGED,
                      Tag.NO_OFFSET):
        assert not (T.REJECTING & int(flag_only)), flag_only


def test_the_aflag_masks_are_the_five_standard_bits_of_daschlab():
    assert T.AFLAG_STANDARD_MASK == (64 | 2048 | 4096 | 8192 | 32768)
    assert T.AFLAG_HARD_MASK & T.AFLAG_SOFT_MASK == 0
    assert T.AFLAG_PLATE_EDGE == 16384 and not T.AFLAG_PLATE_EDGE & T.AFLAG_STANDARD_MASK


def test_row_sets_follow_the_tags():
    c = make(tags=[0, 0, int(Tag.STD_HARD), int(Tag.STD_SOFT), int(Tag.METEOR_REJECTED), int(Tag.NON_BLUE)])
    assert c.detected.tolist() == [True, False, True, True, False, True]
    assert c.good.tolist() == [True, False, False, False, False, True]  # a flag-only tag keeps the point
    assert c.good_for_outburst.tolist() == [True, False, False, True, False, True]  # soft stays
    assert c.usable.tolist() == [True, True, False, False, False, True]  # non-detections stay unless rejected
    assert c.n_good == 2


def test_good_for_outburst_is_never_smaller_than_good():
    rng = np.random.default_rng(0)
    c = make(n=500, mag=np.where(rng.random(500) < 0.7, 12.0, np.nan),
             tags=rng.integers(0, 1 << 13, 500))
    assert np.all(c.good_for_outburst >= c.good) and c.good_for_outburst.sum() >= c.good.sum()


def test_usable_rows_keeps_the_order_and_never_the_rejected():
    c = make(tags=[0, 0, int(Tag.ASTROMETRY), 0, int(Tag.METEOR_REJECTED), 0])
    u = c.usable_rows()
    assert len(u) == 4 and u.time.tolist() == c.time[[0, 1, 3, 5]].tolist()
    assert u.ref_number == 7 and u.good.sum() == 3


def test_with_magnitudes_keeps_tags_and_recomputes_the_good_set():
    c = make(tags=[0, 0, int(Tag.STD_HARD), 0, 0, 0])
    inj = c.with_magnitudes(np.array([np.nan, 12.0, 12.0, 12.0, 12.0, np.nan]))
    assert inj.tags.tolist() == c.tags.tolist()
    assert inj.good.tolist() == [False, True, False, True, True, False]
    assert c.mag[0] == 12.0  # the original is unchanged


def test_curves_are_read_only_and_never_share_memory_with_subsets():
    c = make()
    with pytest.raises(ValueError):
        c.mag[0] = 1.0
    s = c.subset(np.ones(len(c), dtype=bool))
    assert not np.shares_memory(s.mag, c.mag) and len(s) == len(c)


def test_validation():
    with pytest.raises(ValueError, match="rows"):
        Curve(1, np.arange(3.0), np.zeros(2), np.zeros(3), np.array(["a"] * 3), np.array(["a"] * 3),
              np.zeros(3, dtype=np.uint8), np.zeros(3, dtype=np.uint64))
    c = make()
    with pytest.raises(ValueError, match="sorted"):
        Curve(1, c.time[::-1].copy(), c.mag.copy(), c.limit.copy(), c.exposure_id.copy(),
              c.series.copy(), c.kind.copy(), c.tags.copy())
    with pytest.raises(ValueError):
        c.with_magnitudes(np.zeros(3))
    with pytest.raises(ValueError):
        c.subset(np.ones(3, dtype=bool))


def test_tag_counts_names_every_tag():
    c = make(tags=[int(Tag.NON_BLUE | Tag.PLATE_EDGE), 0, 0, int(Tag.NON_BLUE), 0, 0])
    counts = c.tag_counts()
    assert set(counts) == {t.name for t in Tag}
    assert counts["NON_BLUE"] == 2 and counts["PLATE_EDGE"] == 1 and counts["ASTROMETRY"] == 0


def test_a_curve_with_fewer_than_fifty_good_detections_is_killed_on_both_sides_of_the_limit():
    from computers.thresholds import THRESHOLDS

    assert THRESHOLDS["min_good_points"].value == 50
    for n_good, expected in ((49, "too_few_points"), (50, None)):
        n = n_good + 10
        mag = np.r_[np.full(n_good, 12.0), np.full(10, np.nan)]  # ten non-detections do not count
        c = Curve(1, np.arange(n, dtype=float) + 2.4e6, mag, np.full(n, 14.0), np.array([f"e{i}" for i in range(n)]),
                  np.array(["ac"] * n), np.full(n, 2, dtype=np.uint8), np.zeros(n, dtype=np.uint64))
        assert c.kill_reason == expected and c.n_good == n_good
    rejected = make(n=60, mag=np.full(60, 12.0), tags=[int(Tag.STD_HARD)] * 11 + [0] * 49)
    assert rejected.n_good == 49 and rejected.kill_reason == "too_few_points"  # rejected points do not count either
