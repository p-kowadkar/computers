"""Neighbour-ensemble detrending (plan 6.2).

For a target star, choose comparison stars of similar brightness and colour in the same
tile. Each comparison's residual on a plate is its magnitude minus its own median. The
median residual over the comparisons is that plate's offset: the systematic shift every
star near this brightness and colour felt on that plate. Subtracting it from the target
removes what the plate did and leaves what the star did.

Two things are kept deliberately simple:

* One pass. The baselines are not re-estimated after the offsets.
* The per-series zero-point is reported, not subtracted. Plate series cluster in time, so
  subtracting each series' median would also remove real long-term change.

Comparison stars always come from the original curves. An injected host is detrended
with the offsets of its un-injected self, so no comparison is ever injected.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from computers.l1.context import TileContext
from computers.l1.curve import Curve
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

__all__ = [
    "Detrended",
    "Offsets",
    "Pool",
    "detrend",
    "detrended_magnitudes",
    "offsets_for",
    "plate_offsets",
    "select_pool",
]

FEW_COMPARISONS = "few_comparisons"


@dataclass(frozen=True)
class Pool:
    indices: np.ndarray  # rows of the tile matrix, nearest first
    colour_cut: bool  # False when the target has no colour and only magnitude was used


@dataclass(frozen=True)
class Offsets:
    offset: np.ndarray  # per exposure column, NaN where a plate has no valid offset
    count: np.ndarray  # comparison stars with a measurement on each plate
    pool_size: int
    colour_cut: bool
    kill_reason: str | None  # "few_comparisons" when the pool was too small to detrend


@dataclass(frozen=True)
class Detrended:
    curve: Curve  # the cleaned curve, with NO_OFFSET tags added
    mag: np.ndarray  # detrended magnitude; NaN for a non-detection or a point with no offset
    limit: np.ndarray  # the plate limit with the same offset applied
    offsets: Offsets
    detrended: bool  # False when the target could not be detrended and `mag` is the raw magnitude
    zero_points: dict[str, tuple[float, int]]  # series -> (median detrended magnitude, good points)

    @property
    def kill_reason(self) -> str | None:
        return self.offsets.kill_reason


def plate_offsets(residuals: np.ndarray, min_per_plate: int) -> tuple[np.ndarray, np.ndarray]:
    """(offset, count) per plate from comparison residuals shaped (stars, plates).

    The offset is the median over the stars that measured the plate, and NaN when fewer
    than `min_per_plate` did.
    """
    count = np.isfinite(residuals).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN plates give NaN, as intended
        offset = np.nanmedian(residuals, axis=0)
    offset = np.where(count >= min_per_plate, offset, np.nan)
    return offset.astype(np.float64), count


def select_pool(ctx: TileContext, ref: int) -> Pool:
    """Comparison stars for a target: quiet stars of similar brightness and colour, nearest first."""
    i = ctx.index[ref]
    mag_window = THRESHOLDS["comparison_mag_window"].value
    colour_window = THRESHOLDS["comparison_colour_window"].value
    maximum = int(THRESHOLDS["comparison_max"].value)
    dmag = np.abs(ctx.mag - ctx.mag[i])
    ok = ctx.quiet & (dmag <= mag_window)
    ok[i] = False
    distance = dmag / mag_window
    colour_cut = bool(np.isfinite(ctx.colour[i]))
    if colour_cut:
        dcol = np.abs(ctx.colour - ctx.colour[i])
        ok &= np.isfinite(dcol) & (dcol <= colour_window)
        distance = np.maximum(distance, np.where(np.isfinite(dcol), dcol, np.inf) / colour_window)
    idx = np.flatnonzero(ok)
    refs = np.asarray(ctx.refs)[idx]
    order = np.lexsort((refs, distance[idx]))  # nearest first, ties by reference number
    return Pool(idx[order][:maximum], colour_cut)


def offsets_for(ctx: TileContext, ref: int) -> Offsets:
    """The plate offsets for a target, cached on the context. Built from original curves only."""
    cache = ctx.offsets_cache
    if ref in cache:
        return cache[ref]
    pool = select_pool(ctx, ref)
    n_exp = ctx.matrix.shape[1]
    if len(pool.indices) < int(THRESHOLDS["comparison_min"].value):
        result = Offsets(np.full(n_exp, np.nan), np.zeros(n_exp, dtype=int), len(pool.indices),
                         pool.colour_cut, FEW_COMPARISONS)
    else:
        residuals = ctx.matrix[pool.indices].astype(np.float64) - ctx.baseline[pool.indices, None]
        offset, count = plate_offsets(residuals, int(THRESHOLDS["comparison_min_per_plate"].value))
        result = Offsets(offset, count, len(pool.indices), pool.colour_cut, None)
    cache[ref] = result
    return result


def detrended_magnitudes(offsets: Offsets, columns: np.ndarray, mag: np.ndarray) -> np.ndarray:
    """Just the detrended magnitudes: the raw ones when the pool was too small, NaN where a plate has no offset."""
    if offsets.kill_reason is not None:
        return np.asarray(mag, dtype=float).copy()
    return np.asarray(mag, dtype=float) - offsets.offset[columns]  # NaN offsets give NaN


def detrend(
    ctx: TileContext, curve: Curve, *, offsets: Offsets | None = None, columns: np.ndarray | None = None
) -> Detrended:
    """Detrend a target's curve against its comparison pool.

    `curve` may carry injected magnitudes: the offsets come from the star's own un-injected
    pool, which does not depend on the target's magnitudes. A point on a plate with no valid
    offset is tagged `NO_OFFSET` and has no detrended value. WHEN the pool is too small the
    raw magnitudes are returned, flagged `detrended=False`, and nothing is tagged. `columns`
    (from `ctx.columns_of`) can be passed to skip the exposure lookup on repeated calls.
    """
    offsets = offsets or offsets_for(ctx, curve.ref_number)
    if offsets.kill_reason is not None:
        return Detrended(curve, curve.mag.copy(), curve.limit.copy(), offsets, False, _zero_points(curve, curve.mag))
    off = offsets.offset[ctx.columns_of(curve) if columns is None else columns]
    missing = ~np.isfinite(off)
    tagged = curve.with_tag(missing, Tag.NO_OFFSET)
    mag = np.where(missing, np.nan, curve.mag - off)
    limit = np.where(missing, np.nan, curve.limit - off)
    return Detrended(tagged, mag, limit, offsets, True, _zero_points(tagged, mag))


def _zero_points(curve: Curve, mag: np.ndarray) -> dict[str, tuple[float, int]]:
    """The median detrended magnitude of the good points in each series. Reported, not subtracted."""
    good = curve.good & np.isfinite(mag)
    out: dict[str, tuple[float, int]] = {}
    for s in np.unique(curve.series[good]):
        sel = good & (curve.series == s)
        out[str(s)] = (float(np.median(mag[sel])), int(sel.sum()))
    return out
