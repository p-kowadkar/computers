"""Recovery of planted signals by the L1 triggers (spec R9).

A planted signal is recovered when the injected host has a surviving trigger that the
un-injected host did not have, and whose epochs overlap the signal's epochs widened by a
tolerance. Two views of it: any such trigger, and a trigger of the family's own type. The
confusion matrix counts which type matched which family.

The injected curve is cleaned like the host, injected, detrended against the offsets of
the star's un-injected comparison pool (so no comparison star is ever injected), and run
through the same triggers and kill reasons as a real star.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from computers.bench.harness import HostCurve
from computers.bench.inject import DAYS_PER_YEAR, Signal, SignalSpec, inject
from computers.bench.metrics import Outcome
from computers.data.timeconv import jd_to_year
from computers.l1.analyse import analyse_curve
from computers.l1.context import TileContext
from computers.l1.curve import Curve
from computers.l1.detrend import detrended_magnitudes, offsets_for
from computers.l1.triggers import Trigger, TriggerInput, TriggerParams, prepare
from computers.thresholds import THRESHOLDS

__all__ = [
    "FAMILY_TYPE",
    "RecoveryRun",
    "analyse_host",
    "confusion",
    "host_input",
    "host_triggers",
    "matches",
    "run_recovery",
    "signal_span",
    "tolerance_years",
]

# The trigger type that describes each family.
FAMILY_TYPE = {"outburst": "outburst", "dip": "dip", "step": "step", "linear": "long_term", "wave": "long_term"}
DECAY_TIMES = 3.0  # an outburst is "over" after three decay times


def signal_span(signal: Signal, first_year: float, last_year: float) -> tuple[float, float]:
    """The epochs a planted signal occupies, in decimal years."""
    if signal.family == "outburst":
        return signal.t0, signal.t0 + DECAY_TIMES * signal.scale / DAYS_PER_YEAR
    if signal.family == "dip":
        return signal.t0, signal.t0 + signal.scale
    if signal.family == "step":
        return signal.t0, signal.t0
    return first_year, last_year  # linear and wave act over the whole curve


def tolerance_years(family: str) -> float:
    """How far from its span a trigger's epochs may lie and still match. Infinite for whole-curve signals."""
    if family == "outburst":
        return THRESHOLDS["recovery_tolerance_outburst_days"].value / DAYS_PER_YEAR
    if family == "dip":
        return THRESHOLDS["recovery_tolerance_dip_years"].value
    if family == "step":
        return THRESHOLDS["recovery_tolerance_step_years"].value
    return float("inf")


def _overlap(a: tuple[float, float], b: tuple[float, float], tol: float = 0.0) -> bool:
    return a[0] <= b[1] + tol and b[0] <= a[1] + tol


def matches(
    injected: Iterable[Trigger], baseline: Iterable[Trigger], span: tuple[float, float], family: str
) -> list[Trigger]:
    """Surviving triggers of the injected host that overlap the signal and were not already on the host."""
    tol = tolerance_years(family)
    old = [(t.kind, (t.epoch_start, t.epoch_end)) for t in baseline if t.survives]
    out = []
    for t in injected:
        if not t.survives or not _overlap((t.epoch_start, t.epoch_end), span, tol):
            continue
        if any(k == t.kind and _overlap((t.epoch_start, t.epoch_end), e) for k, e in old):
            continue  # the host already had this trigger
        out.append(t)
    return out


@dataclass
class RecoveryRun:
    outcomes: list[Outcome]
    baseline: dict[int, tuple[Trigger, ...]]  # the triggers of each un-injected host
    confusion: Counter = field(default_factory=Counter)  # (family, matched kind or "none") -> count


def _drop(triggers: Sequence[Trigger], ignore: Sequence[str]) -> tuple[Trigger, ...]:
    """Triggers with the ignored kill reasons removed, for ablations that switch a reason off."""
    if not ignore:
        return tuple(triggers)
    return tuple(replace(t, kill_reasons=tuple(r for r in t.kill_reasons if r not in ignore)) for t in triggers)


def host_input(
    ctx: TileContext,
    host: HostCurve,
    mag: np.ndarray,
    params: TriggerParams,
    *,
    detrend: bool = True,
    columns: np.ndarray | None = None,
) -> tuple[Curve, TriggerInput] | None:
    """The curve and trigger input of a host whose magnitudes are `mag`; None when it has too few good points."""
    curve = host.curve.with_magnitudes(mag)
    if curve.n_good < params.min_good_points:
        return None
    if detrend:
        cols = ctx.columns_of(curve) if columns is None else columns
        off = offsets_for(ctx, host.host_id)
        inp = prepare(curve, detrended_magnitudes(off, cols, mag),
                      detrended=off.kill_reason is None, limit=detrended_magnitudes(off, cols, curve.limit))  # fmt: skip
    else:
        inp = prepare(curve)
    return curve, inp


def analyse_host(
    ctx: TileContext,
    host: HostCurve,
    mag: np.ndarray,
    params: TriggerParams,
    *,
    detrend: bool = True,
    ignore_reasons: Sequence[str] = (),
    columns: np.ndarray | None = None,
) -> tuple[Trigger, ...]:
    """The triggers of a host curve whose magnitudes are `mag` (the host's own, or injected)."""
    built = host_input(ctx, host, mag, params, detrend=detrend, columns=columns)
    if built is None:
        return ()
    curve, inp = built
    return _drop(analyse_curve(curve, inp, None, params).triggers, ignore_reasons)


def host_triggers(
    contexts: Mapping[int, TileContext],
    hosts: Mapping[int, HostCurve],
    params: TriggerParams | None = None,
    *,
    detrend: bool = True,
    ignore_reasons: Sequence[str] = (),
) -> dict[int, tuple[Trigger, ...]]:
    """The triggers of every un-injected host: what L1 raises on constant stars."""
    p = params or TriggerParams.from_registry()
    return {
        hid: analyse_host(contexts[hid], h, h.mag, p, detrend=detrend, ignore_reasons=ignore_reasons)
        for hid, h in hosts.items()
    }


def run_recovery(
    contexts: Mapping[int, TileContext],
    hosts: Mapping[int, HostCurve],
    specs: Sequence[SignalSpec],
    params: TriggerParams | None = None,
    *,
    detrend: bool = True,
    ignore_reasons: Sequence[str] = (),
) -> RecoveryRun:
    """Plant every spec in its host, run L1, and record what matched.

    `contexts` maps each host id to the `TileContext` of its tile.
    """
    p = params or TriggerParams.from_registry()
    base: dict[int, tuple[Trigger, ...]] = {}
    cols: dict[int, np.ndarray] = {}
    out: list[Outcome] = []
    conf: Counter = Counter()
    for spec in specs:
        hid = spec.host_id
        h, ctx = hosts[hid], contexts[hid]
        if hid not in base:
            cols[hid] = ctx.columns_of(h.curve)
            base[hid] = analyse_host(ctx, h, h.mag, p, detrend=detrend, ignore_reasons=ignore_reasons, columns=cols[hid])
        first, last = float(jd_to_year(h.time[0])), float(jd_to_year(h.time[-1]))
        span = signal_span(spec.signal, first, last)
        injected = inject(h.time, h.mag, h.limit, spec.signal).mag
        found = analyse_host(ctx, h, injected, p, detrend=detrend, ignore_reasons=ignore_reasons, columns=cols[hid])
        hit = matches(found, base[hid], span, spec.family)
        right = any(t.kind == FAMILY_TYPE[spec.family] for t in hit)
        kind = FAMILY_TYPE[spec.family] if right else (hit[0].kind if hit else None)  # the right type wins
        conf[(spec.family, kind or "none")] += 1
        out.append(Outcome(hid, spec.family, spec.amplitude_index, spec.signal.amplitude, spec.expected_missed,
                           bool(hit), right, kind))  # fmt: skip
    return RecoveryRun(out, base, conf)


def confusion(counter: Counter) -> dict[str, dict[str, int]]:
    """The confusion counts as {family: {matched kind or "none": count}}."""
    table: dict[str, dict[str, int]] = {}
    for (family, kind), n in sorted(counter.items()):
        table.setdefault(family, {})[kind] = n
    return table
