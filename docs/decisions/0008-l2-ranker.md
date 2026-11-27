# 0008. L2 features, ranker and budget

Date: 2026-11-27. Spec: `.kiro/specs/s5-l2-features-ranker/`. Status: **chosen 2026-11-27, by the defaults of the Sprint 5 spec.** Numbers are from `computers bench l2` on the dev split (361 constants, 21,660 planted signals). The test tile has not been run.

## Result in one paragraph

L2 does not meet the pass mark as a filter, so it is not one. The pass mark (decision 5 of the spec) was a gain of at least 3 percentage points of outburst recovery over L1 strict at no more false alarms. The best honest gain is +2.7 points (bootstrap interval +0.5 to +4.7) at one false-alarm constant, and +1.2 points (+0.3 to +3.9) at none, for the plain ranker. The interval spans the pass mark, so the result is not shown. The isolation-forest anomaly score adds nothing and is out of the ranker. L1 strict decides what survives. The ranker only orders the survivors for the budget, and the budget does not bind on the dev tiles.

## What was measured

Three arms on the same constants and signals. Arm 1 is L1 strict (the registry). Arm 2 is L1 with `trigger_min_series` 1 and nothing else changed. Arm 3 is arm 2 followed by the ranker, with its threshold the lowest held-out score at which arm 3 raises no more false alarms than arm 1 (1 of 361). Scores are cross-fitted: 5 folds grouped by host star, the anomaly forest and the ranker both fitted on the other folds' hosts.

| Arm | False alarms (of 361) | Outburst recovered (of 4,332) | Dip | Step | Linear | Wave | Expected-missed recovered |
|---|---|---|---|---|---|---|---|
| 1. L1 strict | 1 (3 per 1,000) | 9.9% | 43.7% | 42.9% | 32.9% | 86.1% | 35 |
| 2. L1 relaxed | 37 (102 per 1,000, 75 to 138) | 19.0% | 50.7% | 46.8% | 45.2% | 88.4% | 64 |
| 3. relaxed then ranker (plain) | 1 | 8.9% | 45.4% | 44.1% | 37.4% | 86.3% | 35 |

Arm 3 against arm 1: 534 signals gained and 366 lost. The cost is the signals strict recovers and arm 3 does not.

Outbursts only, the class the series rule costs most. Recovered share of planted outbursts at each false-alarm budget (constants with a surviving outburst), and the gain over strict's 9.9% with a bootstrap 95% interval over constants:

| Ranker inputs | Pooled AUC | Outburst-only AUC | FA 0 | FA 1 | Gain at FA 0 | Gain at FA 1 |
|---|---|---|---|---|---|---|
| amplitude only (trivial) | 0.836 | 0.370 | 0.1% | 0.2% | -9.7 | -9.6 |
| plain | 0.979 | 0.885 | 10.8% | 12.5% | +1.2 (+0.3 to +3.9) | +2.7 (+0.5 to +4.7) |
| plain + anomaly | 0.982 | 0.885 | 7.8% | 12.7% | -1.9 (-2.7 to +3.9) | +3.0 (-2.6 to +5.0) |
| plain + series count | 0.979 | 0.888 | 11.1% | 12.6% | +1.5 (+0.7 to +4.2) | +2.8 (+0.8 to +5.1) |
| full | 0.983 | 0.888 | 8.2% | 13.1% | -1.4 (-2.3 to +3.9) | +3.2 (-2.2 to +5.3) |

"Plain" is the trigger amplitude, the log of the evidence count, `single_plate`, the trigger type, and whether the curve was detrended. 8,958 positives and 43 negatives (41 false outbursts and 2 false dips on the 361 constants).

## What follows

1. **The ranker is not a filter.** The pass mark was not met at FA 0 or FA 1 for any input set. The only point estimate above 3 (full, FA 1, +3.2) has an interval from -2.2 to +5.3. With 43 negatives, one more or fewer false-alarm constant moves the result by more than the effect. The sprint reports "not shown" and does not change the L1 default (`trigger_min_series` stays 2).
2. **The anomaly score is cut from the ranker.** It leaves the outburst-only AUC at 0.885 and widens the intervals (plain + anomaly against plain). It stays in the code as the baseline that the embedding score of Sprint 7 has to beat.
3. **Amplitude is a poor ranking key.** Planted outbursts that L1 recovers are weaker on average than the false ones on constants (AUC 0.37), so ordering by amplitude would put false alarms first. The plain ranker (AUC 0.885) is used for ordering.
4. **The ranker's probability is not a rate.** Positives outnumber negatives 208 to 1 in the training mix, and the sigmoid calibration restores that prior, so held-out scores sit between 0.98 and 1.00 and the calibration bins show 0.98 to 1.00 in every bin. Read the score as an order. The ordering is only as sharp as the fourth decimal, and six M44 stars score 1.0 to that precision.
5. **The budget does not bind on the dev tiles.** With one trigger per star, 22 M44 stars and 8 T CrB stars reach L2, 17 are selected (14 and 3), the 8 and 5 stars whose only candidates are known variables (13 and 16 candidate rows) take no place, and nothing is left out by the cap of 20 per tile and 60 in total. The 91 candidate rows of Sprint 4 are 30 stars.
6. **Four M44 steps share the epoch 1971.0.** Their candidate epochs are 1971.00 to 1971.04, three of them are selected and one is a known variable. Like the 12 Tabby stars at 1962.6 (record 0007), it looks like a tile-level artefact of the plate mix after the Menzel Gap. It is not a kill reason yet, and the case files must say so.

## What this costs

- Nothing is lost against Sprint 4: the L1 candidates are unchanged, and `l2_score` is filled in.
- The relaxed arm's extra outbursts (19.0% against 9.9%) stay out of reach. Getting some of them back needs evidence the lightcurve does not have, which is the cutout classifier (plan 7.3) or a second look at the plates. This is the case for building that classifier early.

## Registered thresholds

| Threshold | Value | Status | Meaning |
|---|---|---|---|
| `budget_per_tile` | 20 stars | decided | From `0004`. At most this many stars per tile reach L3. |
| `budget_total` | 60 stars | decided | From `0004`. At most this many in all. |
| `l2_filter_min_gain_points` | 3 percentage points | start | The pass mark for letting the ranker act as a filter. Not met, so no filter. |

No ranker threshold is registered because the ranker does not filter.

## Choices for you

1. **Keep the ranker out of the filter role** (default, taken). The alternative is to accept L2 as a filter on the plain ranker's +2.7 (+0.5 to +4.7) at FA 1, which would take the L1 series rule to 1. I do not recommend it: the benefit is inside the noise of 43 negatives and 361 constants.
2. **Keep the anomaly module** as the Sprint 7 baseline (default, taken) or delete it.
3. **Pass mark:** 3 points was my default. A lower bar (2 points, say) is met at FA 1 by every input set but amplitude only, but the lower ends of the intervals stay between -2.6 and +0.8 points.
4. **A bigger negative set** would settle this: more constants (the injection set has 361 dev hosts), or the second dev tile's full constant pool. This is a Sprint 2 question and not a Sprint 5 one.
