"""Score a trained encoder on held-out stars and set it against the baselines (spec S6 R4)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import torch

from computers.ml import baselines as B
from computers.ml import evaluate as E
from computers.ml import masking as M
from computers.ml.lc_encdec.model import Config, LCEncDec, to_batch
from computers.ml.lc_encdec.train import load_stars_for, pick_device

__all__ = ["evaluate_run", "format_report", "model_predictions"]


def model_predictions(model: LCEncDec, evalset: E.EvalSet, device: torch.device, batch_size: int = 128) -> list[np.ndarray]:
    """The model's offset for every token of every sample of the set."""
    model.eval()
    out: list[np.ndarray] = []
    items = [m for _, m in evalset.items]
    with torch.no_grad():
        for a in range(0, len(items), batch_size):
            pred = model(to_batch(items[a : a + batch_size], device)).cpu().numpy()
            out += list(pred)
    return out


def evaluate_run(
    run_dir: str | os.PathLike[str],
    *,
    tiles: tuple[str, ...] | None = None,
    k_per_mode: int = 3,
    seed: int = 0,
    n_boot: int = 1000,
    stars=None,
    device: str = "auto",
) -> dict[str, Any]:
    """Evaluate `best.pt` of a run on the stars of its evaluation tiles (or of `tiles`), and write `eval.json`."""
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "config.json").read_text())
    cfgd = meta["config"]
    tiles = tuple(tiles or cfgd["eval_tiles"])
    if not set(tiles).isdisjoint(cfgd["train_tiles"]):
        raise ValueError(f"evaluation tiles {tiles} include a training tile {cfgd['train_tiles']}; a held-out tile is required")
    held = stars if stars is not None else load_stars_for(cfgd["data_dir"], tiles)
    ck = torch.load(run_dir / "best.pt", map_location="cpu", weights_only=True)
    dev = pick_device(device)
    model = LCEncDec(Config(**ck["config"])).to(dev)
    model.load_state_dict(ck["model"])
    es = E.build_eval_set(held, k_per_mode=k_per_mode, seed=seed)
    methods = {"model": model_predictions(model, es, dev)}
    for name, f in B.BASELINES.items():
        methods[name] = E.predict_all(es, f)
    scored = {k: E.score_predictions(es, v) for k, v in methods.items()}
    result: dict[str, Any] = {
        "run": run_dir.name, "train_tiles": cfgd["train_tiles"], "eval_tiles": list(tiles), "eval_stars": len(held),
        "best_epoch": int(ck["epoch"]), "samples": len(es.items), "seed": seed, "n_boot": n_boot,
        "methods": {k: E.summary(v, n_boot=n_boot, seed=seed) for k, v in scored.items()},
        "model_minus": {
            name: {mode: E.difference(scored["model"], scored[name], mode=mode, n_boot=n_boot, seed=seed) for mode in M.MODES}
            for name in B.BASELINES
        },
    }  # fmt: skip
    (run_dir / "eval.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


def format_report(r: dict[str, Any]) -> str:
    lines = [f"{r['run']}: trained on {r['train_tiles']}, scored on {r['eval_tiles']} ({r['eval_stars']} stars, {r['samples']} samples, best epoch {r['best_epoch']})"]
    for mode in M.MODES:
        lines += [f"\nMasking: {mode}. Error on hidden detections in mag (RMSE with 95% interval over stars, median absolute error)."]
        for name, s in r["methods"].items():
            c = s["by_mode"][mode]
            lines.append(f"  {name:<16} RMSE {c['rmse']:.4f} ({c['low']:.4f} to {c['high']:.4f})  median |err| {c['median_abs']:.4f}  limits predicted too bright {100 * c['limit_brighter_share']:.1f}% of {c['n_limits']}")
        for name, d in r["model_minus"].items():
            x = d[mode]
            verdict = "better" if x["high"] < 0 else ("worse" if x["low"] > 0 else "not shown")
            lines.append(f"  model minus {name:<16} {x['difference']:+.4f} ({x['low']:+.4f} to {x['high']:+.4f})  {verdict}")
    lines.append("\nBy era, pooled over both modes (RMSE).")
    for era in next(iter(r["methods"].values()))["by_era"]:
        lines.append(f"  {era:<12} " + "  ".join(f"{n} {s['by_era'][era]['rmse']:.4f} (n {s['by_era'][era]['n']})" for n, s in r["methods"].items()))
    return "\n".join(lines)
