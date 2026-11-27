"""Hand-built curves for the trigger tests."""

from __future__ import annotations

import numpy as np

from computers.l1 import triggers as T
from computers.l1.curve import Curve
from computers.l1.tags import Tag

J2000 = 2451545.0


def jd(year):
    return J2000 + (np.asarray(year, dtype=float) - 2000.0) * 365.25


def make_curve(years, mag, *, series="ac", tags=None, limit=15.0, ref=1) -> Curve:
    """A curve from decimal years and magnitudes (NaN is a non-detection)."""
    years = np.asarray(years, dtype=float)
    n = len(years)
    order = np.argsort(years, kind="stable")
    ser = np.array([series] * n) if isinstance(series, str) else np.asarray(series)
    tg = np.zeros(n, dtype=np.uint64) if tags is None else np.asarray(tags, dtype=np.uint64)
    return Curve(
        ref, jd(years)[order], np.asarray(mag, dtype=float)[order], np.full(n, limit),
        np.array([f"e{i:05d}" for i in range(n)]), ser[order], np.full(n, 2, dtype=np.uint8), tg[order],
    )


def flat(n=200, base=12.0, noise=0.1, seed=0, first=1895.0, last=1950.0):
    """A quiet star between `first` and `last`, sorted by time."""
    rng = np.random.default_rng(seed)
    years = np.sort(rng.uniform(first, last, n))
    return years, base + rng.normal(0, noise, n)


def with_points(years, mag, extra_years, extra_mag, **kw):
    """The flat curve plus extra points (an outburst, a dip)."""
    return make_curve(np.r_[years, extra_years], np.r_[mag, extra_mag], **kw)


STD_HARD, STD_SOFT = int(Tag.STD_HARD), int(Tag.STD_SOFT)


DAY = 1 / 365.25  # one day in years


def quiet(n=200, noise=0.05, seed=0, hole=None):
    """A quiet star; `hole=(lo, hi)` leaves that stretch of years empty."""
    years, mag = flat(n, noise=noise, seed=seed)
    if hole:
        keep = (years < hole[0]) | (years > hole[1])
        years, mag = years[keep], mag[keep]
    return years, mag


def baseline_of(years, mag):
    return T.prepare(make_curve(years, mag)).baseline


def burst(depths, offsets_days, *, noise=0.05, start=1920.0, tags=None, series="ac", **kw):
    """The quiet star plus points `depths` mag brighter than its baseline, at start + offsets days.

    `tags` gives the tag mask of each extra point; the quiet star's own points carry none.
    """
    years, mag = quiet(noise=noise, **kw)
    b = baseline_of(years, mag)
    ey = start + np.asarray(offsets_days, dtype=float) * DAY
    em = b - np.asarray(depths, dtype=float)
    raw = np.r_[np.zeros(len(years)), np.zeros(len(ey)) if tags is None else np.asarray(tags)]
    return make_curve(np.r_[years, ey], np.r_[mag, em], tags=raw.astype(np.uint64), series=series)
