import numpy as np
import pyarrow as pa
import pytest

from computers.bench import constants as C
from computers.bench.inject import Signal
from computers.thresholds import THRESHOLDS

J2000 = 2451545.0


def jd(year):
    return J2000 + (np.asarray(year) - 2000.0) * 365.25


def curve(seed=0, n=400, noise=0.12, base=12.0, signal=None, first=1890.0, last=1985.0):
    rng = np.random.default_rng(seed)
    year = np.sort(rng.uniform(first, last, n))
    mag = base + rng.normal(0, noise, n)
    if signal is not None:
        mag = mag + signal.dm(year)
    return jd(year), mag, np.zeros(n, dtype=np.uint64)


# ------------------------------------------------------------------ star_stats


def test_flat_noise_has_scatter_near_the_noise_and_a_small_trend():
    s = C.star_stats(*curve(noise=0.12))
    assert s.n_good == 400 and s.n_blocks >= C.MIN_BLOCKS
    assert s.scatter_within == pytest.approx(0.12, abs=0.02)
    assert s.scatter_between < 0.04
    assert s.median_mag == pytest.approx(12.0, abs=0.03)
    assert 1890 <= s.first_year < s.last_year <= 1985


def test_a_step_raises_the_between_scatter_not_the_within_scatter():
    flat = C.star_stats(*curve(seed=1))
    step = C.star_stats(*curve(seed=1, signal=Signal("step", 1.0, 1940.0)))
    assert step.scatter_between > 8 * flat.scatter_between
    assert step.scatter_within == pytest.approx(flat.scatter_within, rel=0.15)


def test_a_slow_wave_raises_the_between_scatter():
    wave = C.star_stats(*curve(seed=2, signal=Signal("wave", 0.5, 1900.0, 40.0)))
    assert wave.scatter_between > 0.15


def test_rejected_points_and_non_detections_are_ignored():
    t, m, r = curve(n=300)
    r[:100] = 1  # rejected
    m[100:150] = np.nan  # non-detections
    assert C.star_stats(t, m, r).n_good == 150


def test_fewer_than_four_usable_blocks_gives_nan_scatter():
    t, m, r = curve(n=300, first=1900.0, last=1912.0)  # three 5-year blocks at most
    s = C.star_stats(t, m, r)
    assert s.n_good == 300 and np.isnan(s.scatter_within) and np.isnan(s.scatter_between)


def test_no_good_points():
    s = C.star_stats(np.array([jd(1900.0)]), np.array([np.nan]), np.array([0], dtype=np.uint64))
    assert s.n_good == 0 and np.isnan(s.median_mag)


def test_blocks_with_too_few_points_are_not_used():
    t, m, r = curve(n=300)
    s = C.star_stats(t, m, r)
    sparse = C.star_stats(t[:30], m[:30], r[:30])  # about 3 points per 5-year block
    assert s.n_blocks > sparse.n_blocks and np.isnan(sparse.scatter_within)


# ------------------------------------------------------------------- tile_stats


def test_tile_stats_groups_by_star_and_matches_star_stats():
    t1, m1, r1 = curve(seed=3, n=300)
    t2, m2, r2 = curve(seed=4, n=250)
    table = pa.table({
        "ref_number": pa.array([7] * 300 + [3] * 250, pa.uint64()),
        "time": np.r_[t1, t2], "magcal_magdep": pa.array(np.r_[m1, m2], pa.float32()),
        "reject": pa.array(np.r_[r1, r2], pa.uint64()),
    })
    got = C.tile_stats(table)
    assert set(got) == {3, 7}
    assert got[7].n_good == 300 and got[3].n_good == 250
    assert got[7].scatter_within == pytest.approx(
        C.star_stats(t1, m1.astype(np.float32).astype(float), r1).scatter_within
    )


def test_null_magnitudes_in_a_table_count_as_non_detections():
    t, m, r = curve(n=300)
    arr = pa.array(m, pa.float32()).to_pylist()
    arr[:60] = [None] * 60
    table = pa.table({"ref_number": pa.array([1] * 300, pa.uint64()), "time": t,
                      "magcal_magdep": pa.array(arr, pa.float32()), "reject": pa.array(r, pa.uint64())})
    assert C.tile_stats(table)[1].n_good == 240


# --------------------------------------------------------------- select_constants


def build(n_flat=60, extras=()):
    """A tile of flat stars of magnitude 11 to 13, plus named extra stars."""
    rng = np.random.default_rng(0)
    rows, stats = [], {}
    all_ids = list(range(1, n_flat + 1)) + [e[0] for e in extras]
    for i in range(1, n_flat + 1):
        rows.append((i, 11.0 + 2.0 * i / n_flat))
        stats[i] = C.star_stats(*curve(seed=i, noise=0.12 + rng.normal(0, 0.005)))
    for ref, mag, sig, n in extras:
        rows.append((ref, mag))
        stats[ref] = C.star_stats(*curve(seed=ref, n=n, signal=sig))
    stars = pa.table({"ref_number": pa.array([r for r, _ in rows], pa.uint64()),
                      "apass_b": pa.array([m for _, m in rows], pa.float32()),
                      "tile": ["m44"] * len(rows)})
    return stars, stats, all_ids


def match_table(ids, seps=None):
    seps = seps or {}
    return pa.table({"ref_number": pa.array(ids, pa.uint64()),
                     "sep_arcsec": pa.array([seps.get(i) for i in ids], pa.float32())})


def reasons(out):
    return dict(zip(out["ref_number"].to_pylist(), out["reason"].to_pylist(), strict=True))


def test_flat_stars_are_constants_and_each_failure_has_its_own_reason():
    extras = [
        (1001, 12.0, Signal("step", 1.0, 1940.0), 400),
        (1002, 12.0, None, 150),  # too few points
        (1003, 12.0, None, 400),  # will get a VSX neighbour
    ]
    stars, stats, ids = build(extras=extras)
    out = C.select_constants(stars, stats, match_table(ids, {1003: 12.0}))
    r = reasons(out)
    assert r[1001] == "scatter_between_high"
    assert r[1002] == "too_few_points"
    assert r[1003] == "vsx_match"
    assert sum(v == "constant" for k, v in r.items() if k <= 60) >= 55
    assert out["is_constant"].to_pylist() == [v == "constant" for v in out["reason"].to_pylist()]


def test_the_vsx_exclusion_radius_boundary():
    stars, stats, ids = build()
    edge = THRESHOLDS["vsx_exclude_radius_arcsec"].value
    out = C.select_constants(stars, stats, match_table(ids, {1: edge, 2: edge + 0.5}))
    r = reasons(out)
    assert r[1] == "vsx_match" and r[2] != "vsx_match"


def _stat(within, between):
    return C.StarStats(400, 12.0, within, between, 15, 1890.0, 1985.0)


def _fixed_tile(within, between):
    """Fifty reference stars with scatter (0.12, 0.04), plus one target star."""
    stars = pa.table({"ref_number": pa.array(range(1, 52), pa.uint64()),
                      "apass_b": pa.array([12.0] * 51, pa.float32()), "tile": ["m44"] * 51})
    stats = {i: _stat(0.12, 0.04) for i in range(1, 51)}
    stats[51] = _stat(within, between)
    return reasons(C.select_constants(stars, stats, match_table(range(1, 52))))[51]


@pytest.mark.parametrize(
    ("within", "between", "expected"),
    [
        (0.179, 0.04, "constant"),  # just under 1.5 times 0.12
        (0.181, 0.04, "scatter_within_high"),  # just over
        (0.12, 0.059, "constant"),  # just under 1.5 times 0.04
        (0.12, 0.061, "scatter_between_high"),
        (0.05, 0.01, "constant"),  # a quieter star is never excluded
    ],
)
def test_the_scatter_cut_is_a_factor_of_the_reference_at_both_edges(within, between, expected):
    assert THRESHOLDS["constant_scatter_factor"].value == 1.5
    assert _fixed_tile(within, between) == expected


def test_a_star_with_no_stats_is_too_few_points():
    stars, stats, ids = build()
    del stats[5]
    assert reasons(C.select_constants(stars, stats, match_table(ids)))[5] == "too_few_points"


def test_output_has_one_row_per_star_with_the_reference_values():
    stars, stats, ids = build()
    out = C.select_constants(stars, stats, match_table(ids))
    assert out.num_rows == stars.num_rows
    assert set(out["reason"].to_pylist()) <= set(C.REASONS)
    assert all(v > 0 for v in out["ref_within"].to_pylist())


def test_reference_ignores_undefined_values_and_gives_nan_when_all_are_undefined():
    vals = np.array([np.nan, 1.0, 3.0, np.nan])
    mags = np.array([10.0, 11.0, 12.0, 13.0])
    assert C._reference(vals, mags).tolist() == [2.0, 2.0, 2.0, 2.0]
    assert np.isnan(C._reference(np.full(4, np.nan), mags)).all()
