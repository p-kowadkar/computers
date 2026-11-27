"""Run L1 on benchmark hosts: cleaned hosts, and a detector that detrends before it decides.

A host in L1 mode is a patrol star's cleaned curve with its rejected rows dropped (good
detections and non-detections remain). An injection is applied to those rows, and the
injected curve is detrended with the offsets of the star's un-injected pool, so no
comparison star is ever injected. The decision rule is the trivial first-decade,
last-decade test: this module measures what L1 does to it, not a new detector.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from computers.bench.harness import HostCurve
from computers.bench.trivial import TrivialDetector
from computers.l1.context import TileContext
from computers.l1.detrend import detrended_magnitudes, offsets_for

__all__ = ["L1Detector", "l1_hosts"]


def l1_hosts(ctx: TileContext, refs: Iterable[int]) -> dict[int, HostCurve]:
    """Cleaned host curves, with rejected rows dropped, carrying the `Curve` they came from."""
    out: dict[int, HostCurve] = {}
    for ref in refs:
        usable = ctx.curve(ref).usable_rows()
        out[ref] = HostCurve(ref, usable.time, usable.mag, usable.limit, usable)
    return out


class L1Detector:
    """The trivial detector, applied to a host's cleaned curve and optionally its detrended curve."""

    takes_host = True

    def __init__(self, ctx: TileContext, *, detrended: bool = True, threshold_mag: float | None = None) -> None:
        self.ctx, self.detrended = ctx, detrended
        self.rule = TrivialDetector(threshold_mag)
        self._columns: dict[int, np.ndarray] = {}

    def magnitudes(self, host: HostCurve, mag: np.ndarray) -> np.ndarray:
        """What the rule sees: the magnitudes as they are, or detrended against the host's pool."""
        if not self.detrended:
            return mag
        if host.curve is None:
            raise ValueError("an L1 detector needs hosts built by l1_hosts")
        if host.host_id not in self._columns:
            self._columns[host.host_id] = self.ctx.columns_of(host.curve)
        offsets = offsets_for(self.ctx, host.host_id)
        return detrended_magnitudes(offsets, self._columns[host.host_id], mag)

    def __call__(self, host: HostCurve, mag: np.ndarray, limit: np.ndarray) -> bool:
        return self.rule(host.time, self.magnitudes(host, mag), limit)
