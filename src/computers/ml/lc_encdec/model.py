"""The lightcurve encoder-decoder and its loss (plan 9.1, spec S6 R2.2 to R2.4, R3).

Each token of a masked sequence is embedded from its visible value and error, an is-limit
flag, a hidden flag, the time (sinusoidal features at scales from a day to a century) and
learned embeddings of the plate series and its kind. A transformer encoder reads them with
padding masked out of attention. A lighter decoder, used in training only, predicts the
magnitude offset of every token. The encoder's output is what later sprints embed.

Magnitudes run backwards. A masked limit says "the star is fainter than this", so a
prediction that is brighter (a smaller offset) than the limit's depth is the error, and a
prediction fainter than the limit is never penalised.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
import torch
from torch import Tensor, nn

from computers.ml import sequences as S
from computers.ml.masking import Masked

__all__ = ["Config", "LCEncDec", "reconstruction_loss", "to_batch"]


@dataclass(frozen=True)
class Config:
    d_model: int = 128
    n_layers: int = 4
    n_heads: int = 4
    n_dec_layers: int = 2
    dropout: float = 0.1
    # loss settings (model hyperparameters, not detection thresholds)
    err_floor: float = 0.05  # mag: smallest error the detection loss divides by
    limit_sigma: float = 0.15  # mag: one measurement's RMS, the scale of the limit penalty

    def to_dict(self) -> dict:
        return asdict(self)


def to_batch(items: Sequence[Masked], device: torch.device | str = "cpu") -> dict[str, Tensor]:
    """Stack masked samples into tensors. `value` and `err` are the targets; `value_in` and `err_in` are what the model sees."""

    def stack(f, dtype):
        return torch.as_tensor(np.stack([f(m) for m in items]), dtype=dtype, device=device)

    return {
        "t": stack(lambda m: m.seq.t, torch.float32),
        "value_in": stack(lambda m: m.value_in, torch.float32),
        "err_in": stack(lambda m: m.err_in, torch.float32),
        "is_limit": stack(lambda m: m.seq.is_limit, torch.bool),
        "hidden": stack(lambda m: m.hidden, torch.bool),
        "series": stack(lambda m: m.seq.series, torch.long),
        "kind": stack(lambda m: m.seq.kind, torch.long),
        "pad": stack(lambda m: m.seq.pad, torch.bool),
        "value": stack(lambda m: m.seq.value, torch.float32),
        "err": stack(lambda m: m.seq.err, torch.float32),
    }


def reconstruction_loss(
    pred: Tensor,
    value: Tensor,
    err: Tensor,
    is_limit: Tensor,
    hidden: Tensor,
    pad: Tensor,
    *,
    err_floor: float = 0.05,
    limit_sigma: float = 0.15,
) -> Tensor:
    """Mean loss over hidden, non-padded tokens.

    A hidden detection costs the squared error over its measurement error (floored). A hidden
    limit costs the squared amount by which the prediction is brighter than the limit (a
    smaller offset), over `limit_sigma`, and nothing when it is at or fainter than the limit.
    """
    live = hidden & ~pad
    det = live & ~is_limit
    lim = live & is_limit
    det_loss = ((pred - value) / err.clamp(min=err_floor)) ** 2
    lim_loss = torch.relu(value - pred) ** 2 / limit_sigma**2
    total = (det_loss * det).sum() + (lim_loss * lim).sum()
    return total / live.sum().clamp(min=1)


def _time_features(t: Tensor) -> Tensor:
    scales = torch.tensor(S.TIME_SCALES, dtype=t.dtype, device=t.device)
    ang = 2.0 * math.pi * t.unsqueeze(-1) / scales
    return torch.cat([ang.sin(), ang.cos()], dim=-1)


class LCEncDec(nn.Module):
    def __init__(self, cfg: Config | None = None) -> None:
        super().__init__()
        self.cfg = cfg or Config()
        d = self.cfg.d_model
        n_series = int(S.SPEC["n_series_ids"])
        self.inp = nn.Linear(3 + 2 * len(S.TIME_SCALES), d)  # value, err, is_limit, time features
        self.series_emb = nn.Embedding(n_series, d)
        self.kind_emb = nn.Embedding(len(S.KIND_CODES), d)
        self.mask_token = nn.Parameter(torch.zeros(d))

        def stack(n: int) -> nn.TransformerEncoder:
            layer = nn.TransformerEncoderLayer(
                d, self.cfg.n_heads, 4 * d, self.cfg.dropout, batch_first=True, norm_first=True
            )
            return nn.TransformerEncoder(layer, n, enable_nested_tensor=False)

        self.encoder = stack(self.cfg.n_layers)
        self.decoder = stack(self.cfg.n_dec_layers)
        self.norm = nn.LayerNorm(d)
        self.head = nn.Linear(d, 1)

    def encode(self, b: dict[str, Tensor]) -> Tensor:
        """Encoder output for every token, shape (batch, length, d_model). Padded positions are not meaningful."""
        x = torch.cat(
            [b["value_in"].unsqueeze(-1), b["err_in"].unsqueeze(-1), b["is_limit"].unsqueeze(-1).float(), _time_features(b["t"])],
            dim=-1,
        )
        h = self.inp(x) + self.series_emb(b["series"]) + self.kind_emb(b["kind"])
        h = h + b["hidden"].unsqueeze(-1).float() * self.mask_token
        return self.encoder(h, src_key_padding_mask=b["pad"])

    def forward(self, b: dict[str, Tensor]) -> Tensor:
        """Predicted magnitude offset of every token, shape (batch, length)."""
        h = self.decoder(self.encode(b), src_key_padding_mask=b["pad"])
        return self.head(self.norm(h)).squeeze(-1)

    def loss(self, b: dict[str, Tensor]) -> Tensor:
        return reconstruction_loss(
            self(b), b["value"], b["err"], b["is_limit"], b["hidden"], b["pad"],
            err_floor=self.cfg.err_floor, limit_sigma=self.cfg.limit_sigma,
        )  # fmt: skip
