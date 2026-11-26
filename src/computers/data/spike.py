"""Pure helpers for the Sprint 0 data spike.

Everything here takes plain arrays or dicts, so it can be tested without network
access or daschlab. Conventions:

* Times are decimal years (for example 1946.2).
* Magnitudes run backwards: a smaller number is brighter. A non-detection has
  ``NaN`` in the magnitude array.
* ``reject`` is DASCH's integer bit-field. Zero means the point is good.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from computers.data.credentials import DASCHLAB_ENV, STARGLASS_ENV, api_mode_from_env, redact

ERUPTION_YEAR = 1946
BASELINE_START_YEAR = 1900
DEFAULT_MARGIN_MAG = 2.0  # [start] requirement R5.1

_REEXPORTED = (DASCHLAB_ENV, STARGLASS_ENV, api_mode_from_env, redact)  # used by the Sprint 0 script and tests

QUIET_B_RANGE = (11.0, 13.0)
QUIET_MIN_SEP_ARCMIN = 5.0
# Conservative on purpose: only plain stars count as quiet. SIMBAD types such as
# "V*", "EB*" or "Mi*" mean a variable, and anything unfamiliar is excluded too.
QUIET_OTYPES = frozenset({"*", "PM*"})


def detected_mask(mag: np.ndarray) -> np.ndarray:
    """True where the star was detected. Non-detections carry NaN."""
    return np.isfinite(np.asarray(mag, dtype=float))


def summarize(
    year: np.ndarray,
    mag: np.ndarray,
    series: Sequence[str],
    reject_standard: np.ndarray,
    reject_extra: np.ndarray,
) -> dict[str, Any]:
    """Summary numbers for one lightcurve (requirement R3).

    ``reject_standard`` is the reject column after ``apply_standard_rejections()``.
    ``reject_extra`` is the column after the extra rejections as well, so it must
    never mark fewer points than ``reject_standard``.
    """
    year = np.asarray(year, dtype=float)
    mag = np.asarray(mag, dtype=float)
    rs = np.asarray(reject_standard)
    re = np.asarray(reject_extra)
    series_arr = np.asarray(series, dtype=object)
    n = len(year)
    if not (len(mag) == len(rs) == len(re) == len(series_arr) == n):
        raise ValueError("all arrays must have the same length")
    if np.any((rs != 0) & (re == 0)):
        raise ValueError("a point rejected by the standard step must stay rejected")

    det = detected_mask(mag)
    good_standard = det & (rs == 0)
    good_extra = det & (re == 0)

    series_counts: dict[str, int] = {}
    for name in series_arr:
        series_counts[str(name)] = series_counts.get(str(name), 0) + 1

    return {
        "rows": int(n),
        "detections": int(det.sum()),
        "non_detections": int((~det).sum()),
        "first_year": float(year.min()) if n else None,
        "last_year": float(year.max()) if n else None,
        "series": dict(sorted(series_counts.items())),
        "rejected_standard": int((rs != 0).sum()),
        "rejected_after_extra": int((re != 0).sum()),
        "good_detections_standard": int(good_standard.sum()),
        "good_detections_after_extra": int(good_extra.sum()),
        "median_detected_mag": float(np.median(mag[det])) if det.any() else None,
    }


def tcrb_outburst_check(
    year: np.ndarray,
    mag: np.ndarray,
    reject: np.ndarray,
    margin: float = DEFAULT_MARGIN_MAG,
) -> dict[str, Any]:
    """Known-answer check for T CrB's 1946 eruption (requirement R5).

    Passes when at least one unrejected detection dated in 1946 is at least
    ``margin`` magnitudes brighter than the median of unrejected detections from
    1900 up to, but not including, 1946.

    Brighter means a smaller magnitude, so the test is ``baseline - mag >= margin``.
    """
    year = np.asarray(year, dtype=float)
    mag = np.asarray(mag, dtype=float)
    reject = np.asarray(reject)
    good = detected_mask(mag) & (reject == 0)

    in_baseline = good & (year >= BASELINE_START_YEAR) & (year < ERUPTION_YEAR)
    in_event_year = good & (year >= ERUPTION_YEAR) & (year < ERUPTION_YEAR + 1)

    result: dict[str, Any] = {
        "passed": False,
        "margin": margin,
        "n_baseline": int(in_baseline.sum()),
        "n_in_1946": int(in_event_year.sum()),
        "baseline_median": None,
        "points": [],
        "reason": "",
    }
    if not in_baseline.any():
        result["reason"] = "no unrejected detections between 1900 and 1945"
        return result

    baseline = float(np.median(mag[in_baseline]))
    result["baseline_median"] = baseline
    if not in_event_year.any():
        result["reason"] = "no unrejected detections in 1946"
        return result

    points = [
        {"year": float(y), "mag": float(m), "brighter_by": baseline - float(m)}
        for y, m in zip(year[in_event_year], mag[in_event_year], strict=True)
    ]
    points.sort(key=lambda p: p["year"])
    result["points"] = points
    result["passed"] = any(p["brighter_by"] >= margin for p in points)
    if not result["passed"]:
        best = max(p["brighter_by"] for p in points)
        result["reason"] = f"brightest 1946 point is only {best:.2f} mag above baseline"
    return result


def angular_separation_arcsec(
    ra1_deg: float | np.ndarray,
    dec1_deg: float | np.ndarray,
    ra2_deg: float | np.ndarray,
    dec2_deg: float | np.ndarray,
) -> np.ndarray:
    """Great-circle separation in arcseconds (haversine form, stable for small angles)."""
    ra1, dec1, ra2, dec2 = (np.radians(np.asarray(v, dtype=float)) for v in (ra1_deg, dec1_deg, ra2_deg, dec2_deg))
    a = np.sin((dec2 - dec1) / 2) ** 2 + np.cos(dec1) * np.cos(dec2) * np.sin((ra2 - ra1) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))) * 3600.0


def separation_report(
    ra_deg: np.ndarray,
    dec_deg: np.ndarray,
    target_ra_deg: float,
    target_dec_deg: float,
    n: int = 3,
    warn_arcsec: float = 5.0,
) -> dict[str, Any]:
    """The ``n`` nearest catalog sources to a target (requirement R2.2).

    Returns their indices and separations, plus ``warn`` when the nearest source
    is farther than ``warn_arcsec``.
    """
    ra = np.asarray(ra_deg, dtype=float)
    dec = np.asarray(dec_deg, dtype=float)
    if ra.shape != dec.shape or ra.ndim != 1:
        raise ValueError("ra and dec must be 1-D arrays of the same length")
    if ra.size == 0:
        return {"nearest": [], "warn": True}
    sep = angular_separation_arcsec(ra, dec, target_ra_deg, target_dec_deg)
    # Stable sort: equal separations keep catalog order, so output is deterministic.
    order = np.argsort(sep, kind="stable")[:n]
    nearest = [{"index": int(i), "sep_arcsec": float(sep[i])} for i in order]
    return {"nearest": nearest, "warn": nearest[0]["sep_arcsec"] > warn_arcsec}


def quiet_star_shortlist(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Steps 1 of the quiet-star rule (design.md).

    Keep sources with APASS B in ``QUIET_B_RANGE``, at least ``QUIET_MIN_SEP_ARCMIN``
    from the target, and with a detection count in the top quartile of the
    remaining group. Each candidate needs ``ref_number``, ``b_mag``,
    ``sep_arcmin`` and ``n_detections``. Returned sorted by ``ref_number``.
    """
    lo, hi = QUIET_B_RANGE
    group = [
        dict(c)
        for c in candidates
        if c.get("b_mag") is not None
        and lo <= float(c["b_mag"]) <= hi
        and float(c["sep_arcmin"]) >= QUIET_MIN_SEP_ARCMIN
    ]
    if not group:
        return []
    cutoff = float(np.percentile([c["n_detections"] for c in group], 75))
    kept = [c for c in group if c["n_detections"] >= cutoff]
    return sorted(kept, key=lambda c: c["ref_number"])


def pick_quiet_star(
    shortlist: Sequence[Mapping[str, Any]],
    otype_by_ref: Mapping[Any, str | None],
) -> dict[str, Any] | None:
    """Step 2 and 3 of the quiet-star rule.

    Drop sources whose SIMBAD type is missing or not in ``QUIET_OTYPES``, then
    return the one with the lowest ``ref_number`` and up to two alternates.
    Returns ``None`` when nothing qualifies.
    """
    ok = [
        dict(c)
        for c in shortlist
        if otype_by_ref.get(c["ref_number"]) in QUIET_OTYPES
    ]
    ok.sort(key=lambda c: c["ref_number"])
    if not ok:
        return None
    return {"chosen": ok[0], "alternates": ok[1:3]}


def cutout_offset(x_pix: float, y_pix: float, shape: tuple[int, int]) -> dict[str, float]:
    """Offset of a pixel position from the cutout centre (requirement R6.5).

    ``shape`` is ``(ny, nx)``. Pixel coordinates are 0-based, so the centre of an
    ``n``-pixel axis is ``(n - 1) / 2``.
    """
    ny, nx = shape
    dx = float(x_pix) - (nx - 1) / 2
    dy = float(y_pix) - (ny - 1) / 2
    return {"dx": dx, "dy": dy, "radius": math.hypot(dx, dy)}


def timing_stats(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Median, slowest and failure count for the timing run (requirement R7.2).

    Each record needs ``seconds`` and ``ok``. Only successful requests count
    toward the median and maximum, because a failed request often returns fast.
    """
    ok_times = [float(r["seconds"]) for r in records if r["ok"]]
    return {
        "n": len(records),
        "n_failed": sum(1 for r in records if not r["ok"]),
        "median_seconds": float(np.median(ok_times)) if ok_times else None,
        "max_seconds": max(ok_times) if ok_times else None,
    }
