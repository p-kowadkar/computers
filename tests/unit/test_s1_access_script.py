"""The access-check script records failures; it never raises them (requirement R9.2)."""

import importlib.util
import json
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("s1_access_checks", ROOT / "scripts" / "s1_access_checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_working_check_is_recorded_with_its_result(script):
    r = script.run_check("x", "a thing", lambda: {"rows": 3}, "server.example")
    assert r["ok"] is True and r["result"] == {"rows": 3} and r["error"] is None
    assert r["server"] == "server.example" and r["seconds"] >= 0 and r["checked_utc"].endswith("Z")


def test_a_failing_check_is_recorded_not_raised(script):
    def boom():
        raise ConnectionError("no route to host")

    r = script.run_check("x", "a thing", boom)
    assert r["ok"] is False and r["result"] is None
    assert r["error"] == "ConnectionError: no route to host"


def test_a_hanging_check_times_out_and_is_recorded(script, monkeypatch):
    monkeypatch.setattr(script, "CHECK_TIMEOUT_S", 0.1)
    started = time.monotonic()
    r = script.run_check("x", "a thing", lambda: time.sleep(2) or {})
    assert r["ok"] is False and "timed out" in r["error"]
    assert time.monotonic() - started < 1.5  # it did not wait for the hung call


def test_long_error_messages_are_truncated(script):
    def boom():
        raise ValueError("x" * 5000)

    assert len(script.run_check("x", "a thing", boom)["error"]) < 300


def test_main_writes_every_result_and_exits_zero_even_when_checks_fail(script, tmp_path, monkeypatch, capsys):
    def boom():
        raise OSError("down")

    monkeypatch.setattr(
        script,
        "all_checks",
        lambda: [("good", "works", lambda: {"n": 1}, "a"), ("bad", "fails", boom, "b")],
    )
    out = tmp_path / "out" / "checks.json"
    assert script.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text())["results"]
    assert [(r["name"], r["ok"]) for r in data] == [("good", True), ("bad", False)]
    text = capsys.readouterr().out
    assert "[ok  ] good" in text and "[FAIL] bad" in text


def test_only_selects_by_name_prefix(script, tmp_path, monkeypatch):
    monkeypatch.setattr(
        script, "all_checks",
        lambda: [("vsx_a", "", dict, None), ("vsx_b", "", dict, None), ("ztf", "", dict, None)],
    )  # fmt: skip
    out = tmp_path / "c.json"
    script.main(["--only", "vsx", "--out", str(out)])
    assert [r["name"] for r in json.loads(out.read_text())["results"]] == ["vsx_a", "vsx_b"]


def test_the_real_check_list_covers_everything_the_spec_names(script):
    names = [c[0] for c in script.all_checks()]
    servers = {(c[0], c[3]) for c in script.all_checks()}
    for needed in ("dasch_health", "dasch_plate_class", "simbad", "ztf_faint", "ztf_bright", "gaia_dr3_esa",
                   "gaia_schemas_esa", "gaia_dr3_vizier"):  # fmt: skip
        assert needed in names, needed
    assert sum(n.startswith("vsx_") for n in names) == 6  # three stars on both VizieR servers
    for service in ("vsx_T CrB", "simbad", "gaia_dr3_vizier"):
        assert {s for n, s in servers if n == service} >= {"vizier.cfa.harvard.edu" if "vsx" in service or "vizier" in service else "simbad.harvard.edu"}


def test_no_check_sends_an_api_key(script):
    source = (ROOT / "scripts" / "s1_access_checks.py").read_text()
    assert "x-api-key" not in source and "STARGLASS_API_KEY" not in source


# --- an empty answer for something that must exist is a failure, not a pass -----------


def test_vsx_check_fails_on_an_empty_answer_such_as_vizier_sends_when_its_database_is_down(script, monkeypatch):
    pytest.importorskip("astroquery")
    from astroquery.utils import TableList
    from astroquery.vizier import VizierClass

    monkeypatch.setattr(VizierClass, "query_region", lambda self, *a, **k: TableList({}))
    result = script.run_check("vsx", "T CrB", lambda: script.check_vsx("vizier.cfa.harvard.edu", 239.9, 25.9))
    assert result["ok"] is False and "empty answer" in result["error"]
    assert "HTTP 200" in result["error"]


def test_both_vizier_checks_bypass_the_cache(script, monkeypatch):
    pytest.importorskip("astroquery")
    from astroquery.utils import TableList
    from astroquery.vizier import VizierClass

    seen = []

    def spy(self, *a, **k):
        seen.append(k.get("cache"))
        return TableList({})

    monkeypatch.setattr(VizierClass, "query_region", spy)
    script.run_check("vsx", "x", lambda: script.check_vsx("vizier.cfa.harvard.edu", 1.0, 2.0))
    script.run_check("gaia", "x", lambda: script.check_gaia_dr3_vizier("vizier.cfa.harvard.edu"))
    assert seen == [False, False]  # both VizieR checks ask for a fresh answer, never a cached one


def test_ztf_check_fails_when_a_faint_star_has_no_rows_but_a_bright_one_may_be_empty(script, monkeypatch):
    import requests

    class Resp:
        content = b"oid,mjd,mag,filtercode\n"
        text = "oid,mjd,mag,filtercode\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    assert script.run_check("z", "faint", lambda: script.check_ztf(1.0, 2.0))["ok"] is False
    assert script.run_check("z", "bright", lambda: script.check_ztf(1.0, 2.0, expect_rows=False))["ok"] is True
