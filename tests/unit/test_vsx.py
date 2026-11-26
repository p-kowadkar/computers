import math

import pyarrow as pa
import pytest
import requests

from computers.data import schemas, vsx
from computers.data.snapshot import SnapshotStore
from computers.data.tiles import TILES

HEADER = ["_RAJ2000", "_DEJ2000", "OID", "Name", "V", "RAJ2000", "DEJ2000", "Type",
          "l_max", "max", "u_max", "n_max", "f_min", "l_min", "min", "u_min", "n_min",
          "l_Period", "Period", "u_Period", "Sp"]


def _row(oid, name, ra, dec, vtype="EA", mx="12.0", mn="15.0", u_min="", period="1.5", var="0"):
    d = dict.fromkeys(HEADER, "")
    d.update({"_RAJ2000": f"{ra:.7f}", "_DEJ2000": f"{dec:.7f}", "OID": str(oid), "Name": name,
              "V": var, "RAJ2000": f"{ra:.5f}", "DEJ2000": f"{dec:.5f}", "Type": vtype,
              "max": mx, "min": mn, "u_min": u_min, "Period": period})
    return "\t".join(d[h] for h in HEADER)


def tsv(rows, header=HEADER):
    head = "\t".join(header)
    units = "\t".join(["deg"] * len(header))
    dashes = "\t".join(["-----"] * len(header))
    return "\n".join(["#", "# VizieR", head, units, dashes, *rows, ""]).encode()


def filler(tile, n=vsx.MIN_ROWS, start=1000):
    return [_row(start + i, f"Gaia DR3 {i}", tile.ra0 + 0.001 * i, tile.dec0 + 0.002 * i, "ROT")
            for i in range(n)]


def good_body(tile):
    name = vsx.SENTINELS.get(tile.name, "Some Star")
    return tsv([_row(1, name, tile.ra0, tile.dec0), *filler(tile)])


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path / "data", client_version="test/1")


# ----------------------------------------------------------------------- parse


def test_parse_row_values_and_types():
    tile = TILES["m44"]
    body = tsv([_row(4821, "RY Cnc", 129.97758, 19.82192, "EA/SD", "12.860", "15.390", "", "1.092941")])
    t = vsx.parse_vsx(body, tile)
    schemas.validate("vsx", t)
    r = t.to_pylist()[0]
    assert (r["oid"], r["name"], r["vtype"], r["tile"]) == (4821, "RY Cnc", "EA/SD", "m44")
    assert r["amplitude_mag"] == pytest.approx(2.53, abs=1e-5)
    assert r["min_is_amplitude"] is False
    assert r["period_d"] == pytest.approx(1.092941)


def test_minimum_column_is_amplitude_when_flagged():
    t = vsx.parse_vsx(tsv([_row(5, "X", 130.1, 19.7, mx="9.0", mn="0.8", u_min=")")]), TILES["m44"])
    r = t.to_pylist()[0]
    assert r["min_is_amplitude"] is True and r["amplitude_mag"] == pytest.approx(0.8, abs=1e-6)


def test_missing_values_are_null_not_zero():
    t = vsx.parse_vsx(tsv([_row(5, "X", 130.1, 19.7, vtype="", mx="", mn="", period="")]), TILES["m44"])
    r = t.to_pylist()[0]
    assert r["vtype"] is None and r["amplitude_mag"] is None and r["period_d"] is None
    assert r["max_mag"] is None and r["min_mag"] is None


def test_type_containing_pipes_survives():
    t = vsx.parse_vsx(tsv([_row(5, "X", 130.1, 19.7, vtype="DSCT|GDOR|SXPHE")]), TILES["m44"])
    assert t.column("vtype")[0].as_py() == "DSCT|GDOR|SXPHE"


def test_rows_outside_the_tile_are_dropped():
    tile = TILES["m44"]
    inside = _row(1, "in", tile.ra0, tile.dec0 + 0.99)
    outside = _row(2, "out", tile.ra0, tile.dec0 + 1.04)
    t = vsx.parse_vsx(tsv([inside, outside]), tile)
    assert t.column("name").to_pylist() == ["in"]


@pytest.mark.parametrize(
    "body",
    [b"", b"<!doctype html><html>Page unavailable</html>", b"# only comments\n#\n",
     tsv([], header=["OID", "Name"])],
)
def test_bad_documents_raise(body):
    with pytest.raises(vsx.VsxFormatError):
        vsx.parse_vsx(body, TILES["m44"])


def test_ragged_row_raises():
    body = tsv([_row(1, "a", 130.1, 19.7) + "\textra"])
    with pytest.raises(vsx.VsxFormatError, match="fields"):
        vsx.parse_vsx(body, TILES["m44"])


def test_duplicate_oid_raises():
    body = tsv([_row(1, "a", 130.1, 19.7), _row(1, "b", 130.2, 19.7)])
    with pytest.raises(vsx.VsxFormatError, match="duplicate"):
        vsx.parse_vsx(body, TILES["m44"])


# -------------------------------------------------------------- check_answer


def test_short_answer_fails_check():
    tile = TILES["tabby"]
    t = vsx.parse_vsx(tsv(filler(tile, vsx.MIN_ROWS - 1)), tile)
    with pytest.raises(vsx.VsxFormatError, match="only"):
        vsx.check_answer(tile, t)


def test_missing_sentinel_fails_check():
    tile = TILES["m44"]
    t = vsx.parse_vsx(tsv(filler(tile)), tile)
    with pytest.raises(vsx.VsxFormatError, match="RY Cnc"):
        vsx.check_answer(tile, t)


def test_good_answer_passes_check():
    for tile in TILES.values():
        vsx.check_answer(tile, vsx.parse_vsx(good_body(tile), tile))


# ---------------------------------------------------------------------- pulling


class Fetcher:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, server, params):
        self.calls.append(server)
        a = self.answers[server]
        if isinstance(a, Exception):
            raise a
        return a


def test_pull_stores_and_second_pull_makes_no_call(store):
    tile = TILES["m44"]
    f = Fetcher({"a": (200, good_body(tile))})
    res = vsx.pull_vsx(tile, store, servers=["a"], fetch=f)
    assert res.server == "a" and f.calls == ["a"] and res.table.num_rows > vsx.MIN_ROWS
    again = vsx.pull_vsx(tile, store, servers=["a"], fetch=f)
    assert again.server is None and f.calls == ["a"]
    assert again.table.equals(res.table)


def test_pull_falls_back_and_reports_errors(store):
    tile = TILES["tcrb"]
    f = Fetcher({
        "a": requests.ConnectTimeout("slow"),
        "b": (404, b"<html>"),
        "c": (200, tsv(filler(tile))),  # empty of the sentinel: a sick mirror
        "d": (200, good_body(tile)),
    })
    res = vsx.pull_vsx(tile, store, servers=["a", "b", "c", "d"], fetch=f)
    assert res.server == "d" and f.calls == ["a", "b", "c", "d"]
    assert set(res.errors) == {"a", "b", "c"}
    assert "HTTP 404" in res.errors["b"] and "T CrB" in res.errors["c"]


def test_failed_answers_are_not_stored(store):
    tile = TILES["m44"]
    f = Fetcher({"a": (200, tsv(filler(tile)))})
    with pytest.raises(vsx.VsxError, match="m44") as exc:
        vsx.pull_vsx(tile, store, servers=["a"], fetch=f)
    assert "a" in str(exc.value)
    assert store.rows(vsx.SOURCE) == []


def test_a_stored_answer_is_reused_even_when_all_servers_fail(store):
    tile = TILES["m44"]
    vsx.pull_vsx(tile, store, servers=["a"], fetch=Fetcher({"a": (200, good_body(tile))}))
    down = Fetcher({"a": requests.ConnectionError("down")})
    assert vsx.pull_vsx(tile, store, servers=["a"], fetch=down).table.num_rows > 0
    assert down.calls == []


def test_query_params_are_a_box_around_the_tile():
    p = vsx.query_params(TILES["tabby"])
    assert p["-source"] == "B/vsx/vsx" and p["-out.max"] == "unlimited"
    assert p["-c.bd"] == "2.1x2.1" and p["-c"].startswith("301.56439 44.45689")


# ---------------------------------------------------------------- cross-match


def _stars(rows):
    return pa.table({
        "ref_number": pa.array([r[0] for r in rows], pa.uint64()),
        "ra_deg": [r[1] for r in rows], "dec_deg": [r[2] for r in rows],
        "tile": ["m44"] * len(rows),
    })


def test_crossmatch_nearest_entry_and_separation():
    tile = TILES["m44"]
    v = vsx.parse_vsx(tsv([_row(1, "near", 130.1, 19.7), _row(2, "far", 130.2, 19.7)]), tile)
    off = 2.0 / 3600  # 2 arcsec in dec
    m = vsx.crossmatch(_stars([(10, 130.1, 19.7 + off), (11, 130.5, 19.7)]), v)
    schemas.validate("vsx_match", m)
    a, b = m.to_pylist()
    assert (a["vsx_name"], a["vsx_oid"]) == ("near", 1)
    assert a["sep_arcsec"] == pytest.approx(2.0, abs=0.01)
    assert b["vsx_name"] is None and b["sep_arcsec"] is None


def test_crossmatch_ra_separation_uses_cos_dec_and_wraps():
    tile = TILES["m44"]
    v = vsx.parse_vsx(tsv([_row(1, "x", 130.1, 19.7)]), tile)
    d_ra = 10.0 / 3600 / math.cos(math.radians(19.7))  # 10 arcsec on the sky
    m = vsx.crossmatch(_stars([(1, 130.1 + d_ra, 19.7)]), v, radius_arcsec=11.0).to_pylist()[0]
    assert m["sep_arcsec"] == pytest.approx(10.0, abs=0.01)
    assert vsx.crossmatch(_stars([(1, 130.1 + d_ra, 19.7)]), v, radius_arcsec=9.0).to_pylist()[0]["vsx_oid"] is None


def test_crossmatch_default_radius_is_the_exclusion_radius():
    tile = TILES["m44"]
    v = vsx.parse_vsx(tsv([_row(1, "x", 130.1, 19.7)]), tile)
    inside = _stars([(1, 130.1, 19.7 + 29.0 / 3600)])
    outside = _stars([(1, 130.1, 19.7 + 31.0 / 3600)])
    assert vsx.crossmatch(inside, v).to_pylist()[0]["vsx_oid"] == 1
    assert vsx.crossmatch(outside, v).to_pylist()[0]["vsx_oid"] is None


def test_crossmatch_with_no_vsx_entries():
    v = vsx.parse_vsx(tsv([]), TILES["m44"])
    m = vsx.crossmatch(_stars([(1, 130.1, 19.7)]), v)
    assert m.to_pylist()[0]["vsx_oid"] is None


# ------------------------------------------------------------------- the command


def test_run_vsx_pull_writes_tables_and_matches(tmp_path):
    from computers.data import silver

    tile = TILES["m44"]
    stars = pa.table({
        "ref_number": pa.array([1, 2], pa.uint64()), "refcat": ["apass"] * 2, "ref_text": ["a", "b"],
        "gsc_bin_index": pa.array([1, 2], pa.uint32()),
        "ra_deg": [tile.ra0, tile.ra0 + 0.3], "dec_deg": [tile.dec0, tile.dec0],
        "pm_ra_masyr": pa.array([None, None], pa.float32()), "pm_dec_masyr": pa.array([None, None], pa.float32()),
        "apass_b": pa.array([12.0, 12.5], pa.float32()), "color": pa.array([None, None], pa.float32()),
        "num_matches": pa.array([100, 100], pa.uint32()), "tile": ["m44"] * 2,
        "n_points": pa.array([None, None], pa.uint32()),
    })
    silver.write_table("stars", stars, silver.silver_path(tmp_path, "stars", "m44"), snapshot_digest="x")
    lines = []
    code = vsx.run_vsx_pull(["m44"], tmp_path, servers=["a"],
                            fetch=Fetcher({"a": (200, good_body(tile))}), say=lines.append)
    assert code == 0
    match = silver.read_table(silver.silver_path(tmp_path, "vsx_match", "m44")).to_pylist()
    assert match[0]["vsx_name"] == "RY Cnc" and match[1]["vsx_name"] is None
    assert silver.read_table(silver.silver_path(tmp_path, "vsx", "m44")).num_rows > 50
    assert any("1 within 3 arcsec" in ln for ln in lines)


def test_run_vsx_pull_failure_writes_nothing_and_other_tiles_run(tmp_path):
    from computers.data import silver

    lines = []
    f = Fetcher({"a": (200, good_body(TILES["tcrb"]))})
    # m44 gets the T CrB answer: its rows fall outside the tile, so it fails the row check.
    code = vsx.run_vsx_pull(["m44", "tcrb"], tmp_path, servers=["a"], fetch=f, say=lines.append)
    assert code == 1
    assert not silver.silver_path(tmp_path, "vsx", "m44").exists()
    assert silver.silver_path(tmp_path, "vsx", "tcrb").exists()
    assert any("FAILED" in ln for ln in lines)


@pytest.mark.network
def test_live_vsx_reaches_tile_edges_and_reference_stars(tmp_path):
    store = SnapshotStore(tmp_path / "data", client_version="test/1")
    for name, sentinel in (("m44", "RY Cnc"), ("tcrb", "T CrB")):
        res = vsx.pull_vsx(TILES[name], store)
        names = set(res.table.column("name").to_pylist())
        assert sentinel in names
        dec = res.table.column("dec_deg").to_pylist()
        assert max(dec) > TILES[name].dec0 + 0.9 and min(dec) < TILES[name].dec0 - 0.9
