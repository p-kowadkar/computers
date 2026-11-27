"""Pull a tile's data from DASCH into the snapshot store and typed silver tables.

Three stages, each safe to repeat (the store answers anything already fetched):

    catalog      four `querycat` boxes -> merge -> clip to the tile -> `stars` (B <= 13.5)
    exposures    five `queryexps` positions -> merge -> `exposures`
    lightcurves  one `lightcurve` request per star -> `measurements`

A run writes a report to `data/reports/`. Failures never stop the other requests:
they are listed, and the run exits non-zero. A silver table is only written when its
stage is complete, so a half-finished table never looks finished. Because every raw
response is stored before it is parsed, an interrupted run loses nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from computers import __version__
from computers.data import dasch_parse as P
from computers.data import schemas, silver
from computers.data.dasch_client import DaschClient, DaschError, HttpError, RetriesExhausted
from computers.data.snapshot import SnapshotStore
from computers.data.tiles import TILES, Tile, catalog_boxes, exposure_positions, in_tile
from computers.thresholds import THRESHOLDS

__all__ = [
    "BRIGHT_LIMIT_B",
    "STAGES",
    "CatalogResult",
    "ExposuresResult",
    "LightcurveResult",
    "StageError",
    "magnitude_bins",
    "pilot_subset",
    "pull_catalog",
    "pull_exposures",
    "pull_lightcurves",
    "run_pull",
]

BRIGHT_LIMIT_B = 13.5  # [start] Johnson B, plan 3.4
STAGES = ("catalog", "exposures", "lightcurves", "neighbours")
PILOT_SEED = 20261124
MAX_SPLIT_DEPTH = 3
EPOCH_START = dt.datetime(1880, 1, 1, tzinfo=dt.UTC)
EPOCH_END = dt.datetime(1991, 1, 1, tzinfo=dt.UTC)
NEIGHBOUR_MAX_B = 30.0  # any finite magnitude
MAX_WORKERS = 4  # threads; the client's gate decides how many requests actually run at once


class StageError(Exception):
    """A stage could not be completed. Nothing partial was written."""


# ------------------------------------------------------------------------ catalog


@dataclass
class CatalogResult:
    tile: str
    stars: pa.Table
    n_returned: int  # rows over all boxes, before merging
    n_unique: int  # distinct sources after merging
    n_multi_box: int  # sources seen in more than one box
    n_in_tile: int
    n_outside_tile: int
    n_with_magnitude: int
    n_bright: int
    bright_bins: dict[str, int]  # upper edge of each 0.5 mag bin (low, high] -> count, B <= limit
    catalog: pa.Table | None = None  # every source in the tile, for the neighbours stage


def magnitude_bins(mags: np.ndarray, limit: float = BRIGHT_LIMIT_B, step: float = 0.5) -> dict[str, int]:
    """Counts per `step`-wide bin `(high - step, high]`, for finite magnitudes up to `limit`.

    Upper edges are inclusive so that a star exactly at the limit is in the last bin,
    matching the "B <= limit" cut.
    """
    mags = np.asarray(mags, dtype=float)
    mags = mags[np.isfinite(mags) & (mags <= limit)]
    if mags.size == 0:
        return {}
    highs = np.ceil(mags / step) * step
    counts = Counter(round(float(h), 1) for h in highs)
    return {f"{h:.1f}": counts[h] for h in sorted(counts)}


def pull_catalog(client: DaschClient, tile: Tile, *, max_b: float = BRIGHT_LIMIT_B) -> CatalogResult:
    boxes = catalog_boxes(tile)

    def fetch(box):
        return P.parse_catalog(client.querycat(box.ra, box.dec, box.radius_arcsec))

    tables: list[pa.Table | None] = [None] * len(boxes)
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(boxes))) as pool:
        futures = {pool.submit(fetch, b): i for i, b in enumerate(boxes)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                tables[i] = fut.result()
            except (DaschError, P.ParseError) as exc:
                b = boxes[i]
                errors.append(f"box {i + 1} of {len(boxes)} (ra {b.ra:.4f}, dec {b.dec:.4f}): {exc}")
    if errors:
        raise StageError(f"catalog for {tile.name} is incomplete, nothing written: " + "; ".join(sorted(errors)))

    parsed = [t for t in tables if t is not None]
    merged, n_multi = P.merge_catalogs(parsed)
    inside = np.asarray(
        in_tile(tile, merged.column("ra_deg").to_numpy(), merged.column("dec_deg").to_numpy())
    )
    catalog = merged.filter(pa.array(inside))
    mags = catalog.column("stdmag").to_numpy(zero_copy_only=False)
    stars = P.stars_from_catalog(catalog, tile, max_b=max_b)
    return CatalogResult(
        tile=tile.name,
        stars=stars,
        n_returned=sum(t.num_rows for t in parsed),
        n_unique=merged.num_rows,
        n_multi_box=n_multi,
        n_in_tile=catalog.num_rows,
        n_outside_tile=merged.num_rows - catalog.num_rows,
        n_with_magnitude=int(np.isfinite(mags).sum()),
        n_bright=stars.num_rows,
        bright_bins=magnitude_bins(mags, max_b),
        catalog=catalog,
    )


# -------------------------------------------------------------------- neighbours


def neighbour_sources(
    catalog: pa.Table, patrol: pa.Table, tile: Tile, radius_arcsec: float
) -> pa.Table:
    """Catalog sources within `radius_arcsec` of a patrol star, as a `stars`-shaped table.

    These are the sources that can split a star's detections between two catalog entries
    (plan 6.1 step 1). A neighbour that is itself a patrol star is returned too, because
    the merge needs it, and the caller decides what is still to be pulled. Sources with no
    magnitude cannot be kept in a `stars` table and are left out.
    """
    ra = catalog.column("ra_deg").to_numpy().astype(float)
    dec = catalog.column("dec_deg").to_numpy().astype(float)
    refs = catalog.column("ref_number").to_numpy()
    p_ra = patrol.column("ra_deg").to_numpy().astype(float)
    p_dec = patrol.column("dec_deg").to_numpy().astype(float)
    p_ref = patrol.column("ref_number").to_numpy()
    near = np.zeros(len(ra), dtype=bool)
    for i in range(len(p_ra)):
        dra = (ra - p_ra[i] + 180.0) % 360.0 - 180.0
        sep = np.hypot(dra * math.cos(math.radians(p_dec[i])), dec - p_dec[i]) * 3600.0
        near |= (sep <= radius_arcsec) & (refs != p_ref[i])
    return P.stars_from_catalog(catalog.filter(pa.array(near)), tile, max_b=NEIGHBOUR_MAX_B)


# ---------------------------------------------------------------------- exposures


# `queryexps` reports these relative to the position that was queried, so the same
# exposure comes back with different values from different positions (measured near
# T CrB: 17,254 exposures were seen from several positions, and every difference was in
# these columns). In a tile-level table they would depend on which position answered
# first, so they are left null. The per-detection `plate_dist` and `limiting_mag_local`
# in `measurements` are the right values for a star. The raw responses keep them.
QUERY_RELATIVE_COLUMNS = ("center_distance", "edge_distance", "lim_mag_apass")


@dataclass
class ExposuresResult:
    tile: str
    table: pa.Table
    n_calls: int
    n_returned: int
    n_conflicts: int  # same exposure_id in two responses, differing in a column that should not vary
    n_position_dependent: int = 0  # copies that differed only in the query-relative columns
    splits: list[str] = field(default_factory=list)


def _iso(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_exposures(
    client: DaschClient,
    ra: float,
    dec: float,
    lo: dt.datetime | None,
    hi: dt.datetime | None,
    depth: int,
    splits: list[str],
    calls: list[int],
) -> list[pa.Table]:
    """One `queryexps`, splitting the date range in two when the call fails.

    A response over the API's 6 MB limit cannot be told apart from other server
    failures, so any retry-exhausted or server-side error triggers a split, up to
    `MAX_SPLIT_DEPTH` times.
    """
    calls[0] += 1
    try:
        lines = client.queryexps(ra, dec, _iso(lo) if lo else None, _iso(hi) if hi else None)
        return [P.parse_exposures(lines)]
    except (RetriesExhausted, HttpError) as exc:
        if isinstance(exc, HttpError) and exc.status not in (413, 500, 502, 503, 504):
            raise
        if depth >= MAX_SPLIT_DEPTH:
            raise StageError(
                f"queryexps at ({ra:.4f}, {dec:.4f}) failed even after splitting the date range "
                f"{MAX_SPLIT_DEPTH} times: {exc}"
            ) from exc
        start, end = lo or EPOCH_START, hi or EPOCH_END
        mid = start + (end - start) / 2
        splits.append(f"({ra:.4f}, {dec:.4f}) {_iso(start)} to {_iso(end)}: {exc}")
        return _fetch_exposures(client, ra, dec, start, mid, depth + 1, splits, calls) + _fetch_exposures(
            client, ra, dec, mid, end, depth + 1, splits, calls
        )


def pull_exposures(client: DaschClient, tile: Tile) -> ExposuresResult:
    splits: list[str] = []
    calls = [0]
    tables: list[pa.Table] = []
    errors: list[str] = []
    positions = exposure_positions(tile)

    def fetch(pos):
        return _fetch_exposures(client, pos[0], pos[1], None, None, 0, splits, calls)

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(positions))) as pool:
        futures = {pool.submit(fetch, p): p for p in positions}
        for fut in as_completed(futures):
            ra, dec = futures[fut]
            try:
                tables.extend(fut.result())
            except (DaschError, P.ParseError, StageError) as exc:
                errors.append(f"({ra:.4f}, {dec:.4f}): {exc}")
    if errors:
        raise StageError(f"exposures for {tile.name} are incomplete, nothing written: " + "; ".join(sorted(errors)))

    combined = pa.concat_tables(tables)
    rows = combined.to_pylist()
    seen: dict[str, dict[str, Any]] = {}
    conflicts = position_dependent = 0
    for r in rows:
        prior = seen.get(r["exposure_id"])
        if prior is None:
            seen[r["exposure_id"]] = r
            continue
        stable = [c for c in r if c not in QUERY_RELATIVE_COLUMNS]
        if any(prior[c] != r[c] for c in stable):
            conflicts += 1
        elif any(prior[c] != r[c] for c in QUERY_RELATIVE_COLUMNS):
            position_dependent += 1
    schema = schemas.SCHEMAS["exposures"].schema
    for r in seen.values():
        for c in QUERY_RELATIVE_COLUMNS:
            r[c] = None
    table = pa.Table.from_pylist(list(seen.values()), schema=schema)
    table = table.sort_by([("obs_date", "ascending"), ("exposure_id", "ascending")])
    return ExposuresResult(
        tile.name, table, calls[0], len(rows), conflicts, position_dependent, sorted(splits)
    )


# ------------------------------------------------------------------- lightcurves


@dataclass
class LightcurveResult:
    tile: str
    table: pa.Table
    n_stars: int
    failures: list[tuple[int, str]]
    n_rows: int


def pilot_subset(stars: pa.Table, max_stars: int, seed: int = PILOT_SEED) -> pa.Table:
    """At most about `max_stars` stars, the same share from every 0.5 mag bin.

    Each bin gives `ceil(n_bin * max_stars / n_total)` stars, so no bin is empty and the
    total can exceed `max_stars` by up to one star per bin. The seed is fixed, so the
    same input always gives the same subset. Fewer stars than `max_stars` returns all.
    """
    if max_stars < 1:
        raise ValueError("max_stars must be at least 1")
    stars = stars.sort_by("ref_number")
    n = stars.num_rows
    if n <= max_stars:
        return stars
    mags = stars.column("apass_b").to_numpy(zero_copy_only=False)
    bins = np.ceil(mags / 0.5).astype(int)
    rng = np.random.default_rng(seed)
    chosen: list[int] = []
    for b in np.unique(bins):
        idx = np.flatnonzero(bins == b)
        take = min(len(idx), math.ceil(len(idx) * max_stars / n))
        chosen.extend(sorted(rng.choice(idx, size=take, replace=False).tolist()))
    return stars.take(pa.array(sorted(chosen)))


def pull_lightcurves(
    client: DaschClient,
    stars: pa.Table,
    tile: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> LightcurveResult:
    rows = list(
        zip(
            stars.column("ref_number").to_pylist(),
            stars.column("gsc_bin_index").to_pylist(),
            stars.column("refcat").to_pylist(),
            strict=True,
        )
    )
    results: dict[int, pa.Table] = {}
    failures: list[tuple[int, str]] = []

    def fetch(item):
        ref, gsc, refcat = item
        lines = client.lightcurve(refcat, ref, gsc)
        return ref, P.apply_standard_rejections(P.parse_lightcurve(lines, ref))

    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    try:
        futures = {pool.submit(fetch, item): item[0] for item in rows}
        for done, fut in enumerate(as_completed(futures), start=1):
            ref = futures[fut]
            try:
                _, table = fut.result()
                results[ref] = table
            except (DaschError, P.ParseError) as exc:
                failures.append((ref, str(exc)))
            if progress and (done % 25 == 0 or done == len(rows)):
                s = client.stats
                progress(
                    f"lightcurves {done}/{len(rows)}  made {s.made}  from store {s.from_store}  "
                    f"failed {len(failures)}  concurrency {client.gate.limit}"
                )
    except BaseException:
        # Stop queued work, but let requests already in flight finish: each one is stored
        # whole (file, then manifest row), so the store is tidy when control returns.
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)

    ordered = [results[r] for r in sorted(results)]
    schema = schemas.SCHEMAS["measurements"].schema
    table = pa.concat_tables(ordered) if ordered else schema.empty_table()
    return LightcurveResult(tile, table, len(rows), sorted(failures), table.num_rows)


# ------------------------------------------------------------------------- report


def _timing(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    a = np.asarray(values)
    return {
        "n": len(values),
        "median_s": round(float(np.median(a)), 3),
        "p95_s": round(float(np.percentile(a, 95)), 3),
        "max_s": round(float(a.max()), 3),
        "total_s": round(float(a.sum()), 1),
    }


def client_summary(client: DaschClient) -> dict[str, Any]:
    s = client.stats
    return {
        "api_mode": client.api_mode,
        "requests_made": s.made,
        "requests_from_store": s.from_store,
        "requests_failed": s.failed,
        "retries_by_status": {str(k): v for k, v in sorted(s.retries_by_status.items(), key=str)},
        "rate_limited": s.rate_limited,
        "concurrency_start": client.gate.initial,
        "concurrency_end": client.gate.limit,
        "concurrency_lowered": s.concurrency_lowered,
        "bytes_stored": s.bytes_stored,
        "seconds_by_endpoint": {k: _timing(v) for k, v in sorted(s.seconds.items())},
    }


def write_report(data_dir: str | os.PathLike[str], tile: str, report: dict[str, Any]) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = Path(data_dir) / "reports" / f"pull-{tile}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------------- runner


def run_pull(
    tile_names: list[str],
    stages: list[str],
    data_dir: str | os.PathLike[str],
    *,
    pilot: bool = False,
    max_stars: int = 2000,
    concurrency: int = 3,
    client: DaschClient | None = None,
    say: Callable[[str], None] = print,
) -> int:
    """Run the stages for each tile. Returns 0 on success, 1 when anything failed."""
    data_dir = Path(data_dir)
    store = SnapshotStore(data_dir, client_version=f"computers/{__version__}")
    client = client or DaschClient(store, concurrency=concurrency)
    store = client.store
    exit_code = 0

    for name in tile_names:
        tile = TILES[name]
        started = time.time()
        report: dict[str, Any] = {
            "tile": name,
            "stages": list(stages),
            "pilot": pilot,
            "max_stars": max_stars,
            "started_utc": dt.datetime.now(dt.UTC).isoformat(),
            "failures": [],
        }
        say(f"== {name}  (API: {client.api_mode}, concurrency {client.gate.limit})")
        try:
            if "catalog" in stages:
                cat = pull_catalog(client, tile)
                path = silver.write_table(
                    "stars", cat.stars, silver.silver_path(data_dir, "stars", name),
                    snapshot_digest=store.digest(),
                )  # fmt: skip
                report["catalog"] = {
                    "returned_rows": cat.n_returned,
                    "unique_sources": cat.n_unique,
                    "seen_in_more_than_one_box": cat.n_multi_box,
                    "in_tile": cat.n_in_tile,
                    "outside_tile_dropped": cat.n_outside_tile,
                    "with_magnitude": cat.n_with_magnitude,
                    "bright_b_le_13_5": cat.n_bright,
                    "bright_bins_by_upper_edge": cat.bright_bins,
                    "file": str(path),
                }
                say(
                    f"   catalog: {cat.n_in_tile} sources in the tile, {cat.n_with_magnitude} with a "
                    f"magnitude, {cat.n_bright} at B <= {BRIGHT_LIMIT_B}; {cat.n_multi_box} seen in "
                    f"more than one box"
                )
            if "exposures" in stages:
                exp = pull_exposures(client, tile)
                path = silver.write_table(
                    "exposures", exp.table, silver.silver_path(data_dir, "exposures", name),
                    snapshot_digest=store.digest(),
                )  # fmt: skip
                report["exposures"] = {
                    "calls": exp.n_calls,
                    "rows_returned": exp.n_returned,
                    "unique_exposures": exp.table.num_rows,
                    "conflicting_duplicates": exp.n_conflicts,
                    "copies_differing_only_in_query_relative_columns": exp.n_position_dependent,
                    "date_range_splits": exp.splits,
                    "file": str(path),
                }
                say(f"   exposures: {exp.table.num_rows} unique from {exp.n_returned} rows in {exp.n_calls} calls")
            if "lightcurves" in stages:
                stars_path = silver.silver_path(data_dir, "stars", name)
                if not stars_path.exists():
                    raise StageError(f"no stars table for {name}: run the catalog stage first")
                stars = silver.read_table(stars_path)
                subset = pilot_subset(stars, max_stars) if pilot else stars.sort_by("ref_number")
                say(f"   lightcurves: {subset.num_rows} of {stars.num_rows} stars")
                lcs = pull_lightcurves(client, subset, name, progress=lambda m: say("   " + m))
                report["lightcurves"] = {
                    "stars_requested": lcs.n_stars,
                    "stars_failed": len(lcs.failures),
                    "rows": lcs.n_rows,
                }
                report["failures"] = [{"ref_number": r, "error": e} for r, e in lcs.failures]
                if lcs.failures:
                    exit_code = 1
                    say(f"   {len(lcs.failures)} stars failed; measurements NOT written. Run again to retry them.")
                else:
                    path = silver.write_table(
                        "measurements", lcs.table, silver.silver_path(data_dir, "measurements", name),
                        snapshot_digest=store.digest(),
                    )  # fmt: skip
                    report["lightcurves"]["file"] = str(path)
                    say(f"   measurements: {lcs.n_rows} rows written")
            if "neighbours" in stages:
                stars_path = silver.silver_path(data_dir, "stars", name)
                if not stars_path.exists():
                    raise StageError(f"no stars table for {name}: run the catalog stage first")
                patrol = silver.read_table(stars_path)
                cat = pull_catalog(client, tile)  # a stored answer: no new request
                radius = THRESHOLDS["source_split_radius_arcsec"].value
                found = neighbour_sources(cat.catalog, patrol, tile, radius)
                label = f"{name}-neighbours"
                found = found.set_column(
                    found.schema.get_field_index("tile"),
                    found.schema.field("tile"),
                    pa.array([label] * found.num_rows, type=pa.string()),
                )
                say(f"   neighbours: {found.num_rows} sources within {radius:g} arcsec of {patrol.num_rows} patrol stars")
                nb = pull_lightcurves(client, found, label, progress=lambda m: say("   " + m))
                report["neighbours"] = {"sources": found.num_rows, "failed": len(nb.failures), "rows": nb.n_rows}
                if nb.failures:
                    exit_code = 1
                    say(f"   {len(nb.failures)} neighbours failed; nothing written. Run again to retry them.")
                else:
                    digest = store.digest()
                    silver.write_table("stars", found, silver.silver_path(data_dir, "stars", label), snapshot_digest=digest)
                    silver.write_table(
                        "measurements", nb.table, silver.silver_path(data_dir, "measurements", label),
                        snapshot_digest=digest,
                    )  # fmt: skip
                    say(f"   neighbours: {nb.n_rows} measurement rows written")
        except StageError as exc:
            exit_code = 1
            report["failures"].append({"stage_error": str(exc)})
            say(f"   FAILED: {exc}")

        report["client"] = client_summary(client)
        report["snapshot_digest"] = store.digest()
        report["elapsed_s"] = round(time.time() - started, 1)
        path = write_report(data_dir, name, report)
        say(f"   report: {path}")
    return exit_code
