"""`computers l2 run`: score the L1 candidates, choose the stars that go on, and report the topology (spec S5 R6, R7).

The ranker orders candidates. It is not used as a filter: on the dev benchmark it did not meet
the pass mark for that (decision 0008), so L1 strict decides what survives and the ranker only
decides which survivors fit under the budget. Every candidate gets a score, known variables
included, so a known outburst with a low score is visible.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa

from computers import __version__
from computers.data import schemas, silver
from computers.data.snapshot import SnapshotStore
from computers.l1.analyse import analyse_star
from computers.l1.candidates import TEST_TILES, check_test_tile_allowed
from computers.l1.context import TileContext
from computers.l2 import ranker as R
from computers.l2.budget import select

__all__ = ["MODEL_NAME", "format_topology", "run_l2"]

MODEL_NAME = "ranker-v0.joblib"
VARIANT = "plain"  # the ranker inputs used on real tiles: no anomaly score (it did not help), no series count
LAYER = "l2"


def _key(kind: str, a: float, b: float) -> tuple[str, float, float]:
    return kind, round(float(a), 2), round(float(b), 2)


def _score_tile(data_dir: Path, tile: str, rows: list[dict], model: R.RankerModel, meteor_policy: str) -> dict[str, float]:
    """Score every candidate row of a tile, by re-running L1 on the stars that have one (L1 is deterministic)."""
    ctx = TileContext.from_silver(data_dir, tile, meteor_policy=meteor_policy)
    trig: dict[tuple[int, tuple[str, float, float]], dict] = {}
    for ref in sorted({int(r["ref_number"]) for r in rows}):
        res = analyse_star(ctx, ref)
        for t in res.survivors:
            trig[(ref, _key(t.kind, t.epoch_start, t.epoch_end))] = R.trigger_row(t, res.inp)
    scores: dict[str, float] = {}
    missing = []
    for r in rows:
        row = trig.get((int(r["ref_number"]), _key(r["event_type"], r["epoch_start"], r["epoch_end"])))
        if row is None:
            missing.append(r["candidate_id"])
            continue
        scores[r["candidate_id"]] = float(R.predict(model, R.design([row], model.columns))[0])
    if missing:
        raise RuntimeError(f"{tile}: {len(missing)} candidates did not reproduce under L1 ({missing[:3]}); rerun `computers l1 run`")
    return scores


def run_l2(
    data_dir: str | os.PathLike[str],
    tiles: Sequence[str],
    *,
    meteor_policy: str = "reject",
    root: Path = Path("."),
    say: Callable[[str], None] = print,
) -> int:
    """Score and select across `tiles`, write scored candidates, L2 telemetry and the topology report."""
    data_dir = Path(data_dir)
    model_path = data_dir / "models" / MODEL_NAME
    if not model_path.is_file():
        say(f"l2: no ranker at {model_path}; run `computers bench l2` first")
        return 1
    model = R.load(model_path)
    t0 = time.perf_counter()
    digest = SnapshotStore(data_dir).digest()
    allrows: list[dict] = []
    tables: dict[str, pa.Table] = {}
    for tile in tiles:
        if tile in TEST_TILES:
            why = check_test_tile_allowed(root)
            if why:
                say(f"l2: {tile} is the held-out test tile and is not run: {why}")
                continue
        path = silver.silver_path(data_dir, "candidates", tile)
        if not path.is_file():
            say(f"l2: no candidates for {tile}; run `computers l1 run --tile {tile}` first")
            return 1
        table = silver.read_table(path)
        rows = table.to_pylist()
        scores = _score_tile(data_dir, tile, rows, model, meteor_policy)
        for r in rows:
            r["score"] = scores[r["candidate_id"]]
            r["flags"] = list(r["l1_flags"] or [])
        tables[tile] = table
        allrows += rows
    if not tables:
        return 1
    sel = select(allrows)
    chosen, left = set(sel.selected), {c: why for c, _, why in sel.left_out}
    run_id = f"l2-{'-'.join(tables)}-{digest[7:15]}-{__version__}"
    seconds = time.perf_counter() - t0
    report: dict[str, Any] = {"run_id": run_id, "budget": {"selected": len(chosen), "left_out": len(left)}, "tiles": {}}
    for tile, table in tables.items():
        mine = [r for r in allrows if r["tile"] == tile]
        by_id = {r["candidate_id"]: r for r in mine}
        keep = ("candidate_id", "ref_number", "tile", "event_type", "epoch_start", "epoch_end", "amplitude_mag",
                "single_plate", "l1_flags", "snapshot_digest", "code_version")  # fmt: skip
        out = pa.Table.from_pylist(
            [{**{k: r[k] for k in keep}, "l2_score": by_id[r["candidate_id"]]["score"]} for r in table.to_pylist()],
            schema=schemas.SCHEMAS["candidates"].schema,
        )
        silver.write_table("candidates", out, silver.silver_path(data_dir, "candidates", tile), snapshot_digest=digest)
        tel_rows, stops = [], Counter()
        for ref in sorted({int(r["ref_number"]) for r in mine}):
            star = [r for r in mine if int(r["ref_number"]) == ref]
            live = [r for r in star if "known_variable" not in r["flags"]]
            if not live:
                layer, reason = LAYER, "known_variable"
            elif any(r["candidate_id"] in chosen for r in live):
                layer, reason = "l3", "selected"
            else:
                layer, reason = LAYER, "below_budget"
            stops[(layer, reason)] += 1
            tel_rows.append({"run_id": run_id, "ref_number": ref, "stop_layer": layer, "reason": reason,
                             "wall_seconds": seconds / max(len(allrows), 1) * len(star), "tokens": None, "cost_usd": 0.0})  # fmt: skip
        silver.write_table("telemetry", pa.Table.from_pylist(tel_rows, schema=schemas.SCHEMAS["telemetry"].schema),
                           silver.silver_path(data_dir, "telemetry", f"{tile}-l2"), snapshot_digest=digest)  # fmt: skip
        l1 = silver.read_table(silver.silver_path(data_dir, "telemetry", tile)).to_pylist()
        report["tiles"][tile] = {
            "stars_in": len(l1),
            "l1_stops": dict(Counter(r["reason"] for r in l1 if r["stop_layer"] == "l1")),
            "l1_seconds": round(sum(r["wall_seconds"] for r in l1), 1),
            "reached_l2": len(tel_rows),
            "l2_stops": {f"{a}:{b}": n for (a, b), n in sorted(stops.items())},
            "selected": [_brief(r) for r in sorted(mine, key=lambda r: -r["score"]) if r["candidate_id"] in chosen],
            "left_out": [{**_brief(by_id[c]), "why": left[c]} for c in left if c in by_id],
            "known_variables": [_brief(r) for r in sorted(mine, key=lambda r: -r["score"]) if "known_variable" in r["flags"]],
            "cost_usd": 0.0,
            "l2_seconds": round(seconds, 1),
        }
    out_dir = data_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "topology.json").write_text(json.dumps(report, indent=1) + "\n")
    text = format_topology(report)
    (out_dir / "topology.txt").write_text(text + "\n")
    say(text)
    return 0


def _brief(r: dict) -> dict[str, Any]:
    return {"candidate_id": r["candidate_id"], "ref_number": int(r["ref_number"]), "event": r["event_type"],
            "epoch": [round(r["epoch_start"], 2), round(r["epoch_end"], 2)], "amplitude_mag": round(r["amplitude_mag"], 2),
            "score": round(r["score"], 4), "flags": r["flags"]}  # fmt: skip


def format_topology(report: dict[str, Any]) -> str:
    lines = [f"Topology report {report['run_id']}: {report['budget']['selected']} stars selected for L3, {report['budget']['left_out']} left out by the cap"]
    for tile, t in report["tiles"].items():
        lines.append(f"\n{tile}: {t['stars_in']} stars in, {t['reached_l2']} reached L2 ({t['l1_seconds']} s in L1, {t['l2_seconds']} s in L2, ${t['cost_usd']:.2f})")
        lines.append("  stopped at L1: " + ", ".join(f"{k} {v}" for k, v in sorted(t["l1_stops"].items())))
        lines.append("  after L2:      " + ", ".join(f"{k} {v}" for k, v in t["l2_stops"].items()))
        lines.append(f"  selected ({len(t['selected'])}):")
        lines += [f"    {c['ref_number']}  {c['event']:<13} {c['epoch'][0]}-{c['epoch'][1]}  {c['amplitude_mag']} mag  score {c['score']}" for c in t["selected"]]
        if t["left_out"]:
            lines.append(f"  left out by the cap ({len(t['left_out'])}), best first: " + ", ".join(f"{c['ref_number']} {c['score']} ({c['why']})" for c in t["left_out"][:8]))
        lines.append(f"  known variables, not sent on ({len(t['known_variables'])}):")
        lines += [f"    {c['ref_number']}  {c['event']:<13} {c['epoch'][0]}-{c['epoch'][1]}  {c['amplitude_mag']} mag  score {c['score']}" for c in t["known_variables"]]
    return "\n".join(lines)
