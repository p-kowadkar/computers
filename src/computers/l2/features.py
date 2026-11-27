"""Hand-made features of one star's lightcurve (plan 7.1, spec S5 R1).

They are read from the curve L1 read (detrended when the star could be detrended, raw
otherwise) and describe its shape. Every shape feature is unchanged by adding a constant
to every magnitude. A feature that cannot be defined is NaN, never zero: the model's imputer
decides what to do with it, and records it.

Magnitudes run backwards: a positive skew is a tail towards fainter values, and a positive
curvature of the decade medians bends towards fainter.
"""
from __future__ import annotations

import numpy as np

from computers.l1.triggers import TriggerInput, _best_split
from computers.thresholds import THRESHOLDS

__all__ = ["FEATURES", "feature_vector", "features"]

FEATURES = (
    "amp_robust", "mad", "skew", "von_neumann", "chi2_red", "decade_slope", "decade_curv", "max_step",
    "longest_run", "frac_3sigma", "zp_spread", "n_good", "span_years", "n_decades",
)  # fmt: skip

MIN_POINTS = 5  # fewest good points for any shape feature
MIN_SERIES_POINTS = 5  # a series needs this many good points to give a zero-point


def _skew(x: np.ndarray) -> float:
    s = x.std()
    return float(np.mean((x - x.mean()) ** 3) / s**3) if s > 0 else float("nan")


def _von_neumann(x: np.ndarray, stretch: np.ndarray) -> float:
    """Mean squared successive difference over the variance, only between neighbours in one stretch."""
    var = x.var(ddof=1)
    same = stretch[1:] == stretch[:-1]
    if var <= 0 or not same.any():
        return float("nan")
    d = np.diff(x)[same]
    return float(np.mean(d**2) / (2 * var))  # about 1 for white noise, small for a smooth curve


def _longest_run(x: np.ndarray, median: float) -> float:
    side = np.sign(x - median)
    side = side[side != 0]
    if len(side) == 0:
        return float("nan")
    change = np.flatnonzero(np.diff(side) != 0)
    edges = np.r_[-1, change, len(side) - 1]
    return float(np.diff(edges).max() / len(side))


def _decades(year: np.ndarray, x: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Centres (in centuries) and medians of the decades that hold at least `k` good points."""
    dec = np.floor(year / 10.0).astype(int)
    keep = [d for d in np.unique(dec) if (dec == d).sum() >= k]
    return (
        np.array([(d * 10 + 5) / 100.0 for d in keep]),
        np.array([np.median(x[dec == d]) for d in keep]),
    )


def features(inp: TriggerInput) -> dict[str, float]:
    """The feature row of a prepared curve. Every name in `FEATURES` is present; undefined values are NaN."""
    nan = float("nan")
    out = dict.fromkeys(FEATURES, nan)
    g = inp.good
    n = int(g.sum())
    out["n_good"] = float(n)
    if n == 0:
        return out
    x, year, stretch = inp.mag[g], inp.year[g], inp.stretch.stretch[g]
    out["span_years"] = float(year[-1] - year[0])
    k = int(THRESHOLDS["seam_min_points"].value)
    cx, cm = _decades(year, x, k)
    out["n_decades"] = float(len(cx))
    if len(cx) >= 2:
        out["decade_slope"] = float(np.polyfit(cx, cm, 1)[0])  # mag per century
    if len(cx) >= 3:
        out["decade_curv"] = float(np.polyfit(cx, cm, 2)[0])  # mag per century squared
    if n < MIN_POINTS:
        return out
    med = float(np.median(x))
    p5, p95 = np.percentile(x, [5, 95])
    out["amp_robust"] = float(p95 - p5)
    out["mad"] = float(np.median(np.abs(x - med)))
    out["skew"] = _skew(x)
    out["von_neumann"] = _von_neumann(x, stretch)
    out["longest_run"] = _longest_run(x, med)
    noise = inp.noise
    if np.isfinite(noise) and noise > 0:
        out["chi2_red"] = float(np.sum(((x - med) / noise) ** 2) / (n - 1))
        out["frac_3sigma"] = float(np.mean(np.abs(x - med) > 3 * noise))
    steps = []
    for s in np.unique(stretch):
        xs = x[stretch == s]
        found = _best_split(xs, k)
        if found is not None:
            steps.append(abs(found[1]))
    if steps:
        out["max_step"] = float(max(steps))
    series = inp.curve.series[g]
    zps = [np.median(x[series == s]) for s in np.unique(series) if (series == s).sum() >= MIN_SERIES_POINTS]
    if len(zps) >= 2:
        out["zp_spread"] = float(np.std(zps))
    return out


def feature_vector(row: dict[str, float]) -> np.ndarray:
    """The row as an array in `FEATURES` order."""
    return np.array([row[name] for name in FEATURES], dtype=float)
