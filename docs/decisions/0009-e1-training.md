# 0009. E1: the lightcurve encoder-decoder, first training

Date: 2026-11-27. Spec: `.kiro/specs/s6-e1-training/`. Status: **pass mark not met. Chosen 2026-11-27, by the default (choice 1 of section 5): stop E1 here and write it up.** Everything is on the dev tiles (M44 and T CrB). Tabby was not touched. The injected benchmark was not used.

## Result in one paragraph

Encoder M (trained on M44, 4 layers, width 128, 1.19 million parameters) predicts the same thing as the star median on T CrB. Its RMSE is 0.1885 mag against 0.1885 for the median, a difference of -0.0001 (95% interval -0.0004 to +0.0003). It beats linear interpolation (0.2269) and the median of the 5 nearest same-series points (0.1954), but those are worse than the median itself, so the margin means little. The pass mark was to beat all three baselines, and it does not beat the median. Encoder T (trained on T CrB, 171 stars) is worse than the median on M44 by 0.21 mag. This is a negative result and it counts as done (`plan.md` 9.0). It is also the failure the plan predicted: "with 0.15 mag noise and sparse sampling, the model may learn the sampling pattern more than the star".

## What was run

| | Encoder M | Encoder T |
|---|---|---|
| Trained on | M44, 383 stars (42 set aside for early stopping) | T CrB, 154 stars (17 set aside) |
| Scored on | T CrB, 171 stars, 1,026 masked samples | M44, 425 stars, 2,550 masked samples |
| Best epoch (stopped at epoch 24, patience 10) | 14 | 14 |
| Time | 18 s per epoch on the Mac GPU, about 8 minutes | 7 s per epoch |

Both used the defaults of the spec: 200 tokens, 30 to 50% masked, half random and half one decade, 20 fresh draws per star per epoch, AdamW at 1e-3 with a 2-epoch warm-up and cosine decay. The Mac was fast enough. Task 1 measured about 900 sequences per second on the Apple GPU.

## Encoder M on T CrB (the pass mark)

RMSE on hidden detections, in mag, with a 95% bootstrap interval over stars. The difference is the model minus the baseline, so negative is better.

| Masking | Model | Median | Interpolation | Nearest 5, same series | Model minus median | Minus interpolation | Minus nearest 5 |
|---|---|---|---|---|---|---|---|
| Random points | 0.1885 (0.1767 to 0.2003) | 0.1885 | 0.2269 | 0.1954 | -0.0001 (-0.0004 to +0.0003), not shown | -0.0384, better | -0.0069, better |
| One decade | 0.1864 (0.1751 to 0.1973) | 0.1864 | 0.2299 | 0.2028 | -0.0000 (-0.0004 to +0.0004), not shown | -0.0434, better | -0.0164, better |

By era, pooled over both modes, the model and the median are equal in every era: before 1953 0.1803 against 0.1806, 1953 to 1969 0.2223 against 0.2213, after 1969 0.2080 against 0.2072.

## Why it fails: it learned to predict the median

- **Its predictions are nearly constant.** On T CrB the hidden-detection predictions have mean -0.015 mag and standard deviation 0.012. No prediction was further than 0.1 mag from zero (the star's median) at the 0.1% precision of the check. The model is the median predictor with a small offset.
- **It does not do better on the stars it trained on, or on same-tile stars.** On 80 of its own training stars it beats the median by 0.003 mag (interval 0.001 to 0.005). On the 42 same-tile stars set aside for early stopping, which share plates with the training stars, the difference is -0.0005 (-0.0019 to +0.0009). So the failure is not a plate leak to the held-out tile: there was no plate or star effect for it to learn even where it was allowed to.
- **The noise dominates.** The median's RMSE (0.18 to 0.19 mag) is about the size of one measurement's noise (0.15 mag recorded error). The stars are mostly constant, so after the median there is little left to predict. Interpolation is worse than the median because it averages in the noise of two neighbours. The median of 5 same-series neighbours is worse for the same reason, and that also says the series zero-points are small after detrending.
- **The one star with structure shows nothing either.** T CrB itself has the largest error (median 0.487, model 0.485). A hidden stretch of its 1946 eruption cannot be predicted from the rest.
- **The model can fit.** On one fixed batch of 64 samples it takes the loss from 174 to 1.9 in 100 steps and 0.9 in 600 (the median predictor scores 174 on the same batch, nearly all of it from limits). The gradients, mask and loss work. It cannot generalise because there is nothing general to learn here.
- **Encoder T fails the other way.** It is 2.25 mag RMSE on M44 in 1953 to 1969 and 0.195 before 1953 against 0.172 for the median. T CrB's training data holds 3,448 rows in 1953 to 1969 (M44 has 4,522), so the era is not absent. I have not found the cause. A guess is a series id or time that T CrB's few stars barely saw, but I did not test it. T CrB gives 154 training stars, and it was not a gate.

## Findings about the data

- **60 to 80% of the limit rows are not upper limits of a faint star.** The limit is deeper than the star's own median magnitude on 80% of M44's limit rows and 61% of T CrB's. The star should have been detected and was not, which is the known DASCH issue of missing points faking deep limits (`astro-domain.md`). L1 kills a vanished star on these, and E1 keeps them as tokens as the plan says. The report row "limits predicted too bright" is 88% for the median predictor on M44 and 68 to 70% on T CrB, which is a property of the data and not of any model. The model learns to put limit tokens fainter (0.0 to 0.1% too bright on T CrB), which costs nothing for the detections because the limit flag is visible.
- **After cleaning, limits are 6% (T CrB) and 12% (M44) of rows,** not the 34 to 45% of the raw tables. The rest were removed by cleaning tags.
- **The meteor policy is `reject`,** so the dataset has no meteor rows at all.
- **Both encoders had their best validation loss at epoch 14 and stopped at epoch 24** (patience 10). For encoder M the training loss kept falling (1.35 at epoch 24) while the validation loss stayed above its best of 1.65.

## What was built (it stays, whatever you choose)

`ml/sequences.py`, `dataset.py`, `masking.py`, `baselines.py`, `evaluate.py`, `datacard.py` (numpy, in the fast suite), and `ml/lc_encdec/model.py`, `train.py`, `report.py` (torch, with `importorskip`). Commands: `computers ml build [--card]`, `computers ml train --config`, `computers ml eval --run`. Configs in `configs/e1/`. Data card `docs/data-cards/lc-sequences-v1.md`, model card `docs/model-cards/lc-encdec-v0.md`. The torch dependency is in the group `ml` (`uv sync --group ml`). A new layer rule keeps `ml/` to `data` and `l1`. Runs write to `data/runs/<name>/` (git-ignored), and `scripts/s6_diagnostics.py` and `scripts/s6_overfit.py` are the throwaway checks above.

## Not done

The third encoder (both tiles, for real use) was not trained. The spec said to show you this table first, and an encoder that predicts the median has no use downstream.

## 5. Choices for you

1. **Accept the negative result and stop E1 here.** The write-up counts as done. Sprint 7 would then drop its E1 parts (probes, retrieval, the timestamp-shuffle test, the anomaly comparison) and keep what does not need an encoder. The plan's L2 stays on the hand-made features (`0008`). This is my recommendation if you do not want to spend more on E1.
2. **One bounded round of ablations on encoder M first** (about 10 minutes of training each, with the same pass mark): (a) no limit tokens, since most are contradictory; (b) no series and kind embeddings, to remove the shortcut that fails across tiles; (c) a task with more structure, with masks one to three decades wide and values detrended more strictly. If none beats the median with an interval excluding zero, stop as in choice 1. I expect this to fail, because the median's error is already the measurement noise, but (a) is cheap and removes the one data problem I found.
3. **Get more data** (more tiles, which means asking DASCH) before trying again. The plan mentions this in 9.1. It is the real limit: 596 stars, mostly constant.
4. **Change what E1 is for.** A reconstruction task on quiet stars may be the wrong objective. A contrastive or supervised target on the injected signals would test structure directly, but that is a different design from the plan's 9.1.

## 6. What was chosen (2026-11-27)

You said to continue with the defaults, which is my recommendation: choice 1. E1 stops here as a measured negative result.

1. **Sprint 7 is cut.** Its exit was a gate decision on E1 "written up, either way". This record is that write-up: E1 does not enter L2, because it equals the star median and the gate needed it to beat the feature baseline on injection recovery. The probes, the retrieval test, the anomaly comparison and the timestamp-shuffle test have no encoder to run on.
2. **Track D1 (erase instrument signal from the embeddings) is moot.** Its own first step says to stop if the vectors barely move when magnitudes are swapped, and write that down. This encoder's output barely depends on the magnitudes at all (predictions have a standard deviation of 0.012 mag). D2 (the judge's sensitivity to forceful argument) is unaffected and stays with Track B.
3. **L2 stays on the hand-made features and the plain ranker** (`0008`). Plan 7.2 (embedding scores) is not built. The isolation forest stays as the anomaly baseline.
4. **The code stays.** The dataset, masking, baselines, evaluation harness, model and training script are tested, and a later attempt (more tiles from DASCH, or a different objective) starts from them. Nothing here is deleted. The `ml` dependency group stays optional.
5. **No third encoder was trained.**
6. **Reopening E1** needs one of: more tiles (the data limit), or a different objective, as in choices 3 and 4. Neither is planned.

