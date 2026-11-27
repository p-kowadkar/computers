"""Turn L1 results into the `candidates` and `telemetry` tables, and run a whole tile.

A surviving trigger becomes one `candidates` row. Every star gets one `telemetry` row, and
every trigger, killed or not, goes to a log, so nothing leaves L1 without a record.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa

from computers import __version__
from computers.data import silver
from computers.data.snapshot import SnapshotStore
from computers.l1.analyse import L1Result, analyse_star
from computers.l1.context import TileContext
from computers.l1.triggers import Trigger, TriggerParams

__all__ = [
    "SPLIT_LOCK",
    "TEST_TILES",
    "candidate_id",
    "candidate_table",
    "check_test_tile_allowed",
    "l1_flags",
    "run_l1",
    "telemetry_table",
    "trigger_log",
]

TEST_TILES = ("tabby",)
THRESHOLD_RECORD = "docs/decisions/0007-l1-trigger-thresholds.md"
# The lock file `computers bench freeze` writes. A test checks it equals `bench.leavitt.LOCK_PATH`;
# L1 may not import `bench`, so the path is repeated here.
SPLIT_LOCK = Path("docs/decisions/leavitt-test-split.lock.json")
CHOSEN_MARK = "Status: **chosen"  # written into the record when you have chosen the thresholds
LAYER = "l1"


def candidate_id(ref: int, trigger: Trigger) -> str:
    """The first 16 hex characters of a hash of the star, kind and epochs rounded to 0.01 year."""
    key = f"{ref}|{trigger.kind}|{trigger.epoch_start:.2f}|{trigger.epoch_end:.2f}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def l1_flags(t: Trigger) -> list[str]:
    flags = []
    if "known_variable" in t.kill_reasons:
        flags.append("known_variable")
    if not t.detrended:
        flags.append("not_detrended")
    if t.lower_limit:
        flags.append("lower_limit")
    if t.needs_cutouts:
        flags.append("needs_cutouts")
    return flags


def candidate_table(results: list[L1Result], tile: str, *, snapshot_digest: str, code_version: str = __version__) -> pa.Table:
    """One row per surviving trigger. A repeated id (two triggers equal to 0.01 year) gets a suffix."""
    rows: dict[str, dict[str, Any]] = {}
    for r in results:
        for t in r.survivors:
            cid = candidate_id(r.ref_number, t)
            n = 1
            while cid in rows:
                n += 1
                cid = hashlib.sha256(f"{cid}|{n}".encode()).hexdigest()[:16]
            rows[cid] = {
                "candidate_id": cid, "ref_number": r.ref_number, "tile": tile, "event_type": t.kind,
                "epoch_start": t.epoch_start, "epoch_end": t.epoch_end, "amplitude_mag": t.amplitude_mag,
                "single_plate": t.single_plate, "l1_flags": l1_flags(t), "l2_score": None,
                "snapshot_digest": snapshot_digest, "code_version": code_version,
            }  # fmt: skip
    return pa.Table.from_pylist(list(rows.values()), schema=_candidate_schema())


def _candidate_schema() -> pa.Schema:
    from computers.data import schemas

    return schemas.SCHEMAS["candidates"].schema


def telemetry_table(results: list[L1Result], run_id: str) -> pa.Table:
    from computers.data import schemas

    rows = [
        {"run_id": run_id, "ref_number": r.ref_number, "stop_layer": r.stop_layer, "reason": r.stop_reason,
         "wall_seconds": r.seconds, "tokens": None, "cost_usd": None}
        for r in results
    ]  # fmt: skip
    return pa.Table.from_pylist(rows, schema=schemas.SCHEMAS["telemetry"].schema)


def trigger_log(results: list[L1Result]) -> list[dict[str, Any]]:
    """Every trigger of every star, survivors and killed, with its kill reasons."""
    return [
        {"ref_number": r.ref_number, "kind": t.kind, "epoch_start": round(t.epoch_start, 4),
         "epoch_end": round(t.epoch_end, 4), "amplitude_mag": round(t.amplitude_mag, 4),
         "single_plate": t.single_plate, "detrended": t.detrended, "evidence_rows": len(t.evidence),
         "kill_reasons": list(t.kill_reasons), "survives": t.survives}
        for r in results
        for t in r.triggers
    ]  # fmt: skip


def check_test_tile_allowed(root: Path = Path(".")) -> str | None:
    """None when the test tile may be run, otherwise why not (thresholds not chosen, or split not frozen)."""
    record = root / THRESHOLD_RECORD
    if not record.is_file() or CHOSEN_MARK not in record.read_text():
        return f"the thresholds are not chosen yet ({THRESHOLD_RECORD} must say '{CHOSEN_MARK}')"
    if not (root / SPLIT_LOCK).is_file():
        return f"the test split is not frozen yet ({SPLIT_LOCK} is missing)"
    return None


def run_l1(
    data_dir: str | os.PathLike[str],
    tile: str,
    *,
    meteor_policy: str = "reject",
    params: TriggerParams | None = None,
    root: Path = Path("."),
    say: Callable[[str], None] = print,
) -> int:
    """Run L1 over every patrol star of a tile and write candidates, telemetry and the trigger log."""
    if tile in TEST_TILES:
        why = check_test_tile_allowed(root)
        if why:
            say(f"l1: {tile} is the held-out test tile and is not run: {why}")
            return 1
    data_dir = Path(data_dir)
    ctx = TileContext.from_silver(data_dir, tile, meteor_policy=meteor_policy)
    vsx = silver.read_table(silver.silver_path(data_dir, "vsx_match", tile))
    sep = dict(zip(vsx.column("ref_number").to_pylist(), vsx.column("sep_arcsec").to_pylist(), strict=True))
    results = [analyse_star(ctx, ref, params, vsx_sep_arcsec=sep.get(ref)) for ref in ctx.refs]
    digest = SnapshotStore(data_dir).digest()
    run_id = f"l1-{tile}-{meteor_policy}-{digest[7:15]}-{__version__}"
    cands = candidate_table(results, tile, snapshot_digest=digest)
    silver.write_table("candidates", cands, silver.silver_path(data_dir, "candidates", tile), snapshot_digest=digest)
    silver.write_table("telemetry", telemetry_table(results, run_id), silver.silver_path(data_dir, "telemetry", tile), snapshot_digest=digest)
    log = data_dir / "reports" / f"l1-triggers-{tile}.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(trigger_log(results), indent=1) + "\n")
    stops: dict[str, int] = {}
    for r in results:
        stops[r.stop_reason] = stops.get(r.stop_reason, 0) + 1
    say(f"== {tile} ({meteor_policy}): {len(results)} stars, {cands.num_rows} candidates, "
        f"{sum(len(r.triggers) for r in results)} triggers in all")
    say("   stopped: " + ", ".join(f"{k} {v}" for k, v in sorted(stops.items())))
    return 0
