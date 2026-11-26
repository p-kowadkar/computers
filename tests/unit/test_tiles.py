import math

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from computers.data import tiles
from computers.data.tiles import Box, Tile

ALL = list(tiles.TILES.values())


def cosd(x):
    return math.cos(math.radians(x))


# ------------------------------------------------------------------- definitions


def test_the_three_tiles_are_defined():
    assert set(tiles.TILES) == {"m44", "tcrb", "tabby"}
    assert (tiles.TILES["m44"].ra0, tiles.TILES["m44"].dec0) == (130.1, 19.7)
    assert tiles.TILES["tcrb"].dec0 == pytest.approx(25.92017)
    assert tiles.TILES["tabby"].ra0 == pytest.approx(301.56439)


@pytest.mark.parametrize(("ra", "dec"), [(-1.0, 10.0), (360.0, 10.0), (10.0, 89.0), (10.0, -89.0)])
def test_invalid_tiles_are_rejected(ra, dec):
    with pytest.raises(ValueError):
        Tile("bad", ra, dec)


# ------------------------------------------------------------------- membership


@pytest.mark.parametrize("tile", ALL)
def test_centre_and_edges_are_inside(tile):
    assert tile.dec0 > 0
    assert tiles.in_tile(tile, tile.ra0, tile.dec0)
    assert tiles.in_tile(tile, tile.ra0, tile.dec0 + 1.0)  # edge is included
    assert tiles.in_tile(tile, tile.ra0, tile.dec0 - 1.0)
    edge = 1.0 / cosd(tile.dec0)
    assert tiles.in_tile(tile, tile.ra0 + edge * 0.999999, tile.dec0)
    assert tiles.in_tile(tile, tile.ra0 - edge * 0.999999, tile.dec0)


@pytest.mark.parametrize("tile", ALL)
def test_just_outside_each_edge_is_outside(tile):
    edge = 1.0 / cosd(tile.dec0)
    assert not tiles.in_tile(tile, tile.ra0, tile.dec0 + 1.0001)
    assert not tiles.in_tile(tile, tile.ra0, tile.dec0 - 1.0001)
    assert not tiles.in_tile(tile, tile.ra0 + edge * 1.0001, tile.dec0)
    assert not tiles.in_tile(tile, tile.ra0 - edge * 1.0001, tile.dec0)


def test_in_tile_works_on_arrays():
    t = tiles.TILES["tcrb"]
    ra = np.array([t.ra0, t.ra0 + 5, t.ra0])
    dec = np.array([t.dec0, t.dec0, t.dec0 + 3])
    assert list(tiles.in_tile(t, ra, dec)) == [True, False, False]


def test_ra_wraps_around_zero():
    t = Tile("wrap", 359.9, 10.0)
    assert tiles.in_tile(t, 0.4, 10.0)  # 0.5 degrees of RA east of the centre
    assert tiles.in_tile(t, 359.5, 10.0)
    assert not tiles.in_tile(t, 5.0, 10.0)
    boxes = tiles.catalog_boxes(t)
    assert all(0.0 <= b.ra < 360.0 for b in boxes)


# ------------------------------------------------------------------------ boxes


@pytest.mark.parametrize("tile", ALL)
def test_four_boxes_with_the_documented_radius(tile):
    boxes = tiles.catalog_boxes(tile)
    assert len(boxes) == 4
    assert {b.radius_arcsec for b in boxes} == {1850.0}
    assert len({(b.ra, b.dec) for b in boxes}) == 4


@pytest.mark.parametrize("tile", ALL)
def test_box_centres_sit_half_a_degree_from_the_tile_centre_on_the_sky(tile):
    for b in tiles.catalog_boxes(tile):
        assert abs(b.dec - tile.dec0) == pytest.approx(0.5)
        on_sky = abs(b.ra - tile.ra0) * cosd(b.dec)
        assert on_sky == pytest.approx(0.5, abs=1e-9)


def test_ra_offset_grows_with_declination():
    low = Tile("low", 100.0, 10.0)
    high = Tile("high", 100.0, 60.0)
    spread = lambda t: max(b.ra for b in tiles.catalog_boxes(t)) - min(
        b.ra for b in tiles.catalog_boxes(t)
    )
    assert spread(high) > spread(low) * 1.5


@pytest.mark.parametrize("tile", ALL)
def test_neighbouring_boxes_overlap(tile):
    """Edges must overlap, so no star on a seam is missed."""
    boxes = tiles.catalog_boxes(tile)
    half = tiles.BOX_RADIUS_ARCSEC / 3600
    south = [b for b in boxes if b.dec < tile.dec0]
    north = [b for b in boxes if b.dec > tile.dec0]
    assert south[0].dec + half > north[0].dec - half  # N and S boxes overlap in dec
    west, east = sorted(south, key=lambda b: b.ra)
    on_sky_gap = (east.ra - west.ra) * cosd(west.dec)
    assert on_sky_gap < 2 * half  # E and W boxes overlap in RA


@pytest.mark.parametrize("tile", ALL)
def test_corners_and_edge_midpoints_are_covered_by_a_box(tile):
    boxes = tiles.catalog_boxes(tile)
    for ra, dec in tiles.exposure_positions(tile):
        assert any(tiles.in_box(b, ra, dec) for b in boxes), (ra, dec)
    for dra, ddec in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        ra = tile.ra0 + dra / cosd(tile.dec0) * 0.9999
        dec = tile.dec0 + ddec * 0.9999
        assert any(tiles.in_box(b, ra, dec) for b in boxes)


@pytest.mark.parametrize("tile", ALL)
@given(x=st.floats(-1, 1), y=st.floats(-1, 1))
def test_every_point_in_a_tile_is_inside_some_box(tile, x, y):
    ra = tile.ra0 + x / cosd(tile.dec0)
    dec = tile.dec0 + y
    assert tiles.in_tile(tile, ra, dec)
    assert any(tiles.in_box(b, ra, dec) for b in tiles.catalog_boxes(tile))


@given(
    ra0=st.floats(0, 359.99),
    dec0=st.floats(-55, 55),  # at |dec| 58 the four boxes miss the tile corners; ours reach dec 44.5
    x=st.floats(-1, 1),
    y=st.floats(-1, 1),
)
def test_coverage_holds_for_any_tile_below_55_degrees(ra0, dec0, x, y):
    tile = Tile("any", ra0, dec0)
    ra = (tile.ra0 + x / cosd(tile.dec0)) % 360
    dec = tile.dec0 + y
    assert tiles.in_tile(tile, ra, dec)
    assert any(tiles.in_box(b, ra, dec) for b in tiles.catalog_boxes(tile))


def test_box_membership_uses_an_on_sky_square():
    b = Box(100.0, 60.0, 3600.0)  # one degree half-width
    assert tiles.in_box(b, 100.0 + 0.99 / cosd(60.0), 60.0)  # RA stretched by 2 at dec 60
    assert not tiles.in_box(b, 100.0 + 1.01 / cosd(60.0), 60.0)
    assert not tiles.in_box(b, 100.0, 61.01)


# ------------------------------------------------------------ exposure positions


@pytest.mark.parametrize("tile", ALL)
def test_five_exposure_positions_centre_and_corners(tile):
    pos = tiles.exposure_positions(tile)
    assert len(pos) == 5 and pos[0] == (tile.ra0, tile.dec0)
    for ra, dec in pos[1:]:
        assert abs(dec - tile.dec0) == pytest.approx(1.0)
        assert abs(ra - tile.ra0) * cosd(dec) == pytest.approx(1.0, abs=1e-9)
    assert len(set(pos)) == 5
