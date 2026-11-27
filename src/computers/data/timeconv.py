"""Time conversions shared by every layer."""

from __future__ import annotations

import numpy as np

__all__ = ["DAYS_PER_YEAR", "jd_to_year"]

DAYS_PER_YEAR = 365.25


def jd_to_year(jd: np.ndarray | float) -> np.ndarray | float:
    """Decimal year from a Julian date (J2000 is JD 2451545.0 and year 2000.0)."""
    return 2000.0 + (np.asarray(jd, dtype=float) - 2451545.0) / DAYS_PER_YEAR
