"""Sprint 1 access checks: which outside services work, from where, and how fast.

Records, for each service the plan relies on, whether a small real query succeeded, on
which server, and how long it took. An unreachable service is a result, not an error.
Safe to run again at any time. In particular, run it again after 2 December 2026, when
Gaia DR4 is released.

    uv run --group spike python scripts/s1_access_checks.py [--only NAME ...]

Writes data/spike/access-checks.json (git-ignored) and prints a table.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import random
import sys
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "spike" / "access-checks.json"
DASCH = "https://api.starglass.cfa.harvard.edu/public"
VIZIER_SERVERS = ["vizier.cds.unistra.fr", "vizier.cfa.harvard.edu"]
SIMBAD_SERVERS = ["simbad.cds.unistra.fr", "simbad.harvard.edu"]
CHECK_TIMEOUT_S = 90

# (name, RA, Dec) of the stars used to test catalog access.
STARS = [("T CrB", 239.87568, 25.92017), ("RY Cnc", 129.97759, 19.82191), ("KIC 8462852", 301.56439, 44.45689)]
# A B=13.2 star near T CrB: faint enough for ZTF. (B=11 stars saturate and return no rows.)
ZTF_FAINT = (239.89251, 25.73808)
ZTF_BRIGHT = (239.61843, 25.94192)  # the Sprint 0 quiet star, B=11.0


def run_check(name: str, what: str, fn: Callable[[], dict[str, Any]], server: str | None = None) -> dict[str, Any]:
    """Run one check. Any failure, including a timeout, is recorded rather than raised."""
    started = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        result = pool.submit(fn).result(timeout=CHECK_TIMEOUT_S)
        ok, error = True, None
    except FutureTimeout:
        result, ok, error = None, False, f"timed out after {CHECK_TIMEOUT_S} s"
    except Exception as exc:  # noqa: BLE001 - an unreachable service is a result to record
        result, ok, error = None, False, f"{type(exc).__name__}: {str(exc)[:240]}"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {
        "name": name,
        "what": what,
        "server": server,
        "ok": ok,
        "seconds": round(time.perf_counter() - started, 2),
        "result": result,
        "error": error,
        "checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ----------------------------------------------------------------------- checks


EMPTY_HINT = (
    "empty answer for an object that must exist. VizieR sends an error document with HTTP 200, "
    "and astroquery turns that into an empty result, when its database is down"
)


def check_vsx(server: str, ra: float, dec: float) -> dict[str, Any]:
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.vizier import Vizier

    v = Vizier(columns=["Name", "Type", "Period", "max", "min"], row_limit=5)
    v.VIZIER_SERVER = server
    res = v.query_region(SkyCoord(ra * u.deg, dec * u.deg), radius=10 * u.arcsec, catalog="B/vsx/vsx", cache=False)  # a cached answer would hide an outage
    rows = [dict(zip(res[0].colnames, [str(x) for x in r], strict=True)) for r in res[0]] if len(res) else []
    if not rows:
        raise RuntimeError(f"VSX: {EMPTY_HINT}")
    return {"rows": len(rows), "first": rows[0]}


def check_simbad(server: str) -> dict[str, Any]:
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.simbad import Simbad

    s = Simbad()
    s.server = server
    s.add_votable_fields("otype")
    res = s.query_region(SkyCoord(239.87568 * u.deg, 25.92017 * u.deg), radius=10 * u.arcsec)
    if res is None or len(res) == 0:
        raise RuntimeError("SIMBAD: empty answer for T CrB")
    return {"rows": len(res), "first": [str(res["main_id"][0]), str(res["otype"][0])]}


def check_ztf(ra: float, dec: float, expect_rows: bool = True) -> dict[str, Any]:
    import requests

    url = f"https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves?POS=CIRCLE%20{ra}%20{dec}%200.0014&FORMAT=csv"
    resp = requests.get(url, timeout=80)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    out: dict[str, Any] = {"rows": len(rows), "bytes": len(resp.content)}
    if expect_rows and not rows:
        raise RuntimeError("ZTF: no rows for a star that is faint enough to be measured")
    if rows:
        mags = [float(r["mag"]) for r in rows]
        out.update(bands=sorted({r["filtercode"] for r in rows}), mag_min=min(mags), mag_max=max(mags),
                   objects=len({r["oid"] for r in rows}))  # fmt: skip
    return out


def check_gaia_dr3_esa() -> dict[str, Any]:
    from astroquery.gaia import Gaia

    job = Gaia.launch_job(
        "select top 2 source_id, phot_g_mean_mag from gaiadr3.gaia_source "
        "where 1=contains(point('ICRS',ra,dec), circle('ICRS',239.87568,25.92017,0.003))"
    )
    res = job.get_results()
    if len(res) == 0:
        raise RuntimeError("Gaia ESA: no source near T CrB")
    return {"rows": len(res)}


def check_gaia_schemas_esa() -> dict[str, Any]:
    from astroquery.gaia import Gaia

    schemas = sorted(Gaia.launch_job("select schema_name from tap_schema.schemas").get_results()["schema_name"])
    return {"schemas": schemas, "has_dr4": any("dr4" in s.lower() for s in schemas)}


def check_gaia_dr3_vizier(server: str) -> dict[str, Any]:
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.vizier import Vizier

    v = Vizier(row_limit=3)
    v.VIZIER_SERVER = server
    res = v.query_region(SkyCoord(239.87568 * u.deg, 25.92017 * u.deg), radius=10 * u.arcsec, catalog="I/355/gaiadr3", cache=False)
    if not len(res) or len(res[0]) == 0:
        raise RuntimeError(f"Gaia DR3 on VizieR: {EMPTY_HINT}")
    return {"rows": len(res[0])}


def _sample_plates(n: int) -> list[str]:
    raw = ROOT / "data" / "spike" / "raw" / "tcrb_queryexps.json.gz"
    if not raw.exists():
        raise FileNotFoundError("needs data/spike/raw/tcrb_queryexps.json.gz (Sprint 0) for a plate sample")
    with gzip.open(raw) as f:
        lines = json.loads(f.read())
    plates = sorted({f"{r.split(',')[0]}{int(r.split(',')[1]):05d}" for r in lines[1:]})
    return random.Random(2).sample(plates, min(n, len(plates)))


def check_plate_class(n: int = 300) -> dict[str, Any]:
    """How complete is plate class from the batch plates endpoint? (queryexps leaves it empty.)"""
    import requests

    plates = _sample_plates(n)
    classes: dict[str, str | None] = {}
    seconds = []
    for i in range(0, len(plates), 100):
        t0 = time.perf_counter()
        r = requests.get(f"{DASCH}/plates", params={"ids": ",".join(plates[i : i + 100])}, timeout=80, headers={"accept": "application/json"})
        r.raise_for_status()
        seconds.append(time.perf_counter() - t0)
        for pid, item in r.json()["results"].items():
            classes[pid] = (item.get("data") or {}).get("class")
    have = sum(1 for v in classes.values() if v)
    return {
        "plates": len(classes),
        "with_class": have,
        "share_with_class": round(have / len(classes), 3) if classes else None,
        "values": dict(Counter(v or "(none)" for v in classes.values()).most_common(12)),
        "seconds_per_batch_of_100": round(sum(seconds) / len(seconds), 2),
    }


def check_class_table() -> dict[str, Any]:
    import requests

    r = requests.get(f"{DASCH}/class", timeout=40, headers={"accept": "application/json"})
    r.raise_for_status()
    rows = r.json()
    return {"classes": len({c["class"] for c in rows}), "plates_listed": sum(c["num_plates"] for c in {c["class"]: c for c in rows}.values())}


def check_dasch_health() -> dict[str, Any]:
    import requests

    r = requests.get(f"{DASCH}/health", timeout=20, headers={"accept": "application/json"})
    r.raise_for_status()
    return r.json()


def all_checks() -> list[tuple[str, str, Callable[[], dict[str, Any]], str | None]]:
    checks: list[tuple[str, str, Callable[[], dict[str, Any]], str | None]] = [
        ("dasch_health", "DASCH API health endpoint", check_dasch_health, "starglass public"),
        ("dasch_class_table", "plate class table", check_class_table, "starglass public"),
        ("dasch_plate_class", "plate class for 300 plates in the T CrB field", check_plate_class, "starglass public"),
    ]
    for server in VIZIER_SERVERS:
        for name, ra, dec in STARS:
            checks.append((f"vsx_{name}", f"VSX match for {name}", lambda s=server, a=ra, d=dec: check_vsx(s, a, d), server))
    for server in SIMBAD_SERVERS:
        checks.append(("simbad", "SIMBAD cone search at T CrB", lambda s=server: check_simbad(s), server))
    checks += [
        ("ztf_faint", "ZTF lightcurve of a B=13.2 star", lambda: check_ztf(*ZTF_FAINT), "irsa.ipac.caltech.edu"),
        ("ztf_bright", "ZTF lightcurve of a B=11.0 star (expected empty: saturated)", lambda: check_ztf(*ZTF_BRIGHT, expect_rows=False), "irsa.ipac.caltech.edu"),
        ("gaia_dr3_esa", "Gaia DR3 cone search, ESA archive", check_gaia_dr3_esa, "gea.esac.esa.int"),
        ("gaia_schemas_esa", "Gaia ESA archive schema list (is DR4 there?)", check_gaia_schemas_esa, "gea.esac.esa.int"),
    ]
    for server in VIZIER_SERVERS:
        checks.append(("gaia_dr3_vizier", "Gaia DR3 cone search through VizieR", lambda s=server: check_gaia_dr3_vizier(s), server))
    return checks


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--only", nargs="+", help="run only checks whose name starts with one of these")
    p.add_argument("--out", type=Path, default=OUT)
    args = p.parse_args(argv)

    selected = [c for c in all_checks() if not args.only or any(c[0].startswith(o) for o in args.only)]
    results = []
    for name, what, fn, server in selected:
        r = run_check(name, what, fn, server)
        results.append(r)
        mark = "ok  " if r["ok"] else "FAIL"
        print(f"[{mark}] {name:20s} {server or ''!s:26s} {r['seconds']:6.1f}s  " + (json.dumps(r["result"])[:90] if r["ok"] else r["error"][:90]), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
