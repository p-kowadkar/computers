"""Small synthetic silver tables for the three tiles, with the real schemas.

Every tile gets `n_stars` flat stars (noise 0.12 mag, 300 rows each, a tenth of them
non-detections), an exposures table, an empty-or-small VSX table and its cross-match.
The M44 and Tabby tiles share a few plates on purpose.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa

from computers.data import schemas, silver
from computers.data.tiles import TILES

J2000 = 2451545.0
N_ROWS = 300


def _default(typ: pa.DataType):
    if pa.types.is_integer(typ):
        return 1
    if pa.types.is_floating(typ):
        return 1.5
    if pa.types.is_string(typ):
        return "x"
    if pa.types.is_boolean(typ):
        return True
    if pa.types.is_timestamp(typ):
        return dt.datetime(1946, 2, 9, tzinfo=dt.UTC)
    raise AssertionError(typ)


def table(table_name: str, n: int, /, **cols) -> pa.Table:
    """A `table_name` table of n rows: given columns as lists, the rest filled with defaults."""
    schema = schemas.SCHEMAS[table_name].schema
    data = {}
    for f in schema:
        data[f.name] = pa.array(cols[f.name] if f.name in cols else [_default(f.type)] * n, type=f.type)
    return pa.table(data, schema=schema)


def plate_ids(tile: str, shared: bool) -> list[tuple[str, int]]:
    base = {"m44": 1000, "tcrb": 3000, "tabby": 5000}[tile]
    own = [("ac", base + i) for i in range(20)]
    common = [("ai", 9000 + i) for i in range(5)] if shared else []
    return own + common


def write_tile(data_dir, tile: str, *, n_stars: int = 50, seed: int = 0, shared: bool = True,
               vsx_for: dict[int, tuple[str, float]] | None = None) -> None:
    """Write stars, measurements, exposures, vsx and vsx_match for one tile.

    `vsx_for` maps a star index to (VSX type, amplitude) for a VSX entry placed on it.
    """
    t = TILES[tile]
    rng = np.random.default_rng(seed * 10 + list(TILES).index(tile))
    refs = [int(1_000_000 * (list(TILES).index(tile) + 1) + i) for i in range(n_stars)]
    stars = table(
        "stars", n_stars, ref_number=refs, tile=[tile] * n_stars,
        ra_deg=[t.ra0 + 0.01 * i for i in range(n_stars)], dec_deg=[t.dec0 + 0.005 * i for i in range(n_stars)],
        apass_b=[11.0 + 2.5 * i / n_stars for i in range(n_stars)], n_points=[N_ROWS] * n_stars,
    )
    silver.write_table("stars", stars, silver.silver_path(data_dir, "stars", tile), snapshot_digest="sha256:test")
    plates = plate_ids(tile, shared)
    ref_col, time_col, mag_col, limit_col, series_col, num_col, expo_col = [], [], [], [], [], [], []
    for ref in refs:
        year = np.sort(rng.uniform(1890, 1985, N_ROWS))
        mag = 12.0 + rng.normal(0, 0.12, N_ROWS)
        mag[rng.random(N_ROWS) < 0.1] = np.nan
        for k in range(N_ROWS):
            s, n = plates[k % len(plates)]
            ref_col.append(ref); time_col.append(J2000 + (year[k] - 2000.0) * 365.25)
            mag_col.append(None if np.isnan(mag[k]) else float(mag[k])); limit_col.append(15.0)
            series_col.append(s); num_col.append(n); expo_col.append(f"{s}{n:05d}e1")
    m = len(ref_col)
    meas = table("measurements", m, ref_number=ref_col, exposure_id=expo_col, time=time_col,
                 magcal_magdep=mag_col, limiting_mag_local=limit_col, series=series_col,
                 platenum=num_col, reject=[0] * m)
    silver.write_table("measurements", meas, silver.silver_path(data_dir, "measurements", tile),
                       snapshot_digest="sha256:test")
    expo = table("exposures", len(plates), exposure_id=[f"{s}{n:05d}e1" for s, n in plates],
                 series=[s for s, _ in plates], platenum=[n for _, n in plates])
    silver.write_table("exposures", expo, silver.silver_path(data_dir, "exposures", tile),
                       snapshot_digest="sha256:test")
    vsx_for = vsx_for or {}
    k = len(vsx_for)
    vsx = table("vsx", k, tile=[tile] * k, oid=[500 + i for i in range(k)], name=[f"V{i}" for i in range(k)],
                ra_deg=[t.ra0 + 0.01 * i for i in vsx_for], dec_deg=[t.dec0 + 0.005 * i for i in vsx_for],
                vtype=[v[0] for v in vsx_for.values()], amplitude_mag=[v[1] for v in vsx_for.values()])
    silver.write_table("vsx", vsx, silver.silver_path(data_dir, "vsx", tile), snapshot_digest="sha256:test")
    from computers.data.vsx import crossmatch

    silver.write_table("vsx_match", crossmatch(stars, vsx), silver.silver_path(data_dir, "vsx_match", tile),
                       snapshot_digest="sha256:test")


def write_all(data_dir, **kw) -> None:
    write_tile(data_dir, "m44", vsx_for={0: ("SRB", 1.3), 1: ("EA", 0.2)}, **kw)
    write_tile(data_dir, "tcrb", vsx_for={2: ("NR", 8.8)}, **kw)
    write_tile(data_dir, "tabby", **kw)
