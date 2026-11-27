"""A deliberately simple detector. It exists to prove the harness works.

This is a test fixture, not a pipeline stage. It flags a star when the median magnitude
of its first decade of good points differs from that of its last decade by more than a
threshold. It has no detrending, no seam rule and no notion of events, so it is expected
to be reported as suspect (it recovers steps placed inside the Menzel Gap).
"""

from __future__ import annotations

import numpy as np

from computers.bench.inject import jd_to_year
from computers.thresholds import THRESHOLDS

__all__ = ["TrivialDetector"]


class TrivialDetector:
    def __init__(self, threshold_mag: float | None = None, decade: float = 10.0, min_points: int = 5):
        self.threshold_mag = (
            THRESHOLDS["amplitude_floor_long_term_mag"].value if threshold_mag is None else threshold_mag
        )
        self.decade = decade
        self.min_points = min_points

    def statistic(self, time_jd: np.ndarray, mag: np.ndarray) -> float | None:
        """The magnitude difference between the last and first decade, or None when there are too few points."""
        good = np.isfinite(mag)
        if good.sum() < 2 * self.min_points:
            return None
        year, m = jd_to_year(np.asarray(time_jd)[good]), np.asarray(mag)[good]
        first, last = year.min(), year.max()
        early, late = m[year <= first + self.decade], m[year >= last - self.decade]
        if len(early) < self.min_points or len(late) < self.min_points:
            return None
        return float(abs(np.median(late) - np.median(early)))

    def __call__(self, time_jd: np.ndarray, mag: np.ndarray, limit_mag: np.ndarray) -> bool:
        stat = self.statistic(time_jd, mag)
        return stat is not None and bool(stat > self.threshold_mag)
