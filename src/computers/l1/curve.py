"""The cleaned curve of one star: its measurements plus what cleaning found about each.

A curve has one entry per measurement row, sorted by time. Cleaning never deletes a row;
it ORs tags into `tags`. Which rows count is read from the tags, so the same curve serves
the benchmark (which swaps in injected magnitudes) and the triggers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from computers.l1.tags import REJECTING, REJECTING_FOR_OUTBURST, Tag
from computers.thresholds import THRESHOLDS

__all__ = ["Curve"]

_ARRAYS = ("time", "mag", "limit", "exposure_id", "series", "kind", "tags")


@dataclass(frozen=True)
class Curve:
    ref_number: int
    time: np.ndarray  # HJD days, ascending
    mag: np.ndarray  # NaN marks a non-detection
    limit: np.ndarray  # the plate's limiting magnitude
    exposure_id: np.ndarray  # strings
    series: np.ndarray  # strings
    kind: np.ndarray  # uint8 series kind code (see tags.KIND_*)
    tags: np.ndarray  # uint64 mask of `Tag` bits

    def __post_init__(self) -> None:
        n = len(self.time)
        for name in _ARRAYS:
            arr = getattr(self, name)
            if len(arr) != n:
                raise ValueError(f"{name} has {len(arr)} rows, expected {n}")
            arr.setflags(write=False)
        if n > 1 and np.any(np.diff(self.time) < 0):
            raise ValueError("a curve must be sorted by time")

    def __len__(self) -> int:
        return len(self.time)

    # ------------------------------------------------------------------- row sets

    @property
    def detected(self) -> np.ndarray:
        return np.isfinite(self.mag)

    @property
    def rejected(self) -> np.ndarray:
        """Rows removed by a rejecting tag, detections and non-detections alike."""
        return (self.tags & np.uint64(REJECTING)) != 0

    @property
    def good(self) -> np.ndarray:
        """Detections that survive every rejecting tag."""
        return self.detected & ~self.rejected

    @property
    def good_for_outburst(self) -> np.ndarray:
        """As `good`, but points rejected only by the soft standard bits stay (decision 0006)."""
        keep = (self.tags & np.uint64(REJECTING_FOR_OUTBURST)) == 0
        return self.detected & keep

    @property
    def usable(self) -> np.ndarray:
        """Good detections and non-detections that no tag removed."""
        return ~self.rejected

    @property
    def n_good(self) -> int:
        return int(self.good.sum())

    @property
    def kill_reason(self) -> str | None:
        """`too_few_points` when the curve has fewer than `min_good_points` good detections."""
        return "too_few_points" if self.n_good < int(THRESHOLDS["min_good_points"].value) else None

    def has(self, tag: Tag) -> np.ndarray:
        return (self.tags & np.uint64(int(tag))) != 0

    # --------------------------------------------------------------- derived curves

    def subset(self, mask: np.ndarray) -> Curve:
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != self.time.shape:
            raise ValueError("mask must have one entry per row")
        return Curve(self.ref_number, *(getattr(self, name)[mask].copy() for name in _ARRAYS))

    def usable_rows(self) -> Curve:
        """The curve an injection or a detector works on."""
        return self.subset(self.usable)

    def with_magnitudes(self, mag: np.ndarray) -> Curve:
        """The same rows and tags with new magnitudes (for example after an injection).

        Tags describe the plate and the measurement, so they are not recomputed here.
        """
        mag = np.array(mag, dtype=float)
        if mag.shape != self.mag.shape:
            raise ValueError("mag must have one entry per row")
        return replace(self, mag=mag)

    def with_tag(self, mask: np.ndarray, tag: Tag) -> Curve:
        """A copy with `tag` ORed into the masked rows. Applying it twice changes nothing."""
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != self.time.shape:
            raise ValueError("mask must have one entry per row")
        tags = self.tags.copy()
        tags[mask] |= np.uint64(int(tag))
        return replace(self, tags=tags)

    def tag_counts(self) -> dict[str, int]:
        """How many rows carry each tag."""
        return {t.name: int(self.has(t).sum()) for t in Tag}
