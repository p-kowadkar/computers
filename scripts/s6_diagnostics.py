"""Sprint 6 diagnostics on a trained run: where does the model differ from the star median?

Throwaway analysis (writes nothing). Run: uv run --group ml python scripts/s6_diagnostics.py data/runs/e1-m
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch

from computers.ml import baselines as B
from computers.ml import evaluate as E
from computers.ml.lc_encdec import report as R
from computers.ml.lc_encdec import train as T
from computers.ml.lc_encdec.model import Config, LCEncDec

run = Path(sys.argv[1])
meta = json.loads((run / "config.json").read_text())["config"]
ck = torch.load(run / "best.pt", map_location="cpu", weights_only=True)
model = LCEncDec(Config(**ck["config"]))
model.load_state_dict(ck["model"])
dev = T.pick_device("auto")
model.to(dev)


def score(stars, label):
    es = E.build_eval_set(stars, k_per_mode=3, seed=0)
    mp = R.model_predictions(model, es, dev)
    md = E.predict_all(es, B.median)
    n5 = E.predict_all(es, B.nearest_in_series)
    tm, tmed, tn5 = (E.score_predictions(es, p) for p in (mp, md, n5))
    print(f"\n{label}: {len(stars)} stars")
    for name, te in (("model", tm), ("median", tmed), ("nearest5", tn5)):
        print(f"  {name:<9} RMSE {np.sqrt(np.mean(te.err ** 2)):.4f}")
    d = E.difference(tm, tmed, n_boot=300)
    print(f"  model minus median {d['difference']:+.4f} ({d['low']:+.4f} to {d['high']:+.4f})")
    # how far from zero does the model predict?
    allp = np.concatenate([p[(~m.seq.pad) & m.hidden & (~m.seq.is_limit)] for p, (_, m) in zip(mp, es.items, strict=True)])
    print(f"  model predictions on hidden detections: mean {allp.mean():+.4f}, std {allp.std():.4f}, |p|>0.1: {100 * np.mean(np.abs(allp) > 0.1):.1f}%")
    # per star
    rm = np.array([np.sqrt(np.mean(tm.err[tm.star == i] ** 2)) for i in range(len(stars))])
    rd = np.array([np.sqrt(np.mean(tmed.err[tmed.star == i] ** 2)) for i in range(len(stars))])
    better = rm < 0.95 * rd
    print(f"  stars where the model is 5% better than the median: {better.sum()} of {len(stars)}; 5% worse: {(rm > 1.05 * rd).sum()}")
    order = np.argsort(-rd)[:5]
    for i in order:
        print(f"    noisiest star ref {stars[i].ref_number}: median RMSE {rd[i]:.3f}, model {rm[i]:.3f}")


val_refs = {(t, r) for t, r in json.loads((run / "split.json").read_text())["val"]}
train_stars = T.load_stars_for(meta["data_dir"], tuple(meta["train_tiles"]))
score([s for s in train_stars if (s.tile, s.ref_number) in val_refs], "set-aside stars of the TRAINING tile (share plates with training stars)")
score([s for s in train_stars if (s.tile, s.ref_number) not in val_refs][:80], "stars the model TRAINED on (first 80)")
score(T.load_stars_for(meta["data_dir"], tuple(meta["eval_tiles"])), "held-out tile")
