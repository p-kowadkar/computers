"""The encoder-decoder and its loss (spec S6 R2.2 to R2.4, R3). Needs the `ml` group."""
import numpy as np
import pytest
from test_sequences import star_from
from trig_helpers import flat

torch = pytest.importorskip("torch")

from computers.ml import masking as M
from computers.ml import sequences as S
from computers.ml.lc_encdec import model as L

SMALL = L.Config(d_model=32, n_layers=2, n_heads=2, n_dec_layers=1, dropout=0.0)


def batch(n=4, length=60, k=40, seed=0):
    items = []
    for i in range(n):
        years, mag = flat(k, noise=0.1, seed=seed + i)
        mag = mag.copy()
        mag[::7] = np.nan
        seq = S.make_sequence(star_from(years, mag, limit=14.0), np.random.default_rng(i), length)
        items.append(M.apply_mask(seq, np.random.default_rng(100 + i), mode="random", fraction=0.4))
    return items


def test_shapes_and_a_scalar_loss():
    m = L.LCEncDec(SMALL)
    b = L.to_batch(batch())
    assert m(b).shape == (4, 60) and m.encode(b).shape == (4, 60, 32)
    assert m.loss(b).ndim == 0 and torch.isfinite(m.loss(b))


def test_padding_does_not_change_the_output_on_real_tokens():
    torch.manual_seed(0)
    m = L.LCEncDec(SMALL).eval()
    items = batch(length=60)
    b = L.to_batch(items)
    base = m(b)
    changed = {k: v.clone() for k, v in b.items()}
    pad = b["pad"]
    assert pad.any()
    changed["value_in"][pad] = 9.0  # garbage in the padded slots
    changed["t"][pad] = 50.0
    changed["series"][pad] = 3
    out = m(changed)
    assert torch.allclose(base[~pad], out[~pad], atol=1e-5)
    longer = L.to_batch(batch(length=90))  # the same stars padded further
    out_long = m(longer)
    real = ~b["pad"]
    assert torch.allclose(base[real], out_long[:, :60][real], atol=1e-4)


def test_the_same_seed_gives_the_same_first_loss():
    b = L.to_batch(batch())
    torch.manual_seed(3)
    a = L.LCEncDec(SMALL).eval().loss(b)
    torch.manual_seed(3)
    c = L.LCEncDec(SMALL).eval().loss(b)
    assert a.item() == c.item()


def loss_of(pred, value, err, is_limit, hidden, pad=None):
    t = lambda x, dt: torch.as_tensor(x, dtype=dt).reshape(1, -1)
    pad_t = t([False] * len(value) if pad is None else pad, torch.bool)
    return L.reconstruction_loss(t(pred, torch.float32), t(value, torch.float32), t(err, torch.float32),
                                 t(is_limit, torch.bool), t(hidden, torch.bool), pad_t)  # fmt: skip


def test_a_prediction_fainter_than_the_limit_costs_nothing_and_a_brighter_one_costs_something():
    # the limit's depth is +2.0 mag above the median, so the star is fainter than +2.0
    args = {"value": [2.0], "err": [0.1], "is_limit": [True], "hidden": [True]}
    assert loss_of([3.5], **args).item() == 0.0  # fainter than the limit: not an error
    assert loss_of([2.0], **args).item() == 0.0  # exactly at the limit: not an error
    assert loss_of([1.9], **args).item() > 0.0  # just brighter than the limit: an error
    assert loss_of([0.0], **args).item() > loss_of([1.5], **args).item()  # more wrong, more cost
    assert loss_of([1.85], **args).item() == pytest.approx((0.15 / 0.15) ** 2, rel=1e-5)


def test_a_detection_costs_the_squared_error_over_its_error_with_a_floor():
    args = {"value": [0.0], "is_limit": [False], "hidden": [True]}
    assert loss_of([0.2], err=[0.1], **args).item() == pytest.approx(4.0, rel=1e-5)
    assert loss_of([0.2], err=[0.001], **args).item() == pytest.approx(16.0, rel=1e-5)  # floored at 0.05
    assert loss_of([-0.2], err=[0.1], **args).item() == pytest.approx(4.0, rel=1e-5)  # symmetric for a detection


def test_unmasked_and_padded_tokens_are_not_in_the_loss():
    v = [0.0, 0.0, 0.0]
    big = [5.0, 5.0, 5.0]
    base = loss_of([0.1, 0, 0], v, [0.1] * 3, [False] * 3, [True, False, True], pad=[False, False, True])
    wrong_elsewhere = loss_of([0.1, 5.0, 5.0], v, [0.1] * 3, [False] * 3, [True, False, True], pad=[False, False, True])
    assert base.item() == wrong_elsewhere.item() == pytest.approx(1.0, rel=1e-5)
    assert big and loss_of([0.0], [0.0], [0.1], [False], [False]).item() == 0.0  # nothing hidden: zero, no division error


def test_unmasked_and_padded_tokens_carry_no_gradient():
    pred = torch.zeros(1, 3, requires_grad=True)
    loss = L.reconstruction_loss(
        pred, torch.ones(1, 3), torch.full((1, 3), 0.1), torch.zeros(1, 3, dtype=torch.bool),
        torch.tensor([[True, False, True]]), torch.tensor([[False, False, True]]),
    )  # fmt: skip
    loss.backward()
    assert pred.grad[0, 0] != 0 and pred.grad[0, 1] == 0 and pred.grad[0, 2] == 0


def test_one_optimiser_step_lowers_the_loss_on_the_same_batch():
    torch.manual_seed(0)
    m = L.LCEncDec(SMALL)
    b = L.to_batch(batch())
    opt = torch.optim.AdamW(m.parameters(), 1e-2)
    first = m.loss(b).item()
    for _ in range(30):
        opt.zero_grad()
        m.loss(b).backward()
        opt.step()
    assert m.loss(b).item() < first
