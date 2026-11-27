"""The Gaia DR4 check script, against a fake archive (it cannot be run for real before the release)."""
import importlib.util
import json
from pathlib import Path

import pytest
import requests

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_gaia_dr4.py"


@pytest.fixture
def mod(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_gaia_dr4", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "OUT", tmp_path / "report.json")
    monkeypatch.chdir(tmp_path)  # no local tile tables: the built-in centres are used
    return m


def fake(mapping):
    def adql(query, fmt="json"):
        for key, reply in mapping.items():
            if key in query:
                return 200, {"data": reply}
        return 200, {"data": []}

    return adql


def test_before_the_release_there_is_no_dr4_schema_and_it_says_so(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "adql", fake({"TAP_SCHEMA.schemas": [["gaiadr3"]]}))
    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "No gaiadr4 schema yet" in out and json.loads(mod.OUT.read_text())["schemas"] == ["gaiadr3"]


def test_an_unreachable_archive_is_reported_and_not_a_crash(mod, monkeypatch, capsys):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(mod, "adql", boom)
    assert mod.main() == 1
    assert "NOT reachable" in capsys.readouterr().out and json.loads(mod.OUT.read_text())["reachable"] is False


def test_after_the_release_it_lists_tables_columns_and_a_cone_count_per_tile(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "adql", fake({
        "TAP_SCHEMA.schemas": [["gaiadr4"]],
        "TAP_SCHEMA.tables": [["gaiadr4.gaia_source"], ["gaiadr4.all_source_photometry"]],
        "TAP_SCHEMA.columns": [["source_id", "long"], ["ra", "double"], ["dec", "double"], ["phot_g_mean_mag", "double"]],
        "COUNT(*)": [[1234]],
    }))
    assert mod.main() == 0
    rep = json.loads(mod.OUT.read_text())
    assert rep["tables"] == ["gaiadr4.all_source_photometry", "gaiadr4.gaia_source"]
    assert rep["gaia_source_has"]["source_id"] is True and rep["gaia_source_has"]["parallax"] is False
    assert set(rep["cones"]) == {"m44", "tcrb", "tabby"} and rep["cones"]["tcrb"]["sources_g_lt_16"] == 1234
    assert rep["time_series_tables"] == ["gaiadr4.all_source_photometry"] and "gaia_source" in capsys.readouterr().out
