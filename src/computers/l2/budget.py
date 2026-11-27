"""The budget rule: which stars go on to L3 (plan 7.5, decision 0004, spec S5 R5).

At most `budget_per_tile` stars per tile and `budget_total` in all, one trigger per star,
best score first. A `known_variable` candidate is kept in the table but takes no place:
known variables are the benchmark's true positives and are not sent to the crew.
Everything the cap leaves out is returned, with its score, so a cap that is too tight shows.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from computers.thresholds import THRESHOLDS

__all__ = ["Selection", "select"]


@dataclass(frozen=True)
class Selection:
    selected: tuple[str, ...]  # candidate ids sent on, best first
    left_out: tuple[tuple[str, float, str], ...]  # (candidate id, score, why) for every unselected eligible candidate
    known_variable: tuple[str, ...]  # candidate ids that took no place
    same_star: tuple[str, ...]  # candidate ids dropped because a better trigger of the same star was taken


def select(
    candidates: Sequence[Mapping[str, Any]], *, per_tile: int | None = None, total: int | None = None
) -> Selection:
    """Choose candidates. Each row needs `candidate_id`, `ref_number`, `tile`, `score` and `flags` (a list of strings)."""
    per_tile = int(THRESHOLDS["budget_per_tile"].value) if per_tile is None else per_tile
    total = int(THRESHOLDS["budget_total"].value) if total is None else total
    known = [c for c in candidates if "known_variable" in c["flags"]]
    eligible = [c for c in candidates if "known_variable" not in c["flags"]]
    # best trigger of each star: highest score, then the id, so a tie is stable
    order = sorted(eligible, key=lambda c: (-c["score"], c["candidate_id"]))
    seen: set[tuple[str, int]] = set()
    best: list[Mapping[str, Any]] = []
    same_star: list[str] = []
    for c in order:
        key = (c["tile"], int(c["ref_number"]))
        if key in seen:
            same_star.append(c["candidate_id"])
        else:
            seen.add(key)
            best.append(c)
    taken: dict[str, int] = {}
    chosen: list[Mapping[str, Any]] = []
    left: list[tuple[str, float, str]] = []
    for c in best:  # best first overall, so the total cap trims the weakest
        if taken.get(c["tile"], 0) >= per_tile:
            left.append((c["candidate_id"], float(c["score"]), "tile_cap"))
        elif len(chosen) >= total:
            left.append((c["candidate_id"], float(c["score"]), "total_cap"))
        else:
            taken[c["tile"]] = taken.get(c["tile"], 0) + 1
            chosen.append(c)
    return Selection(
        tuple(c["candidate_id"] for c in chosen),
        tuple(left),
        tuple(c["candidate_id"] for c in known),
        tuple(same_star),
    )
