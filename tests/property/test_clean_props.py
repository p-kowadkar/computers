import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from computers.l1 import clean as K
from computers.l1.curve import Curve
from computers.l1.tags import REJECTING, Tag

REJECTING_TAGS = [Tag.STD_HARD, Tag.STD_SOFT, Tag.ASTROMETRY, Tag.METEOR_REJECTED]
EXPOSURES = K.Exposures({f"e{i}": 0.1 for i in range(60)})


@st.composite
def curves(draw, n=None):
    n = n or draw(st.integers(1, 60))
    mag = np.array(draw(st.lists(st.one_of(st.floats(8, 16), st.just(np.nan)), min_size=n, max_size=n)))
    tags = np.array(draw(st.lists(st.integers(0, (1 << 13) - 1), min_size=n, max_size=n)), dtype=np.uint64)
    return Curve(
        1, np.arange(n, dtype=float) + 2.4e6, mag, np.full(n, 15.0),
        np.array([f"e{i}" for i in range(n)]), np.array(["ac"] * n), np.full(n, 2, dtype=np.uint8), tags,
    )


@given(curves(), st.data(), st.sampled_from(REJECTING_TAGS))
@settings(max_examples=150, deadline=None)
def test_adding_a_rejection_never_increases_the_good_points(c, data, tag):
    mask = np.array(data.draw(st.lists(st.booleans(), min_size=len(c), max_size=len(c))))
    after = c.with_tag(mask, tag)
    assert after.n_good <= c.n_good
    assert after.good_for_outburst.sum() <= c.good_for_outburst.sum() or tag is Tag.STD_SOFT
    assert not np.any(after.good & ~c.good)  # a rejection never creates a good point
    assert np.array_equal(after.with_tag(mask, tag).tags, after.tags)  # and twice is once


@given(curves())
@settings(max_examples=100, deadline=None)
def test_flag_only_tags_never_change_the_good_set(c):
    flagged = c
    for tag in Tag:
        if not int(tag) & REJECTING:
            flagged = flagged.with_tag(np.ones(len(c), dtype=bool), tag)
    assert np.array_equal(flagged.good, c.good)


@given(st.integers(1, 80), st.integers(0, 2**31))
@settings(max_examples=60, deadline=None)
def test_cleaning_twice_gives_the_same_tags(n, seed):
    rng = np.random.default_rng(seed)
    kw = {
        "ref_number": 1,
        "time": np.sort(rng.uniform(2.41e6, 2.447e6, n)),
        "mag": np.where(rng.random(n) < 0.7, rng.uniform(10, 14, n), np.nan),
        "limit": rng.uniform(11, 16, n),
        "exposure_id": np.array([f"e{i}" for i in range(n)], dtype=object),
        "series": np.array(rng.choice(["ai", "ac", "i", "zz"], n), dtype=object),
        "aflags": rng.choice([0, 64, 2048, 16384, 8192 | 4096], n).astype(float),
        "blended_mag": np.where(rng.random(n) < 0.2, rng.uniform(10, 14, n), np.nan),
        "ra": 10 + rng.normal(0, 3e-4, n),
        "dec": 20 + rng.normal(0, 3e-4, n),
        "star": K.StarInfo(1, 12.0),
        "exposures": EXPOSURES,
    }
    a, b = K.clean_arrays(**kw), K.clean_arrays(**kw)
    assert np.array_equal(a.tags, b.tags) and np.array_equal(a.kind, b.kind)
    assert np.all(a.good <= a.usable) and np.all(a.good_for_outburst >= a.good)
