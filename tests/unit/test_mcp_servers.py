"""The MCP servers, in process, with no network (spec S8 R5). Needs the `mcp` group."""
import asyncio
import socket

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session as connect
from synth_tile import write_tile_files

from computers.data import silver
from computers.mcp import catalogs_server, dasch_server
from computers.mcp._cache import MAX_LIMIT, Store


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    write_tile_files(d, "m44", n_star=6, n_plate=300, noise=0.04, plate_sigma=0.05, seed=3)
    vsx = pa.table({
        "tile": ["m44"] * 3, "oid": [1, 2, 3], "name": ["A Cnc", "B Cnc", "C Cnc"], "var_flag": [1, 1, 1],
        "ra_deg": [130.000, 130.010, 131.0], "dec_deg": [20.0, 20.0, 21.0], "vtype": ["EA", "M", "SR"],
        "max_mag": [9.0, 10.0, 11.0], "min_mag": [10.0, 12.0, 11.5], "min_is_amplitude": [False] * 3,
        "amplitude_mag": [1.0, 2.0, 0.5], "period_d": [2.5, None, 30.0],
    })  # fmt: skip
    p = silver.silver_path(d, "vsx", "m44")
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(vsx, p)
    return d


def call(server, tool, args):
    async def go():
        async with connect(server._mcp_server) as c:
            r = await c.call_tool(tool, args)
            return r.structuredContent, r.isError

    return asyncio.run(go())


def refs(data):
    return silver.read_table(silver.silver_path(data, "stars", "m44")).column("ref_number").to_pylist()


def test_the_servers_register_only_tools_whose_data_exists():
    async def names(server):
        async with connect(server._mcp_server) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert asyncio.run(names(dasch_server.build_server())) == {"get_lightcurve", "list_exposures", "get_plate_info"}
    assert asyncio.run(names(catalogs_server.build_server())) == {"vsx_search"}
    for absent in ("get_cutout", "get_plate_photo_url", "simbad_lookup", "gaia_source", "gaia_timeseries", "ztf_lightcurve"):
        assert absent not in asyncio.run(names(dasch_server.build_server())) | asyncio.run(names(catalogs_server.build_server()))


def test_a_lightcurve_is_bounded_in_time_order_and_says_it_was_thinned(data):
    srv = dasch_server.build_server(Store(data))
    out, err = call(srv, "get_lightcurve", {"tile": "m44", "ref_number": refs(data)[0], "limit": 50})
    assert not err and out["n_total"] == 300 and out["n_returned"] == 50 and out["thinned"] is True
    years = [r["year"] for r in out["rows"]]
    assert years == sorted(years) and len(out["rows"]) == 50
    full, _ = call(srv, "get_lightcurve", {"tile": "m44", "ref_number": refs(data)[0], "limit": 100000})
    assert full["n_returned"] <= MAX_LIMIT and full["n_returned"] == 300 and full["thinned"] is False  # 300 fits under the hard maximum


def test_the_limit_has_a_hard_maximum_even_when_a_caller_asks_for_more(tmp_path):
    d = tmp_path / "d"
    write_tile_files(d, "m44", n_star=3, n_plate=900, noise=0.04, plate_sigma=0.05, seed=1)
    out, _ = call(dasch_server.build_server(Store(d)), "get_lightcurve", {"tile": "m44", "ref_number": refs(d)[0], "limit": 10_000})
    assert out["n_total"] == 900 and out["n_returned"] <= MAX_LIMIT and out["thinned"] is True


def test_year_and_series_filters_apply_before_thinning(data):
    srv = dasch_server.build_server(Store(data))
    ref = refs(data)[0]
    out, _ = call(srv, "get_lightcurve", {"tile": "m44", "ref_number": ref, "start_year": 1930, "end_year": 1940, "limit": 500})
    assert out["n_total"] == out["n_returned"] > 0 and all(1930 <= r["year"] <= 1940 for r in out["rows"])
    one = out["rows"][0]["series"]
    out2, _ = call(srv, "get_lightcurve", {"tile": "m44", "ref_number": ref, "series": one, "limit": 500})
    assert {r["series"] for r in out2["rows"]} == {one}


def test_errors_are_structured_and_say_what_to_pull(data):
    srv = dasch_server.build_server(Store(data))
    out, _ = call(srv, "get_lightcurve", {"tile": "tabby", "ref_number": 1})
    assert out["error"] == "tile_not_served" and out["served"] == ["m44", "tcrb"]
    out, _ = call(srv, "get_lightcurve", {"tile": "tcrb", "ref_number": 1})  # served, but not pulled into this data directory
    assert out["error"] == "not_local" and out["pull_with"] == "computers pull --tile tcrb"
    out, _ = call(srv, "get_lightcurve", {"tile": "m44", "ref_number": 123})
    assert out["error"] == "star_not_found"
    out, _ = call(srv, "get_plate_info", {"exposure_id": "zz99999e0s0"})
    assert out["error"] == "not_local" and "zz99999e0s0" in out["missing"]


def test_the_held_out_tile_is_not_served_unless_it_is_listed(data, monkeypatch):
    assert Store(data).tiles == ("m44", "tcrb")
    monkeypatch.setenv("COMPUTERS_MCP_TILES", "m44,tcrb,tabby")
    assert Store(data).tiles == ("m44", "tcrb", "tabby")


def test_plate_info_and_the_exposure_list_read_the_exposures_table(data):
    srv = dasch_server.build_server(Store(data))
    lst, _ = call(srv, "list_exposures", {"tile": "m44", "limit": 20})
    assert lst["n_returned"] == 20 and lst["n_total"] > 20 and lst["thinned"] is True
    one = lst["rows"][0]["exposure_id"]
    info, _ = call(srv, "get_plate_info", {"exposure_id": one})
    assert info["tile"] == "m44" and info["exposure_id"] == one


def test_no_tool_touches_the_network(data, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a tool tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    srv = dasch_server.build_server(Store(data))
    ref = refs(data)[0]
    for tool, args in (("get_lightcurve", {"tile": "m44", "ref_number": ref}), ("list_exposures", {"tile": "m44"}), ("get_plate_info", {"exposure_id": "x"})):
        out, err = call(srv, tool, args)
        assert not err and out
    assert call(catalogs_server.build_server(Store(data)), "vsx_search", {"ra": 130.0, "dec": 20.0})[0]["n_total"] >= 1


def test_vsx_search_returns_the_nearest_first_within_the_radius(data):
    srv = catalogs_server.build_server(Store(data))
    out, _ = call(srv, "vsx_search", {"ra": 130.0, "dec": 20.0, "radius_arcsec": 60.0})
    assert [r["name"] for r in out["rows"]] == ["A Cnc", "B Cnc"] and out["rows"][0]["sep_arcsec"] == 0.0
    assert 30 < out["rows"][1]["sep_arcsec"] < 40  # 0.010 degrees of RA at dec 20 is about 34 arcsec
    near, _ = call(srv, "vsx_search", {"ra": 130.0, "dec": 20.0, "radius_arcsec": 5.0})
    assert [r["name"] for r in near["rows"]] == ["A Cnc"]
    none, _ = call(srv, "vsx_search", {"ra": 10.0, "dec": -40.0, "radius_arcsec": 600.0})
    assert none["n_total"] == 0 and none["rows"] == []
    missing, _ = call(srv, "vsx_search", {"ra": 1.0, "dec": 1.0, "tile": "tcrb"})
    assert missing["error"] == "not_local"
