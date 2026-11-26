"""AAVSO's Variable Star Index (VSX) for a tile, through the VizieR mirrors.

Facts it is built on (checked live on 2026-11-26): the CfA mirror answers
``/viz-bin/asu-tsv`` for catalog ``B/vsx/vsx`` (its TAP service answers 404 and
Strasbourg was down); ``-c.bd`` is a box in degrees and its RA width is on-sky; the
``Type`` column can contain ``|``, so the output is tab separated; and a mirror that is
unwell can answer with an HTML page or with an empty table, so an answer is checked
before it is stored.
"""
from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow as pa
import requests

from computers import __version__
from computers.data import schemas, silver
from computers.data.snapshot import Request, SnapshotStore
from computers.data.tiles import TILES, Tile, in_tile
from computers.thresholds import THRESHOLDS

__all__ = [
    "SERVERS",
    "SOURCE",
    "Fetch",
    "VsxError",
    "VsxFormatError",
    "VsxResult",
    "check_answer",
    "crossmatch",
    "parse_vsx",
    "pull_vsx",
    "query_params",
    "run_vsx_pull",
]

SOURCE = "vsx-vizier"
ENDPOINT = "/viz-bin/asu-tsv"
SERVERS = ("vizier.cfa.harvard.edu", "vizier.cds.unistra.fr")
BOX_DEG = 2.1  # the tile is 2 degrees on a side; the extra 0.05 on each side is clipped away
TIMEOUT_S = 90.0
MIN_ROWS = 50  # every tile holds hundreds of VSX entries; fewer means a sick mirror
# A known variable that must be in the answer for the tile, when there is one.
SENTINELS: dict[str, str] = {"m44": "RY Cnc", "tcrb": "T CrB"}
_REQUIRED = ("OID", "Name", "RAJ2000", "DEJ2000", "Type", "max", "min", "u_min", "Period")

# (server, params) -> (HTTP status, body). Replaced by a fake in tests.
Fetch = Callable[[str, Mapping[str, str]], tuple[int, bytes]]


class VsxError(RuntimeError):
    """No server gave a usable answer."""


class VsxFormatError(ValueError):
    """An answer is not the table we asked for."""


@dataclass(frozen=True)
class VsxResult:
    table: pa.Table
    server: str | None  # None when the answer came from the snapshot store
    errors: dict[str, str] = field(default_factory=dict)  # servers that failed, and why


def query_params(tile: Tile) -> dict[str, str]:
    """The VizieR query for a tile: a box centred on it, all rows, positions in degrees."""
    return {
        "-source": "B/vsx/vsx",
        "-c": f"{tile.ra0:.5f} {tile.dec0:.5f}",
        "-c.bd": f"{BOX_DEG}x{BOX_DEG}",
        "-out.max": "unlimited",
        "-oc.form": "d",
    }


def http_fetch(server: str, params: Mapping[str, str]) -> tuple[int, bytes]:
    r = requests.get(f"https://{server}{ENDPOINT}", params=dict(params), timeout=TIMEOUT_S)
    return r.status_code, r.content


# ----------------------------------------------------------------------- parsing


def _num(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _split_rows(body: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VsxFormatError("answer is not UTF-8 text") from exc
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    if not lines or lines[0].lstrip().lower().startswith(("<", "<!")):
        raise VsxFormatError("answer is not a table (looks like a web page or is empty)")
    header = [h.strip() for h in lines[0].split("\t")]
    missing = [c for c in _REQUIRED if c not in header]
    if missing:
        raise VsxFormatError(f"answer lacks the columns {missing}")
    # lines[1] holds the units and lines[2] the dashes; data follows.
    if len(lines) < 3 or not set(lines[2].replace("\t", "").replace(" ", "")) <= {"-"}:
        raise VsxFormatError("answer has no units and dashes lines after the header")
    return header, [ln.split("\t") for ln in lines[3:]]


def parse_vsx(body: bytes, tile: Tile) -> pa.Table:
    """Parse a VizieR tab-separated answer into the `vsx` table, clipped to the tile."""
    header, rows = _split_rows(body)
    ix = {name: header.index(name) for name in _REQUIRED}
    var_ix = header.index("V") if "V" in header else None
    out: dict[str, list] = {k: [] for k in (
        "oid", "name", "var_flag", "ra_deg", "dec_deg", "vtype", "max_mag", "min_mag",
        "min_is_amplitude", "amplitude_mag", "period_d",
    )}
    for row in rows:
        if len(row) != len(header):
            raise VsxFormatError(
                f"a data row has {len(row)} fields where the header has {len(header)}"
            )
        ra, dec, oid = _num(row[ix["RAJ2000"]]), _num(row[ix["DEJ2000"]]), _num(row[ix["OID"]])
        if ra is None or dec is None or oid is None:
            raise VsxFormatError(f"a row has no usable OID or position: {row[:3]}")
        if not in_tile(tile, ra, dec):
            continue
        mx, mn = _num(row[ix["max"]]), _num(row[ix["min"]])
        is_amp = row[ix["u_min"]].strip() == ")"
        if mn is None:
            amp = None
        else:
            amp = mn if is_amp else (None if mx is None else mn - mx)
        var = _num(row[var_ix]) if var_ix is not None else None
        vtype = row[ix["Type"]].strip()
        out["oid"].append(int(oid))
        out["name"].append(row[ix["Name"]].strip())
        out["var_flag"].append(None if var is None else int(var))
        out["ra_deg"].append(ra)
        out["dec_deg"].append(dec)
        out["vtype"].append(vtype or None)
        out["max_mag"].append(mx)
        out["min_mag"].append(mn)
        out["min_is_amplitude"].append(is_amp)
        out["amplitude_mag"].append(amp)
        out["period_d"].append(_num(row[ix["Period"]]))
    n = len(out["oid"])
    if len(set(out["oid"])) != n:
        raise VsxFormatError("duplicate OID values in one answer")
    spec = schemas.SCHEMAS["vsx"].schema
    return pa.table({"tile": [tile.name] * n, **out}, schema=spec)


def check_answer(tile: Tile, table: pa.Table) -> None:
    """Raise `VsxFormatError` when an answer is too thin to be right for this tile.

    An empty or short table is what a failing mirror returns, and it would look like
    "no known variables here", which is the wrong thing to believe.
    """
    if table.num_rows < MIN_ROWS:
        raise VsxFormatError(f"only {table.num_rows} entries in {tile.name}; expected at least {MIN_ROWS}")
    sentinel = SENTINELS.get(tile.name)
    if sentinel and sentinel not in set(table.column("name").to_pylist()):
        raise VsxFormatError(f"{sentinel} is missing from the {tile.name} answer")


# ------------------------------------------------------------------------ pulling


def pull_vsx(
    tile: Tile,
    store: SnapshotStore,
    *,
    servers: Sequence[str] = SERVERS,
    fetch: Fetch = http_fetch,
) -> VsxResult:
    """The `vsx` table for a tile. Reuses a stored answer; otherwise tries each server.

    Only an answer that parses and passes `check_answer` is stored. When every server
    fails the error names each one and nothing is written.
    """
    request = Request(SOURCE, ENDPOINT, query_params(tile), method="GET")
    row = store.lookup(request)
    if row is not None:
        table = parse_vsx(store.read(row), tile)
        check_answer(tile, table)
        return VsxResult(table, None)
    errors: dict[str, str] = {}
    for server in servers:
        try:
            status, body = fetch(server, request.body or {})
            if status != 200:
                raise VsxFormatError(f"HTTP {status}")
            table = parse_vsx(body, tile)
            check_answer(tile, table)
        except (requests.RequestException, VsxFormatError) as exc:
            errors[server] = f"{type(exc).__name__}: {str(exc)[:200]}"
            continue
        store.put(request, body)
        return VsxResult(table, server, errors)
    raise VsxError(f"no VSX server gave a usable answer for {tile.name}: {errors}")


# ----------------------------------------------------------------------- matching


def crossmatch(stars: pa.Table, vsx: pa.Table, *, radius_arcsec: float | None = None) -> pa.Table:
    """The nearest VSX entry for each star, as the `vsx_match` table.

    The search radius defaults to the exclusion radius, the larger of the two in
    `thresholds.py`. Callers compare `sep_arcsec` with the match radius themselves.
    """
    if radius_arcsec is None:
        radius_arcsec = THRESHOLDS["vsx_exclude_radius_arcsec"].value
    n = stars.num_rows
    ra = np.asarray(stars.column("ra_deg").to_numpy(zero_copy_only=False), dtype=float)
    dec = np.asarray(stars.column("dec_deg").to_numpy(zero_copy_only=False), dtype=float)
    v_ra = np.asarray(vsx.column("ra_deg").to_numpy(zero_copy_only=False), dtype=float)
    v_dec = np.asarray(vsx.column("dec_deg").to_numpy(zero_copy_only=False), dtype=float)
    best = np.full(n, -1, dtype=np.int64)
    sep = np.full(n, np.inf)
    if len(v_ra):
        for i in range(n):
            dra = (v_ra - ra[i] + 180.0) % 360.0 - 180.0
            d = np.hypot(dra * math.cos(math.radians(dec[i])), v_dec - dec[i]) * 3600.0
            j = int(np.argmin(d))
            if d[j] <= radius_arcsec:
                best[i], sep[i] = j, d[j]
    hit = best >= 0

    def pick(name: str) -> list:
        col = vsx.column(name).to_pylist()
        return [col[j] if j >= 0 else None for j in best]

    spec = schemas.SCHEMAS["vsx_match"].schema
    return pa.table(
        {
            "ref_number": stars.column("ref_number"),
            "tile": stars.column("tile"),
            "vsx_oid": pick("oid"),
            "vsx_name": pick("name"),
            "vsx_type": pick("vtype"),
            "vsx_period_d": pick("period_d"),
            "vsx_amplitude_mag": pick("amplitude_mag"),
            "sep_arcsec": [float(s) if h else None for s, h in zip(sep, hit, strict=True)],
        },
        schema=spec,
    )



# ---------------------------------------------------------------------- the command


def run_vsx_pull(
    tile_names: Sequence[str],
    data_dir: str | os.PathLike[str],
    *,
    servers: Sequence[str] = SERVERS,
    fetch: Fetch = http_fetch,
    say: Callable[[str], None] = print,
) -> int:
    """Pull VSX for each tile, write `vsx` and, when `stars` exists, `vsx_match`.

    Returns 0 when every tile worked and 1 otherwise. A tile that fails writes nothing,
    and the others still run.
    """
    data_dir = Path(data_dir)
    store = SnapshotStore(data_dir, client_version=f"computers/{__version__}")
    exit_code = 0
    for name in tile_names:
        tile = TILES[name]
        try:
            res = pull_vsx(tile, store, servers=servers, fetch=fetch)
        except VsxError as exc:
            say(f"== {name}  FAILED: {exc}")
            exit_code = 1
            continue
        digest = store.digest()
        silver.write_table("vsx", res.table, silver.silver_path(data_dir, "vsx", name), snapshot_digest=digest)
        origin = f"from {res.server}" if res.server else "from the snapshot store"
        say(f"== {name}  {res.table.num_rows} VSX entries {origin}")
        for server, why in res.errors.items():
            say(f"   {server} failed first: {why}")
        stars_path = silver.silver_path(data_dir, "stars", name)
        if stars_path.exists():
            stars = silver.read_table(stars_path)
            match = crossmatch(stars, res.table)
            silver.write_table(
                "vsx_match", match, silver.silver_path(data_dir, "vsx_match", name), snapshot_digest=digest
            )
            radius = THRESHOLDS["vsx_match_radius_arcsec"].value
            seps = [x for x in match.column("sep_arcsec").to_pylist() if x is not None]
            say(
                f"   {stars.num_rows} patrol stars: {sum(x <= radius for x in seps)} within "
                f"{radius:g} arcsec of a VSX entry, {len(seps)} within the exclusion radius"
            )
    return exit_code
