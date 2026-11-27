"""Baselines for masked reconstruction (spec S6 R4.2).

Each predicts the offset of every hidden detection from the visible detections of the same
sample. "Visible" means not hidden and not a limit. Offsets are from the star's median, so
the median baseline predicts 0. None of these learns anything.
"""
from __future__ import annotations

import numpy as np

from computers.ml.masking import Masked

__all__ = ["BASELINES", "interpolate", "median", "nearest_in_series"]

K_NEAREST = 5


def _visible(m: Masked) -> np.ndarray:
    s = m.seq
    return ~s.pad & ~m.hidden & ~s.is_limit


def median(m: Masked) -> np.ndarray:
    """The star's median: offset 0 everywhere."""
    return np.zeros(len(m.seq.pad))


def interpolate(m: Masked) -> np.ndarray:
    """Linear interpolation in time between the nearest visible detections; the nearer one beyond the ends."""
    s = m.seq
    vis = np.flatnonzero(_visible(m))
    out = np.zeros(len(s.pad))
    if len(vis) == 0:
        return out
    out[:] = np.interp(s.t, s.t[vis], s.value[vis])  # np.interp holds the end values beyond the ends
    return out


def nearest_in_series(m: Masked, k: int = K_NEAREST) -> np.ndarray:
    """The median of the `k` nearest visible detections of the same series, or of any series if the series has none."""
    s = m.seq
    vis = np.flatnonzero(_visible(m))
    out = np.zeros(len(s.pad))
    if len(vis) == 0:
        return out
    for i in np.flatnonzero(m.hidden):
        same = vis[s.series[vis] == s.series[i]]
        pool = same if len(same) else vis
        near = pool[np.argsort(np.abs(s.t[pool] - s.t[i]), kind="stable")[:k]]
        out[i] = float(np.median(s.value[near]))
    return out


BASELINES = {"median": median, "interpolate": interpolate, "nearest5_series": nearest_in_series}
