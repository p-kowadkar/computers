"""Kill reasons (plan 6.5): why a trigger should not go any further.

Each reason is a function of a trigger, its evidence and the curve. A killed trigger is
never dropped: it keeps its reasons and is logged. `known_variable` is the exception in
meaning: it is kept and flagged, and does not stop a trigger from surviving.

The stop reason `too_few_points` belongs to the whole curve (`Curve.kill_reason`) and is
applied by `analyse_star` before any trigger runs.
"""

from __future__ import annotations

import numpy as np

from computers.l1.seams import seam_only
from computers.l1.tags import Tag
from computers.l1.triggers import Trigger, TriggerInput, TriggerParams
from computers.thresholds import THRESHOLDS

__all__ = ["KILL_REASONS", "apply_kill_reasons", "kill_reasons"]

KILL_REASONS = (
    "too_few_points",
    "seam_only",
    "single_series_only",
    "non_blue_only",
    "blend_suspect",
    "known_variable",
)


def _seam_only(t: Trigger, inp: TriggerInput, p: TriggerParams) -> bool:
    """A step visible only across a seam, or in the Menzel Gap. Other kinds are judged inside a stretch already."""
    if t.kind != "step":
        return False
    return seam_only(inp.curve, t.epoch_end, inp.stretch, min_points=p.step_min_points).seam_only


def _single_series(t: Trigger, inp: TriggerInput, p: TriggerParams) -> bool:
    rows = np.array(t.evidence, dtype=int)
    return len({str(s) for s in inp.curve.series[rows]}) < p.min_series(t.kind)


def _every(t: Trigger, inp: TriggerInput, tag: Tag) -> bool:
    rows = np.array(t.evidence, dtype=int)
    return bool(len(rows)) and bool(inp.curve.has(tag)[rows].all())


def kill_reasons(
    t: Trigger, inp: TriggerInput, params: TriggerParams | None = None, *, vsx_sep_arcsec: float | None = None
) -> tuple[str, ...]:
    """The kill reasons that apply to a trigger, in the fixed order of `KILL_REASONS`.

    `vsx_sep_arcsec` is the distance to the nearest VSX entry of the star, or None.
    """
    p = params or TriggerParams.from_registry()
    found = []
    if _seam_only(t, inp, p):
        found.append("seam_only")
    if _single_series(t, inp, p):
        found.append("single_series_only")
    if _every(t, inp, Tag.NON_BLUE):  # an unknown emulsion is not a non-blue one
        found.append("non_blue_only")
    if _every(t, inp, Tag.BLEND_SUSPECT):
        found.append("blend_suspect")
    if vsx_sep_arcsec is not None and vsx_sep_arcsec <= THRESHOLDS["vsx_match_radius_arcsec"].value:
        found.append("known_variable")
    return tuple(found)


def apply_kill_reasons(
    triggers: list[Trigger], inp: TriggerInput, params: TriggerParams | None = None, *, vsx_sep_arcsec: float | None = None
) -> list[Trigger]:
    """Every trigger with its kill reasons attached. None is removed."""
    return [t.killed_by(*kill_reasons(t, inp, params, vsx_sep_arcsec=vsx_sep_arcsec)) for t in triggers]
