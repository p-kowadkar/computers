"""The named tags of L1 cleaning (plan 6.1). Nothing in the code uses a bare bit number.

A tag says what cleaning found about a measurement. Some tags reject the point
(`REJECTING`); the rest only flag it, and a later stage decides what to do.
"""

from __future__ import annotations

import enum

__all__ = [
    "AFLAG_HARD_MASK",
    "AFLAG_PLATE_EDGE",
    "AFLAG_SOFT_MASK",
    "AFLAG_STANDARD_MASK",
    "KIND_METEOR",
    "KIND_NARROW",
    "KIND_PATROL",
    "KIND_UNKNOWN",
    "REJECTING",
    "REJECTING_FOR_OUTBURST",
    "Tag",
]


class Tag(enum.IntFlag):
    STD_HARD = 1 << 0  # a hard standard AFLAGS bit: HIGH_BACKGROUND or CLOSE_TO_LIMITING
    STD_SOFT = 1 << 1  # only soft standard bits: LARGE_ISO_RMS, LARGE_LOCAL_SMOOTH_RMS, BIN_DRAD_UNKNOWN
    ASTROMETRY = 1 << 2  # farther than the astrometry limit from the star's median position
    METEOR_REJECTED = 1 << 3  # meteor series, under the "reject" policy
    NON_BLUE = 1 << 4  # the plate's colour term says red, yellow or unset (0.0)
    EMULSION_UNKNOWN = 1 << 5  # the plate has no colour term
    BLEND_SUSPECT = 1 << 6  # blended_mag differs from the catalog magnitude
    SHAPE_OUTLIER = 1 << 7  # image shape far from stars of similar brightness on the plate
    PLATE_EDGE = 1 << 8  # DASCH's outermost radial bin
    DEEP_LIMIT_SUSPECT = 1 << 9  # a non-detection far deeper than the star's usual brightness
    UNKNOWN_SERIES = 1 << 10  # the series is not in the series table
    SOURCE_MERGED = 1 << 11  # the point came from a neighbouring catalog source
    NO_OFFSET = 1 << 12  # detrending had no plate offset for this point


# Tags that remove a point from the good set.
REJECTING = int(Tag.STD_HARD | Tag.STD_SOFT | Tag.ASTROMETRY | Tag.METEOR_REJECTED)
# The outburst trigger also keeps points rejected only by the soft standard bits (W4).
REJECTING_FOR_OUTBURST = int(Tag.STD_HARD | Tag.ASTROMETRY | Tag.METEOR_REJECTED)

# DASCH AFLAGS bits (daschlab.photometry.AFlags, version 1.1.0).
AFLAG_HARD_MASK = (1 << 6) | (1 << 13)  # HIGH_BACKGROUND, CLOSE_TO_LIMITING
AFLAG_SOFT_MASK = (1 << 11) | (1 << 12) | (1 << 15)  # LARGE_ISO_RMS, LARGE_LOCAL_SMOOTH_RMS, BIN_DRAD_UNKNOWN
AFLAG_STANDARD_MASK = AFLAG_HARD_MASK | AFLAG_SOFT_MASK
AFLAG_PLATE_EDGE = 1 << 14  # RADIAL_BIN_9

# Series kind codes stored in a curve (see data/series.py).
KIND_UNKNOWN, KIND_NARROW, KIND_PATROL, KIND_METEOR = 0, 1, 2, 3
