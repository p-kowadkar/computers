"""Masking for masked reconstruction (plan 9.1, spec S6 R2.1).

30 to 50% of the tokens of a sequence are hidden. Timestamps, series and kind stay visible.
In "random" mode the hidden tokens are drawn at random. In "decade" mode a stretch of about
a decade is hidden, so a model cannot simply interpolate from neighbours.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from computers.ml.sequences import Sequence

__all__ = ["FRACTION", "MODES", "Masked", "apply_mask"]

FRACTION = (0.3, 0.5)
MODES = ("random", "decade")
DECADE = 10.0  # years


@dataclass(frozen=True)
class Masked:
    seq: Sequence
    hidden: np.ndarray  # True where the token's value and error are hidden; never padding
    value_in: np.ndarray  # the value the model sees: 0 where hidden
    err_in: np.ndarray  # the error the model sees: 0 where hidden
    mode: str


def _decade_block(t: np.ndarray, real: np.ndarray, n_target: int, rng: np.random.Generator) -> np.ndarray:
    """Indices of a contiguous block of about a decade with at most `n_target` real tokens.

    The block starts at a random token. If a decade would hold more than the target, it is
    cut to the first `n_target` tokens of the decade.
    """
    idx = np.flatnonzero(real)
    start = int(rng.choice(idx))
    inside = idx[(t[idx] >= t[start]) & (t[idx] < t[start] + DECADE)]
    return inside[:n_target]


def apply_mask(
    seq: Sequence,
    rng: np.random.Generator,
    *,
    mode: str | None = None,
    fraction: float | None = None,
) -> Masked:
    """Hide a fraction of the real tokens. `mode` and `fraction` are drawn when not given."""
    if mode is None:
        mode = MODES[int(rng.integers(2))]
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    f = float(rng.uniform(*FRACTION)) if fraction is None else fraction
    real = ~seq.pad
    n = int(real.sum())
    hidden = np.zeros(len(seq.pad), dtype=bool)
    n_target = min(max(round(f * n), 1 if n else 0), n)
    if n_target:
        if mode == "decade":
            block = _decade_block(seq.t, real, n_target, rng)
            hidden[block] = True
        rest = np.flatnonzero(real & ~hidden)
        need = n_target - int(hidden.sum())
        if need > 0 and len(rest):
            hidden[rng.choice(rest, size=min(need, len(rest)), replace=False)] = True
    value_in = np.where(hidden, 0.0, seq.value)
    err_in = np.where(hidden, 0.0, seq.err)
    return Masked(seq, hidden, value_in, err_in, mode)
