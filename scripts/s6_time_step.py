"""Spike for Sprint 6 task 1: can the Mac train a 4-layer, width-128 transformer on 200-token sequences?

Throwaway timing. Writes nothing. Run: uv run --group ml python scripts/s6_time_step.py
"""
import time

import torch
from torch import nn


def model(d=128, layers=4, heads=4):
    enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, heads, 4 * d, 0.1, batch_first=True, norm_first=True), layers)
    dec = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, heads, 4 * d, 0.1, batch_first=True, norm_first=True), 2)
    return nn.ModuleDict({"inp": nn.Linear(24, d), "enc": enc, "dec": dec, "out": nn.Linear(d, 1)})


def step(m, x, pad, opt):
    h = m["inp"](x)
    h = m["enc"](h, src_key_padding_mask=pad)
    h = m["dec"](h, src_key_padding_mask=pad)
    loss = m["out"](h).squeeze(-1).pow(2).mean()
    opt.zero_grad()
    loss.backward()
    opt.step()
    return loss


for name in ("cpu", "mps"):
    if name == "mps" and not torch.backends.mps.is_available():
        print("mps not available")
        continue
    dev = torch.device(name)
    torch.manual_seed(0)
    m = model().to(dev)
    n_par = sum(p.numel() for p in m.parameters())
    opt = torch.optim.AdamW(m.parameters(), 1e-3)
    for bs in (32, 128):
        x = torch.randn(bs, 200, 24, device=dev)
        pad = torch.zeros(bs, 200, dtype=torch.bool, device=dev)
        pad[:, 150:] = True
        for _ in range(3):
            step(m, x, pad, opt)
        if name == "mps":
            torch.mps.synchronize()
        t = time.perf_counter()
        for _ in range(20):
            step(m, x, pad, opt)
        if name == "mps":
            torch.mps.synchronize()
        s = (time.perf_counter() - t) / 20
        print(f"{name} params {n_par / 1e6:.2f} M  batch {bs}: {s * 1000:.0f} ms/step, {bs / s:.0f} sequences/s")
