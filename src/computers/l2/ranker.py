"""The ranker: is this trigger worth an LLM call? (plan 7.4, spec S5 R3)

A logistic regression over a few inputs read from a trigger and its star, calibrated with a
sigmoid. It ranks triggers, and a threshold on its score is chosen on the dev split. The
probability is relative to the benchmark's mix of planted signals and constant stars, not
to the sky, so read it as a score that orders triggers and not as a rate of real events.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression

from computers.l1.triggers import KINDS, Trigger, TriggerInput

__all__ = [
    "ALL_COLUMNS",
    "VARIANTS",
    "RankerModel",
    "design",
    "fit",
    "group_folds",
    "load",
    "predict",
    "save",
    "trigger_row",
]

BASE = ("amplitude_mag", "log_n_evidence", "single_plate", "not_detrended", *(f"kind_{k}" for k in KINDS))
ALL_COLUMNS = (*BASE, "n_series", "anomaly")
# The input sets the report compares. "amplitude" is the trivial baseline (rank by trigger strength).
VARIANTS: dict[str, tuple[str, ...]] = {
    "amplitude": ("amplitude_mag",),
    "plain": BASE,
    "plain+anomaly": (*BASE, "anomaly"),
    "plain+series": (*BASE, "n_series"),
    "full": ALL_COLUMNS,
}


def design(rows: Sequence[Mapping[str, float | str | bool]], columns: Sequence[str]) -> np.ndarray:
    """The input matrix of trigger rows. A row has `kind`, `amplitude_mag`, `n_evidence`, `single_plate`,
    `detrended`, `n_series` and `anomaly`. `detrended` False becomes `not_detrended` 1."""
    for c in columns:
        if c not in ALL_COLUMNS:
            raise KeyError(f"unknown ranker input {c!r}; known: {ALL_COLUMNS}")
    out = np.zeros((len(rows), len(columns)))
    for i, r in enumerate(rows):
        values = {
            "amplitude_mag": float(r["amplitude_mag"]),
            "log_n_evidence": float(np.log(max(int(r["n_evidence"]), 1))),
            "single_plate": float(bool(r["single_plate"])),
            "not_detrended": float(not r["detrended"]),
            "n_series": float(r.get("n_series", np.nan)),
            "anomaly": float(r.get("anomaly", np.nan)),
            **{f"kind_{k}": float(r["kind"] == k) for k in KINDS},
        }
        out[i] = [values[c] for c in columns]
    if np.isnan(out).any():
        raise ValueError(f"a requested input is missing or NaN in some row: {list(columns)}")
    return out


def trigger_row(t: Trigger, inp: TriggerInput) -> dict[str, float | str | bool | int]:
    """The ranker's view of a trigger: its own numbers and the number of series that carry it."""
    ev = np.array(t.evidence, dtype=int)
    n_series = len(set(inp.curve.series[ev])) if len(ev) else 0
    return {
        "kind": t.kind, "amplitude_mag": float(t.amplitude_mag), "n_evidence": len(t.evidence),
        "single_plate": bool(t.single_plate), "detrended": bool(t.detrended), "n_series": n_series,
        "epoch_start": float(t.epoch_start), "epoch_end": float(t.epoch_end),
    }  # fmt: skip


@dataclass(frozen=True)
class RankerModel:
    columns: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray
    clf: object  # a fitted CalibratedClassifierCV, or a LogisticRegression when too few negatives to calibrate
    calibrated: bool
    n_pos: int
    n_neg: int


def fit(x: np.ndarray, y: np.ndarray, columns: Sequence[str], *, seed: int = 0) -> RankerModel:
    """Fit on a matrix `x` and labels `y` (1 for a real planted signal, 0 for a false trigger)."""
    y = np.asarray(y, dtype=int)
    n_pos, n_neg = int(y.sum()), int((y == 0).sum())
    if n_pos < 2 or n_neg < 2:
        raise ValueError(f"need at least 2 of each class, got {n_pos} positive and {n_neg} negative")
    mean = x.mean(axis=0)
    scale = np.where(x.std(axis=0) > 0, x.std(axis=0), 1.0)
    z = (x - mean) / scale
    base = LogisticRegression(C=1.0, class_weight="balanced", max_iter=1000, random_state=seed)
    cv = min(3, n_neg)
    if cv >= 2:
        clf = CalibratedClassifierCV(base, method="sigmoid", cv=cv).fit(z, y)
        return RankerModel(tuple(columns), mean, scale, clf, True, n_pos, n_neg)
    return RankerModel(tuple(columns), mean, scale, base.fit(z, y), False, n_pos, n_neg)


def predict(model: RankerModel, x: np.ndarray) -> np.ndarray:
    """Calibrated probability that each row is a real signal."""
    return model.clf.predict_proba((x - model.mean) / model.scale)[:, 1]


def group_folds(groups: Sequence[int], k: int = 5, seed: int = 0) -> dict[int, int]:
    """Assign each group (a host star) to one of `k` folds, so no star is in training and test together."""
    ids = sorted(set(groups))
    order = np.random.default_rng(seed).permutation(len(ids))
    return {ids[j]: int(rank % k) for rank, j in enumerate(order)}


def save(model: RankerModel, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load(path: str | Path) -> RankerModel:
    model = joblib.load(path)
    if not isinstance(model, RankerModel):
        raise TypeError(f"{path} does not hold a RankerModel")
    return model
