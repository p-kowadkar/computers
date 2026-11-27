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
    Threshold("outburst_min_points", 2.0, "points", "decided", "6.4"),
    Threshold("outburst_window_days", 60.0, "days", "start", "6.4"),
    Threshold("outburst_sigma", 5.0, "noise units", "decided", "6.4"),
    Threshold("outburst_single_point_mag", 2.0, "mag", "decided", "6.4"),
    Threshold("trigger_min_series", 2.0, "series", "decided", "6.5"),
    Threshold("dip_min_points", 3.0, "points", "decided", "6.4"),
    Threshold("dip_max_years", 20.0, "years", "start", "6.4"),
    Threshold("long_term_min_decades", 4.0, "decades", "decided", "6.4"),
    Threshold("disappearance_min_plates", 10.0, "plates", "start", "6.4"),
    Threshold("disappearance_min_series", 2.0, "series", "start", "6.4"),
    Threshold("recovery_tolerance_outburst_days", 60.0, "days", "start", "10.4"),
    Threshold("recovery_tolerance_dip_years", 1.0, "years", "start", "10.4"),
    Threshold("recovery_tolerance_step_years", 5.0, "years", "start", "10.4"),
    Threshold("return_points", 5.0, "points", "decided", "6.4"),
    Threshold("return_recovery", 0.25, "fraction", "decided", "6.4"),
    Threshold("min_good_points", 50.0, "points", "start", "6.5"),
    Threshold("budget_per_tile", 20.0, "stars", "decided", "7.5"),
    Threshold("budget_total", 60.0, "stars", "decided", "7.5"),
    Threshold("l2_filter_min_gain_points", 3.0, "percentage points", "start", "7.4"),
    Threshold("llm_cap_run_usd", 2.0, "US dollars", "start", "8.6"),
    Threshold("source_split_overlap_max", 0.1, "fraction", "start", "6.1"),
    Threshold("blend_max_diff_mag", 0.3, "mag", "start", "6.1"),
    Threshold("shape_outlier_sigma", 3.0, "sigma", "start", "6.1"),
    Threshold("deep_limit_margin_mag", 1.0, "mag", "start", "6.1"),
    Threshold("colorterm_blue_min", -0.53, "colour term", "start", "6.1"),
    Threshold("comparison_max", 40.0, "stars", "start", "6.2"),
    Threshold("comparison_min", 10.0, "stars", "start", "6.2"),
    Threshold("comparison_min_per_plate", 5.0, "stars", "start", "6.2"),
    Threshold("comparison_mag_window", 0.5, "mag", "start", "6.2"),
    Threshold("comparison_colour_window", 0.25, "colour (B-V)", "start", "6.2"),
    Threshold("series_change_share", 0.5, "fraction", "start", "6.3"),
    Threshold("seam_min_points", 20.0, "points", "start", "6.3"),
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
