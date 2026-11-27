"""Train the lightcurve encoder-decoder from a config file (spec S6 R5).

Everything that varies between runs is in the config. Each epoch draws fresh samples of
every star (seeded by run seed, epoch, star and draw), so a resumed run sees exactly the
samples an uninterrupted one would, and dropout is reseeded at the start of each epoch for the
same reason. A run directory holds the config, the metrics log and the checkpoints.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import torch

from computers import __version__
from computers.ml import dataset as D
from computers.ml import masking as M
from computers.ml import sequences as S
from computers.ml.lc_encdec.model import Config, LCEncDec, to_batch

__all__ = ["RunConfig", "load_config", "load_stars_for", "pick_device", "run_dir_for", "train"]


@dataclass(frozen=True)
class RunConfig:
    run_name: str
    train_tiles: tuple[str, ...]
    eval_tiles: tuple[str, ...] = ()
    seed: int = 0
    batch_size: int = 64
    draws_per_star: int = 20
    epochs: int = 100
    lr: float = 1e-3
    weight_decay: float = 0.01
    warmup_epochs: int = 2
    patience: int = 10
    val_fraction: float = 0.1
    device: str = "auto"
    data_dir: str = "data"
    model: Config = field(default_factory=Config)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["train_tiles"], d["eval_tiles"] = list(self.train_tiles), list(self.eval_tiles)
        return d


def load_config(path: str | os.PathLike[str]) -> RunConfig:
    raw = json.loads(Path(path).read_text())
    model = Config(**raw.pop("model", {}))
    raw["train_tiles"] = tuple(raw["train_tiles"])
    raw["eval_tiles"] = tuple(raw.get("eval_tiles", ()))
    return RunConfig(model=model, **raw)


def pick_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    return torch.device(name)


def run_dir_for(cfg: RunConfig) -> Path:
    return Path(cfg.data_dir) / "runs" / cfg.run_name


def load_stars_for(data_dir: str | os.PathLike[str], tiles: tuple[str, ...]) -> list[S.Star]:
    out: list[S.Star] = []
    for t in tiles:
        path = Path(data_dir) / "sequences" / f"v{S.SPEC_VERSION}" / f"tile={t}.npz"
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing; run `computers ml build` first")
        out += D.load_stars(path)
    return out


def split_stars(stars: list[S.Star], val_fraction: float, seed: int) -> tuple[list[S.Star], list[S.Star]]:
    """Set aside a fraction of stars for early stopping. Fixed by the seed and the star ids."""
    order = sorted(range(len(stars)), key=lambda i: hashlib.sha256(f"{seed}|{stars[i].tile}|{stars[i].ref_number}".encode()).hexdigest())
    n_val = max(1, round(val_fraction * len(stars)))
    val = set(order[:n_val])
    return [s for i, s in enumerate(stars) if i not in val], [s for i, s in enumerate(stars) if i in val]


def _epoch_samples(stars: list[S.Star], cfg: RunConfig, epoch: int) -> list[M.Masked]:
    items = []
    for i, star in enumerate(stars):
        for k in range(cfg.draws_per_star):
            rng = np.random.default_rng([cfg.seed, epoch, i, k])
            items.append(M.apply_mask(S.make_sequence(star, rng), rng))
    order = np.random.default_rng([cfg.seed, epoch, 999_983]).permutation(len(items))
    return [items[j] for j in order]


def _val_loss(model: LCEncDec, items: list[M.Masked], device: torch.device, batch_size: int) -> float:
    model.eval()
    total, n = 0.0, 0
    with torch.no_grad():
        for a in range(0, len(items), batch_size):
            chunk = items[a : a + batch_size]
            total += model.loss(to_batch(chunk, device)).item() * len(chunk)
            n += len(chunk)
    return total / max(n, 1)


def _lr_at(cfg: RunConfig, epoch: int) -> float:
    """Linear warm-up for `warmup_epochs`, then cosine to 5% of the peak."""
    if epoch < cfg.warmup_epochs:
        return cfg.lr * (epoch + 1) / cfg.warmup_epochs
    p = (epoch - cfg.warmup_epochs) / max(cfg.epochs - cfg.warmup_epochs, 1)
    return cfg.lr * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(p, 1.0))))


def train(
    cfg: RunConfig, *, resume: bool = False, stars: list[S.Star] | None = None, stop_after: int | None = None, say=print
) -> Path:
    """Run training and return the run directory.

    `stars` overrides the dataset on disk (for tests). `stop_after` ends the run after that epoch
    as if it had been interrupted, so a test can resume it.
    """
    run_dir = run_dir_for(cfg)
    if run_dir.exists() and not resume:
        raise FileExistsError(f"{run_dir} exists; pass resume=True to continue it or choose another run_name")
    run_dir.mkdir(parents=True, exist_ok=True)
    device = pick_device(cfg.device)
    all_stars = stars if stars is not None else load_stars_for(cfg.data_dir, cfg.train_tiles)
    tr, va = split_stars(all_stars, cfg.val_fraction, cfg.seed)
    val_items = [M.apply_mask(S.make_sequence(s, np.random.default_rng([cfg.seed, 7, i])), np.random.default_rng([cfg.seed, 8, i])) for i, s in enumerate(va)]
    cfg_path = run_dir / "config.json"
    if not resume:
        digest = ""
        try:
            from computers.data.snapshot import SnapshotStore

            digest = SnapshotStore(cfg.data_dir).digest()
        except Exception as exc:  # noqa: BLE001  the digest is provenance only; a missing store is recorded, not fatal
            digest = f"unavailable: {type(exc).__name__}"
        cfg_path.write_text(json.dumps({
            "config": cfg.to_dict(), "sequence_spec_hash": S.spec_hash(), "code_version": __version__,
            "snapshot_digest": digest, "device": str(device), "train_stars": len(tr), "val_stars": len(va),
        }, indent=1) + "\n")  # fmt: skip
        (run_dir / "sequence_spec.json").write_text(S.spec_json() + "\n")
        (run_dir / "split.json").write_text(json.dumps({"val": [[s.tile, s.ref_number] for s in va]}) + "\n")

    torch.manual_seed(cfg.seed)
    model = LCEncDec(cfg.model).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    start, best, bad = 0, float("inf"), 0
    last = run_dir / "last.pt"
    if resume:
        if not last.is_file():
            raise FileNotFoundError(f"{last} is missing, nothing to resume")
        ck = torch.load(last, map_location=device, weights_only=True)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        start, best, bad = ck["epoch"] + 1, ck["best"], ck["bad"]
        say(f"resuming at epoch {start}")
    log = run_dir / "metrics.jsonl"
    for epoch in range(start, cfg.epochs):
        t0 = time.perf_counter()
        torch.manual_seed(cfg.seed * 1_000_003 + epoch)  # dropout and nothing else uses torch's generator
        for g in opt.param_groups:
            g["lr"] = _lr_at(cfg, epoch)
        model.train()
        items = _epoch_samples(tr, cfg, epoch)
        total, n = 0.0, 0
        for a in range(0, len(items), cfg.batch_size):
            chunk = items[a : a + cfg.batch_size]
            loss = model.loss(to_batch(chunk, device))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(chunk)
            n += len(chunk)
        val = _val_loss(model, val_items, device, cfg.batch_size)
        improved = val < best
        best, bad = (val, 0) if improved else (best, bad + 1)
        row = {"epoch": epoch, "train_loss": total / n, "val_loss": val, "lr": _lr_at(cfg, epoch), "seconds": round(time.perf_counter() - t0, 2), "best": best}
        with open(log, "a") as f:
            f.write(json.dumps(row) + "\n")
        state = {"model": model.state_dict(), "opt": opt.state_dict(), "epoch": epoch, "best": best, "bad": bad}
        torch.save(state, last)
        if improved:
            torch.save({"model": model.state_dict(), "epoch": epoch, "val_loss": val, "config": cfg.model.to_dict()}, run_dir / "best.pt")
        say(f"epoch {epoch:3d}  train {row['train_loss']:.4f}  val {val:.4f}  {row['seconds']} s" + ("  *" if improved else ""))
        if stop_after is not None and epoch >= stop_after:
            break
        if bad >= cfg.patience:
            say(f"early stop: no improvement for {cfg.patience} epochs")
            break
    return run_dir
