import json

import pytest
from synth_leavitt import write_all

from computers.bench import leavitt as L
from computers.bench import run as R
from computers.cli import main


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "LOCK_PATH", tmp_path / "docs" / "lock.json")
    d = tmp_path / "data"
    write_all(d)
    return d


def cli(data, *args, capsys):
    code = main(["bench", *args, "--data-dir", str(data)])
    return code, capsys.readouterr().out


def test_build_then_smoke_prints_a_completeness_table_and_writes_json(data, capsys):
    assert cli(data, "build", capsys=capsys)[0] == 0
    code, out = cli(data, "--smoke", capsys=capsys)
    assert code == 0
    assert "TrivialDetector" in out and "False alarms on un-injected constants" in out
    assert "Amplitude at 50% and 90% recovery" in out
    result = json.loads((data / "reports" / "bench-smoke.json").read_text())
    assert result["smoke"] and result["split"] == "dev" and result["bins"]
    assert {b["family"] for b in result["bins"]} <= set(R.SMOKE_FAMILIES)
    assert result["hosts"] <= R.SMOKE_HOSTS


def test_smoke_on_flat_synthetic_hosts_recovers_big_steps_and_not_tiny_ones(data):
    L.build_set(data, say=lambda _: None)
    result = R.run_bench(data, smoke=True, say=lambda _: None)
    step = {b["amplitude_index"]: b["fraction"] for b in result["bins"] if b["family"] == "step"}
    assert step[max(step)] > 0.8 and step[min(step)] < 0.2
    assert result["false_alarms"]["k"] == 0


def test_smoke_needs_a_built_set(data, capsys):
    code, out = cli(data, "--smoke", capsys=capsys)
    assert code == 1 and "bench build" in out


def test_the_test_split_is_not_scored_until_it_is_frozen(data, capsys, tmp_path):
    L.build_set(data, say=lambda _: None)
    code, out = cli(data, "run", "--split", "test", capsys=capsys)
    assert code == 1 and "frozen" in out
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "d.md").write_text("approved")
    assert cli(data, "freeze", capsys=capsys)[0] == 2  # no decision named
    L.freeze_test_split(data, decision="d.md", root=tmp_path)
    code, out = cli(data, "run", "--split", "test", capsys=capsys)
    assert code == 0 and "test split" in out
    assert (data / "reports" / "bench-test.json").exists()


def test_check_and_no_action(data, capsys):
    assert cli(data, "check", capsys=capsys)[0] == 1
    L.build_set(data, say=lambda _: None)
    code, out = cli(data, "check", capsys=capsys)
    assert code == 1 and "not frozen" in out
    assert cli(data, capsys=capsys)[0] == 2


def test_run_dev_uses_every_dev_constant(data):
    L.build_set(data, say=lambda _: None)
    result = R.run_bench(data, split="dev", say=lambda _: None)
    cons = [c for c in __import__("pyarrow.parquet", fromlist=["x"]).read_table(
        L.set_dir(data) / "constants.parquet").to_pylist() if c["split"] == "dev" and c["is_constant"]]
    assert result["hosts"] == len(cons) and result["injections"] == len(cons) * 60
    with pytest.raises(ValueError):
        R.run_bench(data, split="all", say=lambda _: None)
