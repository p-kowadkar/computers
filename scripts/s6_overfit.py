"""Sprint 6 sanity check: can the model drive the loss far below the noise floor on one fixed batch?

If it can, gradients and capacity work and a failure to generalise is about the data. Throwaway; writes nothing.
"""
import numpy as np
import torch

from computers.ml import masking as M
from computers.ml import sequences as S
from computers.ml.lc_encdec import train as T
from computers.ml.lc_encdec.model import Config, LCEncDec, to_batch

stars = T.load_stars_for("data", ("m44",))[:64]
items = []
for i, s in enumerate(stars):
    rng = np.random.default_rng([0, i])
    items.append(M.apply_mask(S.make_sequence(s, rng), rng))
dev = T.pick_device("auto")
torch.manual_seed(0)
m = LCEncDec(Config(dropout=0.0)).to(dev)
b = to_batch(items, dev)
opt = torch.optim.AdamW(m.parameters(), 1e-3)
for step in range(601):
    opt.zero_grad()
    loss = m.loss(b)
    loss.backward()
    opt.step()
    if step in (0, 50, 100, 200, 400, 600):
        print(step, round(loss.item(), 3))
# the star-median predictor's loss on the same batch, for scale
with torch.no_grad():
    zero = torch.zeros_like(b["value"])
    from computers.ml.lc_encdec.model import reconstruction_loss as RL
    print("median predictor", round(RL(zero, b["value"], b["err"], b["is_limit"], b["hidden"], b["pad"]).item(), 3))
