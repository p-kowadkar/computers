# Requirements: S4 L1 triggers, kill reasons and the first real completeness curves

**Intent.** Turn a cleaned, detrended curve into zero or more triggers (outburst, dip, step, long-term variation, disappearance), each with the evidence behind it and the reasons it should be killed. Measure what the triggers recover and what they raise falsely on the Leavitt Set, so you can choose the thresholds from data.

**Source:** `plan.md` sections 6.4 to 6.6, 4.5 (`candidates`), 10.4 and S4 in section 13; `docs/decisions/0005`, `0006`. Spec status: **draft, awaiting review**.

## Scope

In:
- The five triggers of 6.4, with the evidence points of each.
- The kill reasons of 6.5 and the telemetry row per star.
- A per-star L1 result, and a `computers l1 run` command that writes the `candidates` table.
- Recovery with an epoch rule in the benchmark, a confusion matrix of injected family against trigger type, and false alarms per 1,000 constants at the L1 exit.
- Threshold sweeps on the dev split, with the proposed values and their cost, for you to choose from.
- The ablations the plan names for L1: detrending, the seam rule, the meteor policy.

Out:
- The "vanished" protocol beyond the trigger itself (cutouts, Gaia, sign-off): the disappearance trigger only nominates a star and marks it as needing cutouts (6.6, Sprints 11 and 13).
- L2 scores, embeddings and the crew.
- A report on the test split. That is run once, after you freeze the split and choose the thresholds.

## Facts found while drafting this spec

- **The plan's outburst rule is far too loose on its own.** On the 361 dev constants, "2 or more good points within 60 days, each at least 1.0 mag brighter and beyond 5 sigma" fires on 22 (61 per 1,000). A single point 2 mag brighter fires on 55 (152 per 1,000).
- **Evidence from two series removes almost all of it.** Requiring the points to come from at least 2 different series leaves 1 false outburst (3 per 1,000). That is the plan's own kill reason `single_series_only`, and it is the main control. Its price is genuine outbursts seen by a single series, which the benchmark measures.
- **Dips are already quiet:** 3 consecutive good points at least 0.5 mag fainter within 20 years fire on 2 constants (6 per 1,000), raw or detrended.
- **Robust scatter of a constant is 0.118 mag**, so 5 sigma is 0.6 mag and the 1.0 mag amplitude is the binding limit for outbursts.
- **The injection set has no disappearance family.** Disappearances can only be tested for false alarms on constants and with a synthetic case. They get no completeness curve.
- **Step and long-term triggers have not been prototyped.** Their false-alarm rates are unknown until task 5.
- **The T CrB eruption has 25 or more points in `good_for_outburst` on several series** (`ac`, `ee`, `rh`), so a two-series rule keeps it.
- **KIC 8462852's decade-median slope is +0.18 mag per century,** under the 0.5 mag floor of the long-term trigger. It should not trigger, for that reason and not because detrending explains it.
- **14% of dev hosts cannot be detrended** (`few_comparisons`). Their triggers read the raw curve, and every candidate says so.

## Acceptance criteria (EARS)

**R1. Baseline and noise**
1. THE baseline of a star SHALL be the median of its good detections, detrended when the star could be detrended and raw otherwise.
2. THE noise SHALL be the star's robust within-block scatter (`scatter_within`). WHEN that is undefined, it SHALL be 1.4826 times the median absolute deviation of the good detections.
3. EVERY trigger SHALL record whether it read the detrended or the raw curve.

**R2. Outburst**
1. THE trigger SHALL fire WHEN at least `outburst_min_points` points of `good_for_outburst` fall within `outburst_window_days`, each at least `amplitude_floor_single_epoch_mag` brighter than the baseline and at least `outburst_sigma` noise units brighter.
2. WHEN a single such point is at least `outburst_single_point_mag` brighter, THE trigger SHALL fire with `single_plate` true.
3. THE amplitude SHALL be the baseline minus the brightest evidence point, and SHALL be reported as a lower limit (plates saturate).
4. THE evidence SHALL list its rows, so the kill reasons can read their series and tags.

**R3. Dip**
1. THE trigger SHALL fire WHEN at least `dip_min_points` consecutive good detections, in time order and ignoring non-detections, each lie at least `amplitude_floor_long_term_mag` fainter than the baseline and span at most `dip_max_years`.
2. THE epoch range SHALL run from the first to the last evidence point, and the amplitude SHALL be the median depth.

**R4. Step**
1. THE trigger SHALL scan the split points of the good detections, and for each compare the median of the points before it with the median after it, using only points in the same stretch. A split is admissible when both sides hold at least `seam_min_points` points.
2. THE trigger SHALL fire at the admissible split of largest shift WHEN that shift is at least `amplitude_floor_long_term_mag`.
3. THE trigger SHALL also scan splits that cross a seam, and SHALL give such a step the kill reason `seam_only` through the seam rule of Sprint 3, so a seam step is logged and not silently absent.
4. A step placed inside the Menzel Gap SHALL always carry `seam_only`.

**R5. Long-term variation**
1. WITHIN each stretch, THE trigger SHALL bin the good detections by decade, keep decades with at least `seam_min_points` points, and fire WHEN at least `long_term_min_decades` decades are populated and their medians span at least `amplitude_floor_long_term_mag`.
2. THE epoch range SHALL run over the populated decades and the amplitude SHALL be the span.

**R6. Disappearance**
1. THE trigger SHALL fire WHEN a star's good detections stop at some epoch and, afterwards, at least `disappearance_min_plates` plates from at least `disappearance_min_series` series are non-detections whose limits are deeper than the baseline by `deep_limit_margin_mag`.
2. THE trigger SHALL be marked `needs_cutouts`, and SHALL NOT be described as a vanished star anywhere. The word "vanished" never appears in an output of this sprint.
3. THE disappearance trigger SHALL be tested on a synthetic case, and SHALL be counted on all 1,688 patrol stars for human review.

**R7. Kill reasons**
1. A curve with fewer than `min_good_points` good detections SHALL stop at L1 with the reason `too_few_points`, before any trigger runs.
2. EVERY trigger SHALL carry its kill reasons as a list. Triggers are never dropped silently.
3. A trigger SHALL get `seam_only` when the seam rule says its change is visible only across a seam, and `single_series_only` WHEN its evidence comes from fewer than `trigger_min_series` series.
4. A trigger SHALL get `non_blue_only` WHEN every evidence point carries `NON_BLUE`, and `blend_suspect` WHEN every evidence point carries `BLEND_SUSPECT`. Points with `EMULSION_UNKNOWN` do not count as non-blue.
5. A star with a VSX entry inside the match radius SHALL have its triggers flagged `known_variable`. They are kept, and not escalated to the crew.
6. A trigger SHALL survive WHEN its kill reasons are empty or only `known_variable`.

**R8. The L1 result, candidates and telemetry**
1. `analyse_star` SHALL return the cleaned curve, the detrending result, every trigger with its kill reasons, and the star's stop reason.
2. THE `candidates` table SHALL receive one row per surviving trigger, with the event type, the epoch range in decimal years, the amplitude, `single_plate`, flags (`known_variable`, `not_detrended`, `lower_limit`, `needs_cutouts`), the snapshot digest and the code version. `candidate_id` SHALL be a hash of the star, event type and epoch range, so reruns agree.
3. EVERY star SHALL get one `telemetry` row: where it stopped (`l1` for a killed or trigger-less star, `l2` for a survivor), the reason, and the seconds it took.
4. A killed trigger SHALL be written to a triggers log (`data/reports/l1-triggers-<tile>.json`) with its kill reasons.
5. `computers l1 run --tile <tile>` SHALL write these files. The test tile SHALL NOT be run until the thresholds are chosen.

**R9. Recovery in the benchmark**
1. A signal SHALL count as recovered WHEN a trigger that survives its kill reasons exists on the injected host, did not exist on the un-injected host with the same type and overlapping epochs, and overlaps the signal's epoch range widened by a tolerance.
2. THE signal epoch range SHALL be: for an outburst, its start to three decay times; for a dip, its start to its end; for a step, its epoch; for a linear fade or a wave, the whole curve. THE tolerances SHALL be thresholds in `thresholds.py` (see below).
3. THE report SHALL give completeness two ways: any surviving trigger that overlaps, and a trigger of the right type (outburst to outburst, dip to dip, step to step, linear and wave to long-term). It SHALL also give the confusion matrix of family against trigger type.
4. A signal planned as `expected_missed` that is recovered SHALL flag the trigger set as suspect (R6.3 of Sprint 2).

**R10. False alarms and sweeps**
1. THE report SHALL give false alarms per 1,000 dev constants at the L1 exit, per trigger type and for any type, with 95% intervals, split by kill reason (what each rule removed).
2. FOR every starting threshold of a trigger, THE report SHALL sweep a range and give the completeness and false alarms at each value, so the cost of each choice is on one page.
3. THE sprint SHALL propose a value for each threshold and give its cost. THE values you choose SHALL be recorded in `docs/decisions/0007-l1-trigger-thresholds.md`, and the registry changed only after that.
4. THE test split SHALL be scored once, after you freeze it and choose the thresholds, and no threshold SHALL change afterwards.

**R11. Known variables and reference targets**
1. THE report SHALL list, for each of the 7 known variables and the reference stars in the tiles, which triggers fire and which survive.
2. T CrB SHALL fire an outburst that survives from the full curve. The committed excerpt finds none under `reject` and one single-series outburst under `keep`, which the series rule kills; the golden test pins that. (Reworded 2026-11-26 after the build.)
3. KIC 8462852 SHALL NOT produce a surviving long-term or step trigger at the chosen thresholds. This is a regression test of the amplitude floors, and not a statement about the star.

**R12. Ablations**
1. THE report SHALL give completeness and false alarms with and without detrending, with and without the seam rule, and under both meteor policies, so each earns its place by a number (principle 2).
2. A component that does not help SHALL be reported as such and left out of the default.

**R13. Speed and layers**
1. THE triggers for one curve SHALL take under 20 ms on average, and the full dev benchmark SHALL run in under 5 minutes.
2. `l1` SHALL import only from `data`, and no trigger SHALL read a column the cleaned curve does not carry.

## Thresholds this sprint adds (status start; you own them)

The plan's values are used as starting points. Where an existing threshold already means the same thing it is reused.

| Name | Value | Meaning |
|---|---|---|
| `outburst_min_points` | 2 | Points in an outburst (plan 6.4) |
| `outburst_window_days` | 60 | Window for those points |
| `outburst_sigma` | 5.0 | Noise units beyond the baseline |
| `outburst_single_point_mag` | 2.0 | A single point this much brighter fires, `single_plate` true |
| `trigger_min_series` | 2 | Series that must carry the evidence, or `single_series_only` (new in the plan's kill list, proposed value) |
| `dip_min_points` | 3 | Consecutive good points (plan 6.4) |
| `dip_max_years` | 20 | Span of a dip |
| `long_term_min_decades` | 4 | Populated decades (plan 6.4) |
| `disappearance_min_plates` | 10 | Plates with deep non-detections |
| `disappearance_min_series` | 2 | Series among them |
| `recovery_tolerance_outburst_days` | 60 | Epoch tolerance for an outburst |
| `recovery_tolerance_dip_years` | 1 | Epoch tolerance for a dip |
| `recovery_tolerance_step_years` | 5 | Epoch tolerance for a step |

Reused: `amplitude_floor_single_epoch_mag` (1.0, outburst), `amplitude_floor_long_term_mag` (0.5, dip, step and long-term), `seam_min_points` (20, step and decade points), `deep_limit_margin_mag` (1.0, disappearance), `min_good_points` (50).

## Decisions for you

1. **`single_series_only` for outbursts.** Proposed: keep the plan's kill reason with a minimum of 2 series. It takes false outbursts from 61 to 3 per 1,000 on the dev constants. The benchmark will show what it costs in recovered outbursts, and you can then lower it to 1 for outbursts only.
2. **False-alarm budget at the L1 exit.** Proposed: at most 10 per 1,000 constants for each trigger type. The L2 ranker and the crew are meant to remove the rest, but the benchmark should show an L1 that is not noisy. The budget decides which sweep value I propose.
3. **Recovery tolerances** (13 above): 60 days for an outburst, 1 year for a dip, 5 years for a step. The step tolerance is wide because plates are sparse near a change.
4. **Build the disappearance trigger now,** as the plan lists it, with a synthetic test and a count on the 1,688 stars and no completeness curve. Or leave it for the sprint that has cutouts.
5. **Where triggers read the curve:** detrended when possible, raw otherwise (R1.1). Say if raw should always be used for outbursts, since detrending cannot touch a point seen on a plate with no comparison.
6. **Order of work:** thresholds are proposed from the dev split first, then you choose, and only then the patrol runs on all three tiles. The test tile is not run before that.
