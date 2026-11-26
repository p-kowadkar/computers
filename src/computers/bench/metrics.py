"""Completeness curves, the 50% and 90% points, and false-alarm rates (plan 10.4).

Completeness is the share of planted signals that a detector recovers, per family and
amplitude step. Signals planned as `expected_missed` (a step inside the Menzel Gap, a
fade under the fade floor) are not counted in it, because the plan expects a good
detector to miss them. They are tracked on their own: a detector that recovers one is
reported as suspect.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

__all__ = [
    "MIN_PER_BIN",
    "Bin",
    "Crossing",
    "Outcome",
    "Rate",
    "amplitude_at",
    "completeness",
    "crossings",
    "false_alarm_rate",
    "suspect_recoveries",
    "wilson",
]

MIN_PER_BIN = 10  # fewer injections than this and a bin's completeness is marked unreliable
Z95 = 1.959964


@dataclass(frozen=True)
class Outcome:
    """One planted signal and whether the detector recovered it."""

    host_id: int
    family: str
    amplitude_index: int
    amplitude: float
    expected_missed: bool
    recovered: bool


@dataclass(frozen=True)
class Bin:
    family: str
    amplitude_index: int
    amplitude: float  # the bin's amplitude (mag, or mag per century for `linear`)
    n: int
    k: int
    fraction: float
    low: float  # 95% Wilson interval
    high: float
    reliable: bool


@dataclass(frozen=True)
class Crossing:
    """The amplitude at which completeness reaches `level`, with a 95% interval.

    Each value is None when the curve never reaches the level.
    """

    family: str
    level: float
    estimate: float | None
    low: float | None  # where the upper confidence curve reaches the level
    high: float | None  # where the lower confidence curve reaches the level
    at_or_below: bool = False  # the lowest amplitude tried already reaches the level, so the estimate is an upper bound


@dataclass(frozen=True)
class Rate:
    """A count out of a total, with a 95% interval, also given per 1,000."""

    k: int
    n: int
    per_1000: float
    low_per_1000: float
    high_per_1000: float


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval for k successes in n trials. (0, 1) when n is 0."""
    if n <= 0:
        return 0.0, 1.0
    if not 0 <= k <= n:
        raise ValueError("k must be between 0 and n")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def completeness(outcomes: Iterable[Outcome]) -> list[Bin]:
    """One bin per (family, amplitude step), from the outcomes that were not expected to be missed."""
    groups: dict[tuple[str, int], list[Outcome]] = defaultdict(list)
    for o in outcomes:
        if not o.expected_missed:
            groups[(o.family, o.amplitude_index)].append(o)
    bins = []
    for (family, index), items in sorted(groups.items()):
        n, k = len(items), sum(o.recovered for o in items)
        lo, hi = wilson(k, n)
        bins.append(
            Bin(family, index, float(np.mean([o.amplitude for o in items])), n, k, k / n, lo, hi,
                n >= MIN_PER_BIN)
        )
    return bins


def amplitude_at(amplitudes: Sequence[float], values: Sequence[float], level: float) -> float | None:
    """The smallest amplitude at which a curve reaches `level`, or None if it never does.

    The curve is first made non-decreasing (a running maximum), then interpolated
    linearly in log amplitude between the two bins that straddle the level. When the
    first bin already reaches the level, its amplitude is returned: the curve is at or
    above the level from there, and the true crossing is below the lowest amplitude tried.
    """
    if len(amplitudes) != len(values):
        raise ValueError("amplitudes and values must have the same length")
    a = np.asarray(amplitudes, dtype=float)
    order = np.argsort(a)
    a = a[order]
    y = np.maximum.accumulate(np.asarray(values, dtype=float)[order])
    reached = np.flatnonzero(y >= level)
    if reached.size == 0:
        return None
    i = int(reached[0])
    if i == 0 or y[i] == y[i - 1]:
        return float(a[i])
    frac = (level - y[i - 1]) / (y[i] - y[i - 1])
    return float(math.exp(math.log(a[i - 1]) + frac * (math.log(a[i]) - math.log(a[i - 1]))))


def crossings(bins: Sequence[Bin], levels: Sequence[float] = (0.5, 0.9)) -> list[Crossing]:
    """The 50% and 90% points per family. Bins marked unreliable are left out."""
    out = []
    for family in sorted({b.family for b in bins}):
        use = [b for b in bins if b.family == family and b.reliable]
        amps = [b.amplitude for b in use]
        for level in levels:
            first = min(use, key=lambda b: b.amplitude) if use else None
            out.append(
                Crossing(
                    family,
                    level,
                    amplitude_at(amps, [b.fraction for b in use], level),
                    amplitude_at(amps, [b.high for b in use], level),
                    amplitude_at(amps, [b.low for b in use], level),
                    first is not None and first.fraction >= level,
                )
            )
    return out


def suspect_recoveries(outcomes: Iterable[Outcome]) -> tuple[int, int]:
    """(recovered, total) among the signals that were expected to be missed.

    Any recovery makes the detector suspect: it is seeing something the plates cannot show.
    """
    missed = [o for o in outcomes if o.expected_missed]
    return sum(o.recovered for o in missed), len(missed)


def false_alarm_rate(flagged: int, total: int) -> Rate:
    """False alarms among `total` constant stars that were not injected."""
    lo, hi = wilson(flagged, total)
    return Rate(flagged, total, 1000 * flagged / total if total else 0.0, 1000 * lo, 1000 * hi)
