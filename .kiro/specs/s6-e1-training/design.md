# Design: S6 E1, the lightcurve encoder-decoder

## Layout

```
src/computers/ml/
  sequences.py    tokens, sampling, padding, the spec (no torch)
  masking.py      the two masking modes (numpy, seeded)
  baselines.py    median, linear interpolation, nearest-5 in series (numpy)
  evaluate.py     masked error, bootstrap by star, era split (numpy)
  dataset.py      build stars from a tile, save and load
  datacard.py     the data card from the stars
  lc_encdec/      model.py (model and one-sided loss), train.py, report.py (torch); this package existed as a placeholder
configs/e1/       one JSON file per run
docs/data-cards/lc-sequences-v1.md
docs/model-cards/lc-encdec-v0.md
docs/decisions/0009-e1-training.md
data/runs/<run_id>/   config.json, metrics.jsonl, checkpoints (git-ignored)
```

Everything except `lc_encdec.py` and `train.py` is numpy, so the data, masking, baselines and evaluation are tested in the fast suite without PyTorch. The two torch files use `pytest.importorskip("torch")`. `ml/` imports `data` and `l1` only. A new layer rule covers it (R6.1).

## The split

```
Encoder M:  train M44 stars (425)        evaluate T CrB stars (172)
Encoder T:  train T CrB stars (172)      evaluate M44 stars (425)
Encoder MT: train both (597)             for real use; never scored on dev
```

Within each training tile, 10% of stars (fixed seed) are set aside for early stopping. They share plates with the training stars, so their loss over-reads the plate effect. It is used only to stop training, and never reported as a result. Tabby is not touched.

## Tokens and the spec (R1)

For each curve row that is in the sample (a good detection, or a non-detection with a finite limit):

| Field | Definition |
|---|---|
| `dmag` | `mag - median(good mag)`; 0 and absent for a non-detection |
| `err` | `magcal_local_rms` of the row, in mag; a non-detection has none, and the star's median error is used |
| `is_limit` | 1 for a non-detection |
| `limit_depth` | `limit - median(good mag)` for a non-detection, otherwise 0 |
| `series_id` | integer id of the series, from a table written in the spec |
| `kind` | series kind: narrow, patrol, meteor, other |
| `t` | years since 1880, fed to sinusoidal features at scales 1/365, 1/30, 1, 10, 100 years |

The time encoding is `[sin(2πt/s), cos(2πt/s)]` over those scales, so 10 features. The magnitude is not divided by any scatter: amplitude is the signal. Rows that L1 rejected (a rejecting tag) are not in the sample. Non-detections are in the sample, because they carry information.

`sequence_spec.json` stores the token fields, the scales, the length (200), the series-id table, the minimum good points, the sampling rule and a version. Its hash goes into every run directory.

Sampling a star with more than 200 usable rows: divide the 1880 to 1990 span into 200 equal bins, take one row at random from each non-empty bin, and fill the shortfall from the largest remaining bins. A fresh draw each epoch is seeded by (run seed, star, epoch), so a resumed run repeats it.

## Masking (R2)

Fraction f drawn uniformly from 30 to 50% per sample. In "random" mode, f of the tokens are hidden at random. In "decade" mode, a random decade inside the star's span is hidden entirely, and random tokens are added until f is reached. A hidden token keeps its time, series and kind and loses `dmag` (or `limit_depth` for a limit) and `err`. A mask token vector replaces the hidden value.

## Model (R3)

- Input: a linear map of the token features (hidden-value channels, `is_limit`, time features, learned embeddings for series id and kind) to width `d`.
- Encoder: `L=4` pre-norm transformer layers, `d=128`, 4 heads, feed-forward 4d, dropout 0.1, key padding mask.
- Decoder: 2 layers of the same kind, and a linear head to one magnitude offset. It is used only in training, and the embedding in Sprint 7 reads the encoder output.
- The predicted offset is in magnitudes relative to the star's median, so `0` is the star median baseline.

## Loss (R2)

For a masked detection with error `e`: `((pred - dmag) / max(e, 0.05))^2`. The floor of 0.05 mag keeps a single tiny error from dominating. For a masked limit at depth `D` (limit minus median): predictions are offsets, so the prediction is brighter than the limit when `pred < D`. The loss is `max(D - pred, 0)^2 / 0.15^2`, which is zero when `pred >= D` (fainter than or at the limit) and grows when the model says the star is brighter than the limit. The 0.15 mag is a single measurement's RMS from the astro-domain notes. The batch loss is the mean over masked tokens. Padded and unmasked tokens are excluded.

Tests pin both sides of the limit boundary: a prediction fainter than the limit must not be penalised.

## Baselines and evaluation (R4)

For each held-out star, `K` masks are drawn (K=5, fixed seed) from the same two modes. Each baseline predicts every masked detection from the unmasked tokens of the same sample:

- **median**: 0 (the star median of the sample).
- **linear interpolation**: between the nearest unmasked detections before and after in time, the nearer one when a side is missing.
- **nearest 5 in series**: the median of the 5 nearest unmasked detections of the same series (nearest in time), falling back to the nearest 5 of any series.

The metric is the root mean square error and the median absolute error, in mag, over masked detections, then pooled over stars. The interval comes from resampling stars 1,000 times. For the model's difference from a baseline, the same resample is applied to both. The era split pools masked points before 1953, between 1953 and 1969, and after 1969.

For masked limits the report gives the share of predictions brighter than the limit, for the model and for each baseline.

## Training (R5)

`computers ml train --config configs/e1/m.json` reads the config (tile train/eval lists, model size, learning rate, batch size, epochs, patience, seed), builds the dataset, and runs AdamW with a cosine schedule. Each epoch writes a line to `metrics.jsonl` (train loss, early-stop loss, seconds) and a checkpoint (model, optimiser, scheduler, epoch, random states). `--resume` reads the last checkpoint. The run directory also holds the config, the spec hash, the code version and the snapshot digest.

A run is seeded, and the device (MPS or CPU) is written to the log. MPS may differ from CPU by float noise, so the reproducibility tests run on CPU.

## Dependency (R6)

`pyproject.toml` gets a group `ml = ["torch==<pinned>"]`. `uv sync --group ml` installs it. The fast suite does not need it.

## Risks

- **The model learns the sampling pattern, not the star.** With timestamps visible, and 100-year coverage that is similar for every star of a tile, "predict the median" can be hard to beat. The tile hold-out guards against plate memory. The timestamp-shuffle test in Sprint 7 is the real check.
- **Most training stars are constants.** Reconstruction of a quiet star is "predict the median plus noise". A model that beats interpolation there may still know nothing about variables. The evaluation is also on mostly quiet stars, so a pass says little about outbursts.
- **Encoder T has 172 stars.** It will probably be undertrained. The spec reports it and does not gate on it.
- **Timing.** Task 1 times a step before the rest is built. If a run would take more than a few hours on the Mac, the spec stops there and asks.
- **Mask tokens and limits.** A model can predict "brighter than limit" cheaply if the penalty is too small. The report's share of violations is the check.

## Findings during the build (see `docs/decisions/0009`)

- **The encoder equals the star median on T CrB.** The pass mark is not met.
- **The spec's loss and masks work.** The model fits a fixed batch (loss 174 to 0.9), padding does not change real outputs, resuming reproduces the losses.
- **`lc_encdec` was already a package,** so the torch code is in `ml/lc_encdec/` and not in a flat `ml/lc_encdec.py`.
- **Limit rows are mostly contradictory** (60 to 80% deeper than the star), which the spec did not know.
- **The time estimate was right:** 18 s per epoch, the whole run about 8 minutes.

