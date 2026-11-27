from pathlib import Path

import numpy as np
import pytest
from synth_leavitt import table, write_all

from computers.data import silver
from computers.l1 import context as X
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

ROOT = Path(__file__).resolve().parents[2]


# -------------------------------------------------------------------- shape statistics


def sample(n=20, centre=3.0):
    """n stars in one plate and brightness bin with widths spread evenly around `centre`."""
    fwhm = centre + (np.arange(n) - n / 2) * 0.01
    return np.zeros(n, dtype=np.int32), np.full(n, 12.3), fwhm, np.full(n, 0.1) + np.arange(n) * 1e-4


def test_shape_stats_use_a_robust_sigma_and_need_ten_stars():
    col, mag, fwhm, ell = sample()
    st = X.build_shape_stats(col, mag, fwhm, ell)
    assert len(st.keys) == 1
    assert st.fwhm_med[0] == pytest.approx(np.median(fwhm))
    assert st.fwhm_sig[0] == pytest.approx(1.4826 * np.median(np.abs(fwhm - np.median(fwhm))))
    few = X.build_shape_stats(*(a[:9] for a in sample()))
    assert len(few.keys) == 0 and X.build_shape_stats(*(a[:10] for a in sample())).keys.size == 1


def test_shape_outlier_boundary_on_both_sides_and_in_both_directions():
    col, mag, fwhm, ell = sample()
    st = X.build_shape_stats(col, mag, fwhm, ell)
    med, sig = st.fwhm_med[0], st.fwhm_sig[0]
    k = THRESHOLDS["shape_outlier_sigma"].value
    probe = np.array([med + 0.99 * k * sig, med + 1.01 * k * sig, med - 0.99 * k * sig, med - 1.01 * k * sig])
    out = st.outliers(np.zeros(4, np.int32), np.full(4, 12.3), probe, np.full(4, np.median(ell)), k)
    assert out.tolist() == [False, True, False, True]


def test_ellipticity_alone_can_make_an_outlier_and_a_missing_value_cannot():
    col, mag, fwhm, ell = sample()
    st = X.build_shape_stats(col, mag, fwhm, ell)
    c0 = np.zeros(2, np.int32)
    out = st.outliers(c0, np.full(2, 12.3), np.array([st.fwhm_med[0], np.nan]), np.array([1.0, np.nan]), 3.0)
    assert out.tolist() == [True, False]


def test_a_star_in_an_unknown_plate_or_brightness_bin_is_never_an_outlier():
    st = X.build_shape_stats(*sample())
    out = st.outliers(np.array([5, 0], np.int32), np.array([12.3, 15.0]), np.array([99.0, 99.0]),
                      np.array([9.0, 9.0]), 3.0)
    assert not out.any()
    assert not X.build_shape_stats(*(a[:0] for a in sample())).outliers(
        np.zeros(1, np.int32), np.array([12.0]), np.array([9.0]), np.array([9.0]), 3.0).any()


def test_brightness_bins_are_one_magnitude_wide_and_separate_plates_stay_apart():
    c = np.r_[np.zeros(10, np.int32), np.ones(10, np.int32)]
    mag = np.r_[np.full(10, 12.1), np.full(10, 12.1)]
    f = np.r_[np.full(10, 3.0) + np.arange(10) * 0.01, np.full(10, 6.0) + np.arange(10) * 0.01]
    st = X.build_shape_stats(c, mag, f, np.full(20, 0.1) + np.arange(20) * 1e-4)
    assert len(st.keys) == 2 and st.fwhm_med[0] != st.fwhm_med[1]
    assert X._shape_key(np.array([0, 0]), np.array([12.99, 13.0])).tolist() == [12, 13]


# ----------------------------------------------------------------------- a tile


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    d = tmp_path_factory.mktemp("ctx") / "data"
    write_all(d)
    return d


def test_the_matrix_has_one_row_per_star_and_one_column_per_exposure(data):
    ctx = X.TileContext.from_silver(data, "m44")
    assert len(ctx) == 50 and ctx.matrix.shape == (50, 25)
    assert ctx.refs == sorted(ctx.refs) and ctx.index[ctx.refs[3]] == 3
    empty = [ctx.exposure_ids[j] for j in np.flatnonzero(~np.isfinite(ctx.matrix).any(axis=0))]
    assert len(empty) == 5 and all(e.startswith("ai") for e in empty)  # meteor plates, rejected by default


def test_the_meteor_policy_changes_the_matrix_and_nothing_else(data):
    reject = X.TileContext.from_silver(data, "m44", meteor_policy="reject")
    keep = X.TileContext.from_silver(data, "m44", meteor_policy="keep")
    assert np.isfinite(keep.matrix).sum() > np.isfinite(reject.matrix).sum()
    ai = [i for i, e in enumerate(reject.exposure_ids) if e.startswith("ai")]
    assert not np.isfinite(reject.matrix[:, ai]).any() and np.isfinite(keep.matrix[:, ai]).any()
    other = [i for i in range(25) if i not in ai]
    assert np.array_equal(reject.matrix[:, other], keep.matrix[:, other], equal_nan=True)
    assert reject.quiet.tolist() == keep.quiet.tolist()  # quietness uses the raw statistics
    with pytest.raises(ValueError, match="meteor_policy"):
        X.TileContext.from_silver(data, "m44", meteor_policy="never")


def test_quiet_stars_exclude_vsx_matches_and_agree_with_the_leavitt_rule(data):
    ctx = X.TileContext.from_silver(data, "m44")
    assert ctx.quiet_reason[0] == "vsx_match" and ctx.quiet_reason[1] == "vsx_match"
    assert ctx.quiet[2:].sum() >= 40 and not ctx.quiet[:2].any()
    from computers.bench.constants import select_constants, tile_stats

    stars = silver.read_table(silver.silver_path(data, "stars", "m44"))
    expect = select_constants(stars, tile_stats(silver.read_table(silver.silver_path(data, "measurements", "m44"))),
                              silver.read_table(silver.silver_path(data, "vsx_match", "m44")))
    assert ctx.quiet_reason == expect.column("reason").to_pylist()


def test_curve_is_rebuilt_on_demand_with_tags(data):
    ctx = X.TileContext.from_silver(data, "m44")
    c = ctx.curve(ctx.refs[10])
    assert len(c) == 300 and np.all(np.diff(c.time) >= 0)
    counts = c.tag_counts()
    assert counts["METEOR_REJECTED"] == 60  # 5 of the 25 plates are series ai, 12 rows each
    assert counts["NON_BLUE"] == 0 and counts["EMULSION_UNKNOWN"] == 0
    assert counts["SHAPE_OUTLIER"] == 0  # constant widths have no spread, so no outliers
    assert np.array_equal(ctx.curve(ctx.refs[10]).tags, c.tags)  # deterministic


def test_a_stars_matrix_row_is_exactly_its_good_magnitudes():
    ctx = _tile_with_shapes()  # one measurement per star and exposure, as in real data
    c = ctx.curve(7)
    cols = ctx.columns_of(c)
    row = ctx.matrix[ctx.index[7]]
    assert np.array_equal(row[cols[c.good]], c.mag[c.good].astype(np.float32))
    assert np.isfinite(row).sum() == c.good.sum() == 10


def _tile_with_shapes(extra_outlier=True):
    """Thirty stars on ten plates with varied widths, and one star with a wild width on plate 3."""
    n_star, n_plate = 30, 10
    rng = np.random.default_rng(3)
    refs = np.repeat(np.arange(1, n_star + 1), n_plate)
    plate = np.tile(np.arange(n_plate), n_star)
    fwhm = 3.0 + rng.normal(0, 0.1, len(refs))
    if extra_outlier:
        fwhm[(refs == 7) & (plate == 3)] = 9.0
    n = len(refs)
    meas = table(
        "measurements", n, ref_number=refs.tolist(), exposure_id=[f"ac{p:05d}e1" for p in plate],
        time=(2415000.0 + plate * 3000.0 + refs).tolist(), magcal_magdep=[12.5] * n,
        series=["ac"] * n, platenum=plate.tolist(), reject=[0] * n,
        fwhm_image=fwhm.tolist(), ellipticity=(0.1 + rng.normal(0, 0.01, n)).tolist(),
    )
    stars = table("stars", n_star, ref_number=list(range(1, n_star + 1)), tile=["m44"] * n_star,
                  apass_b=[12.5] * n_star, color=[0.6] * n_star)
    expo = table("exposures", n_plate, exposure_id=[f"ac{p:05d}e1" for p in range(n_plate)],
                 series=["ac"] * n_plate, platenum=list(range(n_plate)))
    vsx = table("vsx_match", n_star, ref_number=list(range(1, n_star + 1)), tile=["m44"] * n_star,
                vsx_oid=[None] * n_star, vsx_name=[None] * n_star, vsx_type=[None] * n_star,
                vsx_period_d=[None] * n_star, vsx_amplitude_mag=[None] * n_star, sep_arcsec=[None] * n_star)
    return X.TileContext("m44", stars, meas, expo, vsx)


def test_shape_outliers_are_tagged_against_stars_on_the_same_plate():
    ctx = _tile_with_shapes()
    c = ctx.curve(7)
    flagged = np.flatnonzero(c.has(Tag.SHAPE_OUTLIER))
    assert len(flagged) == 1 and c.exposure_id[flagged[0]] == "ac00003e1"
    assert ctx.curve(8).tag_counts()["SHAPE_OUTLIER"] <= 1  # a normal star is almost never flagged
    assert _tile_with_shapes(False).curve(7).tag_counts()["SHAPE_OUTLIER"] == 0
    assert c.good.all()  # a flag does not reject


def test_an_exposure_missing_from_the_exposures_table_still_gets_a_column():
    ctx = _tile_with_shapes()
    assert len(ctx.exposure_ids) == 10
    expo = table("exposures", 9, exposure_id=[f"ac{p:05d}e1" for p in range(9)], series=["ac"] * 9,
                 platenum=list(range(9)))
    short = X.TileContext("m44", ctx._stars, ctx._measurements, expo, table("vsx_match", 30, ref_number=list(range(1, 31)),
                          tile=["m44"] * 30, vsx_oid=[None] * 30, vsx_name=[None] * 30, vsx_type=[None] * 30,
                          vsx_period_d=[None] * 30, vsx_amplitude_mag=[None] * 30, sep_arcsec=[None] * 30))
    assert short.exposure_ids[-1] == "ac00009e1" and short.matrix.shape == (30, 10)
    assert "ac00009e1" not in short.exposures.colourterm
    assert short.curve(1).tag_counts()["EMULSION_UNKNOWN"] == 1  # the star's one row on that plate


# ------------------------------------------------------------------- real data


@pytest.mark.slow
@pytest.mark.skipif(not (ROOT / "data" / "silver" / "stars" / "tile=m44-neighbours.parquet").exists(),
                    reason="needs the local neighbour pull")
def test_real_neighbours_merge_only_where_the_curves_interleave():
    ctx = X.TileContext.from_silver(ROOT / "data", "tabby")
    assert len(ctx._neighbour_rows) == 8
    merged = {ref: int(ctx.curve(ref).has(Tag.SOURCE_MERGED).sum()) for ref in ctx._neighbour_rows}
    assert merged[110302231462] > 0  # the 14-detection neighbour with no shared plate
    assert sum(1 for v in merged.values() if v) == 2  # the other six pairs were left apart (overlap above 0.1)
    assert all(v <= 14 for v in merged.values())
