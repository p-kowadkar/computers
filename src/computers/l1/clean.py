"""Cleaning: the steps of plan 6.1 that need only one star's rows and its exposures.

Every step is a function that returns a boolean mask of the rows it tags. `clean_arrays`
ORs the matching `Tag` into the curve, so a step run twice changes nothing and no row is
ever deleted. Source splitting (step 1) and shape outliers (step 7) need other stars and
live with the tile context.

Magnitudes run backwards: a larger number is fainter. A blend, a deep limit and the like
are compared as differences, and each strict boundary is tested on both sides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from computers.data.series import KIND_CODES, kind_of
from computers.l1 import tags as T
from computers.l1.curve import Curve
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

__all__ = [
    "Exposures",
    "StarInfo",
    "clean_arrays",
    "clean_star",
    "merge_sources",
    "neighbour_map",
    "overlap",
    "step_astrometry",
    "step_blend",
    "step_deep_limit",
    "step_emulsion",
    "step_plate_edge",
    "step_series",
    "step_standard",
]

METEOR_POLICIES = ("reject", "keep")


@dataclass(frozen=True)
class StarInfo:
    ref_number: int
    catalog_mag: float | None  # APASS B, for the blend test


@dataclass(frozen=True)
class Exposures:
    """The per-exposure colour term, looked up by `exposure_id`."""

    colourterm: Mapping[str, float]  # NaN or missing means unknown

    @classmethod
    def from_table(cls, exposures: pa.Table) -> Exposures:
        ids = exposures.column("exposure_id").to_pylist()
        ct = exposures.column("median_colorterm_apass").to_numpy(zero_copy_only=False).astype(float)
        return cls(dict(zip(ids, ct.tolist(), strict=True)))


# ------------------------------------------------------------------------ the steps


def step_standard(aflags: np.ndarray, detected: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Step 2: (hard, soft) masks. Soft means only soft bits are set."""
    a = np.where(np.isfinite(aflags), aflags, 0).astype(np.int64)
    hard = detected & ((a & T.AFLAG_HARD_MASK) != 0)
    soft = detected & ((a & T.AFLAG_SOFT_MASK) != 0) & ~hard
    return hard, soft


def step_astrometry(
    ra: np.ndarray, dec: np.ndarray, detected: np.ndarray, max_sep_arcsec: float | None = None
) -> np.ndarray:
    """Step 3: detections farther than the limit from the star's median measured position."""
    limit = THRESHOLDS["astrometry_max_sep_arcsec"].value if max_sep_arcsec is None else max_sep_arcsec
    have = detected & np.isfinite(ra) & np.isfinite(dec)
    if not have.any():
        return np.zeros(len(ra), dtype=bool)
    ra0, dec0 = np.median(ra[have]), np.median(dec[have])
    dra = (ra - ra0 + 180.0) % 360.0 - 180.0
    sep = np.hypot(dra * np.cos(np.radians(dec0)), dec - dec0) * 3600.0
    out = np.zeros(len(ra), dtype=bool)
    out[have] = sep[have] > limit
    return out


def step_series(series: np.ndarray, meteor_policy: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Step 4: (kind codes, meteor rows to reject, rows of an unknown series)."""
    if meteor_policy not in METEOR_POLICIES:
        raise ValueError(f"meteor_policy must be one of {METEOR_POLICIES}, got {meteor_policy!r}")
    names, inverse = np.unique(np.asarray(series, dtype=str), return_inverse=True)
    codes = np.array([KIND_CODES[kind_of(n)] for n in names], dtype=np.uint8)
    kind = codes[inverse]
    meteor = kind == T.KIND_METEOR
    return kind, meteor & (meteor_policy == "reject"), kind == T.KIND_UNKNOWN


def step_emulsion(exposure_id: np.ndarray, exposures: Exposures) -> tuple[np.ndarray, np.ndarray]:
    """Step 5: (non-blue rows, unknown-emulsion rows).

    DASCH: below -1.13 is red, above -0.53 is blue, in between yellow. So -0.53 itself
    is yellow, and 0.0 is the pipeline's default for "unset".
    """
    blue_min = THRESHOLDS["colorterm_blue_min"].value
    names, inverse = np.unique(np.asarray(exposure_id, dtype=str), return_inverse=True)
    ct = np.array([exposures.colourterm.get(n, np.nan) for n in names], dtype=float)[inverse]
    unknown = np.isnan(ct)
    non_blue = ~unknown & ((ct <= blue_min) | (ct == 0.0))
    return non_blue, unknown


def step_blend(blended_mag: np.ndarray, catalog_mag: float | None, detected: np.ndarray) -> np.ndarray:
    """Step 6: detections whose blended magnitude differs from the catalog's by too much."""
    if catalog_mag is None or not np.isfinite(catalog_mag):
        return np.zeros(len(blended_mag), dtype=bool)
    diff = np.abs(blended_mag - catalog_mag)
    return detected & np.isfinite(blended_mag) & (diff > THRESHOLDS["blend_max_diff_mag"].value)


def step_plate_edge(aflags: np.ndarray, detected: np.ndarray) -> np.ndarray:
    """Step 8: DASCH's outermost radial bin."""
    a = np.where(np.isfinite(aflags), aflags, 0).astype(np.int64)
    return detected & ((a & T.AFLAG_PLATE_EDGE) != 0)


def step_deep_limit(limit: np.ndarray, detected: np.ndarray, usable: np.ndarray, mag: np.ndarray) -> np.ndarray:
    """Step 9: non-detections whose limit is far fainter than the star's usual magnitude.

    The usual magnitude is the median of the detections that survived the rejections.
    The tag marks a suspect, not evidence of anything.
    """
    out = np.zeros(len(limit), dtype=bool)
    good = detected & usable
    if not good.any():
        return out
    median = float(np.median(mag[good]))
    margin = THRESHOLDS["deep_limit_margin_mag"].value
    return ~detected & np.isfinite(limit) & ((limit - median) > margin)


# --------------------------------------------------------------- step 1: source splitting


def neighbour_map(patrol: pa.Table, neighbours: pa.Table, radius_arcsec: float | None = None) -> dict[int, list[int]]:
    """For each patrol star, the neighbour sources within the split radius (never itself)."""
    radius = THRESHOLDS["source_split_radius_arcsec"].value if radius_arcsec is None else radius_arcsec
    n_ra = neighbours.column("ra_deg").to_numpy().astype(float)
    n_dec = neighbours.column("dec_deg").to_numpy().astype(float)
    n_ref = neighbours.column("ref_number").to_pylist()
    out: dict[int, list[int]] = {}
    for ref, ra, dec in zip(
        patrol.column("ref_number").to_pylist(),
        patrol.column("ra_deg").to_pylist(),
        patrol.column("dec_deg").to_pylist(),
        strict=True,
    ):
        dra = (n_ra - ra + 180.0) % 360.0 - 180.0
        sep = np.hypot(dra * np.cos(np.radians(dec)), n_dec - dec) * 3600.0
        found = [r for r, d in zip(n_ref, sep, strict=True) if d <= radius and r != ref]
        if found:
            out[int(ref)] = sorted(int(r) for r in found)
    return out


def _detected_plates(rows: pa.Table) -> set[str]:
    ids = rows.column("exposure_id").to_pylist()
    mag = rows.column("magcal_magdep").to_numpy(zero_copy_only=False).astype(float)
    return {e for e, m in zip(ids, mag, strict=True) if np.isfinite(m)}


def overlap(a: pa.Table, b: pa.Table) -> float:
    """The share of the smaller curve's detection plates that the other curve also detected.

    A source split between two catalog entries puts its detections on plates where the
    other entry has none, so the overlap is near zero. Two real stars detected together
    overlap heavily. Dividing by the plates of either curve would let a long curve hide
    a short one that is detected on the same plates (measured on the 20 real pairs, where
    it merged every pair).
    """
    pa_, pb = _detected_plates(a), _detected_plates(b)
    smaller = min(len(pa_), len(pb))
    return len(pa_ & pb) / smaller if smaller else 0.0


def merge_sources(target: pa.Table, neighbours: list[pa.Table]) -> tuple[pa.Table, list[bool]]:
    """Fold neighbour detections into the target's rows when the curves interleave.

    A neighbour interleaves with the target when it has detections and `overlap` (the share
    of the smaller curve's detection plates that the other also has) is under
    `source_split_overlap_max`. Each plate it covers
    that the target did not detect then takes the neighbour's row, so one star is not split
    over two catalog entries. Returns the merged table, with a `source_merged` boolean
    column marking the borrowed rows, and one verdict per neighbour.
    """
    limit = THRESHOLDS["source_split_overlap_max"].value
    base = target.append_column("source_merged", pa.array([False] * target.num_rows))
    merged_flags: list[bool] = []
    taken = _detected_plates(target)
    rows = base
    for nb in neighbours:
        det = _detected_plates(nb)
        interleaves = bool(det) and overlap(target, nb) < limit
        merged_flags.append(interleaves)
        if not interleaves:
            continue
        ids = nb.column("exposure_id").to_pylist()
        mag = nb.column("magcal_magdep").to_numpy(zero_copy_only=False).astype(float)
        keep = [i for i, (e, m) in enumerate(zip(ids, mag, strict=True)) if np.isfinite(m) and e not in taken]
        if not keep:
            continue
        borrowed = nb.take(pa.array(keep)).append_column("source_merged", pa.array([True] * len(keep)))
        replaced = {ids[i] for i in keep}
        # the target's non-detection (or rejected) row on that plate gives way to the neighbour's detection
        survive = [e not in replaced for e in rows.column("exposure_id").to_pylist()]
        borrowed = borrowed.select(rows.column_names).cast(rows.schema)
        rows = pa.concat_tables([rows.filter(pa.array(survive)), borrowed])
        taken |= replaced
    return rows, merged_flags


# ---------------------------------------------------------------------- the cleaner


def _col(table: pa.Table, name: str, dtype=float) -> np.ndarray:
    if name not in table.column_names:
        return np.full(table.num_rows, np.nan)
    return table.column(name).to_numpy(zero_copy_only=False).astype(dtype)


def clean_arrays(
    *,
    ref_number: int,
    time: np.ndarray,
    mag: np.ndarray,
    limit: np.ndarray,
    exposure_id: np.ndarray,
    series: np.ndarray,
    aflags: np.ndarray,
    blended_mag: np.ndarray,
    ra: np.ndarray,
    dec: np.ndarray,
    star: StarInfo,
    exposures: Exposures,
    meteor_policy: str = "reject",
    source_merged: np.ndarray | None = None,
) -> Curve:
    """Run the one-star steps over a star's rows and return the tagged curve."""
    merged = np.zeros(len(time), dtype=bool) if source_merged is None else np.asarray(source_merged, dtype=bool)
    order = np.lexsort((exposure_id, time))  # a fixed order: time, then exposure
    merged = merged[order]
    time, mag, limit = time[order], mag[order], limit[order]
    exposure_id, series = exposure_id[order], series[order]
    aflags, blended_mag, ra, dec = aflags[order], blended_mag[order], ra[order], dec[order]
    detected = np.isfinite(mag)
    tags = np.zeros(len(time), dtype=np.uint64)

    def mark(mask: np.ndarray, tag: Tag) -> None:
        tags[mask] |= np.uint64(int(tag))

    mark(merged, Tag.SOURCE_MERGED)
    hard, soft = step_standard(aflags, detected)
    mark(hard, Tag.STD_HARD)
    mark(soft, Tag.STD_SOFT)
    mark(step_astrometry(ra, dec, detected), Tag.ASTROMETRY)
    kind, meteor_reject, unknown_series = step_series(series, meteor_policy)
    mark(meteor_reject, Tag.METEOR_REJECTED)
    mark(unknown_series, Tag.UNKNOWN_SERIES)
    non_blue, unknown_emulsion = step_emulsion(exposure_id, exposures)
    mark(non_blue, Tag.NON_BLUE)
    mark(unknown_emulsion, Tag.EMULSION_UNKNOWN)
    mark(step_blend(blended_mag, star.catalog_mag, detected), Tag.BLEND_SUSPECT)
    mark(step_plate_edge(aflags, detected), Tag.PLATE_EDGE)
    rejected = (tags & np.uint64(T.REJECTING)) != 0
    mark(step_deep_limit(limit, detected, ~rejected, mag), Tag.DEEP_LIMIT_SUSPECT)
    return Curve(ref_number, time, mag, limit, exposure_id, series, kind, tags)


def clean_star(
    rows: pa.Table, star: StarInfo, exposures: Exposures, *, meteor_policy: str = "reject"
) -> Curve:
    """Clean one star's `measurements` rows. The table is not changed."""
    merged = (
        rows.column("source_merged").to_numpy(zero_copy_only=False).astype(bool)
        if "source_merged" in rows.column_names
        else np.zeros(rows.num_rows, dtype=bool)
    )
    return clean_arrays(
        source_merged=merged,
        ref_number=star.ref_number,
        time=_col(rows, "time"),
        mag=_col(rows, "magcal_magdep"),
        limit=_col(rows, "limiting_mag_local"),
        exposure_id=np.array(rows.column("exposure_id").to_pylist(), dtype=object),
        series=np.array(rows.column("series").to_pylist(), dtype=object),
        aflags=_col(rows, "aflags"),
        blended_mag=_col(rows, "blended_mag"),
        ra=_col(rows, "ra_deg"),
        dec=_col(rows, "dec_deg"),
        star=star,
        exposures=exposures,
        meteor_policy=meteor_policy,
    )

