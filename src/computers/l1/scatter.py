"""Scatter statistics of a star's lightcurve: how quiet it is, in two ways.

Both are measured on good detections only (`reject == 0`, a measured magnitude):

* **within** scatter: how far points stray from the median of their own 5-year block,
  a robust measure of plate-to-plate noise;
* **between** scatter: the 10th-to-90th percentile range of the 5-year block medians, in
  units of sigma. It sees what a slow trend, a step or a long wave does to a curve.
  A median absolute deviation would miss a step that splits the century in half, because
  the median then lies on one side of it.

The reference scatter of a star is the median over the `REFERENCE_STARS` stars nearest
to it in magnitude in the same tile. The Leavitt Set and the detrending comparison pool
both use these statistics, so they live in L1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from computers.data.timeconv import jd_to_year
from computers.thresholds import THRESHOLDS

__all__ = [
    "BLOCK_YEARS",
    "MAD_TO_SIGMA",
    "MIN_BLOCKS",
    "MIN_BLOCK_POINTS",
    "REASONS",
    "REFERENCE_STARS",
    "SPREAD_TO_SIGMA",
    "QuietResult",
    "StarStats",
    "quiet_reasons",
    "reference_scatter",
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


def reference_scatter(values: np.ndarray, mags: np.ndarray) -> np.ndarray:
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
class QuietResult:
    """Why each star is or is not quiet, and the reference scatter it was compared with."""

    reason: list[str]  # one of REASONS; "constant" means quiet
    ref_within: np.ndarray
    ref_between: np.ndarray

    @property
    def quiet(self) -> np.ndarray:
        return np.array([r == "constant" for r in self.reason], dtype=bool)


def quiet_reasons(
    refs: list[int],
    mags: np.ndarray,
    stats: dict[int, StarStats],
    vsx_sep_arcsec: dict[int, float | None],
) -> QuietResult:
    """Classify each star of one tile. The first reason that applies is the one reported.

    A star is quiet ("constant") when it has no VSX entry within the exclusion radius,
    enough good points, and both scatters at most `constant_scatter_factor` times the
    reference for stars of its brightness. The Leavitt Set and the detrending comparison
    pool share this test.
    """
    factor = THRESHOLDS["constant_scatter_factor"].value
    min_good = int(THRESHOLDS["constant_min_good_points"].value)
    exclude = THRESHOLDS["vsx_exclude_radius_arcsec"].value
    got = [stats.get(r, _EMPTY) for r in refs]
    within = np.array([g.scatter_within for g in got])
    between = np.array([g.scatter_between for g in got])
    ref_within, ref_between = reference_scatter(within, mags), reference_scatter(between, mags)
    reason = []
    for i, r in enumerate(refs):
        g, s = got[i], vsx_sep_arcsec.get(r)
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
    return QuietResult(reason, ref_within, ref_between)
