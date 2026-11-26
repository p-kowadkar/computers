import json

import pytest
from fake_dasch import FakeDasch

from computers.cli import main
from computers.data import dasch_client, plates, silver
from computers.data.tiles import TILES


@pytest.fixture
def fake(monkeypatch):
    """The real client, with the network replaced by a fake DASCH for the T CrB tile."""
    server = FakeDasch(TILES["tcrb"], n_sources=300)
    monkeypatch.setattr(dasch_client, "RequestsTransport", lambda: server)
    monkeypatch.setattr(dasch_client.DaschClient, "__init__", _fast_init(dasch_client.DaschClient.__init__))
    monkeypatch.delenv("STARGLASS_API_KEY", raising=False)
    return server


def _fast_init(original):
    def init(self, store, **kw):
        kw.setdefault("min_interval", 0.0)
        kw.setdefault("sleep", lambda s: None)
        original(self, store, **kw)

    return init


def run(tmp_path, *args):
    return main(["pull", *args, "--data-dir", str(tmp_path / "data")])


def test_pull_catalog_for_a_tile(tmp_path, fake, capsys):
    assert run(tmp_path, "--tile", "tcrb", "--stage", "catalog") == 0
    out = capsys.readouterr().out
    assert "== tcrb" in out and "API: public" in out and "at B <= 13.5" in out
    assert silver.silver_path(tmp_path / "data", "stars", "tcrb").exists()
    assert fake.calls["querycat"] == 4 and fake.calls["queryexps"] == 0


def test_pull_all_stages_then_resume_costs_nothing(tmp_path, fake):
    assert run(tmp_path, "--tile", "tcrb") == 0
    for table in ("stars", "exposures", "measurements"):
        assert silver.silver_path(tmp_path / "data", table, "tcrb").exists()
    made = sum(fake.calls.values())
    assert made > 50
    assert run(tmp_path, "--tile", "tcrb") == 0
    assert sum(fake.calls.values()) == made  # the second run made no requests


def test_tile_all_runs_every_tile(tmp_path, fake, capsys):
    assert run(tmp_path, "--tile", "all", "--stage", "catalog") == 0
    out = capsys.readouterr().out
    for name in ("m44", "tcrb", "tabby"):
        assert f"== {name}" in out
        assert silver.silver_path(tmp_path / "data", "stars", name).exists()
    assert fake.calls["querycat"] == 12


def test_pilot_and_max_stars_are_passed_through(tmp_path, fake):
    assert run(tmp_path, "--tile", "tcrb", "--stage", "catalog") == 0
    assert run(tmp_path, "--tile", "tcrb", "--stage", "lightcurves", "--pilot", "--max-stars", "5") == 0
    stars = silver.read_table(silver.silver_path(tmp_path / "data", "stars", "tcrb"))
    assert 5 <= fake.calls["lightcurve"] < stars.num_rows


def test_a_failure_gives_a_nonzero_exit_status_and_a_report(tmp_path, fake):
    fake.fail_boxes = {1}
    assert run(tmp_path, "--tile", "tcrb", "--stage", "catalog") == 1
    (report,) = (tmp_path / "data" / "reports").glob("pull-tcrb-*.json")
    assert json.loads(report.read_text())["failures"]


@pytest.mark.parametrize("bad", [["--tile", "nowhere"], ["--tile", "tcrb", "--stage", "magic"],
                                 ["--tile", "tcrb", "--concurrency", "0"],
                                 ["--tile", "tcrb", "--concurrency", "5"],
                                 ["--tile", "tcrb", "--max-stars", "0"], []])  # fmt: skip
def test_bad_arguments_are_rejected(tmp_path, bad):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, *bad)
    assert exc.value.code == 2


def test_concurrency_is_applied(tmp_path, fake, capsys):
    run(tmp_path, "--tile", "tcrb", "--stage", "catalog", "--concurrency", "1")
    assert "concurrency 1" in capsys.readouterr().out


def test_the_key_is_not_printed(tmp_path, fake, capsys, monkeypatch):
    key = "Z" * 40
    monkeypatch.setenv("STARGLASS_API_KEY", key)
    run(tmp_path, "--tile", "tcrb", "--stage", "catalog")
    out = capsys.readouterr()
    assert key not in out.out and key not in out.err and "API: full" in out.out
    for p in (tmp_path / "data").rglob("*"):
        if p.is_file():
            assert key.encode() not in p.read_bytes()


# ------------------------------------------------------------------------ plates


def test_plates_command_reports_overlap(tmp_path, fake, capsys):
    run(tmp_path, "--tile", "tcrb", "--stage", "exposures")
    capsys.readouterr()
    assert main(["plates", "--data-dir", str(tmp_path / "data")]) == 0
    out = capsys.readouterr().out
    assert "tcrb" in out and "only one tile" in out


def test_plates_command_with_nothing_pulled(tmp_path, capsys):
    assert main(["plates", "--data-dir", str(tmp_path / "data")]) == 0
    assert "computers pull --stage exposures" in capsys.readouterr().out


def test_plates_uses_tables_written_by_the_pull(tmp_path, fake):
    run(tmp_path, "--tile", "tcrb", "--stage", "exposures")
    table = silver.read_table(silver.silver_path(tmp_path / "data", "exposures", "tcrb"))
    report = plates.load_report(tmp_path / "data")
    assert report.plates["tcrb"] == len(plates.plate_set(table)) > 0
    assert report.exposures["tcrb"] == table.num_rows
