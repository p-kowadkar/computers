"""Triggers: the events L1 nominates from a cleaned, detrended curve (plan 6.4).

A trigger is a description of an event and the rows of the curve that support it. It does
not decide anything: kill reasons (`kill.py`) read its evidence, and later layers read its
kind and epochs. Every threshold is in `TriggerParams`, which starts from the registry so
that a sweep can change one value and leave the rest.

Magnitudes run backwards. "Brighter than the baseline" means `baseline - mag` is positive,
"fainter" means it is negative, and each strict boundary is tested on both sides.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise

import numpy as np

from computers.data.timeconv import jd_to_year
from computers.l1.curve import Curve
from computers.l1.scatter import MAD_TO_SIGMA, star_stats
from computers.l1.seams import Stretches, stretches
from computers.thresholds import THRESHOLDS

__all__ = [
    "COARSE_SPLITS",
    "KINDS",
    "Trigger",
    "TriggerInput",
    "TriggerParams",
    "dip_triggers",
    "disappearance_triggers",
    "long_term_triggers",
    "outburst_triggers",
    "prepare",
    "step_triggers",
]

KINDS = ("outburst", "dip", "step", "long_term", "disappearance")
COARSE_SPLITS = 64  # evenly spaced splits tried before the fine search


def _t(name: str) -> float:
    return THRESHOLDS[name].value


@dataclass(frozen=True)
class TriggerParams:
    """Every number a trigger uses. `from_registry()` reads `thresholds.py`; sweeps use `replace`."""

    outburst_min_points: int
    outburst_window_days: float
    outburst_sigma: float
    outburst_single_point_mag: float
    outburst_amplitude_mag: float
    dip_min_points: int
    dip_max_years: float
    dip_amplitude_mag: float
    step_shift_mag: float
    step_min_points: int
    long_term_span_mag: float
    long_term_min_decades: int
    decade_min_points: int
    disappearance_min_plates: int
    disappearance_min_series: int
    deep_limit_margin_mag: float
    trigger_min_series: int
    min_good_points: int
    return_points: int  # good points after an outburst or dip that must show the star recovering (0 = off)
    return_recovery: float  # the share of the way back to the baseline the star must have recovered
    min_series_overrides: tuple[tuple[str, int], ...] = ()  # (kind, series) pairs that replace trigger_min_series

    @classmethod
    def from_registry(cls) -> TriggerParams:
        return cls(
            outburst_min_points=int(_t("outburst_min_points")),
            outburst_window_days=_t("outburst_window_days"),
            outburst_sigma=_t("outburst_sigma"),
            outburst_single_point_mag=_t("outburst_single_point_mag"),
            outburst_amplitude_mag=_t("amplitude_floor_single_epoch_mag"),
            dip_min_points=int(_t("dip_min_points")),
            dip_max_years=_t("dip_max_years"),
            dip_amplitude_mag=_t("amplitude_floor_long_term_mag"),
            step_shift_mag=_t("amplitude_floor_long_term_mag"),
            step_min_points=int(_t("seam_min_points")),
            long_term_span_mag=_t("amplitude_floor_long_term_mag"),
            long_term_min_decades=int(_t("long_term_min_decades")),
            decade_min_points=int(_t("seam_min_points")),
            disappearance_min_plates=int(_t("disappearance_min_plates")),
            disappearance_min_series=int(_t("disappearance_min_series")),
            deep_limit_margin_mag=_t("deep_limit_margin_mag"),
            trigger_min_series=int(_t("trigger_min_series")),
            min_good_points=int(_t("min_good_points")),
            return_points=int(_t("return_points")),
            return_recovery=_t("return_recovery"),
        )

    def but(self, **changes) -> TriggerParams:
        return replace(self, **changes)

    def min_series(self, kind: str) -> int:
        """Series that must carry a trigger of this kind: its override, or `trigger_min_series`."""
        return dict(self.min_series_overrides).get(kind, self.trigger_min_series)


@dataclass(frozen=True)
class Trigger:
    kind: str  # one of KINDS
    epoch_start: float  # decimal years
    epoch_end: float
    amplitude_mag: float  # positive; a lower limit for an outburst, because plates saturate
    single_plate: bool
    evidence: tuple[int, ...]  # row indices into the curve the trigger read
    detrended: bool  # False when the star could not be detrended and the raw curve was read
    kill_reasons: tuple[str, ...] = ()
    direction: int = 0  # +1 fainter, -1 brighter, 0 not applicable (steps only)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown trigger kind {self.kind!r}; known: {KINDS}")

    @property
    def lower_limit(self) -> bool:
        return self.kind == "outburst"

    @property
    def needs_cutouts(self) -> bool:
        return self.kind == "disappearance"

    @property
    def survives(self) -> bool:
        """A trigger survives when nothing but `known_variable` is against it."""
        return all(r == "known_variable" for r in self.kill_reasons)

    def killed_by(self, *reasons: str) -> Trigger:
        merged = tuple(dict.fromkeys((*self.kill_reasons, *reasons)))
        return replace(self, kill_reasons=merged)


@dataclass(frozen=True)
class TriggerInput:
    """What every trigger reads: the curve, the magnitudes to use, and their summary."""

    curve: Curve
    mag: np.ndarray  # detrended magnitudes, or the raw ones when the star could not be detrended
    limit: np.ndarray  # the plate limits on the same footing as `mag`
    detrended: bool
    year: np.ndarray
    baseline: float  # median of the good detections
    noise: float  # robust within-block scatter of the good detections
    stretch: Stretches

    @property
    def good(self) -> np.ndarray:
        """Good detections with a finite magnitude in the array the triggers read."""
        return self.curve.good & np.isfinite(self.mag)


def prepare(
    curve: Curve, mag: np.ndarray | None = None, *, detrended: bool = False, limit: np.ndarray | None = None
) -> TriggerInput:
    """Bundle a curve with the magnitudes to read, their baseline and noise, and its stretches.

    `mag` and `limit` default to the curve's own. After detrending they are the detrended
    magnitudes and limits. The baseline and noise are NaN for a curve with no good detection.
    """
    mag = curve.mag if mag is None else np.asarray(mag, dtype=float)
    limit = curve.limit if limit is None else np.asarray(limit, dtype=float)
    if mag.shape != curve.mag.shape or limit.shape != curve.limit.shape:
        raise ValueError("mag and limit must have one entry per row of the curve")
    good = curve.good & np.isfinite(mag)
    year = np.asarray(jd_to_year(curve.time), dtype=float)
    if not good.any():
        return TriggerInput(curve, mag, limit, detrended, year, np.nan, np.nan, stretches(curve))
    baseline = float(np.median(mag[good]))
    stats = star_stats(curve.time[good], mag[good], np.zeros(int(good.sum()), dtype=np.uint64))
    noise = stats.scatter_within
    if not np.isfinite(noise):
        noise = MAD_TO_SIGMA * float(np.median(np.abs(mag[good] - baseline)))
    return TriggerInput(curve, mag, limit, detrended, year, baseline, float(noise), stretches(curve))


def _returns(inp: TriggerInput, p: TriggerParams, evidence: np.ndarray) -> bool:
    """Whether the star is recovering after an event, so that it is not a persistent change.

    The median of the `return_points` good detections after the event must have moved back
    towards the baseline by at least `return_recovery` of the way from the event's own level.
    After a nova the next points are fainter than the eruption, so it recovers even though
    the decay is slow; after a persistent shift they sit at the event's level and do not.
    WHEN fewer than `return_points` good detections follow, the recovery cannot be seen and
    the event does not count. Always true when the check is off (`return_points` 0).
    """
    k = p.return_points
    if k <= 0:
        return True
    good = np.flatnonzero(inp.good)
    after = good[int(np.searchsorted(good, int(evidence[-1]), side="right")) :][:k]
    if len(after) < k:
        return False
    level = float(np.median(inp.mag[evidence]))
    away = inp.baseline - level  # positive for a brightening, negative for a fading
    if away == 0.0:
        return False
    return (float(np.median(inp.mag[after])) - level) / away >= p.return_recovery


# ---------------------------------------------------------------------------- outburst


def outburst_triggers(inp: TriggerInput, params: TriggerParams | None = None) -> list[Trigger]:
    """Brightenings: `outburst_min_points` points within the window, or one very bright point.

    Evidence is read from `good_for_outburst`, which keeps points rejected only by the soft
    standard bits (W4). A point counts when it is at least the amplitude floor and
    `outburst_sigma` noise units brighter than the baseline. Points that follow each other
    within the window form one event, so a decay over several months is one trigger.
    """
    p = params or TriggerParams.from_registry()
    c = inp.curve
    if not np.isfinite(inp.baseline):
        return []
    rows = np.flatnonzero(c.good_for_outburst & np.isfinite(inp.mag))
    depth = inp.baseline - inp.mag[rows]  # positive when brighter
    need = max(p.outburst_amplitude_mag, p.outburst_sigma * inp.noise)
    rows, depth = rows[depth >= need], depth[depth >= need]
    if not len(rows):
        return []
    t = c.time[rows]  # days
    in_window = np.zeros(len(rows), dtype=bool)
    for i in range(len(rows)):
        j = np.searchsorted(t, t[i] + p.outburst_window_days, side="right")
        if j - i >= p.outburst_min_points:
            in_window[i:j] = True  # every point of a window that holds enough points
    out: list[Trigger] = []
    taken = np.zeros(len(rows), dtype=bool)
    for group in _chains(t, in_window, p.outburst_window_days):
        taken[group] = True
        if not _returns(inp, p, rows[group]):
            continue  # the star is not recovering: a persistent change
        out.append(_outburst(inp, rows[group], depth[group], single_plate=False))
    lone = (depth >= max(p.outburst_single_point_mag, p.outburst_sigma * inp.noise)) & ~taken
    for group in _chains(t, lone, p.outburst_window_days):
        if not _returns(inp, p, rows[group]):
            continue
        out.append(_outburst(inp, rows[group], depth[group], single_plate=True))
    return sorted(out, key=lambda x: x.epoch_start)


def _chains(t: np.ndarray, mask: np.ndarray, gap: float) -> list[np.ndarray]:
    """Index groups of the masked points in which neighbours are at most `gap` apart."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    cuts = np.flatnonzero(np.diff(t[idx]) > gap) + 1
    return [g for g in np.split(idx, cuts) if len(g)]


def _outburst(inp: TriggerInput, rows: np.ndarray, depth: np.ndarray, *, single_plate: bool) -> Trigger:
    years = inp.year[rows]
    return Trigger("outburst", float(years.min()), float(years.max()), float(depth.max()), single_plate,
                   tuple(int(r) for r in rows), inp.detrended)


# ------------------------------------------------------------------------------- dip


def dip_triggers(inp: TriggerInput, params: TriggerParams | None = None) -> list[Trigger]:
    """Fadings: `dip_min_points` consecutive good detections all fainter than the baseline.

    Non-detections between them are ignored (a missing point is not a faint one). A run
    that lasts longer than `dip_max_years` is not a dip; it is for the step and long-term
    triggers. An outburst or a dip must also be recovering afterwards (`_returns`), so a persistent
    change that noise has chopped into short runs is left to the step and long-term triggers.
    """
    p = params or TriggerParams.from_registry()
    if not np.isfinite(inp.baseline):
        return []
    rows = np.flatnonzero(inp.good)
    faint = (inp.mag[rows] - inp.baseline) >= p.dip_amplitude_mag
    out: list[Trigger] = []
    start = None
    for i in range(len(rows) + 1):
        on = i < len(rows) and faint[i]
        if on and start is None:
            start = i
        if not on and start is not None:
            run = rows[start:i]
            span = float(inp.year[run[-1]] - inp.year[run[0]])
            if (
                len(run) >= p.dip_min_points
                and span <= p.dip_max_years
                and _returns(inp, p, run)
            ):
                depth = float(np.median(inp.mag[run]) - inp.baseline)
                out.append(Trigger("dip", float(inp.year[run[0]]), float(inp.year[run[-1]]), depth, False,
                                   tuple(int(r) for r in run), inp.detrended))
            start = None
    return out


# ------------------------------------------------------------------------------- step


def _split_cost(x: np.ndarray, i: int) -> float:
    """Total absolute deviation of the points about the median of their own side of split `i`."""
    left, right = x[:i], x[i:]
    return float(np.abs(left - np.median(left)).sum() + np.abs(right - np.median(right)).sum())


def _best_split(x: np.ndarray, k: int, coarse: int = COARSE_SPLITS) -> tuple[int, float] | None:
    """The split of `x` that best separates two levels, and its shift; None if no split has `k` points a side.

    The split minimises the summed absolute deviation about each side's median, which has a
    sharp minimum at a real change (the difference of medians is nearly flat around it). The
    shift reported is the difference of the two medians. Coarse to fine: about `coarse` evenly
    spaced splits, then every split within one coarse step of the best. The earliest of equal
    costs wins.
    """
    n = len(x)
    if n < 2 * k:
        return None
    lo, hi = k, n - k
    grid = np.unique(np.linspace(lo, hi, min(coarse, hi - lo + 1)).round().astype(int))
    best = min(grid, key=lambda i: (_split_cost(x, int(i)), int(i)))
    width = max(1, int(np.ceil((hi - lo) / max(len(grid) - 1, 1))))
    fine = range(max(lo, int(best) - width), min(hi, int(best) + width) + 1)
    i = min(fine, key=lambda j: (_split_cost(x, j), j))
    return int(i), float(np.median(x[i:]) - np.median(x[:i]))


def step_triggers(inp: TriggerInput, params: TriggerParams | None = None) -> list[Trigger]:
    """Steps: a lasting change in level, with `step_min_points` good points on each side.

    Inside each continuous stretch the best split is the one that separates two levels best
    (see `_best_split`), and its shift is the difference of the medians on its two sides. Between stretches that hold enough points, the medians of neighbouring
    stretches are compared as well: such a step is a seam effect, and is returned so that
    the kill reason can say so instead of the step going missing. The epoch range runs from
    the last point before the change to the first point after it.
    """
    p = params or TriggerParams.from_registry()
    k = p.step_min_points
    rows = np.flatnonzero(inp.good)
    if len(rows) < 2 * k:
        return []
    ids = inp.stretch.stretch[rows]
    x = inp.mag[rows]
    out: list[Trigger] = []
    big: list[tuple[int, int]] = []  # (start, end) of stretches with enough points, in `rows` order
    for s in np.unique(ids):
        idx = np.flatnonzero(ids == s)
        a, b = int(idx[0]), int(idx[-1]) + 1
        if b - a >= k:
            big.append((a, b))
        found = _best_split(x[a:b], k)
        if found is None or abs(found[1]) < p.step_shift_mag:
            continue
        i, d = found
        out.append(_step(inp, rows, a + i, d, k, a, b))
    for (a1, b1), (a2, b2) in pairwise(big):
        d = float(np.median(x[a2:b2]) - np.median(x[a1:b1]))
        if abs(d) >= p.step_shift_mag:
            out.append(_step(inp, rows, a2, d, k, a1, b2))
    return sorted(out, key=lambda t: (t.epoch_start, t.epoch_end))


def _step(inp: TriggerInput, rows: np.ndarray, split: int, shift: float, k: int, lo: int, hi: int) -> Trigger:
    """A step trigger at `split` (an index into `rows`), with the nearest `k` points of each side as evidence."""
    before, after = rows[max(lo, split - k) : split], rows[split : min(hi, split + k)]
    evidence = tuple(int(r) for r in np.r_[before, after])
    return Trigger(
        "step", float(inp.year[rows[split - 1]]), float(inp.year[rows[split]]), abs(float(shift)), False,
        evidence, inp.detrended, direction=1 if shift > 0 else -1,
    )  # fmt: skip


# -------------------------------------------------------------------------- long term


def long_term_triggers(inp: TriggerInput, params: TriggerParams | None = None) -> list[Trigger]:
    """Slow variation: the medians of at least `long_term_min_decades` populated decades span the floor.

    Each continuous stretch is judged on its own, so a change seen only by comparing the two
    sides of a seam does not count. A decade needs `decade_min_points` good points. The
    amplitude is the span of the decade medians, and the direction is that of the last
    populated decade against the first.
    """
    p = params or TriggerParams.from_registry()
    rows = np.flatnonzero(inp.good)
    if not len(rows):
        return []
    ids = inp.stretch.stretch[rows]
    out: list[Trigger] = []
    for s in np.unique(ids):
        r = rows[ids == s]
        decade = np.floor(inp.year[r] / 10.0).astype(int)
        keep, medians = [], []
        for d in np.unique(decade):
            sel = decade == d
            if sel.sum() >= p.decade_min_points:
                keep.append(r[sel])
                medians.append(float(np.median(inp.mag[r[sel]])))
        if len(keep) < p.long_term_min_decades:
            continue
        span = max(medians) - min(medians)
        if span < p.long_term_span_mag:
            continue
        used = np.sort(np.concatenate(keep))
        out.append(
            Trigger(
                "long_term", float(inp.year[used[0]]), float(inp.year[used[-1]]), float(span), False,
                tuple(int(x) for x in used), inp.detrended, direction=1 if medians[-1] > medians[0] else -1,
            )
        )  # fmt: skip
    return out


# ------------------------------------------------------------------------ disappearance


def disappearance_triggers(inp: TriggerInput, params: TriggerParams | None = None) -> list[Trigger]:
    """Detections stop while the plates stay deep enough to show the star.

    After the last good detection, `disappearance_min_plates` plates from at least
    `disappearance_min_series` series are non-detections whose limits are fainter than the
    baseline by `deep_limit_margin_mag`. Missing database points and misplaced detections
    produce this signature too, so the trigger only nominates the star: it needs cutouts
    before anything is said about it. The amplitude is the median depth of those limits
    beyond the baseline.
    """
    p = params or TriggerParams.from_registry()
    c = inp.curve
    good = np.flatnonzero(inp.good)
    if not len(good) or not np.isfinite(inp.baseline):
        return []
    last = good[-1]
    after = np.arange(last + 1, len(c))
    after = after[~c.detected[after] & c.usable[after] & np.isfinite(inp.limit[after])]
    deep = after[(inp.limit[after] - inp.baseline) > p.deep_limit_margin_mag]
    if len(deep) < p.disappearance_min_plates or len(set(c.series[deep])) < p.disappearance_min_series:
        return []
    depth = float(np.median(inp.limit[deep] - inp.baseline))
    return [
        Trigger("disappearance", float(inp.year[last]), float(inp.year[deep[-1]]), depth, False,
                tuple(int(r) for r in deep), inp.detrended)
    ]  # fmt: skip
