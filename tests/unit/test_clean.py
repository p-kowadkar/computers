import numpy as np
import pyarrow as pa
import pytest

from computers.l1 import clean as K
from computers.l1 import tags as T
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

NAN = np.nan


def arr(*v):
    return np.array(v, dtype=float)


# ------------------------------------------------------------------ step 2: standard


def test_standard_rejections_split_into_hard_and_soft():
    # HIGH_BACKGROUND 64, CLOSE_TO_LIMITING 8192 | LARGE_ISO_RMS 2048, LARGE_LOCAL_SMOOTH_RMS 4096,
    # BIN_DRAD_UNKNOWN 32768 | a bit that is not standard (UNCERTAIN_CATALOG_MAG) and zero
    a = arr(64, 8192, 2048, 4096, 32768, 524288, 0, 2048 | 64, NAN)
    det = np.ones(len(a), dtype=bool)
    hard, soft = K.step_standard(a, det)
    assert hard.tolist() == [True, True, False, False, False, False, False, True, False]
    assert soft.tolist() == [False, False, True, True, True, False, False, False, False]  # soft only


def test_non_detections_are_never_rejected_by_aflags():
    hard, soft = K.step_standard(arr(64, 2048), np.array([False, False]))
    assert not hard.any() and not soft.any()


# --------------------------------------------------------------- step 3: astrometry


def test_astrometry_boundary_both_sides_and_ra_wraps():
    dec0 = 20.0
    one_arcsec = 1 / 3600
    dra = lambda s: s * one_arcsec / np.cos(np.radians(dec0))
    ra = arr(10.0, 10.0 + dra(14.9), 10.0 + dra(15.1), 10.0, 10.0, 10.0 - dra(20))
    dec = arr(dec0, dec0, dec0, dec0 + 14.9 * one_arcsec, dec0 + 15.1 * one_arcsec, dec0)
    det = np.ones(6, dtype=bool)
    # median position is the first row's, because most rows sit there
    ra[[3, 4]] = 10.0
    flagged = K.step_astrometry(np.r_[ra, [10.0] * 8], np.r_[dec, [dec0] * 8], np.r_[det, [True] * 8])
    assert flagged[:6].tolist() == [False, False, True, False, True, True]
    wrapped = K.step_astrometry(arr(359.9999, 0.0001, 0.0001, 0.0001), arr(0, 0, 0, 0), np.ones(4, dtype=bool))
    assert not wrapped.any()  # 0.0002 degrees across the wrap is 0.7 arcsec, not 360 degrees


def test_astrometry_ignores_null_positions_and_non_detections():
    out = K.step_astrometry(arr(10, NAN, 99), arr(20, NAN, 50), np.array([True, True, False]))
    assert not out.any()
    assert not K.step_astrometry(arr(), arr(), np.array([], dtype=bool)).any()


def test_astrometry_limit_is_the_registry_value():
    assert THRESHOLDS["astrometry_max_sep_arcsec"].value == 15.0


# ------------------------------------------------------------------- step 4: series


def test_series_kinds_and_the_meteor_policy():
    s = np.array(["ai", "ac", "i", "zz"], dtype=object)
    kind, reject, unknown = K.step_series(s, "reject")
    assert kind.tolist() == [T.KIND_METEOR, T.KIND_PATROL, T.KIND_NARROW, T.KIND_UNKNOWN]
    assert reject.tolist() == [True, False, False, False]
    assert unknown.tolist() == [False, False, False, True]
    _, keep_reject, _ = K.step_series(s, "keep")
    assert not keep_reject.any()
    with pytest.raises(ValueError, match="meteor_policy"):
        K.step_series(s, "sometimes")


# ----------------------------------------------------------------- step 5: emulsion


def test_emulsion_boundaries_on_both_sides():
    ex = K.Exposures({"a": -0.52, "b": -0.53, "c": -0.54, "d": 0.0, "e": -1.5, "f": NAN, "g": 0.3})
    ids = np.array(list("abcdefgh"), dtype=object)  # "h" is not in the table
    non_blue, unknown = K.step_emulsion(ids, ex)
    assert non_blue.tolist() == [False, True, True, True, True, False, False, False]
    assert unknown.tolist() == [False, False, False, False, False, True, False, True]
    assert not (non_blue & unknown).any()


def test_exposures_from_table_reads_the_colour_term():
    t = pa.table({"exposure_id": ["x", "y"], "median_colorterm_apass": pa.array([-0.1, None], pa.float32())})
    ex = K.Exposures.from_table(t)
    assert ex.colourterm["x"] == pytest.approx(-0.1) and np.isnan(ex.colourterm["y"])


# ------------------------------------------------------------------- step 6: blends


def test_blend_boundary_both_sides_and_in_both_directions():
    b = arr(12.29, 12.31, 11.69, 11.71, NAN, 12.31)
    det = np.array([True, True, True, True, True, False])
    out = K.step_blend(b, 12.0, det)
    assert out.tolist() == [False, True, True, False, False, False]  # fainter or brighter, 0.3 mag
    assert not K.step_blend(b, None, det).any() and not K.step_blend(b, NAN, det).any()


# ---------------------------------------------------------------- step 8: plate edge


def test_plate_edge_is_the_radial_bin_9_bit():
    out = K.step_plate_edge(arr(16384, 16384 | 64, 64, 0, NAN), np.array([True, True, True, True, True]))
    assert out.tolist() == [True, True, False, False, False]
    assert not K.step_plate_edge(arr(16384), np.array([False])).any()


# ------------------------------------------------------------------ step 9: deep limit


def test_deep_limit_boundary_both_sides():
    mag = arr(12.0, 12.0, 12.0, NAN, NAN, NAN, NAN)
    limit = arr(14, 14, 14, 12.9, 13.1, 20.0, NAN)
    det = np.isfinite(mag)
    out = K.step_deep_limit(limit, det, np.ones(7, dtype=bool), mag)
    assert out.tolist() == [False, False, False, False, True, True, False]  # median 12.0, margin 1.0


def test_deep_limit_uses_only_surviving_detections_and_needs_one():
    mag = arr(12.0, 20.0, NAN)
    limit = arr(14, 14, 13.5)
    det = np.isfinite(mag)
    usable = np.array([True, False, True])  # the 20.0 detection was rejected
    assert K.step_deep_limit(limit, det, usable, mag).tolist() == [False, False, True]
    assert not K.step_deep_limit(arr(13.5), np.array([False]), np.array([True]), arr(NAN)).any()


# ----------------------------------------------------------------------- whole star


def rows(n=8, aflags=None, series="ac", mag=None):
    t = 2415000.0 + np.arange(n) * 100.0
    mag = [12.0] * n if mag is None else list(mag)
    mag = [None if (m is None or np.isnan(m)) else float(m) for m in mag]  # silver stores a null
    return pa.table({
        "time": t, "magcal_magdep": pa.array(mag, pa.float32()),
        "limiting_mag_local": pa.array(np.full(n, 14.0), pa.float32()),
        "exposure_id": [f"e{i}" for i in range(n)], "series": [series] * n,
        "aflags": pa.array([0] * n if aflags is None else aflags, pa.uint32()),
        "blended_mag": pa.array([None] * n, pa.float32()),
        "ra_deg": pa.array([10.0] * n), "dec_deg": pa.array([20.0] * n),
    })


STAR = K.StarInfo(5, 12.0)
BLUE = K.Exposures({f"e{i}": 0.1 for i in range(100)})


def test_clean_star_tags_without_deleting_or_changing_the_input():
    t = rows(aflags=[0, 64, 2048, 16384, 0, 0, 0, 0], mag=[12, 12, 12, 12, NAN, 12, 12, 12])
    before = pa.concat_tables([t])  # a separate reference to the same immutable data
    c = K.clean_star(t, STAR, BLUE)
    assert len(c) == t.num_rows and t.equals(before)
    counts = c.tag_counts()
    assert counts["STD_HARD"] == 1 and counts["STD_SOFT"] == 1 and counts["PLATE_EDGE"] == 1
    assert c.good.sum() == 5 and c.n_good == 5  # 8 rows - 1 non-detection - 2 rejected
    assert c.good_for_outburst.sum() == 6  # the soft one stays


def test_clean_star_sorts_rows_by_time_and_is_deterministic():
    t = rows()
    shuffled = t.take(pa.array([3, 1, 7, 0, 5, 2, 6, 4]))
    a, b = K.clean_star(t, STAR, BLUE), K.clean_star(shuffled, STAR, BLUE)
    assert np.array_equal(a.time, b.time) and np.array_equal(a.tags, b.tags)
    assert np.all(np.diff(a.time) >= 0)
    c = K.clean_star(shuffled, STAR, BLUE)
    assert np.array_equal(b.tags, c.tags)


def test_clean_star_under_both_meteor_policies():
    t = rows(series="ai")
    assert K.clean_star(t, STAR, BLUE, meteor_policy="reject").n_good == 0
    keep = K.clean_star(t, STAR, BLUE, meteor_policy="keep")
    assert keep.n_good == 8 and keep.tag_counts()["METEOR_REJECTED"] == 0
    assert (keep.kind == T.KIND_METEOR).all()


def test_a_star_with_no_detections_has_no_deep_limit_tags():
    t = rows(mag=[NAN] * 8)
    c = K.clean_star(t, STAR, BLUE)
    assert c.n_good == 0 and c.tag_counts()["DEEP_LIMIT_SUSPECT"] == 0


def test_missing_optional_columns_are_tolerated():
    t = rows().drop_columns(["blended_mag", "aflags"])
    assert K.clean_star(t, STAR, BLUE).n_good == 8


# ------------------------------------------------------------- step 1: source splitting


def plates(n, detected, ref=1, mag=12.0):
    """n plates e0..e{n-1}; the plates in `detected` have a detection, the rest a non-detection."""
    t = 2415000.0 + np.arange(n) * 100.0
    m = [mag if i in detected else None for i in range(n)]
    return pa.table({
        "ref_number": pa.array([ref] * n, pa.uint64()), "time": t,
        "magcal_magdep": pa.array(m, pa.float32()), "limiting_mag_local": pa.array([14.0] * n, pa.float32()),
        "exposure_id": [f"e{i}" for i in range(n)], "series": ["ac"] * n,
        "aflags": pa.array([0] * n, pa.uint32()), "blended_mag": pa.array([None] * n, pa.float32()),
        "ra_deg": [10.0] * n, "dec_deg": [20.0] * n,
    })


def test_overlap_is_the_share_of_the_smaller_curves_plates_the_other_also_has():
    a, b = plates(10, {0, 1, 2, 3}), plates(10, {3, 4, 5})
    assert K.overlap(a, b) == pytest.approx(1 / 3)  # one of the smaller curve's three plates
    assert K.overlap(a, b) == K.overlap(b, a)
    assert K.overlap(plates(4, set()), plates(4, set())) == 0.0
    assert K.overlap(a, plates(10, set())) == 0.0
    assert K.overlap(a, a) == 1.0
    long_curve, short = plates(100, set(range(100))), plates(100, set(range(10)), ref=2)
    assert K.overlap(long_curve, short) == 1.0  # a short curve inside a long one is not hidden by it


def test_interleaved_curves_merge_and_the_borrowed_rows_are_marked():
    target = plates(10, {0, 2, 4, 6, 8})
    neighbour = plates(10, {1, 3, 5, 7, 9}, ref=2, mag=12.4)
    merged, flags = K.merge_sources(target, [neighbour])
    assert flags == [True] and merged.num_rows == 10
    got = dict(zip(merged.column("exposure_id").to_pylist(), merged.column("source_merged").to_pylist(), strict=True))
    assert [got[f"e{i}"] for i in range(10)] == [False, True] * 5
    c = K.clean_star(merged, STAR, BLUE)
    assert c.n_good == 10 and c.tag_counts()["SOURCE_MERGED"] == 5
    assert np.all(np.isin(c.mag[c.has(Tag.SOURCE_MERGED)], np.float32(12.4)))


def test_overlap_boundary_both_sides():
    assert THRESHOLDS["source_split_overlap_max"].value == 0.1
    # the smaller curve has 20 detection plates, 2 shared: 0.10 is not under the limit, no merge
    target, nb = plates(60, set(range(40))), plates(60, set(range(38, 58)), ref=2)
    assert K.overlap(target, nb) == pytest.approx(0.1) and K.merge_sources(target, [nb])[1] == [False]
    # 21 detection plates, 2 shared: 0.095 is under, merge
    target, nb = plates(60, set(range(40))), plates(60, set(range(38, 59)), ref=2)
    assert K.overlap(target, nb) < 0.1 and K.merge_sources(target, [nb])[1] == [True]


def test_a_neighbour_detected_on_the_same_plates_is_a_real_neighbour_and_is_not_merged():
    target, nb = plates(10, set(range(10))), plates(10, set(range(10)), ref=2, mag=14.0)
    merged, flags = K.merge_sources(target, [nb])
    assert flags == [False] and not any(merged.column("source_merged").to_pylist())


def test_a_neighbour_with_no_detections_changes_nothing_and_a_plate_the_target_detected_keeps_its_row():
    target = plates(6, {0, 1})
    merged, flags = K.merge_sources(target, [plates(6, set(), ref=2)])
    assert flags == [False] and merged.num_rows == 6
    _, f2 = K.merge_sources(target, [plates(6, {1, 4, 5}, ref=2, mag=13.0)])
    assert f2 == [False]  # overlap 1 of 5 = 0.2
    # one shared plate out of the smaller curve's 12 is 0.083: it merges, but the shared plate keeps the target's row
    t3, nb3 = plates(30, set(range(12))), plates(30, set(range(11, 30)), ref=2, mag=13.0)
    m3, f3 = K.merge_sources(t3, [nb3])
    got = {e: (m, b) for e, m, b in zip(
        m3.column("exposure_id").to_pylist(),
        m3.column("magcal_magdep").to_numpy(zero_copy_only=False),
        m3.column("source_merged").to_pylist(), strict=True)}
    assert f3 == [True] and m3.num_rows == 30
    assert got["e11"] == (pytest.approx(12.0), False)  # the target's own detection is kept
    assert got["e12"] == (pytest.approx(13.0), True)  # a plate it missed takes the neighbour's


def test_neighbour_map_uses_the_radius_and_never_returns_the_star_itself():
    patrol = pa.table({"ref_number": [1, 2], "ra_deg": [10.0, 50.0], "dec_deg": [20.0, 20.0]})
    d = 1 / 3600 / np.cos(np.radians(20.0))
    nb = pa.table({"ref_number": [10, 11, 12, 1], "ra_deg": [10.0 + 4.9 * d, 10.0 + 5.1 * d, 50.0 + 2 * d, 10.0],
                   "dec_deg": [20.0] * 4})
    assert K.neighbour_map(patrol, nb) == {1: [10], 2: [12]}
    assert K.neighbour_map(patrol, nb, radius_arcsec=6.0) == {1: [10, 11], 2: [12]}
