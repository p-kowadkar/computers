"""One star through L1: cleaning, detrending, triggers and kill reasons.

`analyse_star` is the unit the patrol runs per star and the benchmark runs per injected
curve. It never calls a model and never drops a trigger: a killed trigger stays in the
result with its reasons.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from computers.l1.context import TileContext
from computers.l1.curve import Curve
from computers.l1.detrend import Detrended, detrend
from computers.l1.kill import apply_kill_reasons
from computers.l1.triggers import (
    Trigger,
    TriggerInput,
    TriggerParams,
    dip_triggers,
    disappearance_triggers,
    long_term_triggers,
    outburst_triggers,
    prepare,
    step_triggers,
)

__all__ = ["L1Result", "analyse_curve", "analyse_star", "find_triggers"]

NO_TRIGGER, PASSED = "no_trigger", "passed"


@dataclass(frozen=True)
class L1Result:
    ref_number: int
    curve: Curve
    detrended: Detrended | None  # None when the star was stopped before detrending
    inp: TriggerInput | None
    triggers: tuple[Trigger, ...] = ()
    stop_reason: str = NO_TRIGGER
    seconds: float = 0.0
    kill_counts: dict[str, int] = field(default_factory=dict)

    @property
    def survivors(self) -> tuple[Trigger, ...]:
        return tuple(t for t in self.triggers if t.survives)

    @property
    def stop_layer(self) -> str:
        """`l2` when something survived and the next layer takes over, `l1` otherwise."""
        return "l2" if self.stop_reason == PASSED else "l1"


def find_triggers(inp: TriggerInput, params: TriggerParams) -> list[Trigger]:
    out = (
        outburst_triggers(inp, params)
        + dip_triggers(inp, params)
        + step_triggers(inp, params)
        + long_term_triggers(inp, params)
        + disappearance_triggers(inp, params)
    )
    return sorted(out, key=lambda t: (t.epoch_start, t.kind))


def analyse_curve(
    curve: Curve,
    inp: TriggerInput,
    detrended: Detrended | None,
    params: TriggerParams | None = None,
    *,
    vsx_sep_arcsec: float | None = None,
    started: float | None = None,
) -> L1Result:
    """Triggers and kill reasons for an already prepared curve (the benchmark calls this directly)."""
    p = params or TriggerParams.from_registry()
    t0 = time.perf_counter() if started is None else started
    triggers = apply_kill_reasons(find_triggers(inp, p), inp, p, vsx_sep_arcsec=vsx_sep_arcsec)
    kills = Counter(r for t in triggers for r in t.kill_reasons if r != "known_variable")
    if not triggers:
        stop = NO_TRIGGER
    elif any(t.survives for t in triggers):
        stop = PASSED
    else:
        stop = "killed_by_" + min(kills.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return L1Result(curve.ref_number, curve, detrended, inp, tuple(triggers), stop, time.perf_counter() - t0, dict(kills))


def analyse_star(
    ctx: TileContext,
    ref: int,
    params: TriggerParams | None = None,
    *,
    vsx_sep_arcsec: float | None = None,
) -> L1Result:
    """Clean, detrend and trigger one patrol star of a tile."""
    t0 = time.perf_counter()
    p = params or TriggerParams.from_registry()
    curve = ctx.curve(ref)
    if curve.n_good < p.min_good_points:
        return L1Result(ref, curve, None, None, (), "too_few_points", time.perf_counter() - t0)
    d = detrend(ctx, curve)
    inp = prepare(d.curve, d.mag, detrended=d.detrended, limit=d.limit)
    return analyse_curve(d.curve, inp, d, p, vsx_sep_arcsec=vsx_sep_arcsec, started=t0)
