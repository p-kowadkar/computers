"""Patrol tiles and how to cover them with DASCH catalog queries.

A tile is a 2 by 2 degree on-sky square. The catalog endpoint takes a centre and a
"radius" and returns a true on-sky square of half-width `radius`: its RA extent is
stretched by 1/cos(dec). Radius is capped well below 1 degree in practice (3,000
arcsec and above time out), so a tile is four boxes. See
docs/decisions/0001-sprint0-findings.md, W1, and .kiro/specs/s1b-thin-client/.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "BOX_RADIUS_ARCSEC",
    "HALF_SIDE_DEG",
    "TILES",
    "Box",
    "Tile",
    "catalog_boxes",
    "exposure_positions",
    "in_box",
    "in_tile",
]

HALF_SIDE_DEG = 1.0  # a tile is 2 degrees on a side
BOX_RADIUS_ARCSEC = 1850.0  # 1800 plus a 50 arcsec overlap between neighbouring boxes
_MAX_ABS_DEC = 85.0  # the cos(dec) stretch is meaningless near the poles
_EDGE_TOLERANCE_DEG = 1e-9  # edges count as inside, despite floating-point rounding


@dataclass(frozen=True)
class Tile:
    name: str
    ra0: float  # degrees
    dec0: float  # degrees

    def __post_init__(self) -> None:
        if not 0.0 <= self.ra0 < 360.0:
            raise ValueError(f"{self.name}: ra0 must be in [0, 360)")
        if abs(self.dec0) > _MAX_ABS_DEC - HALF_SIDE_DEG:
            raise ValueError(f"{self.name}: too close to a pole for this geometry")


@dataclass(frozen=True)
class Box:
    ra: float
    dec: float
    radius_arcsec: float


# M44's centre is the plan's approximate figure (3.4). The other two are the resolved
# positions of T CrB and KIC 8462852 from Sprint 0.
TILES: dict[str, Tile] = {
    "m44": Tile("m44", 130.1, 19.7),
    "tcrb": Tile("tcrb", 239.87568, 25.92017),
    "tabby": Tile("tabby", 301.56439, 44.45689),
}


def _wrap_ra(ra: float) -> float:
    return ra % 360.0


def _dra(ra: np.ndarray | float, ra0: float) -> np.ndarray | float:
    """RA difference in degrees, wrapped into [-180, 180)."""
    return (np.asarray(ra, dtype=float) - ra0 + 180.0) % 360.0 - 180.0


def _ra_offset(dec_deg: float, on_sky_deg: float) -> float:
    return on_sky_deg / math.cos(math.radians(dec_deg))


def catalog_boxes(tile: Tile) -> list[Box]:
    """Four query boxes covering the tile, centred half a degree from its centre."""
    boxes = []
    for sign_dec in (-1, 1):
        dec_b = tile.dec0 + sign_dec * HALF_SIDE_DEG / 2
        for sign_ra in (-1, 1):
            ra_b = _wrap_ra(tile.ra0 + sign_ra * _ra_offset(dec_b, HALF_SIDE_DEG / 2))
            boxes.append(Box(ra_b, dec_b, BOX_RADIUS_ARCSEC))
    return boxes


def exposure_positions(tile: Tile) -> list[tuple[float, float]]:
    """The tile centre and its four corners, for `queryexps`."""
    out = [(tile.ra0, tile.dec0)]
    for sign_dec in (-1, 1):
        dec_c = tile.dec0 + sign_dec * HALF_SIDE_DEG
        for sign_ra in (-1, 1):
            out.append((_wrap_ra(tile.ra0 + sign_ra * _ra_offset(dec_c, HALF_SIDE_DEG)), dec_c))
    return out


def in_tile(tile: Tile, ra: np.ndarray | float, dec: np.ndarray | float) -> np.ndarray | bool:
    """True where a position lies inside the tile. Works on scalars and arrays."""
    dec = np.asarray(dec, dtype=float)
    x = np.abs(_dra(ra, tile.ra0)) * math.cos(math.radians(tile.dec0))
    limit = HALF_SIDE_DEG + _EDGE_TOLERANCE_DEG
    inside = (np.abs(dec - tile.dec0) <= limit) & (x <= limit)
    return bool(inside) if inside.ndim == 0 else inside


def in_box(box: Box, ra: np.ndarray | float, dec: np.ndarray | float) -> np.ndarray | bool:
    """Whether a position is inside a query box, as the API defines the box."""
    half = box.radius_arcsec / 3600.0
    dec = np.asarray(dec, dtype=float)
    x = np.abs(_dra(ra, box.ra)) * math.cos(math.radians(box.dec))
    inside = (np.abs(dec - box.dec) <= half) & (x <= half)
    return bool(inside) if inside.ndim == 0 else inside
