"""The anomaly score: an isolation forest over the hand-made features (plan 7.1, spec S5 R2).

The score is higher for a stranger star. The imputer medians and the scaler are stored
with the forest, so a saved model gives the same score for the same features. Training
rows should be quiet stars: the forest then learns what quiet looks like.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from computers.l2.features import FEATURES

__all__ = ["AnomalyModel", "fit", "load", "save", "score"]

N_TREES = 200


@dataclass(frozen=True)
class AnomalyModel:
    features: tuple[str, ...]
    fill: np.ndarray  # median of each feature on the training rows (the imputer)
    mean: np.ndarray
    scale: np.ndarray
    forest: IsolationForest
    seed: int
    n_train: int
    snapshot_digest: str = ""


def _impute(x: np.ndarray, fill: np.ndarray) -> np.ndarray:
    x = np.array(x, dtype=float, copy=True)
    bad = ~np.isfinite(x)
    x[bad] = np.broadcast_to(fill, x.shape)[bad]
    return x


def fit(x: np.ndarray, *, seed: int = 0, snapshot_digest: str = "") -> AnomalyModel:
    """Fit on a matrix with one row per star and the columns of `FEATURES`. NaN is filled with the column median.

    A column that is NaN in every row has no median; it is filled with 0 and gets a scale of 1,
    so it contributes nothing and the model still loads and scores.
    """
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(FEATURES):
        raise ValueError(f"expected a matrix with {len(FEATURES)} columns, got shape {x.shape}")
    if x.shape[0] < 10:
        raise ValueError("an isolation forest needs at least 10 training rows")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN column warns, and is handled just below
        fill = np.nanmedian(np.where(np.isfinite(x), x, np.nan), axis=0)
    fill = np.where(np.isfinite(fill), fill, 0.0)
    z = _impute(x, fill)
    mean = z.mean(axis=0)
    scale = z.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    forest = IsolationForest(n_estimators=N_TREES, random_state=seed).fit((z - mean) / scale)
    return AnomalyModel(FEATURES, fill, mean, scale, forest, seed, int(x.shape[0]), snapshot_digest)


def score(model: AnomalyModel, x: np.ndarray) -> np.ndarray:
    """Anomaly score of each row: higher is stranger. Rows are imputed with the training medians."""
    x = np.atleast_2d(np.asarray(x, dtype=float))
    if x.shape[1] != len(model.features):
        raise ValueError(f"expected {len(model.features)} columns, got {x.shape[1]}")
    z = (_impute(x, model.fill) - model.mean) / model.scale
    return -model.forest.score_samples(z)


def save(model: AnomalyModel, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load(path: str | Path) -> AnomalyModel:
    model = joblib.load(path)
    if not isinstance(model, AnomalyModel):
        raise TypeError(f"{path} does not hold an AnomalyModel")
    if tuple(model.features) != FEATURES:
        raise ValueError("the saved model's feature list differs from the code's; refit it")
    return model
