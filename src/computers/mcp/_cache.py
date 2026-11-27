"""Bounded reads of the local silver tables for the MCP servers (spec S8 R5).

Nothing here touches the network. A table that is not local gives a structured error that says what is missing and which
command pulls it. Replies are bounded: when more rows match than the limit, an even sample is returned and the reply
says how many matched, how many were returned and that it was thinned.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from computers.data import silver
from computers.data.timeconv import jd_to_year

__all__ = ["DEV_TILES", "MAX_LIMIT", "Store", "thin"]

DEV_TILES = ("m44", "tcrb")  # the held-out test tile is not served until you have frozen the split and say so
MAX_LIMIT = 500


def thin(n: int, limit: int) -> np.ndarray:
    """Indices of an even sample of at most `limit` of `n` rows, in order."""
    if n <= limit:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, limit).round().astype(int))


class Store:
    def __init__(self, data_dir: str | os.PathLike[str] = "data", tiles: tuple[str, ...] | None = None) -> None:
        self.data_dir = Path(data_dir)
        env = os.environ.get("COMPUTERS_MCP_TILES")
        self.tiles = tuple(tiles or (env.split(",") if env else DEV_TILES))

    def table(self, name: str, tile: str) -> pa.Table | dict[str, Any]:
        """The table, or an error dictionary."""
        if tile not in self.tiles:
            return {"error": "tile_not_served", "tile": tile, "served": list(self.tiles),
                    "hint": "the held-out test tile is served only after the split is frozen and COMPUTERS_MCP_TILES lists it"}  # fmt: skip
        path = silver.silver_path(self.data_dir, name, tile)
        if not path.is_file():
            return {"error": "not_local", "missing": f"{name} for tile {tile}", "pull_with": f"computers pull --tile {tile}"}
        return silver.read_table(path)

    @staticmethod
    def clamp(limit: int) -> int:
        return max(1, min(int(limit), MAX_LIMIT))

    def lightcurve(
        self, tile: str, ref_number: int, start_year: float | None, end_year: float | None, series: str | None, limit: int
    ) -> dict[str, Any]:
        t = self.table("measurements", tile)
        if isinstance(t, dict):
            return t
        t = t.filter(pc.equal(t.column("ref_number"), pa.scalar(int(ref_number), t.schema.field("ref_number").type)))
        if t.num_rows == 0:
            return {"error": "star_not_found", "ref_number": ref_number, "tile": tile}
        t = t.take(pc.sort_indices(t, sort_keys=[("time", "ascending"), ("exposure_id", "ascending")]))
        jd = t.column("time").to_numpy(zero_copy_only=False).astype(float)
        year = np.asarray(jd_to_year(jd), dtype=float)
        keep = np.ones(len(year), dtype=bool)
        if start_year is not None:
            keep &= year >= start_year
        if end_year is not None:
            keep &= year <= end_year
        if series:
            keep &= np.array(t.column("series").to_pylist()) == series
        rows = np.flatnonzero(keep)
        pick = rows[thin(len(rows), self.clamp(limit))]
        sub = t.take(pa.array(pick))
        mag = sub.column("magcal_magdep").to_numpy(zero_copy_only=False).astype(float)
        lim = sub.column("limiting_mag_local").to_numpy(zero_copy_only=False).astype(float)
        out = [
            {"year": round(float(year[i]), 3), "series": s, "exposure_id": e,
             "mag": None if not np.isfinite(m) else round(float(m), 3), "limiting_mag": None if not np.isfinite(lm) else round(float(lm), 3),
             "aflags": int(a or 0)}
            for i, s, e, m, lm, a in zip(pick, sub.column("series").to_pylist(), sub.column("exposure_id").to_pylist(), mag, lim,
                                         sub.column("aflags").to_pylist(), strict=True)
        ]  # fmt: skip
        return {"ref_number": ref_number, "tile": tile, "n_total": len(rows), "n_returned": len(out),
                "thinned": len(out) < len(rows), "rows": out, "note": "magnitudes are as measured, not detrended; None is a non-detection"}  # fmt: skip

    def exposures(self, tile: str, start_year: float | None, end_year: float | None, series: str | None, limit: int) -> dict[str, Any]:
        t = self.table("exposures", tile)
        if isinstance(t, dict):
            return t
        rows = t.to_pylist()
        sel = [r for r in rows if (not series or r["series"] == series)]
        if start_year is not None or end_year is not None:
            keep = []
            for r in sel:
                y = _year_of(r)
                if y is None:
                    continue
                if (start_year is None or y >= start_year) and (end_year is None or y <= end_year):
                    keep.append(r)
            sel = keep
        sel.sort(key=lambda r: (str(r.get("obs_date")), r["exposure_id"]))
        pick = thin(len(sel), self.clamp(limit))
        out = [_exposure_brief(sel[i]) for i in pick]
        return {"tile": tile, "n_total": len(sel), "n_returned": len(out), "thinned": len(out) < len(sel), "rows": out}

    def plate_info(self, exposure_id: str) -> dict[str, Any]:
        for tile in self.tiles:
            t = self.table("exposures", tile)
            if isinstance(t, dict):
                continue
            hit = t.filter(pc.equal(t.column("exposure_id"), exposure_id))
            if hit.num_rows:
                return {"tile": tile, **{k: _plain(v) for k, v in hit.to_pylist()[0].items()}}
        return {"error": "not_local", "missing": f"exposure {exposure_id} in tiles {list(self.tiles)}", "pull_with": "computers pull --stage exposures"}

    def vsx(self, ra: float, dec: float, radius_arcsec: float, tile: str | None) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        errors = []
        for tl in [tile] if tile else self.tiles:
            t = self.table("vsx", tl)
            if isinstance(t, dict):
                errors.append(t)
                continue
            ra_v = np.radians(t.column("ra_deg").to_numpy(zero_copy_only=False).astype(float))
            dec_v = np.radians(t.column("dec_deg").to_numpy(zero_copy_only=False).astype(float))
            r0, d0 = np.radians(ra), np.radians(dec)
            cos_sep = np.sin(d0) * np.sin(dec_v) + np.cos(d0) * np.cos(dec_v) * np.cos(ra_v - r0)
            sep = np.degrees(np.arccos(np.clip(cos_sep, -1, 1))) * 3600.0
            for i in np.flatnonzero(sep <= radius_arcsec):
                row = {k: _plain(v) for k, v in t.slice(int(i), 1).to_pylist()[0].items()}
                out.append({**row, "sep_arcsec": round(float(sep[i]), 2)})
        if not out and errors and len(errors) == len([tile] if tile else self.tiles):
            return errors[0]
        out.sort(key=lambda r: r["sep_arcsec"])
        return {"n_total": len(out), "n_returned": min(len(out), 50), "thinned": len(out) > 50, "rows": out[:50]}


def _plain(v: Any) -> Any:
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v if isinstance(v, str | int | float | bool | type(None)) else str(v)


def _year_of(r: dict[str, Any]) -> float | None:
    d = r.get("obs_date")
    if d is None:
        return None
    try:
        return float(d.year) + (float(d.timetuple().tm_yday) - 1) / 366.0 if hasattr(d, "year") else float(str(d)[:4])
    except (TypeError, ValueError):
        return None


def _exposure_brief(r: dict[str, Any]) -> dict[str, Any]:
    keys = ("exposure_id", "series", "series_kind", "platenum", "obs_date", "plate_class", "lim_mag_apass", "median_colorterm_apass", "has_imaging")
    return {k: _plain(r.get(k)) for k in keys}
