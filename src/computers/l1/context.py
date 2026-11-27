"""Everything about a tile that cleaning and detrending need from the other stars.

Built once per tile and meteor policy. It holds:

* a matrix of every star's good magnitudes by exposure, for the comparison pool;
* the per-plate shape statistics for step 7 (shape outliers);
* which stars are quiet (the Leavitt Set's constants), so the pool never contains a known
  variable or a noisy star;
* the neighbour sources that split a star's detections (step 1).

Cleaned curves are not stored: `curve(ref)` rebuilds one on demand, which takes a few
milliseconds. The tile's strings are kept as integer codes, because 5.7 million rows of
text would cost most of a gigabyte.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np
import pyarrow as pa

from computers.data import silver
from computers.l1 import clean as K
from computers.l1.curve import Curve
from computers.l1.scatter import quiet_reasons, tile_stats
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

__all__ = ["MIN_SHAPE_STARS", "ShapeStats", "TileContext"]

MIN_SHAPE_STARS = 10  # fewer stars in a plate and brightness bin and the bin gives no shape tag
SHAPE_BIN_MAG = 1.0  # width of a brightness bin for "stars of similar brightness"
_KEY_STRIDE = 64  # brightness bins per plate in a shape key; bins run 0..63 (magnitude 0 to 63)
MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class ShapeStats:
    """Median and robust sigma of image width and ellipticity, per (plate, brightness bin)."""

    keys: np.ndarray  # sorted int64
    fwhm_med: np.ndarray
    fwhm_sig: np.ndarray
    ell_med: np.ndarray
    ell_sig: np.ndarray

    def outliers(self, col: np.ndarray, mag: np.ndarray, fwhm: np.ndarray, ell: np.ndarray, sigma: float) -> np.ndarray:
        """Detections whose shape is more than `sigma` robust sigma from their bin's median."""
        out = np.zeros(len(col), dtype=bool)
        key = _shape_key(col, mag)
        pos = np.searchsorted(self.keys, key)
        pos = np.minimum(pos, len(self.keys) - 1) if len(self.keys) else pos
        if not len(self.keys):
            return out
        known = self.keys[pos] == key
        for med, sig, x in ((self.fwhm_med, self.fwhm_sig, fwhm), (self.ell_med, self.ell_sig, ell)):
            ok = known & np.isfinite(x) & (sig[pos] > 0)
            out[ok] |= np.abs(x[ok] - med[pos][ok]) > sigma * sig[pos][ok]
        return out


def _shape_key(col: np.ndarray, mag: np.ndarray) -> np.ndarray:
    bins = np.clip(np.floor(np.where(np.isfinite(mag), mag, 0.0) / SHAPE_BIN_MAG), 0, _KEY_STRIDE - 1)
    return col.astype(np.int64) * _KEY_STRIDE + bins.astype(np.int64)


def _robust(x: np.ndarray) -> tuple[float, float]:
    med = float(np.median(x))
    return med, MAD_TO_SIGMA * float(np.median(np.abs(x - med)))


def build_shape_stats(col: np.ndarray, mag: np.ndarray, fwhm: np.ndarray, ell: np.ndarray) -> ShapeStats:
    """Statistics per (plate, brightness bin) from the tile's good detections."""
    ok = np.isfinite(mag)
    col, mag, fwhm, ell = col[ok], mag[ok], fwhm[ok], ell[ok]
    key = _shape_key(col, mag)
    order = np.argsort(key, kind="stable")
    key, fwhm, ell = key[order], fwhm[order], ell[order]
    cuts = np.flatnonzero(np.diff(key)) + 1
    keys, fm, fs, em, es = [], [], [], [], []
    for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, len(key)], strict=True):
        if hi - lo < MIN_SHAPE_STARS:
            continue
        f, e = fwhm[lo:hi], ell[lo:hi]
        f, e = f[np.isfinite(f)], e[np.isfinite(e)]
        if len(f) < MIN_SHAPE_STARS or len(e) < MIN_SHAPE_STARS:
            continue
        keys.append(key[lo])
        (a, b), (c, d) = _robust(f), _robust(e)
        fm.append(a), fs.append(b), em.append(c), es.append(d)
    return ShapeStats(np.array(keys, dtype=np.int64), np.array(fm), np.array(fs), np.array(em), np.array(es))


class TileContext:
    def __init__(
        self,
        tile: str,
        stars: pa.Table,
        measurements: pa.Table,
        exposures: pa.Table,
        vsx_match: pa.Table,
        *,
        neighbours: pa.Table | None = None,
        neighbour_measurements: pa.Table | None = None,
        meteor_policy: str = "reject",
    ) -> None:
        if meteor_policy not in K.METEOR_POLICIES:
            raise ValueError(f"meteor_policy must be one of {K.METEOR_POLICIES}")
        self.tile, self.meteor_policy = tile, meteor_policy
        stars = stars.sort_by("ref_number")
        self.refs = [int(r) for r in stars.column("ref_number").to_pylist()]
        self.index = {r: i for i, r in enumerate(self.refs)}
        self.mag = np.asarray(stars.column("apass_b").to_numpy(zero_copy_only=False), dtype=float)
        self.colour = np.asarray(stars.column("color").to_numpy(zero_copy_only=False), dtype=float)
        self.exposures = K.Exposures.from_table(exposures)
        self._stars = stars
        self._measurements = measurements
        # neighbours: patrol ref -> list of neighbour tables
        self._neighbour_rows: dict[int, list[pa.Table]] = {}
        if neighbours is not None and neighbour_measurements is not None and neighbours.num_rows:
            nmap = K.neighbour_map(stars, neighbours)
            by_ref = _group_by_ref(neighbour_measurements)
            for ref, nlist in nmap.items():
                self._neighbour_rows[ref] = [by_ref[n] for n in nlist if n in by_ref]
        self._load_columns(measurements, exposures)
        self._first_pass()
        sep = {
            int(r): s
            for r, s in zip(
                vsx_match.column("ref_number").to_pylist(), vsx_match.column("sep_arcsec").to_pylist(), strict=True
            )
        }
        result = quiet_reasons(self.refs, self.mag, tile_stats(measurements), sep)
        self.quiet = result.quiet
        self.quiet_reason = result.reason

    # --------------------------------------------------------------- construction

    @classmethod
    def from_silver(cls, data_dir: str | os.PathLike[str], tile: str, *, meteor_policy: str = "reject") -> TileContext:
        d = Path(data_dir)

        def read(table: str, label: str = tile) -> pa.Table:
            return silver.read_table(silver.silver_path(d, table, label))

        nb = f"{tile}-neighbours"
        have_nb = silver.silver_path(d, "stars", nb).exists() and silver.silver_path(d, "measurements", nb).exists()
        return cls(
            tile, read("stars"), read("measurements"), read("exposures"), read("vsx_match"),
            neighbours=read("stars", nb) if have_nb else None,
            neighbour_measurements=read("measurements", nb) if have_nb else None,
            meteor_policy=meteor_policy,
        )

    def _load_columns(self, meas: pa.Table, exposures: pa.Table) -> None:
        ids = exposures.column("exposure_id").to_pylist()
        enc = meas.column("exposure_id").combine_chunks().dictionary_encode()
        dictionary = enc.dictionary.to_pylist()
        extra = sorted(set(dictionary) - set(ids))
        self.exposure_ids = list(ids) + extra
        col_of = {e: i for i, e in enumerate(self.exposure_ids)}
        lookup = np.array([col_of[e] for e in dictionary], dtype=np.int32)
        ref = meas.column("ref_number").to_numpy()
        order = np.argsort(ref, kind="stable")
        self._order = order
        self._exp_col = lookup[enc.indices.to_numpy(zero_copy_only=False)][order]
        sdict = meas.column("series").combine_chunks().dictionary_encode()
        self._series_names = np.array(sdict.dictionary.to_pylist(), dtype=object)
        self._series_code = sdict.indices.to_numpy(zero_copy_only=False)[order].astype(np.int16)
        self._exp_names = np.array(self.exposure_ids, dtype=object)
        self._col_of = col_of

        def num(name: str) -> np.ndarray:
            if name not in meas.column_names:
                return np.full(meas.num_rows, np.nan)
            return meas.column(name).to_numpy(zero_copy_only=False).astype(float)[order]

        self._time, self._mag, self._limit = num("time"), num("magcal_magdep"), num("limiting_mag_local")
        self._aflags, self._blended = num("aflags"), num("blended_mag")
        self._ra, self._dec = num("ra_deg"), num("dec_deg")
        self._fwhm, self._ell = num("fwhm_image"), num("ellipticity")
        self._ref_sorted = ref[order]
        starts = np.searchsorted(self._ref_sorted, self.refs, side="left")
        ends = np.searchsorted(self._ref_sorted, self.refs, side="right")
        self._bounds = dict(zip(self.refs, zip(starts.tolist(), ends.tolist(), strict=True), strict=True))

    # ------------------------------------------------------------------ per star

    def _arrays(self, ref: int) -> dict[str, np.ndarray]:
        """The star's rows as arrays sorted by (time, exposure): the order `clean_arrays` uses."""
        lo, hi = self._bounds[ref]
        merged = np.zeros(hi - lo, dtype=bool)
        if ref in self._neighbour_rows:
            table = self._rows_table(lo, hi)
            table, _ = K.merge_sources(table, self._neighbour_rows[ref])
            return self._from_table(table)
        sl = slice(lo, hi)
        a = {
            "time": self._time[sl], "mag": self._mag[sl], "limit": self._limit[sl],
            "exposure_id": self._exp_names[self._exp_col[sl]], "series": self._series_names[self._series_code[sl]],
            "aflags": self._aflags[sl], "blended_mag": self._blended[sl], "ra": self._ra[sl], "dec": self._dec[sl],
            "fwhm": self._fwhm[sl], "ell": self._ell[sl], "source_merged": merged,
        }  # fmt: skip
        return _sorted(a)

    def _rows_table(self, lo: int, hi: int) -> pa.Table:
        """The star's original silver rows, so its schema matches its neighbours'."""
        return self._measurements.take(pa.array(self._order[lo:hi]))

    @staticmethod
    def _from_table(t: pa.Table) -> dict[str, np.ndarray]:
        def num(name: str) -> np.ndarray:
            if name not in t.column_names:
                return np.full(t.num_rows, np.nan)
            return t.column(name).to_numpy(zero_copy_only=False).astype(float)

        merged = (
            t.column("source_merged").to_numpy(zero_copy_only=False).astype(bool)
            if "source_merged" in t.column_names
            else np.zeros(t.num_rows, dtype=bool)
        )
        a = {
            "time": num("time"), "mag": num("magcal_magdep"), "limit": num("limiting_mag_local"),
            "exposure_id": np.array(t.column("exposure_id").to_pylist(), dtype=object),
            "series": np.array(t.column("series").to_pylist(), dtype=object),
            "aflags": num("aflags"), "blended_mag": num("blended_mag"), "ra": num("ra_deg"), "dec": num("dec_deg"),
            "fwhm": num("fwhm_image"), "ell": num("ellipticity"), "source_merged": merged,
        }  # fmt: skip
        return _sorted(a)

    def _clean(self, ref: int, a: dict[str, np.ndarray]) -> Curve:
        return K.clean_arrays(
            ref_number=ref, time=a["time"], mag=a["mag"], limit=a["limit"], exposure_id=a["exposure_id"],
            series=a["series"], aflags=a["aflags"], blended_mag=a["blended_mag"], ra=a["ra"], dec=a["dec"],
            star=K.StarInfo(ref, float(self.mag[self.index[ref]])), exposures=self.exposures,
            meteor_policy=self.meteor_policy, source_merged=a["source_merged"],
        )  # fmt: skip

    def _columns(self, exposure_id: np.ndarray) -> np.ndarray:
        col_of = self._col_of
        return np.array([col_of[e] for e in exposure_id], dtype=np.int32)

    def _first_pass(self) -> None:
        """Clean every star: fill the magnitude matrix and gather the shape sample."""
        self.matrix = np.full((len(self.refs), len(self.exposure_ids)), np.nan, dtype=np.float32)
        cols, mags, fw, el = [], [], [], []
        for i, ref in enumerate(self.refs):
            a = self._arrays(ref)
            curve = self._clean(ref, a)
            good = curve.good
            col = self._columns(a["exposure_id"])
            self.matrix[i, col[good]] = curve.mag[good].astype(np.float32)
            cols.append(col[good]), mags.append(curve.mag[good]), fw.append(a["fwhm"][good]), el.append(a["ell"][good])
        self.shape = build_shape_stats(
            np.concatenate(cols) if cols else np.array([], dtype=np.int32),
            np.concatenate(mags) if mags else np.array([]),
            np.concatenate(fw) if fw else np.array([]),
            np.concatenate(el) if el else np.array([]),
        )

    def curve(self, ref: int) -> Curve:
        """The cleaned curve of a patrol star, with the shape-outlier tag applied."""
        a = self._arrays(ref)
        curve = self._clean(ref, a)
        col = self._columns(a["exposure_id"])
        mask = self.shape.outliers(col, curve.mag, a["fwhm"], a["ell"], THRESHOLDS["shape_outlier_sigma"].value)
        return curve.with_tag(mask & curve.detected, Tag.SHAPE_OUTLIER)

    def columns_of(self, curve: Curve) -> np.ndarray:
        """The matrix column of each row of a curve."""
        return self._columns(curve.exposure_id)

    @cached_property
    def baseline(self) -> np.ndarray:
        """Each star's median good magnitude (NaN for a star with no good point)."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmedian(self.matrix, axis=1)

    @cached_property
    def offsets_cache(self) -> dict:
        """Plate offsets per target, filled by `detrend.offsets_for`."""
        return {}

    def __len__(self) -> int:
        return len(self.refs)


def _sorted(a: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    order = np.lexsort((np.asarray(a["exposure_id"], dtype=str), a["time"]))
    return {k: v[order] for k, v in a.items()}


def _group_by_ref(table: pa.Table) -> dict[int, pa.Table]:
    ref = table.column("ref_number").to_numpy()
    order = np.argsort(ref, kind="stable")
    sorted_table = table.take(pa.array(order))
    ref = ref[order]
    cuts = np.flatnonzero(np.diff(ref)) + 1
    return {
        int(ref[lo]): sorted_table.slice(lo, hi - lo)
        for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, len(ref)], strict=True)
        if hi > lo
    }
