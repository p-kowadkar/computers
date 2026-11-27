"""Evaluation of masked reconstruction (spec S6 R4).

An `EvalSet` is a fixed list of masked samples of held-out stars, so the model and every
baseline predict the same hidden tokens. Errors are in magnitudes. The bootstrap resamples
stars, not tokens, because the tokens of one star are not independent.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from computers.ml import masking as M
from computers.ml import sequences as S

__all__ = [
    "ERAS",
    "EvalSet",
    "TokenErrors",
    "build_eval_set",
    "difference",
    "score_predictions",
    "summary",
]

ERAS = (("before 1953", -np.inf, 1953.0), ("1953 to 1969", 1953.0, 1969.0), ("after 1969", 1969.0, np.inf))


@dataclass(frozen=True)
class EvalSet:
    items: tuple[tuple[int, M.Masked], ...]  # (index of the star in the list given, masked sample)
    n_stars: int


def build_eval_set(
    stars: Sequence[S.Star], *, k_per_mode: int = 3, seed: int = 0, length: int = S.LENGTH
) -> EvalSet:
    """`k_per_mode` masks of each mode for each star, fixed by the seed."""
    items = []
    for i, star in enumerate(stars):
        for mi, mode in enumerate(M.MODES):
            for k in range(k_per_mode):
                rng = np.random.default_rng([seed, i, mi, k])
                seq = S.make_sequence(star, rng, length)
                items.append((i, M.apply_mask(seq, rng, mode=mode)))
    return EvalSet(tuple(items), len(stars))


@dataclass(frozen=True)
class TokenErrors:
    """Prediction minus truth for every hidden detection, and which hidden limits were predicted brighter than the limit."""

    star: np.ndarray  # int, star index
    mode: np.ndarray  # int, index into M.MODES
    era: np.ndarray  # int, index into ERAS
    err: np.ndarray  # magnitudes, prediction minus truth
    limit_star: np.ndarray
    limit_mode: np.ndarray
    limit_brighter: np.ndarray  # bool: the prediction is brighter than the limit (an error)
    n_stars: int


def score_predictions(evalset: EvalSet, preds: Sequence[np.ndarray]) -> TokenErrors:
    """Errors of one method, given its prediction for every sample of the set (arrays of the sample's length)."""
    if len(preds) != len(evalset.items):
        raise ValueError(f"{len(preds)} predictions for {len(evalset.items)} samples")
    st, mo, er, ee, lst, lmo, lbr = [], [], [], [], [], [], []
    for (i, m), p in zip(evalset.items, preds, strict=True):
        s = m.seq
        mi = M.MODES.index(m.mode)
        det = m.hidden & ~s.is_limit
        year = s.t[det] + S.T0
        n = int(det.sum())
        st.append(np.full(n, i))
        mo.append(np.full(n, mi))
        er.append(np.array([next(j for j, (_, lo, hi) in enumerate(ERAS) if lo <= y < hi) for y in year], dtype=int))
        ee.append(np.asarray(p, dtype=float)[det] - s.value[det])
        lim = m.hidden & s.is_limit
        k = int(lim.sum())
        lst.append(np.full(k, i))
        lmo.append(np.full(k, mi))
        # magnitudes run backwards: a prediction smaller than the limit's depth says the star is brighter than the limit
        lbr.append(np.asarray(p, dtype=float)[lim] < s.value[lim])
    cat = np.concatenate
    return TokenErrors(
        cat(st).astype(int), cat(mo).astype(int), cat(er).astype(int), cat(ee), cat(lst).astype(int), cat(lmo).astype(int),
        cat(lbr).astype(bool), evalset.n_stars,
    )  # fmt: skip


def _rmse(te: TokenErrors, sel: np.ndarray, w: np.ndarray) -> float:
    e2 = np.bincount(te.star[sel], weights=te.err[sel] ** 2, minlength=te.n_stars)
    n = np.bincount(te.star[sel], minlength=te.n_stars)
    return float(np.sqrt((w * e2).sum() / max((w * n).sum(), 1e-12)))


def _select(te: TokenErrors, mode: str | None, era: int | None) -> np.ndarray:
    sel = np.ones(len(te.err), dtype=bool)
    if mode is not None:
        sel &= te.mode == M.MODES.index(mode)
    if era is not None:
        sel &= te.era == era
    return sel


def _boot_weights(n_stars: int, n_boot: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_stars, size=(n_boot, n_stars))
    return np.stack([np.bincount(d, minlength=n_stars) for d in draws]).astype(float)


def summary(te: TokenErrors, *, n_boot: int = 1000, seed: int = 0) -> dict:
    """RMSE with a bootstrap 95% interval, the median absolute error, and the share of limits predicted too bright.

    Reported for each masking mode and for each era, pooled over modes.
    """
    w_all = _boot_weights(te.n_stars, n_boot, seed)
    out: dict = {"by_mode": {}, "by_era": {}}

    def cell(sel: np.ndarray) -> dict:
        if not sel.any():
            return {"n": 0, "rmse": float("nan"), "low": float("nan"), "high": float("nan"), "median_abs": float("nan")}
        boots = np.array([_rmse(te, sel, w) for w in w_all])
        return {
            "n": int(sel.sum()), "rmse": _rmse(te, sel, np.ones(te.n_stars)),
            "low": float(np.percentile(boots, 2.5)), "high": float(np.percentile(boots, 97.5)),
            "median_abs": float(np.median(np.abs(te.err[sel]))),
        }  # fmt: skip

    for mode in M.MODES:
        out["by_mode"][mode] = cell(_select(te, mode, None))
        lim = te.limit_mode == M.MODES.index(mode)
        out["by_mode"][mode]["limit_brighter_share"] = float(te.limit_brighter[lim].mean()) if lim.any() else float("nan")
        out["by_mode"][mode]["n_limits"] = int(lim.sum())
    for j, (name, _, _) in enumerate(ERAS):
        out["by_era"][name] = cell(_select(te, None, j))
    return out


def difference(a: TokenErrors, b: TokenErrors, *, mode: str | None = None, n_boot: int = 1000, seed: int = 0) -> dict:
    """RMSE of `a` minus RMSE of `b` on the same tokens, with a bootstrap 95% interval over stars.

    Negative means `a` is better. The same resampled stars are used for both methods.
    """
    if not (np.array_equal(a.star, b.star) and np.array_equal(a.mode, b.mode)):
        raise ValueError("the two methods must be scored on the same tokens")
    sel = _select(a, mode, None)
    w_all = _boot_weights(a.n_stars, n_boot, seed)
    d = np.array([_rmse(a, sel, w) - _rmse(b, sel, w) for w in w_all])
    return {
        "difference": _rmse(a, sel, np.ones(a.n_stars)) - _rmse(b, sel, np.ones(b.n_stars)),
        "low": float(np.percentile(d, 2.5)), "high": float(np.percentile(d, 97.5)),
    }  # fmt: skip


def predict_all(evalset: EvalSet, f: Callable[[M.Masked], np.ndarray]) -> list[np.ndarray]:
    """Run a per-sample predictor (a baseline) over the whole set."""
    return [f(m) for _, m in evalset.items]
