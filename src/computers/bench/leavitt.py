"""Build, freeze and check the Leavitt Set (plan 10.1, 10.6).

Outputs go to `data/gold/leavitt/<version>/`:

* `constants.parquet`  one row per patrol star with its statistics and its reason;
* `variables.parquet`  patrol stars that match a known-variable VSX entry;
* `injections.parquet` every planned injection, with its host, family, truth and split;
* `test_split.json`    the held-out stars and the plates dropped from them, with a hash;
* `manifest.json`      version, seed, counts, thresholds and the hash of every input.

The split follows decision 0004: dev is M44 and T CrB, test is Tabby, and test
measurements on a plate that also appears in a dev tile are dropped. The test split is a
function of the catalogs and exposures alone, so tuning a threshold cannot change it.
`freeze` copies its hash into a committed lock file, and `check` fails when the data no
longer reproduce it.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from computers.bench import constants as C
from computers.bench import variables as V
from computers.bench.harness import HostCurve
from computers.bench.inject import FAMILIES, HostSpan, plan_injections
from computers.data import plates, silver
from computers.thresholds import THRESHOLDS

__all__ = [
    "DEV_TILES",
    "LOCK_PATH",
    "TEST_TILES",
    "VERSION",
    "BuildError",
    "build_set",
    "check_set",
    "freeze_test_split",
    "held_out_split",
    "host_curves",
    "set_dir",
]

VERSION = "v0"
SEED = 20261126
DEV_TILES = ("m44", "tcrb")
TEST_TILES = ("tabby",)
LOCK_PATH = Path("docs/decisions/leavitt-test-split.lock.json")
_SILVER_TABLES = ("stars", "measurements", "exposures", "vsx", "vsx_match")


class BuildError(RuntimeError):
    pass


def set_dir(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / "gold" / "leavitt" / VERSION


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(data_dir: Path, table: str, tile: str) -> pa.Table:
    path = silver.silver_path(data_dir, table, tile)
    if not path.exists():
        raise BuildError(f"{path} is missing; run the pull for {tile} first ({table})")
    return silver.read_table(path)


# ------------------------------------------------------------------ test split


def held_out_split(data_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """The held-out stars, the plates dropped from them, and a hash of both."""
    data_dir = Path(data_dir)
    dev_plates: set[str] = set()
    for t in DEV_TILES:
        dev_plates |= plates.plate_set(_load(data_dir, "exposures", t))
    refs: list[int] = []
    excluded: set[str] = set()
    for t in TEST_TILES:
        refs += [int(r) for r in _load(data_dir, "stars", t).column("ref_number").to_pylist()]
        excluded |= plates.plate_set(_load(data_dir, "exposures", t)) & dev_plates
    body = {"tiles": list(TEST_TILES), "ref_numbers": sorted(refs), "excluded_plates": sorted(excluded)}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**body, "sha256": digest}


# ---------------------------------------------------------------------- hosts


def host_curves(
    measurements: pa.Table, refs: set[int] | None = None, exclude_plates: set[str] | None = None
) -> dict[int, HostCurve]:
    """Host curves from a `measurements` table.

    A host curve holds the good detections and the non-detections, in time order. Rejected
    detections are dropped: they are bad data, not evidence that the star was faint. Rows on
    `exclude_plates` (the test split's shared plates) are dropped as well.
    """
    ref = measurements.column("ref_number").to_numpy()
    keep = np.ones(len(ref), dtype=bool)
    if refs is not None:
        keep &= np.isin(ref, np.fromiter(refs, dtype=np.uint64))
    mag_all = measurements.column("magcal_magdep").to_numpy(zero_copy_only=False).astype(float)
    reject = measurements.column("reject").to_numpy()
    keep &= ~np.isfinite(mag_all) | (reject == 0)
    if exclude_plates:
        series = measurements.column("series").to_pylist()
        nums = measurements.column("platenum").to_pylist()
        keep &= np.array([f"{s}{n:05d}" not in exclude_plates for s, n in zip(series, nums, strict=True)])
    idx = np.flatnonzero(keep)
    ref, mag = ref[idx], mag_all[idx]
    time = measurements.column("time").to_numpy()[idx]
    limit = measurements.column("limiting_mag_local").to_numpy(zero_copy_only=False).astype(float)[idx]
    order = np.lexsort((time, ref))
    ref, time, mag, limit = ref[order], time[order], mag[order], limit[order]
    cuts = np.flatnonzero(np.diff(ref)) + 1
    out: dict[int, HostCurve] = {}
    for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, len(ref)], strict=True):
        if hi > lo:
            out[int(ref[lo])] = HostCurve(int(ref[lo]), time[lo:hi], mag[lo:hi], limit[lo:hi])
    return out


# ---------------------------------------------------------------------- build


def _reference_rows(stars_by_tile: dict[str, pa.Table], match_radius: float) -> list[dict]:
    rows = []
    for tgt in V.REFERENCE_TARGETS:
        best, best_sep, best_tile = None, np.inf, None
        for tile, stars in stars_by_tile.items():
            ra = np.asarray(stars.column("ra_deg").to_numpy(zero_copy_only=False), float)
            dec = np.asarray(stars.column("dec_deg").to_numpy(zero_copy_only=False), float)
            dra = (ra - tgt.ra + 180.0) % 360.0 - 180.0
            sep = np.hypot(dra * np.cos(np.radians(tgt.dec)), dec - tgt.dec) * 3600.0
            if len(sep) and sep.min() < best_sep:
                i = int(sep.argmin())
                best, best_sep, best_tile = int(stars.column("ref_number")[i].as_py()), float(sep[i]), tile
        found = best is not None and best_sep <= match_radius
        rows.append({
            "name": tgt.name, "ra": round(tgt.ra, 5), "dec": round(tgt.dec, 5), "why": tgt.why,
            "patrol_ref_number": best if found else None, "tile": best_tile if found else None,
            "separation_arcsec": round(best_sep, 2) if found else None,
        })
    return rows


def build_set(
    data_dir: str | os.PathLike[str],
    *,
    seed: int = SEED,
    n_amplitudes: int = 6,
    replicates: int = 2,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Build every file of the set and return the manifest. Raises `BuildError` on a missing input
    or when the test split no longer matches a frozen lock."""
    data_dir = Path(data_dir)
    out_dir = set_dir(data_dir)
    match_r = THRESHOLDS["vsx_match_radius_arcsec"].value
    tiles = DEV_TILES + TEST_TILES
    split = held_out_split(data_dir)
    lock = _read_lock()
    if lock is not None and lock["sha256"] != split["sha256"]:
        raise BuildError(
            "the test split changed since it was frozen "
            f"({lock['sha256'][:12]} then, {split['sha256'][:12]} now). Record a decision before changing it."
        )
    constants_parts, variable_rows, specs_parts, tile_info = [], [], [], {}
    stars_by_tile: dict[str, pa.Table] = {}
    inputs: dict[str, str] = {}
    digests: set[str] = set()
    for tile in tiles:
        stars = _load(data_dir, "stars", tile)
        vsx_match = _load(data_dir, "vsx_match", tile)
        meas = _load(data_dir, "measurements", tile)
        for table in _SILVER_TABLES:
            p = silver.silver_path(data_dir, table, tile)
            inputs[f"{table}/{tile}"] = _sha256_file(p)
            digests.add(silver.read_metadata(p).get("computers.snapshot_digest", ""))
        stars_by_tile[tile] = stars
        split_name = "test" if tile in TEST_TILES else "dev"
        excluded = set(split["excluded_plates"]) if tile in TEST_TILES else None
        stats = C.tile_stats(_without_plates(meas, excluded))
        cons = C.select_constants(stars, stats, vsx_match)
        cons = cons.append_column("split", pa.array([split_name] * cons.num_rows))
        constants_parts.append(cons)
        # known variables: a close VSX match of a kept type
        m = vsx_match.to_pylist()
        for row in m:
            if row["sep_arcsec"] is not None and row["sep_arcsec"] <= match_r:
                klass = V.classify(row["vsx_type"], row["vsx_amplitude_mag"])
                if klass:
                    variable_rows.append({**row, "klass": klass, "split": split_name})
        # injections for this tile's constants
        crow = cons.to_pylist()
        hosts = [HostSpan(r["ref_number"], r["first_year"], r["last_year"]) for r in crow if r["is_constant"]]
        specs = plan_injections(hosts, seed=seed, n_amplitudes=n_amplitudes, replicates=replicates,
                                forbidden={v["ref_number"] for v in variable_rows})
        specs_parts.append(_specs_table(specs, tile, split_name))
        reasons = Counter(r["reason"] for r in crow)
        tile_info[tile] = {"split": split_name, "stars": len(crow), "constants": reasons["constant"],
                           "reasons": dict(sorted(reasons.items())),
                           "known_variables": sum(1 for v in variable_rows if v["tile"] == tile),
                           "injections": len(specs),
                           "expected_missed": sum(s.expected_missed for s in specs)}
        say(f"   {tile}: {len(crow)} stars, {reasons['constant']} constants, "
            f"{tile_info[tile]['known_variables']} known variables, {len(specs)} injections")
    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.concat_tables(constants_parts), out_dir / "constants.parquet", compression="zstd")
    pq.write_table(_variables_table(variable_rows), out_dir / "variables.parquet", compression="zstd")
    pq.write_table(pa.concat_tables(specs_parts), out_dir / "injections.parquet", compression="zstd")
    (out_dir / "test_split.json").write_text(json.dumps(split, indent=1) + "\n")
    manifest = {
        "version": VERSION, "seed": seed, "families": list(FAMILIES),
        "n_amplitudes": n_amplitudes, "replicates": replicates,
        "dev_tiles": list(DEV_TILES), "test_tiles": list(TEST_TILES),
        "test_split_sha256": split["sha256"], "test_stars": len(split["ref_numbers"]),
        "test_excluded_plates": len(split["excluded_plates"]),
        "snapshot_digests": sorted(d for d in digests if d),
        "thresholds": {n: THRESHOLDS[n].value for n in sorted(THRESHOLDS)},
        "tiles": tile_info,
        "reference_targets": _reference_rows(stars_by_tile, match_r),
        "inputs_sha256": dict(sorted(inputs.items())),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def _without_plates(meas: pa.Table, excluded: set[str] | None) -> pa.Table:
    if not excluded:
        return meas
    series, nums = meas.column("series").to_pylist(), meas.column("platenum").to_pylist()
    keep = np.array([f"{s}{n:05d}" not in excluded for s, n in zip(series, nums, strict=True)])
    return meas.filter(pa.array(keep))


def _specs_table(specs, tile: str, split: str) -> pa.Table:
    return pa.table({
        "host_id": pa.array([s.host_id for s in specs], pa.uint64()),
        "tile": [tile] * len(specs), "split": [split] * len(specs),
        "family": [s.family for s in specs],
        "amplitude_index": pa.array([s.amplitude_index for s in specs], pa.int32()),
        "replicate": pa.array([s.replicate for s in specs], pa.int32()),
        "amplitude": pa.array([s.signal.amplitude for s in specs], pa.float64()),
        "t0_year": pa.array([s.signal.t0 for s in specs], pa.float64()),
        "scale": pa.array([s.signal.scale for s in specs], pa.float64()),
        "sign": pa.array([s.signal.sign for s in specs], pa.int8()),
        "expected_missed": pa.array([s.expected_missed for s in specs], pa.bool_()),
        "reason": [s.reason for s in specs],
    })


def _variables_table(rows: list[dict]) -> pa.Table:
    cols = ("ref_number", "tile", "vsx_oid", "vsx_name", "vsx_type", "vsx_period_d",
            "vsx_amplitude_mag", "sep_arcsec", "klass", "split")
    return pa.table({c: [r[c] for r in rows] for c in cols}) if rows else pa.table({c: [] for c in cols})


# --------------------------------------------------------------- freeze and check


def _read_lock() -> dict | None:
    return json.loads(LOCK_PATH.read_text()) if LOCK_PATH.exists() else None


def freeze_test_split(data_dir: str | os.PathLike[str], *, decision: str, root: Path = Path(".")) -> Path:
    """Write the lock file for the current test split. This is the human sign-off step."""
    split = held_out_split(data_dir)
    if not (root / decision).is_file():
        raise BuildError(f"decision file {decision} does not exist")
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": VERSION, "decision": decision, "sha256": split["sha256"],
        "stars": len(split["ref_numbers"]), "excluded_plates": len(split["excluded_plates"]),
        "tiles": split["tiles"],
    }, indent=2, sort_keys=True) + "\n")
    return path


def check_set(data_dir: str | os.PathLike[str]) -> list[str]:
    """Problems with the built set, as readable lines. Empty means it is consistent."""
    data_dir = Path(data_dir)
    problems: list[str] = []
    out = set_dir(data_dir)
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        return [f"{manifest_path} is missing; run `computers bench build`"]
    manifest = json.loads(manifest_path.read_text())
    try:
        now = held_out_split(data_dir)
    except BuildError as exc:
        return [str(exc)]
    if now["sha256"] != manifest["test_split_sha256"]:
        problems.append("the test split differs from the one the set was built with; rebuild and review")
    lock = _read_lock()
    if lock is None:
        problems.append(f"the test split is not frozen yet ({LOCK_PATH} is missing)")
    elif lock["sha256"] != now["sha256"]:
        problems.append(
            f"the test split changed since it was frozen ({lock['sha256'][:12]} then, "
            f"{now['sha256'][:12]} now); record a decision first"
        )
    for name, want in manifest["inputs_sha256"].items():
        table, tile = name.split("/")
        p = silver.silver_path(data_dir, table, tile)
        if not p.exists():
            problems.append(f"{p} is missing")
        elif _sha256_file(p) != want:
            problems.append(f"{p} changed since the set was built")
    return problems
