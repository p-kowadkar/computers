"""A synthetic tile with known plate systematics, as a TileContext.

Every star has one measurement per plate (as in real data), a constant magnitude plus a
common plate offset plus noise. Plates are spread over 1890 to 1985 so the scatter
statistics have enough blocks. All stars are forced quiet unless a test says otherwise.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa
from synth_leavitt import table

from computers.l1.context import TileContext

J2000 = 2451545.0


def tile_tables(
    n_star: int = 60,
    n_plate: int = 120,
    *,
    seed: int = 0,
    noise: float = 0.05,
    plate_sigma: float = 0.1,
    mags=None,
    colours=None,
    plate_offset=None,
    star_offset=None,
    missing=None,
    series=None,
    quiet: bool = True,
):
    """The four silver tables of a synthetic tile: (stars, measurements, exposures, vsx_match).

    `star_offset(i, j)` adds a signal; `missing(i, j)` drops a measurement; both optional.
    `quiet` is accepted so the same keyword list serves `make_tile`."""
    rng = np.random.default_rng(seed)
    base = 12.0 + rng.normal(0, 0.01, n_star) if mags is None else np.asarray(mags, dtype=float)
    colour = np.full(n_star, 0.6) if colours is None else np.asarray(colours, dtype=float)
    offs = rng.normal(0, plate_sigma, n_plate) if plate_offset is None else np.asarray(plate_offset, dtype=float)
    year = np.linspace(1890.0, 1985.0, n_plate)
    jd = J2000 + (year - 2000.0) * 365.25
    refs, ids, times, mag, ser, plate_no = [], [], [], [], [], []
    for i in range(n_star):
        for j in range(n_plate):
            if missing and missing(i, j):
                continue
            m = base[i] + offs[j] + rng.normal(0, noise)
            if star_offset:
                m += star_offset(i, j)
            refs.append(i + 1)
            ids.append(f"ac{j:05d}e1")
            times.append(jd[j])
            mag.append(float(m))
            ser.append("ac" if series is None else series(j))
            plate_no.append(j)
    n = len(refs)
    meas = table(
        "measurements", n, ref_number=refs, exposure_id=ids, time=times, magcal_magdep=mag,
        series=ser, platenum=plate_no, reject=[0] * n, limiting_mag_local=[15.0] * n,
        ra_deg=[10.0] * n, dec_deg=[20.0] * n, blended_mag=[None] * n,
    )
    stars = table(
        "stars", n_star, ref_number=list(range(1, n_star + 1)), tile=["m44"] * n_star,
        apass_b=base.tolist(), color=[None if np.isnan(c) else float(c) for c in colour],
    )
    expo = table("exposures", n_plate, exposure_id=[f"ac{j:05d}e1" for j in range(n_plate)],
                 series=["ac"] * n_plate, platenum=list(range(n_plate)))
    vsx = table(
        "vsx_match", n_star, ref_number=list(range(1, n_star + 1)), tile=["m44"] * n_star,
        vsx_oid=[None] * n_star, vsx_name=[None] * n_star, vsx_type=[None] * n_star,
        vsx_period_d=[None] * n_star, vsx_amplitude_mag=[None] * n_star, sep_arcsec=[None] * n_star,
    )
    return stars, meas, expo, vsx


def make_tile(*args, quiet: bool = True, **kw) -> TileContext:
    stars, meas, expo, vsx = tile_tables(*args, quiet=quiet, **kw)
    ctx = TileContext("m44", stars, meas, expo, vsx)
    if quiet:
        ctx.quiet = np.ones(stars.num_rows, dtype=bool)
    return ctx


def write_tile_files(data_dir, tile: str = "m44", **kw) -> None:
    """Write the synthetic tile's tables as silver files, relabelled with `tile`."""
    from computers.data import silver

    stars, meas, expo, vsx = tile_tables(**kw)
    relabel = {"stars": stars, "vsx_match": vsx}
    for name, t in relabel.items():
        i = t.schema.get_field_index("tile")
        relabel[name] = t.set_column(i, t.schema.field("tile"), pa.array([tile] * t.num_rows, pa.string()))
    for name, t in (("stars", relabel["stars"]), ("measurements", meas), ("exposures", expo), ("vsx_match", relabel["vsx_match"])):
        silver.write_table(name, t, silver.silver_path(data_dir, name, tile), snapshot_digest="sha256:test")
