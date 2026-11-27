"""Training: config, run directory, resume, early stop (spec S6 R5). Needs the `ml` group."""
import dataclasses
import json

import numpy as np
import pytest
from test_sequences import star_from
from trig_helpers import flat

pytest.importorskip("torch")

import itertools

from computers.ml.lc_encdec import model as L
from computers.ml.lc_encdec import train as T

SMALL = L.Config(d_model=16, n_layers=1, n_heads=2, n_dec_layers=1, dropout=0.1)


def stars(n=12):
    out = []
    for i in range(n):
        years, mag = flat(80, noise=0.1, seed=i)
        out.append(star_from(years, mag))
    return out


def cfg(tmp_path, **kw):
    base = {"run_name": "r", "train_tiles": ("t",), "seed": 1, "batch_size": 8, "draws_per_star": 2, "epochs": 4, "lr": 1e-2,
                "warmup_epochs": 1, "patience": 50, "val_fraction": 0.25, "device": "cpu", "data_dir": str(tmp_path), "model": SMALL}  # fmt: skip
    base.update(kw)
    return T.RunConfig(**base)


def metrics(run_dir):
    return [json.loads(x) for x in (run_dir / "metrics.jsonl").read_text().splitlines()]


def test_a_run_writes_config_spec_split_metrics_and_checkpoints(tmp_path):
    d = T.train(cfg(tmp_path), stars=stars(), say=lambda _: None)
    for f in ("config.json", "sequence_spec.json", "split.json", "metrics.jsonl", "last.pt", "best.pt"):
        assert (d / f).is_file(), f
    meta = json.loads((d / "config.json").read_text())
    assert meta["config"]["run_name"] == "r" and meta["sequence_spec_hash"] and meta["code_version"] and meta["device"] == "cpu"
    assert len(metrics(d)) == 4 and all(np.isfinite(m["train_loss"]) for m in metrics(d))


def test_resuming_gives_the_same_later_losses_as_an_uninterrupted_run(tmp_path):
    a = T.train(cfg(tmp_path / "a"), stars=stars(), say=lambda _: None)
    cb = cfg(tmp_path / "b")
    T.train(cb, stars=stars(), stop_after=1, say=lambda _: None)
    assert len(metrics(T.run_dir_for(cb))) == 2
    b = T.train(cb, resume=True, stars=stars(), say=lambda _: None)
    ma, mb = metrics(a), metrics(b)
    assert len(mb) == 4
    for x, y in zip(ma, mb, strict=True):
        assert x["train_loss"] == pytest.approx(y["train_loss"], rel=1e-5) and x["val_loss"] == pytest.approx(y["val_loss"], rel=1e-5)


def test_an_existing_run_is_not_overwritten_and_a_resume_without_a_checkpoint_fails(tmp_path):
    c = cfg(tmp_path)
    T.train(c, stars=stars(), say=lambda _: None)
    with pytest.raises(FileExistsError):
        T.train(c, stars=stars(), say=lambda _: None)
    empty = cfg(tmp_path, run_name="other")
    T.run_dir_for(empty).mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="nothing to resume"):
        T.train(empty, resume=True, stars=stars(), say=lambda _: None)


def test_early_stopping_ends_the_run_when_the_validation_loss_stops_improving(tmp_path):
    d = T.train(cfg(tmp_path, epochs=60, patience=2, lr=5.0), stars=stars(), say=lambda _: None)  # a wild rate that cannot keep improving
    assert len(metrics(d)) < 60


def test_the_validation_stars_are_fixed_by_the_seed_and_disjoint_from_training(tmp_path):
    s = stars(20)
    s = [dataclasses.replace(x, ref_number=100 + i) for i, x in enumerate(s)]  # the test helper gives every star ref 1
    tr, va = T.split_stars(s, 0.25, seed=3)
    assert len(va) == 5 and len(tr) == 15 and not ({x.ref_number for x in tr} & {x.ref_number for x in va})
    assert [x.ref_number for x in T.split_stars(s, 0.25, seed=3)[1]] == [x.ref_number for x in va]
    assert [x.ref_number for x in T.split_stars(s, 0.25, seed=4)[1]] != [x.ref_number for x in va]


def test_no_secret_reaches_the_run_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("STARGLASS_API_KEY", "sk-test-secret-value-12345")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-secret-67890")
    d = T.train(cfg(tmp_path), stars=stars(), say=lambda _: None)
    for f in d.iterdir():
        blob = f.read_bytes()
        assert b"sk-test-secret-value-12345" not in blob and b"sk-or-test-secret-67890" not in blob


def test_load_config_reads_a_json_file(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"run_name": "x", "train_tiles": ["m44"], "eval_tiles": ["tcrb"], "model": {"d_model": 32, "n_heads": 2}}))
    c = T.load_config(p)
    assert c.train_tiles == ("m44",) and c.eval_tiles == ("tcrb",) and c.model.d_model == 32 and c.model.n_layers == 4


def test_the_learning_rate_warms_up_then_decays():
    c = T.RunConfig("x", ("t",), epochs=20, lr=1.0, warmup_epochs=2)
    lrs = [T._lr_at(c, e) for e in range(20)]
    assert lrs[0] < lrs[1] <= 1.0 and lrs[2] == pytest.approx(1.0) and lrs[-1] < 0.2 and all(a >= b for a, b in itertools.pairwise(lrs[1:]))
