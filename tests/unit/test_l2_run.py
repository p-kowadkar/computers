"""`computers l2 run` (spec S5 R6, R7, R9.3)."""
import json

import numpy as np
import pyarrow.parquet as pq
import pytest
from synth_tile import write_tile_files

from computers.cli import main
from computers.data import schemas, silver
from computers.l2 import ranker as R
from computers.l2.run import MODEL_NAME, VARIANT, run_l2


def toy_model():
    rng = np.random.default_rng(0)
    base = {"kind": "outburst", "n_evidence": 4, "single_plate": False, "detrended": True}
    pos = [{**base, "amplitude_mag": float(rng.normal(2.5, 0.4))} for _ in range(100)]
    neg = [{**base, "amplitude_mag": float(rng.normal(1.1, 0.1))} for _ in range(20)]
    x = R.design(pos + neg, R.VARIANTS[VARIANT])
    return R.fit(x, np.r_[np.ones(100), np.zeros(20)], R.VARIANTS[VARIANT])


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    write_tile_files(d, "m44", n_star=40, n_plate=600, noise=0.04, plate_sigma=0.05, seed=3,
                     star_offset=lambda i, j: -1.8 if (i == 0 and j in (300, 301)) else 0.0,
                     series=lambda j: {300: "ac", 301: "i"}.get(j, "ac" if j % 2 else "i"))  # fmt: skip
    R.save(toy_model(), d / "models" / MODEL_NAME)
    assert main(["l1", "run", "--tile", "m44", "--data-dir", str(d)]) == 0
    return d


def test_l2_run_scores_the_candidate_selects_it_and_writes_telemetry_and_the_report(data, capsys):
    assert main(["l2", "run", "--tile", "m44", "--data-dir", str(data)]) == 0
    cands = silver.read_table(silver.silver_path(data, "candidates", "m44"))
    schemas.validate("candidates", cands)
    assert cands.num_rows == 1 and 0.0 <= cands.column("l2_score").to_pylist()[0] <= 1.0
    tel = silver.read_table(silver.silver_path(data, "telemetry", "m44-l2"))
    schemas.validate("telemetry", tel)
    assert tel.to_pylist()[0]["stop_layer"] == "l3" and tel.to_pylist()[0]["reason"] == "selected"
    assert tel.to_pylist()[0]["cost_usd"] == 0.0
    rep = json.loads((data / "reports" / "topology.json").read_text())
    t = rep["tiles"]["m44"]
    assert t["stars_in"] == 40 and t["reached_l2"] == 1 and len(t["selected"]) == 1 and t["left_out"] == []
    assert "1 stars selected for L3" in capsys.readouterr().out
    # L1 telemetry is left alone
    assert silver.read_table(silver.silver_path(data, "telemetry", "m44")).num_rows == 40


def test_a_second_run_gives_identical_scores(data):
    main(["l2", "run", "--tile", "m44", "--data-dir", str(data)])
    first = pq.read_table(silver.silver_path(data, "candidates", "m44")).to_pylist()
    main(["l2", "run", "--tile", "m44", "--data-dir", str(data)])
    assert pq.read_table(silver.silver_path(data, "candidates", "m44")).to_pylist() == first


def test_a_missing_model_or_missing_candidates_is_reported_and_fails(tmp_path):
    lines = []
    assert run_l2(tmp_path / "data", ["m44"], say=lines.append) == 1 and "no ranker" in lines[0]
    d = tmp_path / "d2"
    R.save(toy_model(), d / "models" / MODEL_NAME)
    assert run_l2(d, ["m44"], say=lines.append) == 1 and "run `computers l1 run" in lines[-1]


def test_the_test_tile_is_refused_before_the_freeze(data, tmp_path):
    lines = []
    assert run_l2(data, ["tabby"], root=tmp_path, say=lines.append) == 1
    assert "held-out test tile" in lines[0]


def test_a_known_variable_gets_a_score_and_no_place(data):
    # flag the one candidate as a known variable, as `l1 run` would for a VSX match
    path = silver.silver_path(data, "candidates", "m44")
    rows = pq.read_table(path).to_pylist()
    rows[0]["l1_flags"] = ["known_variable"]
    import pyarrow as pa

    silver.write_table("candidates", pa.Table.from_pylist(rows, schema=schemas.SCHEMAS["candidates"].schema), path, snapshot_digest="x")
    assert main(["l2", "run", "--tile", "m44", "--data-dir", str(data)]) == 0
    tel = silver.read_table(silver.silver_path(data, "telemetry", "m44-l2")).to_pylist()[0]
    assert tel["stop_layer"] == "l2" and tel["reason"] == "known_variable"
    rep = json.loads((data / "reports" / "topology.json").read_text())["tiles"]["m44"]
    assert rep["selected"] == [] and len(rep["known_variables"]) == 1 and rep["known_variables"][0]["score"] > 0


def test_a_candidate_that_no_longer_reproduces_under_l1_stops_the_run(data):
    path = silver.silver_path(data, "candidates", "m44")
    rows = pq.read_table(path).to_pylist()
    rows[0]["epoch_start"] += 5.0
    import pyarrow as pa

    silver.write_table("candidates", pa.Table.from_pylist(rows, schema=schemas.SCHEMAS["candidates"].schema), path, snapshot_digest="x")
    with pytest.raises(RuntimeError, match="did not reproduce"):
        run_l2(data, ["m44"], say=lambda _: None)
