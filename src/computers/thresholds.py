"""The single place for numeric thresholds.

Every threshold the science depends on lives here, with its unit, where the plan
states it, and whether it is only a starting value or has been decided from data.

Changing a value needs a decision record: write a new file in ``docs/decisions/``,
point the threshold's entry in ``docs/decisions/thresholds.lock.json`` at it, then
change the value. Tests fail if the value, the lock and the decision file disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["LOCK_PATH", "THRESHOLDS", "Threshold", "change_violations", "problems"]

LOCK_PATH = Path("docs/decisions/thresholds.lock.json")
DECISIONS_DIR = Path("docs/decisions")
STATUSES = ("start", "decided")


@dataclass(frozen=True)
class Threshold:
    name: str
    value: float
    unit: str
    status: str  # "start" = a value to tune, "decided" = chosen from benchmark data
    plan: str  # plan.md section that states it

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"{self.name}: status must be one of {STATUSES}")


_REGISTRY = [
    Threshold("amplitude_floor_long_term_mag", 0.5, "mag", "start", "3.2"),
    Threshold("amplitude_floor_single_epoch_mag", 1.0, "mag", "start", "3.2"),
    Threshold("astrometry_max_sep_arcsec", 15.0, "arcsec", "start", "6.1"),
    Threshold("source_split_radius_arcsec", 5.0, "arcsec", "start", "6.1"),
    Threshold("menzel_gap_start_year", 1953.0, "year", "start", "3.1"),
    Threshold("menzel_gap_end_year", 1969.0, "year", "start", "3.1"),
    Threshold("constant_min_good_points", 200.0, "points", "start", "10.1"),
    Threshold("constant_scatter_factor", 1.5, "ratio", "start", "10.1"),
    Threshold("fade_floor_mag_per_century", 0.3, "mag/century", "start", "10.2"),
    Threshold("vsx_match_radius_arcsec", 3.0, "arcsec", "start", "10.1"),
    Threshold("vsx_exclude_radius_arcsec", 30.0, "arcsec", "start", "10.1"),
]

THRESHOLDS: dict[str, Threshold] = {t.name: t for t in _REGISTRY}


def problems(lock: dict, root: Path, registry: dict[str, Threshold] | None = None) -> list[str]:
    """Disagreements between the registry, the lock and the decision files on disk."""
    registry = THRESHOLDS if registry is None else registry
    found: list[str] = []
    for name, t in sorted(registry.items()):
        entry = lock.get(name)
        if entry is None:
            found.append(f"{name}: not in {LOCK_PATH}; add it with a decision record")
            continue
        if entry.get("value") != t.value:
            found.append(
                f"{name}: value is {t.value} but the lock says {entry.get('value')}. "
                "Write a new decision file, point the lock at it, and update the value there."
            )
        decision = entry.get("decision", "")
        path = root / decision
        in_decisions = decision.startswith(f"{DECISIONS_DIR.as_posix()}/") and decision.endswith(
            ".md"
        )
        if not in_decisions:
            found.append(f"{name}: decision {decision!r} must be a .md file under {DECISIONS_DIR}/")
        elif not path.is_file():
            found.append(f"{name}: decision file {decision} does not exist")
    for name in sorted(set(lock) - set(registry)):
        found.append(f"{name}: in {LOCK_PATH} but no longer in the registry; record why in a decision")
    return found


def change_violations(old: dict, new: dict) -> list[str]:
    """Values that changed against an earlier lock while still citing the same decision.

    ``old`` is the lock in git HEAD. A changed value must cite a different decision
    file than before, so a change always leaves a new record behind.
    """
    out = []
    for name in sorted(set(old) & set(new)):
        if old[name].get("value") != new[name].get("value") and (
            old[name].get("decision") == new[name].get("decision")
        ):
            out.append(
                f"{name}: value changed from {old[name].get('value')} to "
                f"{new[name].get('value')} but the decision file is still "
                f"{new[name].get('decision')}; write a new one"
            )
    return out
