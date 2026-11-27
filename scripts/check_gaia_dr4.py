"""Check whether Gaia DR4 is reachable and what its tables look like (plan 3.5, Sprint 1 follow-up).

Gaia DR4 is due 2026-12-02 12:00 CET, and the ESA archive has been offline since 2026-11-25. Run this once
after the release. It asks the ESA TAP service which `gaiadr4` tables exist, lists the columns of the source
table, and runs a small cone search at the centre of each tile. It reads, never writes, except for one JSON
report under data/spike/ (git-ignored). No key is needed. Run: uv run python scripts/check_gaia_dr4.py

What to do with the result:
- Tables found and the cone searches return rows: write `docs/decisions/0011-gaia-dr4-access.md` with the table
  and column names, then add the `gaia_source` and `gaia_timeseries` tools to the `catalogs` MCP server.
- Reachable but no `gaiadr4` schema: DR4 is not released to the archive yet. Run again later. DR3 is the fallback.
- Not reachable: the archive may be busy on release day. Try again in an hour. DR3 stays on the CfA VizieR mirror.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
OUT = Path("data/spike/gaia-dr4-check.json")
TIMEOUT = 60
# Tile centres in degrees: the median position of the stars in each local tile, or a known star as a fallback.
FALLBACK = {"m44": (130.1, 19.67), "tcrb": (239.875, 25.92), "tabby": (301.564, 44.457)}


def adql(query: str, fmt: str = "json") -> tuple[int, object]:
    r = requests.post(TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": fmt, "QUERY": query}, timeout=TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


def rows(body: object) -> list[list]:
    return body.get("data", []) if isinstance(body, dict) else []


def tile_centres() -> dict[str, tuple[float, float]]:
    out = dict(FALLBACK)
    try:
        import pyarrow.parquet as pq

        for tile in out:
            p = Path(f"data/silver/stars/tile={tile}.parquet")
            if p.is_file():
                t = pq.read_table(p, columns=["ra_deg", "dec_deg"]).to_pydict()
                ra, dec = sorted(t["ra_deg"]), sorted(t["dec_deg"])
                out[tile] = (ra[len(ra) // 2], dec[len(dec) // 2])
    except Exception as exc:  # noqa: BLE001  the centres are a convenience; fall back to the built-in ones and say so
        print(f"note: could not read local tile centres ({type(exc).__name__}); using built-in ones")
    return out


def main() -> int:
    report: dict = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "tap": TAP}
    try:
        status, body = adql("SELECT schema_name FROM TAP_SCHEMA.schemas WHERE schema_name LIKE 'gaiadr%'")
    except requests.RequestException as exc:
        report["reachable"] = False
        report["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print("Gaia archive is NOT reachable:", report["error"])
        print("If this is before 2026-12-02 12:00 CET, that is expected. Try again later.")
        _write(report)
        return 1
    report["reachable"] = status == 200
    schemas = sorted(r[0] for r in rows(body))
    report["schemas"] = schemas
    print(f"archive answered with HTTP {status}; gaia schemas: {schemas or 'none'}")
    dr4 = [s for s in schemas if s.startswith("gaiadr4")]
    if not dr4:
        print("No gaiadr4 schema yet. DR4 is not in the archive. Run this again later; DR3 stays the fallback.")
        _write(report)
        return 1
    schema = dr4[0]
    _, body = adql(f"SELECT table_name FROM TAP_SCHEMA.tables WHERE schema_name = '{schema}'")
    tables = sorted(r[0] for r in rows(body))
    report["tables"] = tables
    print(f"{schema} tables ({len(tables)}):", ", ".join(tables[:30]))
    src = next((t for t in tables if t.endswith("gaia_source")), None)
    if src:
        _, body = adql(f"SELECT column_name, datatype FROM TAP_SCHEMA.columns WHERE table_name = '{src}'")
        cols = sorted(r[0] for r in rows(body))
        report["gaia_source_columns"] = len(cols)
        wanted = ("source_id", "ra", "dec", "parallax", "pmra", "pmdec", "phot_g_mean_mag", "phot_bp_mean_mag", "phot_rp_mean_mag", "phot_variable_flag")
        report["gaia_source_has"] = {c: c in cols for c in wanted}
        print(f"{src}: {len(cols)} columns; expected columns present:", {c: ok for c, ok in report["gaia_source_has"].items()})
        report["cones"] = {}
        for tile, (ra, dec) in tile_centres().items():
            q = f"SELECT COUNT(*) FROM {src} WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, 0.25)) AND phot_g_mean_mag < 16"
            st, b = adql(q)
            n = rows(b)[0][0] if rows(b) else None
            report["cones"][tile] = {"ra": ra, "dec": dec, "radius_deg": 0.25, "sources_g_lt_16": n, "http": st}
            print(f"  {tile}: {n} sources with G < 16 within 0.25 degrees of ({ra:.3f}, {dec:.3f}), HTTP {st}")
    else:
        print("No gaia_source table found in", schema)
    ts = [t for t in tables if "photometry" in t or "epoch" in t]
    report["time_series_tables"] = ts
    print("tables that look like photometry or epoch data:", ts or "none found (epoch data may be served through DataLink)")
    _write(report)
    return 0


def _write(report: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
