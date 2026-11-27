"""What L1 does to the benchmark: before and after cleaning and detrending (spec R8).

All numbers are on the dev split (M44 and T CrB). The test tile is not touched here.

1. Scatter of the dev constants, before and after detrending.
2. False alarms of the trivial detector at 0.2, 0.3 and 0.5 mag on raw, cleaned and
   detrended constants.
3. Each meteor policy: false alarms and completeness.
4. The seam rule on injected steps.

A component whose number does not improve is reported as such.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from computers.bench import leavitt as L
from computers.bench import metrics as M
from computers.bench.harness import HostCurve, run_injections
from computers.bench.inject import inject
from computers.bench.l1_adapter import L1Detector, l1_hosts
from computers.bench.run import specs_from_rows
from computers.bench.trivial import TrivialDetector
from computers.data import silver
from computers.l1.context import TileContext
from computers.l1.detrend import detrended_magnitudes, offsets_for
from computers.l1.scatter import star_stats
from computers.l1.seams import seam_only, stretches

__all__ = ["format_l1_report", "run_l1_report"]

FALSE_ALARM_THRESHOLDS = (0.2, 0.3, 0.5)
POLICIES = ("reject", "keep")


def _median(values: list[float]) -> float | None:
    v = [x for x in values if x is not None and np.isfinite(x)]
    return float(np.median(v)) if v else None


def _scatter(hosts: dict[int, HostCurve], mags: dict[int, np.ndarray]) -> dict[str, float | int | None]:
    within, between, n = [], [], 0
    for ref, h in hosts.items():
        m = mags[ref]
        good = np.isfinite(m)
        s = star_stats(h.time[good], m[good], np.zeros(int(good.sum()), dtype=np.uint64))
        within.append(s.scatter_within)
        between.append(s.scatter_between)
        n += 1
    return {"hosts": n, "within": _median(within), "between": _median(between)}


def _statistics(hosts: dict[int, HostCurve], mags: dict[int, np.ndarray]) -> list[float | None]:
    rule = TrivialDetector()
    return [rule.statistic(h.time, mags[r]) for r, h in hosts.items()]


def _false_alarms(stats: list[float | None]) -> dict[str, Any]:
    out = {}
    for t in FALSE_ALARM_THRESHOLDS:
        k = sum(s is not None and s > t for s in stats)
        r = M.false_alarm_rate(k, len(stats))
        out[f"{t:g}"] = {"k": k, "n": len(stats), "per_1000": r.per_1000, "low": r.low_per_1000, "high": r.high_per_1000}
    return out


def run_l1_report(
    data_dir: str | os.PathLike[str], *, say: Callable[[str], None] = print
) -> dict[str, Any]:
    """Run every table of the report on the dev split and return the numbers."""
    data_dir = Path(data_dir)
    started = time.time()
    out_dir = L.set_dir(data_dir)
    cons = [r for r in pq.read_table(out_dir / "constants.parquet").to_pylist() if r["split"] == "dev" and r["is_constant"]]
    inj = [r for r in pq.read_table(out_dir / "injections.parquet").to_pylist() if r["split"] == "dev"]
    refs_by_tile: dict[str, list[int]] = {}
    for r in cons:
        refs_by_tile.setdefault(r["tile"], []).append(r["ref_number"])
    result: dict[str, Any] = {"hosts": {t: len(v) for t, v in refs_by_tile.items()}, "configs": {}}

    # the benchmark's original hosts: good detections and non-detections from the silver `reject` column
    raw: dict[int, HostCurve] = {}
    for tile, refs in refs_by_tile.items():
        raw.update(L.host_curves(silver.read_table(silver.silver_path(data_dir, "measurements", tile)), set(refs)))
    configs: dict[str, dict[str, Any]] = {"raw": {"hosts": raw, "mags": {r: h.mag for r, h in raw.items()}, "detector": TrivialDetector(), "kind": "raw"}}

    contexts: dict[tuple[str, str], TileContext] = {}
    for policy in POLICIES:
        l1: dict[int, HostCurve] = {}
        det: dict[int, np.ndarray] = {}
        undetrended: dict[int, np.ndarray] = {}
        by_tile: dict[int, str] = {}
        few = 0
        for tile, refs in refs_by_tile.items():
            ctx = contexts[(tile, policy)] = TileContext.from_silver(data_dir, tile, meteor_policy=policy)
            hosts = l1_hosts(ctx, refs)
            for ref, h in hosts.items():
                cols = ctx.columns_of(h.curve)
                off = offsets_for(ctx, ref)
                few += off.kill_reason is not None
                det[ref] = detrended_magnitudes(off, cols, h.mag)
                undetrended[ref] = h.mag
                by_tile[ref] = tile
            l1.update(hosts)
        result["configs"][f"l1_{policy}"] = {"few_comparisons": few}
        configs[f"l1_{policy}_clean"] = {"hosts": l1, "mags": undetrended, "kind": "l1", "policy": policy, "detrended": False}
        configs[f"l1_{policy}_detrended"] = {"hosts": l1, "mags": det, "kind": "l1", "policy": policy, "detrended": True}
        result["configs"][f"l1_{policy}"]["tile_of"] = by_tile
    # tables 1 and 2
    for name, cfg in configs.items():
        entry = result["configs"].setdefault(name, {})
        entry["scatter"] = _scatter(cfg["hosts"], cfg["mags"])
        entry["false_alarms"] = _false_alarms(_statistics(cfg["hosts"], cfg["mags"]))
        entry["good_points_per_host"] = _median([float(np.isfinite(m).sum()) for m in cfg["mags"].values()])
        say(f"   {name}: scatter within {entry['scatter']['within']:.3f} between {entry['scatter']['between']:.3f}")
    # table 3: completeness of each configuration
    for name, cfg in configs.items():
        outcomes = []
        for tile, refs in refs_by_tile.items():
            specs = specs_from_rows([r for r in inj if r["tile"] == tile])
            if cfg["kind"] == "raw":
                outcomes += run_injections({r: raw[r] for r in refs}, specs, cfg["detector"])
            else:
                ctx = contexts[(tile, cfg["policy"])]
                hosts = {r: cfg["hosts"][r] for r in refs}
                outcomes += run_injections(hosts, specs, L1Detector(ctx, detrended=cfg["detrended"]))
        bins = M.completeness(outcomes)
        entry = result["configs"][name]
        entry["crossings"] = [c.__dict__ for c in M.crossings(bins)]
        entry["suspect"] = M.suspect_recoveries(outcomes)
        entry["bins"] = [b.__dict__ for b in bins]
        say(f"   {name}: completeness done ({len(outcomes)} injections, {time.time() - started:.0f} s)")
    # table 4: the seam rule on injected steps (default policy)
    result["seams"] = _seam_table(contexts, refs_by_tile, inj)
    result["seconds"] = round(time.time() - started, 1)
    for entry in result["configs"].values():
        entry.pop("tile_of", None)
    return result


def _seam_table(contexts, refs_by_tile, inj) -> dict[str, Any]:
    """Share of injected steps judged `seam_only`, by group, with the reasons for the non-gap ones."""
    groups: dict[str, list[int]] = {}
    reasons: dict[str, int] = {}
    for tile, refs in refs_by_tile.items():
        ctx = contexts[(tile, "reject")]
        hosts = l1_hosts(ctx, refs)
        steps = [r for r in inj if r["tile"] == tile and r["family"] == "step"]
        for spec in specs_from_rows(steps):
            h = hosts[spec.host_id]
            curve = h.curve.with_magnitudes(inject(h.time, h.mag, h.limit, spec.signal).mag)
            result = seam_only(curve, spec.signal.t0, stretches(curve))
            gap = "Menzel" in spec.reason
            groups.setdefault("gap" if gap else f"amplitude_{spec.amplitude_index}", []).append(int(result.seam_only))
            if not gap:
                reasons[result.reason] = reasons.get(result.reason, 0) + 1
    table = {k: {"n": len(v), "seam_only": sum(v), "share": sum(v) / len(v)} for k, v in sorted(groups.items())}
    table["reasons_for_non_gap_steps"] = dict(sorted(reasons.items()))
    return table


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def format_l1_report(result: dict[str, Any]) -> str:
    cfg = result["configs"]
    lines = [f"L1 report on the dev split: {sum(result['hosts'].values())} constants ({result['hosts']}), {result['seconds']} s", ""]
    names = [n for n in cfg if "scatter" in cfg[n]]
    lines += ["1. Scatter of the constants (median, mag): within-block / between-block"]
    for n in names:
        s = cfg[n]["scatter"]
        lines.append(f"   {n:<22} {_fmt(s['within'])} / {_fmt(s['between'])}   ({s['hosts']} hosts, {_fmt(cfg[n]['good_points_per_host'])[:-4]} good points each)")
    lines += ["", "2. False alarms of the trivial detector, per 1,000 constants (95% interval)"]
    lines.append(f"   {'':<22}" + "".join(f"{t:>26}" for t in ("0.2 mag", "0.3 mag", "0.5 mag")))
    for n in names:
        fa = cfg[n]["false_alarms"]
        cells = [f"{fa[k]['k']}/{fa[k]['n']} = {fa[k]['per_1000']:.0f} ({fa[k]['low']:.0f}-{fa[k]['high']:.0f})" for k in ("0.2", "0.3", "0.5")]
        lines.append(f"   {n:<22}" + "".join(f"{c:>26}" for c in cells))
    lines += ["", "3. Completeness at 0.5 mag: amplitude at 50% and 90% recovery (mag; mag per century for linear)"]
    fams = sorted({c["family"] for n in names for c in cfg[n]["crossings"]})
    lines.append(f"   {'':<22}" + "".join(f"{f + ' 50/90':>22}" for f in fams))
    for n in names:
        cells = []
        for f in fams:
            cs = {c["level"]: c for c in cfg[n]["crossings"] if c["family"] == f}
            def one(c):
                return "-" if c is None or c["estimate"] is None else f"{'<=' if c['at_or_below'] else ''}{c['estimate']:.2f}"
            cells.append(f"{one(cs.get(0.5))} / {one(cs.get(0.9))}")
        lines.append(f"   {n:<22}" + "".join(f"{c:>22}" for c in cells))
    lines += ["", "   expected-missed signals recovered (the trivial detector has no seam rule; table 4 tests the rule itself):"]
    for n in names:
        lines.append(f"   {n:<22} {cfg[n]['suspect'][0]} of {cfg[n]['suspect'][1]}")
    lines += ["", "   hosts that could not be detrended (few_comparisons): " + ", ".join(f"{p} {cfg['l1_' + p]['few_comparisons']}" for p in POLICIES)]
    lines += ["", "4. Steps judged seam_only (default policy)"]
    for k, v in result["seams"].items():
        if k != "reasons_for_non_gap_steps":
            lines.append(f"   {k:<14} {v['seam_only']:>5} of {v['n']:<5} = {v['share']:.1%}")
    lines.append("   reasons for all non-gap steps: " + ", ".join(f"{k} {v}" for k, v in result["seams"]["reasons_for_non_gap_steps"].items()))
    return "\n".join(lines)
