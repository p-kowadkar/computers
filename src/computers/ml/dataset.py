"""Build the E1 dataset: one `Star` per patrol star of a tile (spec S6 R1).

The rows are the ones L1 reads: the detrended curve when the star could be detrended, the raw
curve otherwise. Measurement errors come from the silver measurements table, which the `Curve`
does not carry. No injected curve is ever built here, and the builder takes no benchmark input.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from computers.data import silver
from computers.l1.context import TileContext
from computers.l1.curve import Curve
from computers.l1.detrend import detrend
from computers.ml import sequences as S
from computers.thresholds import THRESHOLDS

__all__ = ["build_tile", "load_stars", "save_stars", "star_from_curve"]


def star_from_curve(
    tile: str,
    curve: Curve,
    mag: np.ndarray,
    limit: np.ndarray,
    err_by_exposure: dict[str, float],
    *,
    detrended: bool,
    min_good: int | None = None,
) -> S.Star | None:
    """The star's rows, or None when it has fewer than `min_good_points` good detections.

    `mag` and `limit` are the magnitudes and plate limits to read (detrended or raw).
    """
    min_good = int(THRESHOLDS["min_good_points"].value) if min_good is None else min_good
    det = curve.good & np.isfinite(mag)
    if int(det.sum()) < min_good:
        return None
    lim = (~curve.detected) & curve.usable & np.isfinite(limit)
    keep = det | lim
    median = float(np.median(mag[det]))
    value = np.where(det, mag, limit) - median
    raw_err = np.array([err_by_exposure.get(e, np.nan) for e in curve.exposure_id[keep]], dtype=float)
    ok = np.isfinite(raw_err) & ~lim[keep]
    default = float(np.median(raw_err[ok])) if ok.any() else S.ERR_DEFAULT
    err = np.where(ok, raw_err, default)
    err = np.maximum(err, S.ERR_FLOOR)
    year = (curve.time[keep] - 2451545.0) / 365.25 + 2000.0
    return S.Star(
        tile, curve.ref_number, year, value[keep], err, lim[keep],
        np.array([S.series_id(s) for s in curve.series[keep]], dtype=np.int16),
        curve.kind[keep].astype(np.uint8), median, detrended,
    )  # fmt: skip


def _errors(data_dir: Path, tile: str) -> dict[int, dict[str, float]]:
    """magcal_local_rms of every row, by star and exposure."""
    meas = silver.read_table(silver.silver_path(data_dir, "measurements", tile)).select(
        ["ref_number", "exposure_id", "magcal_local_rms"]
    )
    ref = meas.column("ref_number").to_numpy()
    order = np.argsort(ref, kind="stable")
    meas = meas.take(order)
    ref = ref[order]
    exp = meas.column("exposure_id").to_pylist()
    rms = meas.column("magcal_local_rms").to_numpy(zero_copy_only=False).astype(float)
    out: dict[int, dict[str, float]] = {}
    starts = np.flatnonzero(np.r_[True, ref[1:] != ref[:-1]])
    ends = np.r_[starts[1:], len(ref)]
    for a, b in zip(starts, ends, strict=True):
        out[int(ref[a])] = {exp[i]: float(rms[i]) for i in range(a, b)}
    return out


def build_tile(
    data_dir: str | os.PathLike[str], tile: str, *, meteor_policy: str = "reject"
) -> list[S.Star]:
    """Every patrol star of the tile that has enough good points, in the order of the tile's star table."""
    data_dir = Path(data_dir)
    ctx = TileContext.from_silver(data_dir, tile, meteor_policy=meteor_policy)
    errs = _errors(data_dir, tile)
    stars = []
    for ref in ctx.refs:
        curve = ctx.curve(ref)
        if curve.kill_reason is not None:
            continue
        d = detrend(ctx, curve)
        star = star_from_curve(tile, d.curve, d.mag, d.limit, errs.get(ref, {}), detrended=d.detrended)
        if star is not None:
            stars.append(star)
    return stars


def save_stars(stars: list[S.Star], path: str | os.PathLike[str]) -> None:
    """Write stars as one compressed file with a row-offset index, plus the spec version."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lens = np.array([len(s) for s in stars], dtype=np.int64)
    cat = lambda name, dt: np.concatenate([getattr(s, name) for s in stars]).astype(dt) if stars else np.zeros(0, dt)
    np.savez_compressed(
        path,
        lens=lens,
        ref=np.array([s.ref_number for s in stars], dtype=np.int64),
        tile=np.array([s.tile for s in stars]),
        median=np.array([s.median_mag for s in stars]),
        detrended=np.array([s.detrended for s in stars]),
        year=cat("year", np.float64), value=cat("value", np.float64), err=cat("err", np.float64),
        is_limit=cat("is_limit", bool), series=cat("series", np.int16), kind=cat("kind", np.uint8),
        spec=np.array(json.dumps({"version": S.SPEC_VERSION, "hash": S.spec_hash()})),
    )  # fmt: skip


def load_stars(path: str | os.PathLike[str]) -> list[S.Star]:
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["spec"]))
    if meta["hash"] != S.spec_hash():
        raise ValueError(f"{path} was built with sequence spec {meta['hash']}, the code has {S.spec_hash()}; rebuild it")
    ends = np.cumsum(z["lens"])
    starts = ends - z["lens"]
    return [
        S.Star(
            str(z["tile"][i]), int(z["ref"][i]), z["year"][a:b], z["value"][a:b].astype(float), z["err"][a:b].astype(float),
            z["is_limit"][a:b], z["series"][a:b], z["kind"][a:b], float(z["median"][i]), bool(z["detrended"][i]),
        )
        for i, (a, b) in enumerate(zip(starts, ends, strict=True))
    ]  # fmt: skip
