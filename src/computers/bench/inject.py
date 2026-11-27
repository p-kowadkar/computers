"""Plant a signal into a real lightcurve (plan 10.2).

The host curve keeps its own sampling, gaps and noise. A signal is a magnitude offset
`dm(t)`: positive makes the star fainter, negative brighter. It is applied to the flux,
and the result is converted back to magnitude, so a 2 mag outburst is a factor 6.3 in light.

Limits are re-applied after injection:

* a detection pushed fainter than its plate's limit becomes a non-detection (NaN);
* a non-detection stays one, unless the signal would put the host's typical magnitude
  above that plate's limit. The star's true magnitude on that plate is unknown (it was
  fainter than the limit), so the host's median detection stands in for it, and the point
  is reported as converted;
* a point the signal did not make fainter is never turned into a non-detection. This
  keeps a zero-amplitude injection an exact identity, even when real data hold a
  detection a little below its own limit.

No noise is added: the real noise is already in the curve. All times are HJD days, and
signal epochs are decimal years.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from computers.data.timeconv import DAYS_PER_YEAR, jd_to_year
from computers.thresholds import THRESHOLDS

__all__ = [
    "FAMILIES",
    "HostSpan",
    "InjectedCurve",
    "Signal",
    "SignalSpec",
    "inject",
    "jd_to_year",
    "plan_injections",
]

FAMILIES = ("linear", "step", "dip", "outburst", "wave")
# Plan 10.2 ranges: (low, high) of the amplitude, in mag (mag per century for linear).
AMPLITUDE_RANGE = {
    "linear": (0.1, 1.5),
    "step": (0.2, 1.5),
    "dip": (0.3, 2.0),
    "outburst": (1.0, 8.0),
    "wave": (0.3, 1.5),
}
DIP_YEARS = (1.0, 20.0)
OUTBURST_TAU_DAYS = (3.0, 120.0)  # e-folding times; "decay from days to months"
WAVE_PERIOD_YEARS = (10.0, 100.0)
DIP_TAPER = 0.25  # fraction of the dip's duration spent on each cosine edge
STEP_IN_GAP_FRACTION = 1 / 3  # share of steps whose epoch is placed inside the Menzel Gap

# ------------------------------------------------------------------------ signals


@dataclass(frozen=True)
class Signal:
    """One signal. `amplitude` is mag (mag per century for `linear`), always positive.

    `sign` is +1 for a fade and -1 for a brightening. An outburst always brightens.
    `t0` is the start epoch in decimal years, and `scale` is the duration in years for
    a dip, the decay time in days for an outburst, and the period in years for a wave.
    """

    family: str
    amplitude: float
    t0: float
    scale: float = 0.0
    sign: int = 1

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"unknown family {self.family!r}; known: {FAMILIES}")
        if not math.isfinite(self.amplitude) or self.amplitude < 0:
            raise ValueError("amplitude must be finite and not negative")
        if self.sign not in (-1, 1):
            raise ValueError("sign must be +1 (fainter) or -1 (brighter)")
        if self.family in ("dip", "outburst", "wave") and not self.scale > 0:
            raise ValueError(f"{self.family} needs a positive scale")

    def dm(self, year: np.ndarray | float) -> np.ndarray:
        """The magnitude offset at each decimal year. Positive is fainter."""
        t = np.asarray(year, dtype=float)
        a, s = self.amplitude, float(self.sign)
        if self.family == "linear":
            return s * a * (t - self.t0) / 100.0
        if self.family == "step":
            return s * a * (t >= self.t0)
        if self.family == "dip":
            u = (t - self.t0) / self.scale  # 0 at the start, 1 at the end
            ramp = np.clip(np.minimum(u, 1.0 - u) / DIP_TAPER, 0.0, 1.0)
            shape = 0.5 * (1.0 - np.cos(np.pi * ramp))
            return s * a * np.where((u >= 0) & (u <= 1), shape, 0.0)
        if self.family == "outburst":
            days = (t - self.t0) * DAYS_PER_YEAR
            return -a * np.where(days >= 0, np.exp(-np.clip(days, 0, None) / self.scale), 0.0)
        return s * a * np.sin(2.0 * np.pi * (t - self.t0) / self.scale)  # wave


@dataclass(frozen=True)
class InjectedCurve:
    mag: np.ndarray  # NaN marks a non-detection
    converted: np.ndarray  # non-detections the signal turned into detections
    lost: np.ndarray  # detections the signal pushed below the limit

    @property
    def n_converted(self) -> int:
        return int(self.converted.sum())

    @property
    def n_lost(self) -> int:
        return int(self.lost.sum())


def inject(
    time_jd: np.ndarray, mag: np.ndarray, limit_mag: np.ndarray, signal: Signal
) -> InjectedCurve:
    """Return the host curve with the signal planted. The inputs are not changed."""
    time_jd = np.asarray(time_jd, dtype=float)
    mag = np.asarray(mag, dtype=float)
    limit = np.asarray(limit_mag, dtype=float)
    if not (time_jd.shape == mag.shape == limit.shape and mag.ndim == 1):
        raise ValueError("time, mag and limit must be 1-D arrays of the same length")
    dm = np.asarray(signal.dm(jd_to_year(time_jd)), dtype=float)
    detected = np.isfinite(mag)
    out = np.full(mag.shape, np.nan)
    converted = np.zeros(mag.shape, dtype=bool)
    lost = np.zeros(mag.shape, dtype=bool)
    if detected.any():
        flux = 10.0 ** (-0.4 * mag[detected]) * 10.0 ** (-0.4 * dm[detected])
        new = -2.5 * np.log10(flux)
        untouched = dm[detected] == 0.0
        new[untouched] = mag[detected][untouched]  # no round-trip error where nothing changed
        # A point is lost only when the signal made it fainter and it is now below the limit.
        gone = (dm[detected] > 0) & (new > limit[detected])
        new[gone] = np.nan
        out[detected] = new
        lost[detected] = gone
        baseline = float(np.median(mag[detected]))
    else:
        return InjectedCurve(out, converted, lost)  # nothing to say about a star never seen
    seen_now = ~detected & np.isfinite(limit)
    proxy = baseline + dm
    lifted = seen_now & (dm < 0) & (proxy < limit)
    out[lifted] = proxy[lifted]
    converted[lifted] = True
    return InjectedCurve(out, converted, lost)


# --------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class HostSpan:
    """What the planner needs to know about a host: who it is and when it was observed."""

    host_id: int
    first_year: float
    last_year: float


@dataclass(frozen=True)
class SignalSpec:
    """One planned injection: the signal, its host, and what the truth is."""

    host_id: int
    family: str
    amplitude_index: int
    replicate: int
    signal: Signal
    expected_missed: bool
    reason: str


def _in_gap(year: float) -> bool:
    return THRESHOLDS["menzel_gap_start_year"].value <= year <= THRESHOLDS["menzel_gap_end_year"].value


def _loguniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(math.exp(rng.uniform(math.log(lo), math.log(hi))))


def plan_injections(
    hosts: Sequence[HostSpan],
    *,
    seed: int,
    n_amplitudes: int = 6,
    replicates: int = 2,
    families: Iterable[str] = FAMILIES,
    forbidden: Iterable[int] = (),
) -> list[SignalSpec]:
    """Plan every injection: each host, family, amplitude step and replicate.

    Everything is drawn from a generator seeded by (seed, host, family, amplitude step,
    replicate), so a rerun, or a different host list, gives the same signal for the same
    key. Hosts named in `forbidden` (known variables, comparison stars) raise an error
    rather than being skipped, because a silent skip would hide a selection bug.
    """
    bad = set(forbidden) & {h.host_id for h in hosts}
    if bad:
        raise ValueError(f"hosts are known variables or comparison stars: {sorted(bad)[:5]}")
    if n_amplitudes < 1 or replicates < 1:
        raise ValueError("n_amplitudes and replicates must be at least 1")
    families = tuple(families)
    for f in families:
        if f not in FAMILIES:
            raise ValueError(f"unknown family {f!r}")
    gap_lo = THRESHOLDS["menzel_gap_start_year"].value
    gap_hi = THRESHOLDS["menzel_gap_end_year"].value
    fade_floor = THRESHOLDS["fade_floor_mag_per_century"].value
    specs: list[SignalSpec] = []
    for host in hosts:
        if not host.last_year > host.first_year:
            raise ValueError(f"host {host.host_id} has no time span")
        for f in families:
            lo, hi = AMPLITUDE_RANGE[f]
            grid = np.geomspace(lo, hi, n_amplitudes) if n_amplitudes > 1 else np.array([lo])
            for ai, amp in enumerate(grid):
                for rep in range(replicates):
                    rng = np.random.default_rng(
                        np.random.SeedSequence([seed, host.host_id, FAMILIES.index(f), ai, rep])
                    )
                    specs.append(
                        _one_spec(host, f, float(amp), ai, rep, rng, gap_lo, gap_hi, fade_floor)
                    )
    return specs


def _one_spec(
    host: HostSpan,
    family: str,
    amp: float,
    ai: int,
    rep: int,
    rng: np.random.Generator,
    gap_lo: float,
    gap_hi: float,
    fade_floor: float,
) -> SignalSpec:
    sign = int(rng.choice([-1, 1]))
    first, last = host.first_year, host.last_year
    missed, reason = False, ""
    if family == "linear":
        sig = Signal("linear", amp, t0=first, sign=sign)
        if amp < fade_floor:
            missed, reason = True, f"slope under {fade_floor} mag per century"
    elif family == "step":
        in_gap = rng.random() < STEP_IN_GAP_FRACTION
        lo, hi = (max(first, gap_lo), min(last, gap_hi)) if in_gap else (first, last)
        if not hi > lo:  # the host has no data in the gap: fall back to anywhere
            lo, hi, in_gap = first, last, False
        t0 = float(rng.uniform(lo, hi))
        sig = Signal("step", amp, t0=t0, sign=sign)
        if _in_gap(t0):
            missed, reason = True, "step placed inside the Menzel Gap"
    elif family == "dip":
        dur = _loguniform(rng, *DIP_YEARS)
        dur = min(dur, 0.9 * (last - first))
        t0 = float(rng.uniform(first, last - dur))
        sig = Signal("dip", amp, t0=t0, scale=dur, sign=sign)
    elif family == "outburst":
        tau = _loguniform(rng, *OUTBURST_TAU_DAYS)
        t0 = float(rng.uniform(first, last))
        sig = Signal("outburst", amp, t0=t0, scale=tau, sign=-1)
    else:
        period = _loguniform(rng, *WAVE_PERIOD_YEARS)
        t0 = float(rng.uniform(first, last))
        sig = Signal("wave", amp, t0=t0, scale=period, sign=sign)
    return SignalSpec(host.host_id, family, ai, rep, sig, missed, reason)
