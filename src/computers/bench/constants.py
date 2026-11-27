"""Pick quiet stars to host injections and to measure false alarms (plan 10.1).

A constant star must be quiet in two ways, both measured on good detections only
(`reject == 0`, a measured magnitude):

* **within** scatter: how far points stray from the median of their own 5-year block,
  a robust measure of plate-to-plate noise;
* **between** scatter: the 10th-to-90th percentile range of the 5-year block medians,
  in units of sigma. It sees what a slow trend, a step or a long wave does to a curve.
  A median absolute deviation would miss a step that splits the century in half, because
  the median then lies on one side of it.

A star is compared with stars of similar brightness in its own tile: the reference is
the median of the statistic over the `REFERENCE_STARS` stars nearest in magnitude. It is
quiet when both of its scatters are at most `constant_scatter_factor` times that
reference. Stars with a VSX entry near them, or with too few points, are never constants.

Every star gets a first reason, so an exclusion can always be explained.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from computers.l1 import scatter as _scatter
from computers.l1.scatter import (
    BLOCK_YEARS,
    StarStats,
    star_stats,
    tile_stats,
)

__all__ = [
    "BLOCK_YEARS",
    "REASONS",
    "StarStats",
    "select_constants",
    "star_stats",
    "tile_stats",
]

REASONS = _scatter.REASONS


def select_constants(
    stars: pa.Table,
    stats: dict[int, StarStats],
    vsx_match: pa.Table,
) -> pa.Table:
    """One row per star of a single tile: its statistics, and whether it is a constant.

    `stars` is the `stars` table and `vsx_match` the matching `vsx_match` table. The
    classification itself is `computers.l1.scatter.quiet_reasons`, shared with detrending.
    """
    refs = [int(r) for r in stars.column("ref_number").to_pylist()]
    mags = np.asarray(stars.column("apass_b").to_numpy(zero_copy_only=False), dtype=float)
    sep = {
        int(r): s
        for r, s in zip(
            vsx_match.column("ref_number").to_pylist(),
            vsx_match.column("sep_arcsec").to_pylist(),
            strict=True,
        )
    }
    got = [stats.get(r, _scatter._EMPTY) for r in refs]
    result = _scatter.quiet_reasons(refs, mags, stats, sep)

    def col(name: str) -> list:
        return [getattr(g, name) for g in got]

    return pa.table(
        {
            "ref_number": pa.array(refs, pa.uint64()),
            "tile": stars.column("tile"),
            "apass_b": pa.array(mags, pa.float32()),
            "n_good": pa.array(col("n_good"), pa.int32()),
            "n_blocks": pa.array(col("n_blocks"), pa.int32()),
            "first_year": pa.array(col("first_year"), pa.float64()),
            "last_year": pa.array(col("last_year"), pa.float64()),
            "scatter_within": pa.array(col("scatter_within"), pa.float32()),
            "scatter_between": pa.array(col("scatter_between"), pa.float32()),
            "ref_within": pa.array(result.ref_within, pa.float32()),
            "ref_between": pa.array(result.ref_between, pa.float32()),
            "reason": pa.array(result.reason, pa.string()),
            "is_constant": pa.array(result.quiet, pa.bool_()),
        }
    )
