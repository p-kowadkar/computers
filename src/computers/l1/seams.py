"""Seams: where a curve changes character, and whether a change can be trusted (plan 6.3).

Calibration and the mix of plates change across the Menzel Gap and when the main series
changes. A step that is visible only by comparing data on either side of such a seam is
indistinguishable from the seam, so it must not trigger. A change counts only when it is
visible inside one continuous stretch.

* `stretches` cuts a curve at the Menzel Gap and wherever the series mix changes sharply.
* `seam_only` says whether a change at a given epoch is a seam effect.

Both read the good points only. Epochs are decimal years.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from computers.data.timeconv import jd_to_year
from computers.l1.curve import Curve
from computers.thresholds import THRESHOLDS

__all__ = ["SeamResult", "Stretches", "seam_only", "stretches"]

WINDOW_YEARS = 5.0  # width of a window for the series-mix test
WINDOW_ORIGIN = 1880.0
MIN_WINDOW_POINTS = 10  # a window needs this many good points to take part in the test


@dataclass(frozen=True)
class Stretches:
    year: np.ndarray  # decimal year of every row of the curve
    stretch: np.ndarray  # stretch id of every row, from 0, increasing in time
    breaks: tuple[tuple[float, str], ...]  # (epoch, reason) in time order; reason is "gap_start", "gap_end" or "series"

    @property
    def n(self) -> int:
        return int(self.stretch.max()) + 1 if len(self.stretch) else 0


@dataclass(frozen=True)
class SeamResult:
    seam_only: bool
    reason: str  # "in_gap", "no_data_one_side", "across_break", "too_few_points" or "visible"


def _series_breaks(year: np.ndarray, series: np.ndarray, good: np.ndarray) -> list[float]:
    """Window boundaries where a main series appears or disappears."""
    share_min = THRESHOLDS["series_change_share"].value
    y, s = year[good], series[good]
    if not len(y):
        return []
    window = np.floor((y - WINDOW_ORIGIN) / WINDOW_YEARS).astype(int)
    names = np.unique(s)
    shares: dict[int, dict[str, float]] = {}
    for w in np.unique(window):
        sel = window == w
        if sel.sum() >= MIN_WINDOW_POINTS:
            shares[int(w)] = {str(n): float((s[sel] == n).mean()) for n in names}
    out = []
    for w, now in shares.items():
        before = shares.get(w - 1)
        if before is None:
            continue
        for n in now:
            a, b = before[n], now[n]
            if (a >= share_min and b < share_min / 2) or (b >= share_min and a < share_min / 2):
                out.append(WINDOW_ORIGIN + WINDOW_YEARS * w)
                break
    return out


def stretches(curve: Curve) -> Stretches:
    """Cut the curve into continuous stretches."""
    year = np.asarray(jd_to_year(curve.time), dtype=float)
    gap0 = THRESHOLDS["menzel_gap_start_year"].value
    gap1 = THRESHOLDS["menzel_gap_end_year"].value
    cuts = [(gap0, "gap_start"), (float(np.nextafter(gap1, np.inf)), "gap_end")]
    cuts += [(e, "series") for e in _series_breaks(year, curve.series, curve.good)]
    cuts.sort()
    ids = np.zeros(len(year), dtype=int)
    for epoch, _ in cuts:
        ids += year >= epoch
    return Stretches(year, ids, tuple(cuts))


def seam_only(
    curve: Curve, epoch: float, st: Stretches | None = None, *, min_points: int | None = None
) -> SeamResult:
    """Whether a change at `epoch` is visible only across a seam.

    It is a seam effect when the epoch lies in the Menzel Gap, when the nearest good
    points on either side of it belong to different stretches, or when either side holds
    fewer than `seam_min_points` good points inside that stretch. `min_points` overrides the
    registry value (for sweeps).
    """
    st = st or stretches(curve)
    if THRESHOLDS["menzel_gap_start_year"].value <= epoch <= THRESHOLDS["menzel_gap_end_year"].value:
        return SeamResult(True, "in_gap")
    good = curve.good
    before = np.flatnonzero(good & (st.year < epoch))
    after = np.flatnonzero(good & (st.year >= epoch))
    if not len(before) or not len(after):
        return SeamResult(True, "no_data_one_side")
    left, right = st.stretch[before[-1]], st.stretch[after[0]]
    if left != right:
        return SeamResult(True, "across_break")
    need = int(THRESHOLDS["seam_min_points"].value) if min_points is None else min_points
    in_stretch = good & (st.stretch == left)
    if (in_stretch & (st.year < epoch)).sum() < need or (in_stretch & (st.year >= epoch)).sum() < need:
        return SeamResult(True, "too_few_points")
    return SeamResult(False, "visible")
