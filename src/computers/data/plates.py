"""Which plates two tiles have in common.

Every star in a tile appears on nearly the same plates, so a model trained on one tile
can have seen the plates of another. The plan assumed tiles this far apart share none.
Sprint 0 found 14.6% of T CrB's plates in Tabby's field. This module measures it for
whole tiles from the exposures tables.
"""

from __future__ import annotations

import itertools
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa

from computers.data import silver
from computers.data.tiles import TILES

__all__ = ["PairOverlap", "PlateReport", "load_report", "plate_set", "report_text"]


def plate_set(exposures: pa.Table) -> set[str]:
    """Distinct plates (series plus a five-digit plate number). A plate with several
    exposures counts once."""
    series = exposures.column("series").to_pylist()
    nums = exposures.column("platenum").to_pylist()
    return {f"{s}{n:05d}" for s, n in zip(series, nums, strict=True)}


def _series_of(plate: str) -> str:
    return re.sub(r"\d+$", "", plate)


@dataclass(frozen=True)
class PairOverlap:
    a: str
    b: str
    shared: int
    share_of_a: float
    share_of_b: float
    series: dict[str, int]


@dataclass
class PlateReport:
    plates: dict[str, int] = field(default_factory=dict)
    exposures: dict[str, int] = field(default_factory=dict)
    pairs: list[PairOverlap] = field(default_factory=list)
    in_all: int = 0


def load_report(data_dir: str | os.PathLike[str], tiles: list[str] | None = None) -> PlateReport:
    """Overlap of every pair of tiles that has an exposures table."""
    names = tiles or list(TILES)
    sets: dict[str, set[str]] = {}
    report = PlateReport()
    for name in names:
        path = silver.silver_path(data_dir, "exposures", name)
        if not Path(path).exists():
            continue
        table = silver.read_table(path)
        sets[name] = plate_set(table)
        report.plates[name] = len(sets[name])
        report.exposures[name] = table.num_rows
    for a, b in itertools.combinations(sets, 2):
        shared = sets[a] & sets[b]
        by_series = Counter(_series_of(p) for p in shared)
        report.pairs.append(
            PairOverlap(
                a, b, len(shared),
                len(shared) / len(sets[a]) if sets[a] else 0.0,
                len(shared) / len(sets[b]) if sets[b] else 0.0,
                dict(by_series.most_common()),
            )
        )  # fmt: skip
    if len(sets) >= 2:
        report.in_all = len(set.intersection(*sets.values()))
    return report


def report_text(report: PlateReport) -> str:
    if not report.plates:
        return "no exposures tables found: run `computers pull --stage exposures` first"
    lines = ["Plates per tile (series plus plate number; a plate with several exposures counts once):"]
    for name, n in report.plates.items():
        lines.append(f"  {name:6s} {n:7d} plates from {report.exposures[name]:7d} exposures")
    if not report.pairs:
        lines.append("only one tile has an exposures table, so there is nothing to compare")
        return "\n".join(lines)
    lines += ["", "Plates shared between tiles:"]
    for p in report.pairs:
        top = ", ".join(f"{s} {n}" for s, n in list(p.series.items())[:6]) or "none"
        lines.append(
            f"  {p.a} / {p.b}: {p.shared} shared "
            f"({100 * p.share_of_a:.1f}% of {p.a}, {100 * p.share_of_b:.1f}% of {p.b}); series: {top}"
        )
    lines.append(f"  plates in every tile: {report.in_all}")
    lines.append(
        "\nThese counts come from the exposures that touch the tile centre and its four corners, "
        "so they are a lower bound for whole-tile overlap."
    )
    return "\n".join(lines)
