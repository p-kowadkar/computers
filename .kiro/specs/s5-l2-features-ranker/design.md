# Design: S5 L2 features, ranker and the first topology report

## Layout

```
src/computers/l2/
  features.py    feature table from an L1 result (plain numpy)
  anomaly.py     isolation forest fit, save, load, score
  ranker.py      logistic regression, calibration, grouped cross-validation
  budget.py      choose stars under the caps, report what was left out
  run.py         l2 run: candidates in, scored candidates and telemetry out
src/computers/bench/
  l2_bench.py    the three-arm comparison on the dev split
docs/model-cards/ranker-v0.md
docs/model-cards/anomaly-v0.md
docs/decisions/0008-l2-ranker.md
```

`l2` imports `l1` and `data`. `bench` may import `l2`. Sklearn is imported in `l2` only, and the layer test already forbids LLM clients there.

## Features (R1)

One function, `features(result: L1Result) -> dict[str, float]`, reads `result.inp` (the curve L1 read, its magnitudes and its stretches) and the good detections. Definitions follow 7.1:

| Feature | Definition |
|---|---|
| `amp_robust` | 95th minus 5th percentile of the good magnitudes |
| `mad` | median absolute deviation |
| `skew` | sample skewness |
| `von_neumann` | mean squared successive difference over variance, in time order, within stretches |
| `chi2_red` | reduced chi-square against the median, with the star's within-block scatter as the noise |
| `decade_slope`, `decade_curv` | linear and quadratic coefficients of decade medians against decade, decades with at least `seam_min_points` points |
| `max_step` | largest shift from the step scan of L1 over admissible splits |
| `longest_run` | longest run above or below the median, as a share of the points |
| `frac_3sigma` | share of points beyond 3 noise units |
| `zp_spread` | spread of per-series median magnitudes |
| `n_good`, `span_years`, `n_decades` | counts |

A feature that cannot be defined is NaN. Magnitudes run backwards, so a "brighter" feature has its own sign test. Feature shape is invariant to a constant magnitude shift (R1.4), and tests pin that with Hypothesis.

## Anomaly score (R2)

`IsolationForest(n_estimators=200, random_state=seed)` on the standardised, median-imputed dev features. The score is the negated `score_samples`, so higher is stranger. The imputer medians and scaler are stored with the model. Training stars are the dev patrol stars that are not known variables and are not hosts of an injected signal in the evaluation fold, so the forest never sees the signals it will be asked to rank. This is stated in the model card.

The baseline is `rank by amplitude_mag, then by significance`. R2.3 compares the ranker with and without the anomaly score on held-out folds.

## Ranker (R3)

Rows are triggers. Inputs: `amplitude_mag`, `log(n_evidence)`, `single_plate`, the one-hot trigger type, `not_detrended`, `lower_limit`, and the anomaly score of the star. `known_variable` is not an input: a known variable is a true positive for L1 and does not take a budget place (R5.2).

Training pairs come from the benchmark at the relaxed operating point:

- Positive: a recovered injected signal, one row per signal (the first matching trigger).
- Negative: a surviving trigger on an un-injected constant.

`LogisticRegression(C=1.0, class_weight="balanced")` then `CalibratedClassifierCV(method="sigmoid")` inside a `GroupKFold` keyed by host star (5 folds). The held-out scores of all folds are pooled for the AUC, the calibration bins and the threshold. With about 40 negatives the report gives bootstrap intervals and the model card says how few there are.

## The comparison (R4)

Three arms on the same constants and the same injected signals:

1. **L1 strict**: the registry, as now. (The Sprint 4 full dev report is reused.)
2. **L1 relaxed**: `TriggerParams.from_registry()` with `trigger_min_series` 1.
3. **L1 relaxed then L2**: arm 2's survivors, kept when the held-out ranker score is at or above the threshold.

The threshold is the lowest score at which arm 3's false alarms per 1,000 do not exceed arm 1's. The report then gives arm 3's recovery per family against arm 1's. Decision 5 sets the pass mark. Held-out scores come from the grouped folds, so the threshold is not tuned on the scored data of a host.

Cost: signals that arm 1 recovered and arm 3 does not.

## Budget (R5)

`select(candidates, per_tile=20, total=60)`: drop `known_variable` rows from the competition, keep the best-scored trigger per star, order by score, take `per_tile` per tile, then trim to `total` across tiles by score. A tile with fewer candidates than the cap sends all of them. `left_out` is the list of stars under the cap with their scores. Property tests: the output never exceeds either cap, and adding a worse candidate never displaces a better one.

## Candidates, telemetry and the report (R6, R7)

`candidates` already has `l2_score` (nullable float32), so no schema bump is expected. `l2 run` writes `data/silver/candidates/tile=<tile>.parquet` anew with `l2_score` filled for every row and writes `telemetry/tile=<tile>-l2.parquet` with the `telemetry` schema: one row per star that reached L2. `stop_layer` is `l3` for a selected star and `l2` for the rest, with the reason in `stop_reason`. L1 telemetry is not touched.

The topology report is a text and JSON file in `data/reports/`: per tile, stars in, stopped at L1 by reason, stopped at L2 by reason, selected, seconds and dollars.

The test tile guard is the one in `l1.candidates.check_test_tile_allowed`, called by `l2 run`.

## Risks

- **Too few negatives.** About 40 false triggers at the relaxed point. The model has under 10 inputs and the intervals are reported. If the interval of the false-alarm rate is wider than the effect, the report says the result is not shown.
- **The injected signals are not the sky.** A ranker trained on injections may learn the injection engine. The test is the held-out host split, and the real candidates are listed with their scores for a human to read. They are not scored.
- **Detrended and raw curves differ.** The `not_detrended` flag is an input so a raw-curve star is not ranked against detrended ones as if the same.
- **Sparse eras.** Features that depend on order or runs are computed within stretches and are NaN when a stretch has fewer than `seam_min_points` points.
- **A negative result is a result.** If decision 5 is not met, L2 is reported as not helping and the topology keeps L1 strict plus a budget ordered by amplitude.

## Findings during the build (see `docs/decisions/0008`)

- **L2 does not meet the filter pass mark.** The plain ranker gains +2.7 points of outburst recovery at one false-alarm constant (interval +0.5 to +4.7). Pass mark 3.
- **The pooled comparison and the outburst-only comparison disagree in size,** because the pooled false-alarm cap counts every trigger kind. Outburst-only is the fair test of what the series rule cost, and both are in the report.
- **The anomaly score adds nothing** and is not a ranker input on real tiles.
- **`n_series` as an input** was added to the spec's list as a variant. It adds 0.003 to the outburst-only AUC.
- **The threshold search had an off-by-one bug** (candidates sat on the negatives' own scores, so no threshold ever excluded them). A synthetic test caught it.
- **Calibration is uninformative here.** The class ratio is 208 to 1, so held-out scores sit between 0.98 and 1.00.
- **The budget does not bind** on the dev tiles at star level.

