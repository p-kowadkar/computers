"""Pick quiet stars to host injections and to measure false alarms (plan 10.1).

A constant star must be quiet in two ways, both measured on good detections only
(`reject == 0`, a measured magnitude):

* **within** scatter: how far points stray from the median of their own 5-year block,
  a robust measure of plate-to-plate noise;
* **between** scatter: the 10th-to-90th percentile range of the 5-year block medians,
  in units of sigma. It sees what a slow trend, a step or a long wave does to a curve.
  A median absolute deviation would miss a step that splits the century in half, because
  the median then lies on one side of it.

A star is compared with stars of similar brightness in its own tile: the reference is
the median of the statistic over the `REFERENCE_STARS` stars nearest in magnitude. It is
quiet when both of its scatters are at most `constant_scatter_factor` times that
reference. Stars with a VSX entry near them, or with too few points, are never constants.

Every star gets a first reason, so an exclusion can always be explained.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from computers.bench.inject import jd_to_year
from computers.thresholds import THRESHOLDS

__all__ = [
    "BLOCK_YEARS",
    "REASONS",
    "StarStats",
    "select_constants",
    "star_stats",
    "tile_stats",
]

BLOCK_YEARS = 5.0  # width of a block for the running-median trend
BLOCK_ORIGIN = 1880.0
MIN_BLOCK_POINTS = 10  # a block median needs this many good points
MIN_BLOCKS = 4  # a between-block scatter needs this many usable blocks
REFERENCE_STARS = 41  # stars nearest in magnitude that set the reference scatter
MAD_TO_SIGMA = 1.4826
SPREAD_TO_SIGMA = 2.5631  # the 10th-to-90th percentile range of a normal is 2.5631 sigma

REASONS = (
    "constant",
    "vsx_match",
    "too_few_points",
    "too_few_blocks",
    "scatter_within_high",
    "scatter_between_high",
    "no_reference",
)


@dataclass(frozen=True)
class StarStats:
    n_good: int
    median_mag: float
    scatter_within: float  # nan when there are too few usable blocks
    scatter_between: float  # nan when there are too few usable blocks
    n_blocks: int
    first_year: float
    last_year: float


_EMPTY = StarStats(0, np.nan, np.nan, np.nan, 0, np.nan, np.nan)


def star_stats(time_jd: np.ndarray, mag: np.ndarray, reject: np.ndarray) -> StarStats:
    """Statistics of one star from its measurements. Non-detections and rejected points are ignored."""
    time_jd = np.asarray(time_jd, dtype=float)
    mag = np.asarray(mag, dtype=float)
    good = np.isfinite(mag) & (np.asarray(reject) == 0) & np.isfinite(time_jd)
    n = int(good.sum())
    if n == 0:
        return _EMPTY
    year, m = jd_to_year(time_jd[good]), mag[good]
    block = np.floor((year - BLOCK_ORIGIN) / BLOCK_YEARS).astype(np.int64)
    medians, residuals = [], []
    for b in np.unique(block):
        sel = block == b
        if sel.sum() >= MIN_BLOCK_POINTS:
            med = float(np.median(m[sel]))
            medians.append(med)
            residuals.append(np.abs(m[sel] - med))
    within = between = np.nan
    if len(medians) >= MIN_BLOCKS:
        within = MAD_TO_SIGMA * float(np.median(np.concatenate(residuals)))
        lo, hi = np.percentile(np.array(medians), [10, 90])
        between = float(hi - lo) / SPREAD_TO_SIGMA
    return StarStats(
        n, float(np.median(m)), within, between, len(medians), float(year.min()), float(year.max())
    )


def tile_stats(measurements: pa.Table) -> dict[int, StarStats]:
    """`star_stats` for every star in a `measurements` table."""
    ref = measurements.column("ref_number").to_numpy()
    order = np.argsort(ref, kind="stable")
    ref = ref[order]
    time = measurements.column("time").to_numpy()[order]
    mag = measurements.column("magcal_magdep").to_numpy(zero_copy_only=False)[order].astype(float)
    reject = measurements.column("reject").to_numpy()[order]
    cuts = np.flatnonzero(np.diff(ref)) + 1
    out: dict[int, StarStats] = {}
    for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, len(ref)], strict=True):
        out[int(ref[lo])] = star_stats(time[lo:hi], mag[lo:hi], reject[lo:hi])
    return out


def _reference(values: np.ndarray, mags: np.ndarray) -> np.ndarray:
    """For each star, the median of `values` over the stars nearest to it in magnitude.

    Stars with an undefined value (NaN) do not count towards the median. A star whose
    window holds no defined value gets NaN.
    """
    n = len(values)
    order = np.argsort(mags, kind="stable")
    ranked = values[order]
    out = np.full(n, np.nan)
    half = REFERENCE_STARS // 2
    for pos in range(n):
        lo = max(0, min(pos - half, n - REFERENCE_STARS))
        window = ranked[lo : lo + REFERENCE_STARS]
        window = window[np.isfinite(window)]
        if window.size:
            out[order[pos]] = float(np.median(window))
    return out


def select_constants(
    stars: pa.Table,
    stats: dict[int, StarStats],
    vsx_match: pa.Table,
) -> pa.Table:
    """One row per star of a single tile: its statistics, and whether it is a constant.

    `stars` is the `stars` table and `vsx_match` the matching `vsx_match` table.
    """
    factor = THRESHOLDS["constant_scatter_factor"].value
    min_good = int(THRESHOLDS["constant_min_good_points"].value)
    exclude = THRESHOLDS["vsx_exclude_radius_arcsec"].value
    refs = [int(r) for r in stars.column("ref_number").to_pylist()]
    mags = np.asarray(stars.column("apass_b").to_numpy(zero_copy_only=False), dtype=float)
    sep = {
        int(r): s
        for r, s in zip(
            vsx_match.column("ref_number").to_pylist(),
            vsx_match.column("sep_arcsec").to_pylist(),
            strict=True,
        )
    }
    got = [stats.get(r, _EMPTY) for r in refs]
    within = np.array([g.scatter_within for g in got])
    between = np.array([g.scatter_between for g in got])
    ref_within, ref_between = _reference(within, mags), _reference(between, mags)
    reason, quiet = [], []
    for i, r in enumerate(refs):
        g = got[i]
        s = sep.get(r)
        if s is not None and s <= exclude:
            why = "vsx_match"
        elif g.n_good < min_good:
            why = "too_few_points"
        elif not np.isfinite(g.scatter_within):
            why = "too_few_blocks"
        elif not (np.isfinite(ref_within[i]) and np.isfinite(ref_between[i])):
            why = "no_reference"
        elif g.scatter_within > factor * ref_within[i]:
            why = "scatter_within_high"
        elif g.scatter_between > factor * ref_between[i]:
            why = "scatter_between_high"
        else:
            why = "constant"
        reason.append(why)
        quiet.append(why == "constant")

    def col(name: str) -> list:
        return [getattr(g, name) for g in got]

    return pa.table(
        {
            "ref_number": pa.array(refs, pa.uint64()),
            "tile": stars.column("tile"),
            "apass_b": pa.array(mags, pa.float32()),
            "n_good": pa.array(col("n_good"), pa.int32()),
            "n_blocks": pa.array(col("n_blocks"), pa.int32()),
            "first_year": pa.array(col("first_year"), pa.float64()),
            "last_year": pa.array(col("last_year"), pa.float64()),
            "scatter_within": pa.array(within, pa.float32()),
            "scatter_between": pa.array(between, pa.float32()),
            "ref_within": pa.array(ref_within, pa.float32()),
            "ref_between": pa.array(ref_between, pa.float32()),
            "reason": pa.array(reason, pa.string()),
            "is_constant": pa.array(quiet, pa.bool_()),
        }
    )
