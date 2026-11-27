"""Tokens and sequences for the lightcurve encoder-decoder (plan 9.1, spec S6 R1).

One token per observation of a star. A token holds the magnitude offset from the star's
median (or, for a non-detection, the limit's depth relative to that median), the measurement
error, an is-limit flag, a plate-series id, a series kind and the time in years since 1880.
Nothing is divided by the star's scatter, because amplitude is the signal.

Magnitudes run backwards: a larger offset is fainter, and a limit's depth is the magnitude
the star is fainter than.

Numpy only. The spec below is written to `sequence_spec.json` in every run directory, and a
change to it is a new version.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np

from computers.data.series import SERIES

__all__ = [
    "LENGTH",
    "SPEC",
    "SPEC_VERSION",
    "T0",
    "TIME_SCALES",
    "Sequence",
    "Star",
    "make_sequence",
    "sample_indices",
    "series_id",
    "spec_hash",
    "spec_json",
    "time_features",
]

SPEC_VERSION = 1
LENGTH = 200
T0 = 1880.0  # time zero of the encoding, in decimal years
SPAN = (1880.0, 1990.0)  # the century the even sampling covers
TIME_SCALES = (1 / 365.25, 30 / 365.25, 1.0, 10.0, 100.0)  # years: a day, a month, a year, a decade, a century
ERR_FLOOR = 0.01  # mag; a recorded error below this is a rounding artefact
ERR_DEFAULT = 0.1  # mag, for a star with no recorded error at all
KIND_CODES = {"unknown": 0, "narrow": 1, "patrol": 2, "meteor": 3}

# Series ids are fixed by the sorted series table, so a new series appended later gets a new id and old ids hold.
SERIES_IDS: dict[str, int] = {name: i + 1 for i, name in enumerate(sorted(SERIES))}  # 0 is "unknown"


def series_id(name: str) -> int:
    return SERIES_IDS.get(name, 0)


SPEC: dict = {
    "version": SPEC_VERSION,
    "length": LENGTH,
    "t0_year": T0,
    "span_years": list(SPAN),
    "time_scales_years": list(TIME_SCALES),
    "tokens": {
        "value": "magnitude offset from the star's median good magnitude (detection) or limit depth relative to it (limit); never divided by scatter",
        "err": "magcal_local_rms in mag, floored at 0.01; the star's median error where missing",
        "is_limit": "1 for a non-detection",
        "series_id": "index in the sorted series table, 0 for unknown",
        "kind": KIND_CODES,
    },
    "rows": "good detections with a finite (detrended when possible) magnitude, and non-detections that no tag removed and that have a finite limit",
    "sampling": "more than `length` rows: one random row from each non-empty bin of the span (`length` equal bins), then the largest remaining bins until full; fewer: all rows, padded",
    "n_series_ids": len(SERIES_IDS) + 1,
}


def spec_json() -> str:
    return json.dumps(SPEC, indent=1, sort_keys=True)


def spec_hash() -> str:
    return hashlib.sha256(spec_json().encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Star:
    """One star's usable rows, in time order. Built once from a cleaned, detrended curve."""

    tile: str
    ref_number: int
    year: np.ndarray  # decimal years
    value: np.ndarray  # offset from the median (detection) or limit depth relative to it (limit)
    err: np.ndarray  # mag
    is_limit: np.ndarray  # bool
    series: np.ndarray  # int16 series id
    kind: np.ndarray  # uint8 series kind code
    median_mag: float
    detrended: bool

    def __post_init__(self) -> None:
        n = len(self.year)
        for name in ("value", "err", "is_limit", "series", "kind"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"{name} has {len(getattr(self, name))} rows, expected {n}")
        if n > 1 and np.any(np.diff(self.year) < 0):
            raise ValueError("a star's rows must be in time order")

    def __len__(self) -> int:
        return len(self.year)

    @property
    def n_detections(self) -> int:
        return int((~self.is_limit).sum())


@dataclass(frozen=True)
class Sequence:
    """A fixed-length sample of a star: arrays of `length` entries, padded at the end."""

    t: np.ndarray  # years since 1880
    value: np.ndarray
    err: np.ndarray
    is_limit: np.ndarray
    series: np.ndarray
    kind: np.ndarray
    pad: np.ndarray  # True for padding
    index: np.ndarray  # row of the star each entry came from, -1 for padding


def time_features(t_years: np.ndarray) -> np.ndarray:
    """Sine and cosine of the time at each scale: shape (n, 2 * len(TIME_SCALES))."""
    t = np.asarray(t_years, dtype=float)[:, None]
    ang = 2.0 * np.pi * t / np.asarray(TIME_SCALES)[None, :]
    return np.concatenate([np.sin(ang), np.cos(ang)], axis=1)


def sample_indices(year: np.ndarray, rng: np.random.Generator, length: int = LENGTH) -> np.ndarray:
    """Row indices of a sample, in time order.

    Up to `length` rows: all of them. More: one random row from each non-empty time bin,
    then extra rows from the bins that still hold the most, so the sample is spread across
    the century and not across the dense decades.
    """
    n = len(year)
    if n <= length:
        return np.arange(n)
    lo, hi = SPAN
    bins = np.clip(((year - lo) / (hi - lo) * length).astype(int), 0, length - 1)
    members = [np.flatnonzero(bins == b) for b in range(length)]
    chosen: list[int] = []
    left: list[list[int]] = []
    for m in members:
        if len(m):
            pick = int(rng.choice(m))
            chosen.append(pick)
            left.append([int(i) for i in m if i != pick])
    while len(chosen) < length:
        sizes = np.array([len(x) for x in left])
        if sizes.max() == 0:
            break
        top = np.flatnonzero(sizes == sizes.max())
        b = int(rng.choice(top))
        j = int(rng.integers(len(left[b])))
        chosen.append(left[b].pop(j))
    return np.sort(np.array(chosen, dtype=int))


def make_sequence(star: Star, rng: np.random.Generator, length: int = LENGTH) -> Sequence:
    """A padded sample of a star."""
    idx = sample_indices(star.year, rng, length)
    k = len(idx)

    def pad(a: np.ndarray, fill: float | bool) -> np.ndarray:
        out = np.full(length, fill, dtype=a.dtype)
        out[:k] = a[idx]
        return out

    t = np.zeros(length)
    t[:k] = star.year[idx] - T0
    index = np.full(length, -1, dtype=int)
    index[:k] = idx
    padmask = np.ones(length, dtype=bool)
    padmask[:k] = False
    return Sequence(
        t, pad(star.value, 0.0), pad(star.err, ERR_DEFAULT), pad(star.is_limit, False),
        pad(star.series, 0), pad(star.kind, 0), padmask, index,
    )  # fmt: skip
