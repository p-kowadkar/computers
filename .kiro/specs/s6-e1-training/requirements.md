# Requirements: S6 E1, the lightcurve encoder-decoder

**Intent.** Build and train, on the Mac, a transformer that reads a star's irregular 100-year history and reconstructs hidden magnitudes, and find out whether it beats three simple baselines on stars it has not seen. This sprint stops at "does the model learn anything beyond sampling and plate effects". Whether its vector helps rank or retrieve stars is Sprint 7.

**Source:** `plan.md` section 9.1 (Track E1) and 9.0 (shared rules), S6 in section 13; `docs/decisions/0004`, `0008`. Spec status: **draft, awaiting review**.

## Scope

In:
- The sequence spec and the dataset of 9.1: one token per observation, 200 tokens, evenly sampled across the century.
- A masked-reconstruction model: encoder, light decoder, the one-sided loss for upper limits.
- Three baselines for the same masked points, and an evaluation on stars from the other tile.
- A training script that runs from a config file, logs to a file, checkpoints and resumes, and runs on the Mac.
- A data card, a model card, and a decision record with the numbers.

Out:
- Embeddings, probes, retrieval, anomaly comparison, the timestamp-shuffle test and the fine-tuning head (Sprint 7).
- Any AWS use. The plan says E1 runs entirely on the Mac. If the Mac is too slow, the spec task 1 says so and stops.
- Changing L1, L2 or the benchmark.
- The Tabby tile. It stays untouched until a gate needs it.

## Facts found while drafting this spec

- **The data is small.** The dev tiles have 597 stars with 50 or more good points (M44 425, T CrB 172) and about 657,000 good detections. The median star has 1,171 (M44) or 742 (T CrB) good points, so a 200-token sample sees about a sixth of a star's history, and fresh draws each epoch are the main source of variety. Upper limits are 45% (M44) and 34% (T CrB) of all rows, which the model must handle.
- **361 of those 597 stars are benchmark hosts.** The Leavitt Set injects signals into them. The plan says benchmark hold-out stars are excluded from pretraining, which would leave 236 stars (178 on M44, 58 on T CrB).
- **Same-tile stars share plates.** A transformer that sees timestamps and series ids can memorise "the plate at this date reads 0.1 mag bright" from one star and use it on another star of the same tile. A split by star would reward that. A split by tile does not: M44 shares about no plates with T CrB.
- **The Mac is enough to try.** Apple M5 Pro, 24 GB. PyTorch is not installed. Task 1 installs it and times a training step before anything else is built.
- **Astromer 2's weights are not usable here.** The repository is TensorFlow and Keras, the weights are archived on Zenodo, and they were trained on 1.5 million MACHO curves. Its window of 200 matches ours, but a PyTorch model cannot load them, and MACHO spans years, not a century. This closes the plan's "[verify, Sprint 6]". Retrained from scratch it is also the model the plan wants us to build and be able to explain.
- **`Curve` does not carry the measurement error.** `magcal_local_rms` is in the silver measurements table. The dataset builder joins it by row.

## Decisions for you (defaults in bold)

1. **The split.** **Default: a two-way tile hold-out.** Encoder M is trained on M44 and evaluated on T CrB. Encoder T is trained on T CrB and evaluated on M44. Each encoder never sees a plate of the tile it is scored on. Because the benchmark hosts of the held-out tile are in the evaluation tile, the encoders can also score injected hosts in Sprint 7 without the plate leak. A third encoder on both tiles is trained at the end for real use. The alternative is the plan's rule (train on the 236 non-host stars only), which leaves too little data and still leaks plates within a tile.
2. **The pass mark.** **Default: encoder M beats every baseline on T CrB in both masking modes, with the 95% bootstrap interval (stars resampled) of the difference in error excluding zero.** Encoder T (172 training stars) is reported and does not gate the sprint. A fail is written up as a negative result.
3. **The framework.** **Default: PyTorch, pinned, in a new optional dependency group `ml`**, using the Apple GPU (MPS) when it works and the CPU otherwise. The core install stays small.
4. **The experiment tracker.** The plan says to pick one in this sprint. **Default: none. Each run writes `config.json`, `metrics.jsonl` and checkpoints into `data/runs/<run_id>/`, and the numbers that matter go into a decision record.** One person, one machine, and every run launched from a config file. A hosted tracker is a framework with no job here (principle 8). TensorBoard can read the JSONL later if you want charts.
5. **Astromer weights.** **Default: not used** (see the facts).
6. **Model size.** **Default: encoder 4 layers, width 128, 4 heads; decoder 2 layers; about 1 million parameters.** The data supports nothing larger. The width and depth are config values, and a wider run is a sweep for later.

## Acceptance criteria (EARS)

**R1. Sequence spec and dataset**
1. THE builder SHALL read the curve L1 read for each star (detrended when the star could be detrended, raw otherwise) and SHALL include every star with at least `min_good_points` good detections, upper limits included.
2. EACH token SHALL carry: the magnitude offset from the star's median good magnitude (not divided by any scatter), the measurement error `magcal_local_rms`, an is-upper-limit flag, the limit depth relative to the star's median, the plate-series id, and the series kind. A non-detection's magnitude offset SHALL be absent and its limit depth present.
3. TIME SHALL be years since 1880, passed to the model through sinusoidal features at scales from days to decades.
4. A curve with more than 200 usable rows SHALL be sampled evenly across the century, stratified by time, with a fresh random draw at each epoch. A shorter curve SHALL be padded and carry an attention mask.
5. THE sequence spec SHALL be written to a versioned `sequence_spec.json` with every choice above. A change to it SHALL be a new version.
6. THE dataset SHALL NOT contain injected curves, and SHALL NOT contain a star of the tile an encoder is evaluated on.
7. A data card SHALL give the star count per tile, the distribution of points per star, the series mix per decade, and the share of upper limits.

**R2. Masking and loss**
1. THE masking SHALL hide 30 to 50% of magnitudes, keep every timestamp, and in half the samples hide random points and in the other half one contiguous decade with random points added up to the fraction.
2. THE loss on a masked detection SHALL be the squared error divided by the measurement's variance (with a floor, so one tiny error cannot dominate).
3. THE loss on a masked upper limit SHALL be zero when the prediction is fainter than or equal to the limit and the squared excess brightness when it is brighter. A prediction fainter than the limit SHALL NEVER be penalised. (Magnitudes run backwards: brighter is a smaller number.)
4. UNMASKED and padded tokens SHALL NOT enter the loss.

**R3. Model**
1. THE encoder SHALL be a transformer over the tokens, with padding masked in attention, and the decoder SHALL be lighter and used in training only.
2. THE output on padded positions SHALL NOT change the output on real positions (padding invariance).
3. THE model SHALL be reproducible: the same seed, config and data SHALL give the same first-epoch loss on the same device.

**R4. Baselines and evaluation**
1. THE evaluation SHALL score masked detections on stars of the held-out tile only, with a fixed seed so every method sees the same masks.
2. THE baselines SHALL be: the star's median, linear interpolation in time between the nearest unmasked detections, and the median of the nearest 5 unmasked detections in the same series. A masked point with no unmasked detection on a side SHALL use the nearer side.
3. THE report SHALL give, for both masking modes, the error on masked detections in magnitudes (root mean square, and the median absolute error) for the model and each baseline, and the share of predictions on masked upper limits that are brighter than the limit.
4. THE report SHALL give a 95% bootstrap interval, resampling stars, for the model's error and for its difference from each baseline.
5. THE report SHALL give the error split by the era of the masked point (before 1953, after 1969) so a model that only works in the dense decades is visible.

**R5. Training and reproducibility**
1. EVERY run SHALL start from a config file, and SHALL write the config, the sequence-spec version, the code version, the data snapshot digest, a metrics log and checkpoints into one run directory.
2. A run SHALL resume from its last checkpoint and give the same later losses as an uninterrupted run, to within float tolerance on the same device.
3. THE run SHALL stop early when the held-out-star loss (stars of the training tile that were set aside) has not improved for a configured number of epochs.
4. NO key, token or other secret SHALL appear in a config, a log or a checkpoint.

**R6. Layer and dependency rules**
1. `ml/` SHALL import `data` and `l1` only, and nothing under it SHALL import `nooa`, `litellm`, `openai` or `anthropic`. A test SHALL enforce both, with a case that breaks the rule and is caught.
2. THE `ml` dependency group SHALL pin PyTorch to an exact version, and the fast test suite SHALL run without it (tests that need it use `importorskip`).

**R7. Reporting**
1. THE sprint SHALL write `docs/data-cards/lc-sequences-v1.md`, `docs/model-cards/lc-encdec-v0.md` and a decision record `0009` with the table of results for both encoders, and the pass or fail against decision 2.
2. A negative result SHALL be reported as one.
