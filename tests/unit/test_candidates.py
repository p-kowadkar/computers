import json

import pyarrow.parquet as pq
import pytest
from synth_tile import make_tile, write_tile_files
from test_kill import tile_with_eruption

from computers import __version__
from computers.bench import leavitt
from computers.cli import main
from computers.data import schemas, silver
from computers.l1 import analyse as A
from computers.l1 import candidates as C
from computers.l1.triggers import Trigger


def results_for(ctx, refs, **kw):
    return [A.analyse_star(ctx, r, **kw) for r in refs]


def test_the_split_lock_path_matches_the_benchmark_so_the_test_tile_guard_is_real():
    assert C.SPLIT_LOCK == leavitt.LOCK_PATH


def test_the_candidate_id_is_stable_and_depends_on_the_star_kind_and_epochs():
    t = Trigger("outburst", 1946.1234, 1946.2, 4.0, False, (1, 2), True)
    assert C.candidate_id(7, t) == C.candidate_id(7, t) and len(C.candidate_id(7, t)) == 16
    assert C.candidate_id(8, t) != C.candidate_id(7, t)
    assert C.candidate_id(7, Trigger("dip", 1946.1234, 1946.2, 4.0, False, (1,), True)) != C.candidate_id(7, t)
    moved = Trigger("outburst", 1946.2, 1946.2, 4.0, False, (1, 2), True)
    assert C.candidate_id(7, moved) != C.candidate_id(7, t)
    same_to_a_hundredth = Trigger("outburst", 1946.1249, 1946.2, 9.0, True, (5,), False)
    assert C.candidate_id(7, same_to_a_hundredth) == C.candidate_id(7, t)  # epochs round to 0.01 year


def test_flags_say_what_a_reader_must_know():
    assert C.l1_flags(Trigger("outburst", 1.0, 2.0, 1.0, False, (1,), True)) == ["lower_limit"]
    assert C.l1_flags(Trigger("dip", 1.0, 2.0, 1.0, False, (1,), False)) == ["not_detrended"]
    assert C.l1_flags(Trigger("disappearance", 1.0, 2.0, 1.0, False, (1,), True)) == ["needs_cutouts"]
    k = Trigger("step", 1.0, 2.0, 1.0, False, (1,), True, kill_reasons=("known_variable",))
    assert C.l1_flags(k) == ["known_variable"]


def test_a_surviving_trigger_becomes_a_valid_candidate_row_and_a_killed_one_does_not():
    good = results_for(tile_with_eruption(), [1])
    table = C.candidate_table(good, "m44", snapshot_digest="sha256:abc")
    schemas.validate("candidates", table)
    (row,) = table.to_pylist()
    assert row["ref_number"] == 1 and row["event_type"] == "outburst" and row["tile"] == "m44"
    assert row["l1_flags"] == ["lower_limit"] and row["l2_score"] is None and row["single_plate"] is False
    assert row["snapshot_digest"] == "sha256:abc" and row["code_version"] == __version__
    assert row["epoch_start"] <= row["epoch_end"] and row["amplitude_mag"] > 1.0
    killed = results_for(tile_with_eruption(("ac", "ac")), [1])
    assert C.candidate_table(killed, "m44", snapshot_digest="sha256:abc").num_rows == 0


def test_rerunning_gives_the_same_candidate_ids():
    a = C.candidate_table(results_for(tile_with_eruption(), [1]), "m44", snapshot_digest="x")
    b = C.candidate_table(results_for(tile_with_eruption(), [1]), "m44", snapshot_digest="x")
    assert a.column("candidate_id").to_pylist() == b.column("candidate_id").to_pylist()


def test_there_is_one_telemetry_row_per_star_with_where_it_stopped():
    ctx = tile_with_eruption()
    results = results_for(ctx, [1, 2, 3])
    table = C.telemetry_table(results, "run-1")
    schemas.validate("telemetry", table)
    rows = {r["ref_number"]: r for r in table.to_pylist()}
    assert len(rows) == 3 and rows[1]["stop_layer"] == "l2" and rows[1]["reason"] == "passed"
    assert rows[2]["stop_layer"] == "l1" and rows[2]["reason"] == "no_trigger"
    assert all(r["run_id"] == "run-1" and r["wall_seconds"] > 0 and r["tokens"] is None for r in rows.values())


def test_the_trigger_log_keeps_killed_triggers_with_their_reasons():
    log = C.trigger_log(results_for(tile_with_eruption(("ac", "ac")), [1, 2]))
    assert len(log) == 1 and log[0]["kill_reasons"] == ["single_series_only"] and not log[0]["survives"]
    assert log[0]["ref_number"] == 1 and log[0]["kind"] == "outburst" and log[0]["evidence_rows"] == 2
    ok = C.trigger_log(results_for(tile_with_eruption(), [1]))
    assert ok[0]["survives"] and ok[0]["kill_reasons"] == []


# ----------------------------------------------------------------------- the command


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    write_tile_files(d, "m44", n_star=40, n_plate=600, noise=0.04, plate_sigma=0.05, seed=3,
                     star_offset=lambda i, j: -1.8 if (i == 0 and j in (300, 301)) else 0.0,
                     series=lambda j: {300: "ac", 301: "i"}.get(j, "ac" if j % 2 else "i"))  # fmt: skip
    return d


def test_l1_run_writes_candidates_telemetry_and_the_log(data, capsys):
    assert main(["l1", "run", "--tile", "m44", "--data-dir", str(data)]) == 0
    out = capsys.readouterr().out
    assert "40 stars, 1 candidates" in out and "no_trigger 39" in out and "passed 1" in out
    cands = silver.read_table(silver.silver_path(data, "candidates", "m44"))
    tel = silver.read_table(silver.silver_path(data, "telemetry", "m44"))
    schemas.validate("candidates", cands)
    schemas.validate("telemetry", tel)
    assert cands.num_rows == 1 and tel.num_rows == 40 and len(set(tel.column("ref_number").to_pylist())) == 40
    assert len(set(tel.column("run_id").to_pylist())) == 1
    log = json.loads((data / "reports" / "l1-triggers-m44.json").read_text())
    assert [t["kind"] for t in log] == ["outburst"]
    assert silver.read_metadata(silver.silver_path(data, "candidates", "m44"))["computers.schema_version"] == "1"


def test_a_second_run_gives_identical_tables(data):
    main(["l1", "run", "--tile", "m44", "--data-dir", str(data)])
    first = pq.read_table(silver.silver_path(data, "candidates", "m44"))
    main(["l1", "run", "--tile", "m44", "--data-dir", str(data)])
    assert pq.read_table(silver.silver_path(data, "candidates", "m44")).equals(first)


def test_the_test_tile_is_refused_until_the_thresholds_are_chosen_and_the_split_frozen(tmp_path, capsys):
    lines = []
    assert C.run_l1(tmp_path / "data", "tabby", root=tmp_path, say=lines.append) == 1
    assert "not chosen" in lines[0]
    rec = tmp_path / C.THRESHOLD_RECORD
    rec.parent.mkdir(parents=True)
    rec.write_text("# 0007\n\nStatus: **in progress**.\n")
    assert "not chosen" in C.check_test_tile_allowed(tmp_path)
    rec.write_text("# 0007\n\nStatus: **chosen 2026-12-01**.\n")
    assert "not frozen" in C.check_test_tile_allowed(tmp_path)
    (tmp_path / C.SPLIT_LOCK).write_text("{}")
    assert C.check_test_tile_allowed(tmp_path) is None


def test_the_dev_tiles_are_never_blocked(data, tmp_path):
    assert C.check_test_tile_allowed(tmp_path) is not None  # the guard fails closed here...
    assert C.run_l1(data, "m44", root=tmp_path, say=lambda _: None) == 0  # ...and does not touch m44


def test_make_tile_still_builds_a_context():
    assert len(make_tile(n_star=12, n_plate=60)) == 12
