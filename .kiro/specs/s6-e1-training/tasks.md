# Tasks: S6 E1, the lightcurve encoder-decoder

- [x] 1. Spike: can the Mac train this?
  - Add the pinned `ml` group, check that MPS works, and time one forward and backward step and one epoch's worth of a dummy 200-token model of the chosen size. Write the numbers down. If a full run would take more than a few hours, stop and report.
  - _R6.2, design "Timing"_

- [x] 2. Layer rule and dependency
  - Add `ml/` to the layer test (imports `data` and `l1` only; no LLM client), with a case that breaks it and is caught.
  - _R6.1_

- [x] 3. Sequence spec and dataset
  - `ml/sequences.py` and `sequence_spec.json`: tokens, the series-id table, even sampling across the century, padding mask, the join to `magcal_local_rms`. Tests: median subtraction and no scatter division (a shift changes nothing, a scale does), limits carry depth and no magnitude, every bin of the century is covered, fresh draws differ and are reproducible, padding mask, no injected curve in the data.
  - _R1_

- [x] 4. Data card
  - Star counts per tile, points-per-star distribution, series mix per decade and the share of upper limits, written by a script from the real tiles.
  - _R1.7_

- [x] 5. Masking and baselines
  - `ml/masking.py` (both modes, 30 to 50%, seeded, timestamps kept) and `ml/baselines.py`. Property tests: the mask fraction is in range, the decade mode hides a contiguous decade, a hidden token loses its value and keeps its time; each baseline is exact on a constant and linear interpolation is exact on a line.
  - _R2.1, R4.2_

- [x] 6. Evaluation harness
  - `ml/evaluate.py`: masked error, bootstrap by star, era split, share of limit violations. Tested with a perfect predictor (zero error) and a known-bad one.
  - _R4_

- [x] 7. Model and loss
  - `ml/lc_encdec.py`: encoder, decoder, the one-sided loss. Tests (torch): shapes, padding invariance, the loss is zero for a prediction fainter than the limit and positive for a brighter one on both sides of the boundary, unmasked and padded tokens carry no gradient, the same seed gives the same first loss.
  - _R2.2 to R2.4, R3_

- [x] 8. Training script
  - `computers ml train --config`: config, loop, checkpoint, resume, early stop, run directory. Test (CPU, tiny): resuming gives the same later losses as an uninterrupted run, and no secret reaches a log.
  - _R5_

- [x] 9. Train and evaluate
  - Encoders M and T, then the evaluation of each on the other tile. Show you the table before the third encoder is trained. You decide whether the result passes.
  - _R4, decision 2_

- [x] 10. Decision record and cards
  - `0009`, the model card, and the plan, steering and spec updates. The third encoder (MT) was NOT trained: the result is negative and it waits for your choice.
  - _R7_

- [x] 11. Exit test and report
  - `uv run pytest` (without the `ml` group, then with it), `ruff`, secret scan, schema, thresholds, snapshot and `bench check`, `pre-commit run --all-files`. Report and wait before Sprint 7.
