"""The century-trend check for KIC 8462852 (Tabby's Star), spec R9.

Without any comparison stars, Tabby's Star fades by 0.18 mag over the century in DASCH.
The plan's claim is that this trend is common: stars of similar brightness in the same
tile show similar trends, because of the plates and not the star. This check fits a
straight line to decade medians for the star and for its look-alikes, on the cleaned
curves and on the detrended curves, and says where the star sits in each distribution.

It is a diagnostic about the method on one reference star. It sets no threshold, and the
result is not a claim about the star. The Tabby tile is the held-out test tile of the
Leavitt Set, so nothing here may be used to tune anything.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from computers.data.timeconv import jd_to_year
from computers.l1.context import TileContext
from computers.l1.detrend import detrend
from computers.thresholds import THRESHOLDS

__all__ = ["KIC_8462852", "format_tabby_report", "run_tabby_check", "trend_slope"]

KIC_8462852 = 420061551442725
DECADE_MIN_POINTS = 20  # good points a decade needs for its median to count (plan 6.4's 20 per side)
MIN_DECADES = 4  # decades needed for a slope (plan 6.4)


def trend_slope(time_jd: np.ndarray, mag: np.ndarray) -> float | None:
    """Theil-Sen slope of decade medians, in mag per century. A positive slope is a fade.

    Only decades with at least `DECADE_MIN_POINTS` finite magnitudes count, and at least
    `MIN_DECADES` of them are needed. Returns None otherwise.
    """
    good = np.isfinite(mag)
    year, m = np.asarray(jd_to_year(np.asarray(time_jd)[good])), np.asarray(mag)[good]
    decade = np.floor(year / 10.0).astype(int)
    centres, medians = [], []
    for d in np.unique(decade):
        sel = decade == d
        if sel.sum() >= DECADE_MIN_POINTS:
            centres.append(10.0 * d + 5.0)
            medians.append(float(np.median(m[sel])))
    if len(centres) < MIN_DECADES:
        return None
    pairs = combinations(zip(centres, medians, strict=True), 2)  # (earlier, later) in time order
    slopes = [(m2 - m1) / (c2 - c1) for (c1, m1), (c2, m2) in pairs]
    return float(np.median(slopes) * 100.0)


def _summary(values: list[float], star: float | None) -> dict[str, Any]:
    v = np.array(values)
    lo, med, hi = np.percentile(v, [2.5, 50, 97.5]) if len(v) else (np.nan,) * 3
    return {
        "n": len(v), "median": float(med), "low": float(lo), "high": float(hi),
        "star": star,
        "inside": None if star is None or not len(v) else bool(lo <= star <= hi),
        "rank": None if star is None or not len(v) else float(np.mean(v < star)),
    }


LOOK_ALIKES = ("quiet", "non_vsx")


def run_tabby_check(
    data_dir: str | os.PathLike[str],
    *,
    say: Callable[[str], None] = print,
    meteor_policy: str = "reject",
    look_alikes: str = "quiet",
) -> dict[str, Any]:
    """Run the check. `look_alikes` is "quiet" (the Leavitt constants within 0.5 mag) or "non_vsx"
    (every star within 0.5 mag with enough points and no VSX entry, whatever its scatter).

    The quiet set is selected for a small between-block scatter, which truncates the spread of
    slopes; the non-VSX set does not, so it is the fairer comparison.
    """
    if look_alikes not in LOOK_ALIKES:
        raise ValueError(f"look_alikes must be one of {LOOK_ALIKES}")
    ctx = TileContext.from_silver(Path(data_dir), "tabby", meteor_policy=meteor_policy)
    window = THRESHOLDS["comparison_mag_window"].value
    i = ctx.index[KIC_8462852]
    usable = {"quiet": ("constant",), "non_vsx": ("constant", "scatter_within_high", "scatter_between_high")}[look_alikes]
    others = [
        r for r, j in ctx.index.items()
        if r != KIC_8462852 and ctx.quiet_reason[j] in usable and abs(ctx.mag[j] - ctx.mag[i]) <= window
    ]  # fmt: skip
    raw: dict[int, float] = {}
    det: dict[int, float] = {}
    not_detrended = 0
    for ref in [KIC_8462852, *others]:
        c = ctx.curve(ref)
        s = trend_slope(c.time, np.where(c.good, c.mag, np.nan))
        d = detrend(ctx, c)
        if not d.detrended:
            not_detrended += 1
        s2 = trend_slope(c.time, np.where(c.good, d.mag, np.nan)) if d.detrended else None
        if s is not None:
            raw[ref] = s
        if s2 is not None:
            det[ref] = s2
    kic = ctx.curve(KIC_8462852)
    pool = detrend(ctx, kic)
    result = {
        "meteor_policy": meteor_policy,
        "look_alike_set": look_alikes,
        "kic": {"B": float(ctx.mag[i]), "colour": float(ctx.colour[i]), "raw": raw.get(KIC_8462852),
                "detrended": det.get(KIC_8462852), "pool": pool.offsets.pool_size},
        "look_alikes": len(others),
        "look_alikes_not_detrended": not_detrended - (0 if pool.detrended else 1),
        "raw": _summary([v for r, v in raw.items() if r != KIC_8462852], raw.get(KIC_8462852)),
        "detrended": _summary([v for r, v in det.items() if r != KIC_8462852], det.get(KIC_8462852)),
    }
    say(format_tabby_report(result))
    return result


def format_tabby_report(r: dict[str, Any]) -> str:
    k = r["kic"]
    lines = [
        (
            f"KIC 8462852 (B {k['B']:.2f}, colour {k['colour']:.2f}) against {r['look_alikes']} {r['look_alike_set']} stars within "
            f"0.5 mag in the Tabby tile, meteor policy {r['meteor_policy']}; its comparison pool has {k['pool']} stars"
        ),
        "slope of decade medians, mag per century (positive is a fade):",
    ]
    for name in ("raw", "detrended"):
        s = r[name]
        star = "n/a" if s["star"] is None else f"{s['star']:+.3f}"
        where = "n/a" if s["inside"] is None else f"{'inside' if s['inside'] else 'OUTSIDE'} the central 95% ({s['rank']:.0%} of the others are below it)"
        lines.append(
            f"  {name:<10} star {star:>7}   look-alikes ({s['n']}): median {s['median']:+.3f}, "
            f"2.5% {s['low']:+.3f}, 97.5% {s['high']:+.3f}   -> {where}"
        )
    return "\n".join(lines)
