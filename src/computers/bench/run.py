"""Score a detector on the built Leavitt Set and report it (plan 10.4, 10.6).

`run_bench` reads the injection plan written by `computers bench build`, loads the host
curves, plants each signal, runs the detector and returns completeness, the 50% and 90%
points, the false-alarm rate and the suspect count. The test split is scored only after it
has been frozen, because thresholds are tuned on dev and reported once on test.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from computers.bench import leavitt as L
from computers.bench import metrics as M
from computers.bench.harness import Detector, HostCurve, false_alarms, run_injections
from computers.bench.inject import Signal, SignalSpec
from computers.bench.trivial import TrivialDetector
from computers.data import silver

__all__ = ["SMOKE_AMPLITUDE_INDICES", "SMOKE_FAMILIES", "SMOKE_HOSTS", "format_report", "run_bench"]

SMOKE_HOSTS = 40
SMOKE_FAMILIES = ("linear", "step", "outburst")
SMOKE_AMPLITUDE_INDICES = (0, 2, 5)  # the lowest, a middle and the highest amplitude step


def _specs(rows: list[dict]) -> list[SignalSpec]:
    return [
        SignalSpec(
            int(r["host_id"]), r["family"], r["amplitude_index"], r["replicate"],
            Signal(r["family"], r["amplitude"], r["t0_year"], r["scale"], r["sign"]),
            r["expected_missed"], r["reason"],
        )
        for r in rows
    ]


def _hosts(data_dir: Path, split: str, ids: set[int]) -> dict[int, HostCurve]:
    tiles = L.DEV_TILES if split == "dev" else L.TEST_TILES
    excluded = set(L.held_out_split(data_dir)["excluded_plates"]) if split == "test" else None
    hosts: dict[int, HostCurve] = {}
    for tile in tiles:
        meas = silver.read_table(silver.silver_path(data_dir, "measurements", tile))
        hosts.update(L.host_curves(meas, refs=ids, exclude_plates=excluded))
    return hosts


def run_bench(
    data_dir: str | os.PathLike[str],
    *,
    split: str = "dev",
    detector: Detector | None = None,
    smoke: bool = False,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run the detector (the trivial one by default) over a split and return the results."""
    data_dir = Path(data_dir)
    if split not in ("dev", "test"):
        raise ValueError("split must be 'dev' or 'test'")
    if split == "test":
        problems = L.check_set(data_dir)
        if problems:
            raise L.BuildError("the test split is scored only on a frozen, consistent set: " + "; ".join(problems))
    started = time.time()
    out = L.set_dir(data_dir)
    if not (out / "injections.parquet").exists():
        raise L.BuildError(f"{out / 'injections.parquet'} is missing; run `computers bench build`")
    inj = [r for r in pq.read_table(out / "injections.parquet").to_pylist() if r["split"] == split]
    cons = [r for r in pq.read_table(out / "constants.parquet").to_pylist()
            if r["split"] == split and r["is_constant"]]
    host_ids = sorted(r["ref_number"] for r in cons)
    if smoke:
        host_ids = host_ids[:SMOKE_HOSTS]
        keep = set(host_ids)
        inj = [r for r in inj if r["host_id"] in keep and r["family"] in SMOKE_FAMILIES
               and r["amplitude_index"] in SMOKE_AMPLITUDE_INDICES and r["replicate"] == 0]
    hosts = _hosts(data_dir, split, set(host_ids))
    missing = set(host_ids) - set(hosts)
    if missing:
        raise L.BuildError(f"{len(missing)} constant hosts have no measurements, for example {sorted(missing)[:3]}")
    detector = detector or TrivialDetector()
    outcomes = run_injections(hosts, _specs(inj), detector)
    bins = M.completeness(outcomes)
    result = {
        "split": split, "smoke": smoke, "detector": type(detector).__name__,
        "hosts": len(hosts), "injections": len(outcomes),
        "bins": [b.__dict__ for b in bins],
        "crossings": [c.__dict__ for c in M.crossings(bins)],
        "false_alarms": false_alarms(hosts, detector).__dict__,
        "expected_missed_recovered": M.suspect_recoveries(outcomes),
        "seconds": round(time.time() - started, 1),
    }
    result["suspect"] = result["expected_missed_recovered"][0] > 0
    say(format_report(result))
    return result


def _fmt(x: float | None) -> str:
    return "not reached" if x is None else f"{x:.2f}"


def format_report(result: dict[str, Any]) -> str:
    """The completeness table and summary lines, as plain text."""
    lines = [
        (
            f"Leavitt Set {L.VERSION}, {result['split']} split{' (smoke)' if result['smoke'] else ''}: "
            f"{result['detector']} on {result['hosts']} hosts, {result['injections']} injections, "
            f"{result['seconds']} s"
        ),
        "",
        f"{'family':<9}{'step':>5}{'amplitude':>11}{'n':>6}{'recovered':>11}{'fraction':>10}{'95% interval':>16}",
    ]
    for b in result["bins"]:
        flag = "" if b["reliable"] else "  unreliable (n under 10)"
        interval = f"{b['low']:.2f} to {b['high']:.2f}"
        lines.append(
            f"{b['family']:<9}{b['amplitude_index']:>5}{b['amplitude']:>11.2f}{b['n']:>6}{b['k']:>11}"
            f"{b['fraction']:>10.2f}{interval:>16}{flag}"
        )
    lines += ["", "Amplitude at 50% and 90% recovery (mag; mag per century for linear), with a 95% interval:"]
    for c in result["crossings"]:
        est = ("at or below " if c["at_or_below"] else "") + _fmt(c["estimate"])
        interval = "" if c["estimate"] is None else f"   ({_fmt(c['low'])} to {_fmt(c['high'])})"
        lines.append(f"  {c['family']:<9}{int(c['level'] * 100):>3}%  {est:>24}{interval}")
    fa = result["false_alarms"]
    lines += [
        "",
        (
            f"False alarms on un-injected constants: {fa['k']} of {fa['n']} "
            f"({fa['per_1000']:.0f} per 1,000, 95% interval {fa['low_per_1000']:.0f} to {fa['high_per_1000']:.0f})"
        ),
    ]
    rec, tot = result["expected_missed_recovered"]
    lines.append(
        f"Expected-missed signals recovered: {rec} of {tot}"
        + ("  -> SUSPECT: the detector sees what the plates cannot show (no seam rule?)" if rec else "")
    )
    return "\n".join(lines)


def write_result(data_dir: str | os.PathLike[str], result: dict[str, Any], name: str) -> Path:
    path = Path(data_dir) / "reports" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, sort_keys=True, default=float) + "\n")
    return path
