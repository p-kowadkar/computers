"""Sprint 0 data spike: pull four stars, plot, cut out, time, and record.

Run from the repo root:

    export STARGLASS_API_KEY=...          # optional, see below
    uv run --group spike python scripts/s0_spike.py

Without STARGLASS_API_KEY the public API is used. The key is read only from the
environment, handed to daschlab for this process, and never printed or written.

Spec: .kiro/specs/s0-data-spike/. Everything disposable goes under data/spike/
(git-ignored). Plots go to docs/decisions/assets/s0/. A full machine-readable
record is written to data/spike/results.json for the findings write-up.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from computers.data import spike

ROOT = Path(__file__).resolve().parents[1]
SPIKE_DIR = ROOT / "data" / "spike"
ASSET_DIR = ROOT / "docs" / "decisions" / "assets" / "s0"
API_ROOT = "https://api.starglass.cfa.harvard.edu"
REFCAT_RADIUS_ARCSEC = 850.0  # what daschlab 1.1.0 uses for a session

TARGETS: dict[str, dict[str, Any]] = {
    "rycnc": {"label": "RY Cnc", "name": "V* RY Cnc"},
    "tcrb": {"label": "T CrB", "name": "V* T CrB", "fallback": (239.875, 25.92)},
    "tabby": {"label": "KIC 8462852", "name": "KIC 8462852", "fallback": (301.56, 44.46)},
    "quiet": {"label": "quiet star", "name": None},
}
ORDER = ["rycnc", "tcrb", "tabby", "quiet"]

# DASCH's "medium" lightcurve column set (lightcurve-columns page, 2026-11-23).
MEDIUM_COLUMNS = [
    "time", "magcal_magdep", "limiting_mag_local", "pos", "fwhm_world", "ellipticity",
    "theta_j2000", "iso_area_world", "aflags", "bflags", "plate_quality_flag",
    "local_bin_reject_flag", "series", "platenum", "mosnum", "solnum", "expnum", "image_x",
    "image_y", "magcal_local_rms", "magcal_local_error", "drad_rms2", "extinction",
    "magcor_local", "blended_mag", "npoints_local", "background", "fwhm_image", "kron_radius",
    "time_accuracy", "catalog_ra", "catalog_dec", "pm_ra_cosdec", "pm_dec", "plate_dist",
]  # fmt: skip
# The `measurements` table in plan.md section 4.5.
PLAN_MEASUREMENT_COLUMNS = [
    "ref_number", "time", "magcal_magdep", "magcal_local_rms", "limiting_mag_local", "series",
    "platenum", "aflags", "bflags", "plate_quality_flag", "fwhm_image", "ellipticity",
    "plate_dist", "blended_mag", "reject",
]  # fmt: skip


def log(msg: str) -> None:
    print(f"[s0] {msg}", flush=True)


# --------------------------------------------------------------------------- helpers


def to_float(col: Any) -> np.ndarray:
    """Masked or plain column to a float array, NaN where masked."""
    mask = getattr(col, "mask", None)
    mask = np.zeros(len(col), dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    data = getattr(col, "unmasked", col)
    data = getattr(data, "value", data)
    vals = np.asarray(np.ma.getdata(data), dtype=float)
    return np.where(mask, np.nan, vals)


def to_int(col: Any, fill: int = 0) -> np.ndarray:
    return np.asarray(np.ma.filled(col, fill), dtype=np.int64)


def curve_arrays(lc: Any) -> dict[str, np.ndarray]:
    return {
        "year": np.asarray(lc["time"].decimalyear, dtype=float),
        "mag": to_float(lc["magcal_magdep"]),
        "limit": to_float(lc["limiting_mag_local"]),
        "aflags": to_int(lc["aflags"]),
        "series": np.array([str(s) for s in lc["series"]], dtype=object),
        "exp_local_id": to_int(lc["exp_local_id"], fill=-1),
    }


def save_png(fig: Any, path: Path, max_kb: int = 500) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    for dpi in (110, 90, 70, 55):
        fig.savefig(path, dpi=dpi)
        size = path.stat().st_size
        if size <= max_kb * 1024:
            return size
    return path.stat().st_size


def plot_lightcurve(a: dict[str, np.ndarray], reject: np.ndarray, title: str, path: Path) -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    det = spike.detected_mask(a["mag"])
    keep = reject == 0
    fig, ax = plt.subplots(figsize=(9, 4.5))
    good = det & keep
    nondet = ~det & keep
    if good.any():
        ax.plot(a["year"][good], a["mag"][good], ".", ms=3, color="tab:blue", label="detections")
    if nondet.any():
        ax.plot(
            a["year"][nondet], a["limit"][nondet], "v", ms=2.5, color="0.65", label="limits"
        )
    if good.any():
        lo, hi = np.nanmin(a["mag"][good]), np.nanmax(a["mag"][good])
        pad = max(0.5, 0.1 * (hi - lo))
        ax.set_ylim(hi + pad, lo - pad)  # inverted: brighter is up
    else:
        ax.invert_yaxis()
    ax.set_xlabel("year")
    ax.set_ylabel("magnitude (smaller is brighter)")
    ax.set_title(f"{title}  ({int(good.sum())} detections, {int(nondet.sum())} limits shown)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    size = save_png(fig, path)
    plt.close(fig)
    return size


# --------------------------------------------------------------------------- session


def open_target_session(key: str, target: dict[str, Any], refresh: bool) -> Any:
    from daschlab import open_session

    root = SPIKE_DIR / key
    if refresh and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    sess = open_session(str(root), interactive=False)
    if target.get("ra_deg") is not None:
        sess.select_target(ra_deg=target["ra_deg"], dec_deg=target["dec_deg"])
    else:
        try:
            sess.select_target(name=target["name"])
        except Exception as exc:  # name resolution can fail; fall back to plan.md 3.4
            fb = target.get("fallback")
            if not fb:
                raise
            log(f"  name lookup failed ({type(exc).__name__}); using plan.md coordinates {fb}")
            target["used_fallback_coords"] = True
            sess.select_target(ra_deg=fb[0], dec_deg=fb[1])
    sess.select_refcat("apass")
    return sess


def process_star(key: str, target: dict[str, Any], sess: Any, out: dict[str, Any]) -> dict[str, Any]:
    """Lightcurve, rejections, summary, plots for the source nearest the target."""
    from astropy import units as u
    from daschlab.photometry import AFlags

    q = sess.query()
    refcat = sess.refcat()
    ra = np.asarray([c.ra.deg for c in refcat["pos"]], dtype=float)
    dec = np.asarray([c.dec.deg for c in refcat["pos"]], dtype=float)
    rep = spike.separation_report(ra, dec, q.ra_deg, q.dec_deg)
    out["target_position"] = {"ra_deg": q.ra_deg, "dec_deg": q.dec_deg}
    out["catalog_sources_in_session"] = len(refcat)
    out["nearest_sources"] = [
        {
            **n,
            "ref_text": str(refcat["ref_text"][n["index"]]),
            "stdmag": float(to_float(refcat["stdmag"])[n["index"]]),
            "num_matches": int(refcat["num_matches"][n["index"]]),
        }
        for n in rep["nearest"]
    ]
    if rep["warn"]:
        log(f"  WARNING: nearest catalog source is {rep['nearest'][0]['sep_arcsec']:.1f} arcsec away")
        for n in out["nearest_sources"]:
            log(f"    {n['ref_text']}  sep={n['sep_arcsec']:.1f}\"  mag={n['stdmag']:.2f}")
    out["match_warning"] = bool(rep["warn"])
    exps = sess.exposures()
    out["exposure_columns"] = list(exps.colnames)
    out["exposure_count"] = len(exps)

    t0 = time.perf_counter()
    lc = sess.lightcurve(0)
    out["lightcurve_fetch_seconds_incl_cache"] = time.perf_counter() - t0
    out["columns"] = list(lc.colnames)

    a = curve_arrays(lc)
    raw_reject = np.zeros(len(lc), dtype=np.int64)
    lc.apply_standard_rejections()
    rej_std = np.asarray(lc["reject"], dtype=np.int64).copy()
    lc.reject.sep_above(15 * u.arcsec, tag="sep", verbose=False)
    lc.reject.any_aflags(AFlags.SUSPECTED_DEFECT, tag="defect", verbose=False)
    rej_ext = np.asarray(lc["reject"], dtype=np.int64).copy()

    out["summary"] = spike.summarize(a["year"], a["mag"], a["series"], rej_std, rej_ext)
    out["aflag_bit_counts_among_detections"] = {
        f.name: int(((a["aflags"] & int(f)) != 0)[spike.detected_mask(a["mag"])].sum())
        for f in AFlags
    }

    label = target["label"]
    out["plots"] = {
        "raw": str(ASSET_DIR / f"{key}_raw.png"),
        "clean": str(ASSET_DIR / f"{key}_clean.png"),
    }
    out["plot_bytes"] = {
        "raw": plot_lightcurve(a, raw_reject, f"{label}: raw", ASSET_DIR / f"{key}_raw.png"),
        "clean": plot_lightcurve(a, rej_ext, f"{label}: cleaned", ASSET_DIR / f"{key}_clean.png"),
    }
    out["_arrays"] = {"a": a, "raw": raw_reject, "std": rej_std, "ext": rej_ext, "lc": lc}
    return out


# --------------------------------------------------------------------------- T CrB check


def run_tcrb_check(out: dict[str, Any]) -> bool:
    arr = out["_arrays"]
    a = arr["a"]
    res = {
        "raw": spike.tcrb_outburst_check(a["year"], a["mag"], arr["raw"]),
        "standard": spike.tcrb_outburst_check(a["year"], a["mag"], arr["std"]),
        "standard_plus_extra": spike.tcrb_outburst_check(a["year"], a["mag"], arr["ext"]),
    }
    out["tcrb_check"] = res
    std, raw = res["standard"], res["raw"]
    log(f"  T CrB check (standard rejections): {'PASS' if std['passed'] else 'FAIL'}")
    log(f"    baseline median {std['baseline_median']}, 1946 points {std['n_in_1946']}")
    if raw["passed"] and not std["passed"]:
        log("  NOTE: eruption visible in RAW data only. A standard rejection removes a real event.")
    if not std["passed"]:
        log(f"    reason: {std['reason']}")
        for p in raw["points"]:
            log(f"    raw 1946 point: year {p['year']:.3f} mag {p['mag']:.2f}")
    return bool(std["passed"])


# --------------------------------------------------------------------------- cutouts


def render_cutout(path_fits: Path, title: str, png: Path) -> dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from astropy.io import fits

    with fits.open(str(path_fits)) as hdul:
        hdu = next(h for h in hdul if h.data is not None and h.data.ndim == 2)
        data = np.asarray(hdu.data, dtype=float)
    ny, nx = data.shape
    finite = data[np.isfinite(data)]
    lo, hi = np.percentile(finite, [1, 99.5]) if finite.size else (0, 1)

# Strongest blob within +/- 24 pixels (about 35 arcsec) of the centre, on a 3x3-binned
# copy. A wider window just finds bright neighbours. Approximate by design: it says
# where the image is, not what the star's true position is. `significance` is the peak
# height in robust sigmas of the whole cutout, so a faint star is not mistaken for noise.
    b = 3
    ny3, nx3 = (ny // b) * b, (nx // b) * b
    binned = np.nan_to_num(data[:ny3, :nx3]).reshape(ny3 // b, b, nx3 // b, b).mean(axis=(1, 3))
    med = np.median(binned)
    dev = np.abs(binned - med)
    sigma = 1.4826 * float(np.median(dev)) or 1.0
    cy, cx = dev.shape[0] // 2, dev.shape[1] // 2
    w = 8  # +/- 24 pixels
    window = dev[cy - w : cy + w + 1, cx - w : cx + w + 1]
    iy, ix = np.unravel_index(int(np.argmax(window)), window.shape)
    peak_x = (cx - w + ix) * b + b / 2 - 0.5
    peak_y = (cy - w + iy) * b + b / 2 - 0.5
    off = spike.cutout_offset(peak_x, peak_y, (ny, nx))
    significance = float(window[iy, ix] / sigma)

    fig, ax = plt.subplots(figsize=(5.5, 5.5))
    ax.imshow(data, origin="lower", cmap="gray", vmin=lo, vmax=hi)
    ax.add_patch(plt.Circle(((nx - 1) / 2, (ny - 1) / 2), 30, fill=False, color="tab:red", lw=0.8))
    ax.set_title(title, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    size = save_png(fig, png)
    plt.close(fig)
    return {
        "shape": [ny, nx],
        "peak_offset_pixels": off,
        "peak_significance_sigma": significance,
        "png": str(png),
        "png_bytes": size,
    }


def do_cutout(
    sess: Any, exp_local_id: int, tag: str, title: str, key: str
) -> dict[str, Any]:
    rec: dict[str, Any] = {"tag": tag, "exp_local_id": int(exp_local_id)}
    if exp_local_id < 0:
        rec["skipped"] = "lightcurve point has no matching exposure row (exp_local_id = -1)"
        return rec
    exps = sess.exposures()
    row = exps[int(exp_local_id)]
    rec["exposure"] = row.exp_id()
    rec["obs_year"] = float(row["obs_date"].jyear)
    if not row.has_imaging():
        rec["skipped"] = "exposure has no imaging (plate not scanned or no WCS solution)"
        return rec
    t0 = time.perf_counter()
    rel = sess.cutout(int(exp_local_id))
    rec["seconds"] = time.perf_counter() - t0
    if not rel:
        rec["skipped"] = "daschlab returned no cutout"
        return rec
    rec.update(render_cutout(sess.path(rel), title, ASSET_DIR / f"{key}_{tag}.png"))
    rec["fits"] = str(sess.path(rel))
    return rec


def cutouts_for(key: str, sess: Any, out: dict[str, Any]) -> list[dict[str, Any]]:
    arr = out["_arrays"]
    a, rej = arr["a"], arr["std"]
    det_good = spike.detected_mask(a["mag"]) & (rej == 0) & (a["exp_local_id"] >= 0)
    results: list[dict[str, Any]] = []
    if not det_good.any():
        return [{"tag": "main", "skipped": "no good detections with an exposure row"}]

    idx_good = np.flatnonzero(det_good)
    if key == "tcrb":
        in1946 = idx_good[(a["year"][idx_good] >= 1946) & (a["year"][idx_good] < 1947)]
        if in1946.size:
            pick = int(in1946[np.argmin(a["mag"][in1946])])  # brightest 1946 point
            tag = "1946"
        else:
            pick = int(idx_good[np.argmin(np.abs(a["year"][idx_good] - np.median(a["year"][idx_good])))])
            tag = "main"
            out["tcrb_cutout_note"] = "no 1946 detection survived the standard rejections"
    else:
        pick = int(idx_good[np.argmin(np.abs(a["year"][idx_good] - np.median(a["year"][idx_good])))])
        tag = "main"
    title = f"{out['label']} {tag}: year {a['year'][pick]:.2f}, mag {a['mag'][pick]:.2f}"
    results.append(do_cutout(sess, int(a["exp_local_id"][pick]), tag, title, key))

    if key == "rycnc":
        from daschlab.photometry import AFlags

        flagged = spike.detected_mask(a["mag"]) & ((a["aflags"] & int(AFlags.SUSPECTED_DEFECT)) != 0)
        flagged &= a["exp_local_id"] >= 0
        if flagged.any() and np.isfinite(out["summary"]["median_detected_mag"]):
            idx = np.flatnonzero(flagged)
            # The flagged point that looks most like a flare: brightest relative to the median.
            j = int(idx[np.argmin(a["mag"][idx])])
            t = f"RY Cnc SUSPECTED_DEFECT: year {a['year'][j]:.2f}, mag {a['mag'][j]:.2f}"
            results.append(do_cutout(sess, int(a["exp_local_id"][j]), "defect", t, key))
        else:
            results.append({"tag": "defect", "skipped": "no detection carries SUSPECTED_DEFECT"})
    return results


# --------------------------------------------------------------------------- timing and raw API


def run_timing(sess: Any, n: int = 10) -> dict[str, Any]:
    """Time lightcurve fetches for catalog sources 1..n (source 0 is T CrB itself).

    Exposures are fetched first so the first measurement does not include them.
    """
    t0 = time.perf_counter()
    sess.exposures()
    exposures_seconds = time.perf_counter() - t0
    records = []
    for i in range(1, n + 1):
        ref_text = str(sess.refcat()["ref_text"][i])
        was_cached = sess.path("lightcurves", f"{ref_text}.ecsv").exists()
        t0 = time.perf_counter()
        ok, err, size, rows = True, None, None, None
        try:
            lc = sess.lightcurve(i)
            rows = len(lc)
            ref = str(sess.refcat()["ref_text"][i])
            f = sess.path("lightcurves", f"{ref}.ecsv")
            size = f.stat().st_size if f.exists() else None
        except Exception as exc:  # noqa: BLE001 - any failure is a data point; recorded, not fatal
            ok, err = False, f"{type(exc).__name__}: {exc}"
        records.append(
            {
                "local_id": i,
                "seconds": time.perf_counter() - t0,
                "was_cached": was_cached,
                "ok": ok,
                "rows": rows,
                "saved_ecsv_bytes": size,
                "error": err,
            }
        )
        note = " (CACHED: not a measurement, rerun with --refresh)" if was_cached else ""
        log(f"  timing {i}/{n}: {records[-1]['seconds']:.2f}s ok={ok} rows={rows}{note}")
    return {
        "exposures_seconds": exposures_seconds,
        "records": records,
        "n_cached_excluded": sum(1 for r in records if r["was_cached"]),
        "stats": spike.timing_stats([r for r in records if not r["was_cached"]]),
        "max_rows": max((r["rows"] or 0) for r in records),
        "max_saved_ecsv_bytes": max((r["saved_ecsv_bytes"] or 0) for r in records),
    }


def raw_capture(mode: str, key_value: str | None, sess: Any) -> dict[str, Any]:
    """One direct querycat and one direct lightcurve call, saved gzipped (R8)."""
    import requests

    base = f"{API_ROOT}/{'full' if mode == 'full' else 'public'}"
    headers = {"Accept": "application/json", "User-Agent": "computers-s0-spike"}
    if key_value:
        headers["x-api-key"] = key_value
    q = sess.query()
    src = sess.refcat()[0]
    rawdir = SPIKE_DIR / "raw"
    rawdir.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {"base": base.replace(API_ROOT, "<starglass>")}

    def call(endpoint: str, payload: dict[str, Any], fname: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        resp = requests.post(f"{base}{endpoint}", json=payload, headers=headers, timeout=120)
        seconds = time.perf_counter() - t0
        body = resp.content
        with gzip.open(rawdir / fname, "wb") as f:
            f.write(body)
        info: dict[str, Any] = {
            "status": resp.status_code,
            "seconds": seconds,
            "bytes": len(body),
            "content_type": resp.headers.get("content-type"),
        }
        try:
            data = resp.json()
        except ValueError:
            info["json"] = False
            return info
        info["json_type"] = type(data).__name__
        if isinstance(data, list) and data:
            info["n_records"] = len(data)
            info["header"] = str(data[0]).split(",")
            info["first_row"] = str(data[1]).split(",") if len(data) > 1 else None
        else:
            info["non_list_body_preview"] = str(data)[:300]
        return info

    rec["querycat"] = call(
        "/dasch/dr7/querycat",
        {
            "refcat": "apass",
            "ra_deg": q.ra_deg,
            "dec_deg": q.dec_deg,
            "radius_arcsec": REFCAT_RADIUS_ARCSEC,
        },
        "tcrb_querycat.json.gz",
    )
    rec["lightcurve"] = call(
        "/dasch/dr7/lightcurve",
        {
            "refcat": "apass",
            "ref_number": int(src["ref_number"]),
            "gsc_bin_index": int(src["gsc_bin_index"]),
        },
        "tcrb_lightcurve.json.gz",
    )
    rec["queryexps"] = call(
        "/dasch/dr7/queryexps",
        {"ra_deg": q.ra_deg, "dec_deg": q.dec_deg},
        "tcrb_queryexps.json.gz",
    )
    rec["limit_6mb"] = 6 * 1024 * 1024
    return rec


# --------------------------------------------------------------------------- quiet star


def choose_quiet_star(tcrb_sess: Any) -> dict[str, Any]:
    """Apply the quiet-star rule to the T CrB catalog (design.md)."""
    from astropy import units as u
    from astropy.coordinates import SkyCoord
    from astroquery.simbad import Simbad

    cat = tcrb_sess.refcat()
    stdmag = to_float(cat["stdmag"])
    sep_arcmin = np.hypot(
        np.asarray(cat["dra"].to(u.arcsec).value, dtype=float),
        np.asarray(cat["ddec"].to(u.arcsec).value, dtype=float),
    ) / 60.0
    cands = [
        {
            "ref_number": int(cat["ref_number"][i]),
            "b_mag": None if np.isnan(stdmag[i]) else float(stdmag[i]),
            "sep_arcmin": float(sep_arcmin[i]),
            "n_detections": int(cat["num_matches"][i]),
            "local_id": int(i),
        }
        for i in range(len(cat))
    ]
    shortlist = spike.quiet_star_shortlist(cands)
    log(f"  quiet-star shortlist: {len(shortlist)} of {len(cands)} sources")
    otypes: dict[int, str | None] = {}
    # The Strasbourg SIMBAD server returned 404 on 2026-11-23 while the Harvard mirror
    # answered, so try the servers in order and record which one worked.
    servers = [s for s in os.environ.get("SPIKE_SIMBAD_SERVERS", "").split(",") if s] or [
        "simbad.cds.unistra.fr",
        "simbad.harvard.edu",
    ]
    server_errors: dict[str, str] = {}
    simbad = None
    probe = SkyCoord(ra=cat["pos"][0].ra, dec=cat["pos"][0].dec, frame="icrs")
    for server in servers:
        try:
            cand = Simbad()
            cand.server = server
            cand.add_votable_fields("otype")  # contacts the server, so it belongs in the try
            cand.query_region(probe, radius=5 * u.arcsec)
            simbad = cand
            break
        except Exception as exc:  # noqa: BLE001 - any server failure means try the next mirror
            server_errors[server] = f"{type(exc).__name__}: {str(exc)[:120]}"
            log(f"  SIMBAD server {server} failed: {type(exc).__name__}")
    if simbad is None:
        raise RuntimeError(f"no SIMBAD server answered: {server_errors}")
    for c in shortlist[:25]:  # bounded: a polite number of SIMBAD queries
        pos = cat["pos"][c["local_id"]]
        coord = SkyCoord(ra=pos.ra, dec=pos.dec, frame="icrs")
        try:
            res = simbad.query_region(coord, radius=5 * u.arcsec)
            col = next((n for n in res.colnames if n.lower() == "otype"), None) if res else None
            otypes[c["ref_number"]] = str(res[col][0]).strip() if col else None
        except Exception as exc:  # noqa: BLE001 - a failed lookup leaves the type unknown
            log(f"  SIMBAD lookup failed for {c['ref_number']}: {type(exc).__name__}")
            otypes[c["ref_number"]] = None
        time.sleep(0.3)
    pick = spike.pick_quiet_star(shortlist[:25], otypes)
    return {
        "candidates_total": len(cands),
        "simbad_server_used": simbad.server,
        "simbad_server_errors": server_errors,
        "shortlist": shortlist,
        "otypes": {str(k): v for k, v in otypes.items()},
        "pick": pick,
        "proxy_note": "SIMBAD object type is a proxy for 'not variable'. VSX arrives in Task 4.",
        "assumptions": "stdmag taken as APASS B; num_matches taken as DASCH detection count",
    }


# --------------------------------------------------------------------------- main


def clean_for_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): clean_for_json(v) for k, v in obj.items() if k != "_arrays"}
    if isinstance(obj, list | tuple):
        return [clean_for_json(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--targets", nargs="+", choices=ORDER, default=ORDER)
    p.add_argument("--refresh", action="store_true", help="delete cached sessions and refetch")
    p.add_argument("--no-timing", action="store_true", help="skip the timing and raw-API steps")
    args = p.parse_args(argv)

    mode, key_value = spike.api_mode_from_env(os.environ)
    if key_value:
        os.environ[spike.DASCHLAB_ENV] = key_value  # for this process only
        log("Using the FULL API (key found in STARGLASS_API_KEY).")
    else:
        os.environ.pop(spike.DASCHLAB_ENV, None)
        log("NOTICE: STARGLASS_API_KEY is not set. Using the PUBLIC API (lower rate limits).")

    import daschlab

    # A subset run updates only its own targets and keeps the rest of the earlier record.
    results_path = SPIKE_DIR / "results.json"
    results: dict[str, Any] = {"targets": {}}
    if results_path.exists():
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
        except ValueError:
            results = {"targets": {}}
    results.update(
        {
            "daschlab_version": daschlab.__version__,
            "python": sys.version.split()[0],
            "last_run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_run_api_mode": mode,
        }
    )
    results.setdefault("targets", {})
    sessions: dict[str, Any] = {}
    exit_code = 0

    for key in [k for k in ORDER if k in args.targets]:
        target = dict(TARGETS[key])
        out: dict[str, Any] = {"label": target["label"], "api_mode": mode}
        results["targets"][key] = out
        log(f"== {target['label']}")
        try:
            if key == "quiet":
                if "tcrb" not in sessions:
                    sessions["tcrb"] = open_target_session("tcrb", TARGETS["tcrb"], False)
                choice = choose_quiet_star(sessions["tcrb"])
                out["selection"] = choice
                if not choice["pick"]:
                    out["error"] = "no quiet-star candidate qualified"
                    log("  no quiet-star candidate qualified")
                    continue
                chosen = choice["pick"]["chosen"]
                cat = sessions["tcrb"].refcat()
                pos = cat["pos"][chosen["local_id"]]
                target["ra_deg"], target["dec_deg"] = float(pos.ra.deg), float(pos.dec.deg)
                log(f"  chose ref_number {chosen['ref_number']} (B~{chosen['b_mag']:.2f})")
            sess = open_target_session(key, target, args.refresh)
            sessions[key] = sess
            process_star(key, target, sess, out)
            log(f"  rows={out['summary']['rows']} detections={out['summary']['detections']}")
            if key == "tcrb" and not run_tcrb_check(out):
                exit_code = 1
            out["cutouts"] = cutouts_for(key, sess, out)
            for c in out["cutouts"]:
                log(f"  cutout {c.get('tag')}: {c.get('skipped') or c.get('exposure')}")
            if key == "tcrb" and not args.no_timing:
                out["timing"] = run_timing(sess)
                out["raw_api"] = raw_capture(mode, key_value, sess)
        except Exception as exc:  # noqa: BLE001 - one failing target must not stop the others
            out["error"] = spike.redact(f"{type(exc).__name__}: {exc}", key_value)
            out["traceback"] = spike.redact(traceback.format_exc(), key_value)
            log(f"  ERROR: {out['error']}")
            if key == "tcrb":
                exit_code = 1

    # Column comparison (R9), from whichever lightcurve was fetched first.
    first = next((t for t in results["targets"].values() if t.get("columns")), None)
    if first:
        cols = set(first["columns"])
        results["columns"] = {
            "present": sorted(cols),
            "plan_4_5_missing": [c for c in PLAN_MEASUREMENT_COLUMNS if c not in cols],
            "medium_set_missing": [c for c in MEDIUM_COLUMNS if c not in cols],
        }

    SPIKE_DIR.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(clean_for_json(results), indent=2, default=str), encoding="utf-8"
    )
    log(f"wrote {results_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
