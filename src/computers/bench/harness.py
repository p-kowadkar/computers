"""Run a detector over planted signals and un-injected hosts (plan 10.2 to 10.4).

A detector is any callable `(time_jd, mag, limit_mag) -> bool`, where `mag` is NaN for a
non-detection. A detector with `takes_host = True` is called `(host, mag, limit_mag)` instead,
so it can use the host's cleaned `Curve` (the L1 detector does). A planted signal counts as recovered when the detector flags the injected
curve and did not already flag the host without it: a host that raises an alarm on its
own noise would otherwise be credited with every signal. Detectors that report an epoch
will tighten this when the real triggers arrive (Sprint 4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from computers.bench.inject import SignalSpec, inject
from computers.bench.metrics import Outcome, Rate, false_alarm_rate
from computers.l1.curve import Curve

__all__ = ["Detector", "HostCurve", "false_alarms", "run_injections"]

Detector = Callable[..., bool]


@dataclass(frozen=True)
class HostCurve:
    host_id: int
    time: np.ndarray  # HJD days, ascending
    mag: np.ndarray  # NaN marks a non-detection
    limit: np.ndarray
    curve: Curve | None = None  # the cleaned curve these arrays came from, for L1 detectors


def _call(detector: Detector, host: HostCurve, mag: np.ndarray) -> bool:
    if getattr(detector, "takes_host", False):
        return bool(detector(host, mag, host.limit))
    return bool(detector(host.time, mag, host.limit))


def run_injections(
    hosts: Mapping[int, HostCurve], specs: Sequence[SignalSpec], detector: Detector
) -> list[Outcome]:
    """Plant every spec in its host and record whether the detector recovered it."""
    baseline: dict[int, bool] = {}
    out: list[Outcome] = []
    for spec in specs:
        host = hosts[spec.host_id]
        if host.host_id not in baseline:
            baseline[host.host_id] = _call(detector, host, host.mag)
        flagged = _call(detector, host, inject(host.time, host.mag, host.limit, spec.signal).mag)
        out.append(
            Outcome(
                spec.host_id,
                spec.family,
                spec.amplitude_index,
                spec.signal.amplitude,
                spec.expected_missed,
                flagged and not baseline[host.host_id],
            )
        )
    return out


def false_alarms(hosts: Mapping[int, HostCurve], detector: Detector) -> Rate:
    """How many un-injected constant hosts the detector flags."""
    flagged = sum(_call(detector, h, h.mag) for h in hosts.values())
    return false_alarm_rate(flagged, len(hosts))
