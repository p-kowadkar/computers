# 0007. L1 trigger thresholds

Date: 2026-11-26. Spec: `.kiro/specs/s4-l1-triggers/`. Status: **chosen 2026-11-26, by the defaults in section 6**. Everything below is measured on the dev split with `computers bench triggers`; the test tile has not been run.

## Starting values

All are status **start**. They are the plan's values, or the proposals in the Sprint 4 spec, and they are not yet chosen from data. The existing thresholds that the triggers reuse are `amplitude_floor_single_epoch_mag` (outburst), `amplitude_floor_long_term_mag` (dip, step, long-term), `seam_min_points` (step and decade points), `deep_limit_margin_mag` (disappearance) and `min_good_points`.

| Threshold | Starting value | Plan | Meaning |
|---|---|---|---|
| `outburst_min_points` | 2 points | 6.4 |  |
| `outburst_window_days` | 60 days | 6.4 |  |
| `outburst_sigma` | 5 noise units | 6.4 |  |
| `outburst_single_point_mag` | 2 mag | 6.4 |  |
| `trigger_min_series` | 2 series | 6.5 |  |
| `dip_min_points` | 3 points | 6.4 |  |
| `dip_max_years` | 20 years | 6.4 |  |
| `long_term_min_decades` | 4 decades | 6.4 |  |
| `disappearance_min_plates` | 10 plates | 6.4 |  |
| `disappearance_min_series` | 2 series | 6.4 |  |
| `recovery_tolerance_outburst_days` | 60 days | 10.4 |  |
| `recovery_tolerance_dip_years` | 1 years | 10.4 |  |
| `recovery_tolerance_step_years` | 5 years | 10.4 |  |

## Decisions taken by default in the review

1. `single_series_only` for outbursts keeps a minimum of 2 series. On the 361 dev constants it takes false outbursts from 61 to 3 per 1,000 (prototype, Sprint 4 drafting).
2. The false-alarm budget at the L1 exit is at most 10 per 1,000 constants for each trigger type.
3. Recovery tolerances: 60 days for an outburst, 1 year for a dip, 5 years for a step.
4. The disappearance trigger is built now, with a synthetic test and a count on all stars and no completeness curve.
5. Triggers read the detrended curve when the star could be detrended and the raw curve otherwise.
6. The test tile is not run before this record holds the chosen values and the split is frozen.

## 1. What L1 raises falsely, and what it recovers (plan starting values)

361 dev constants, 21,660 injections, default policy `reject`. Run with `computers bench triggers`.

| Trigger | Constants with a trigger | Surviving the kill reasons | Per 1,000 (95% interval) | Killed by |
|---|---|---|---|---|
| Outburst | 38 | 0 | 0 (0 to 11) | `single_series_only` 38, `non_blue_only` 2 |
| Dip | 2 | 1 | 3 (0 to 16) | `single_series_only` 1 |
| Step | 0 | 0 | 0 (0 to 11) | |
| Long-term | 0 | 0 | 0 (0 to 11) | |
| Disappearance | 0 | 0 | 0 (0 to 11) | |
| Any | 38 | 1 | 3 (0 to 16) | |

The budget of at most 10 per 1,000 per type is met. Steps and long-term variation are quiet because constants were selected for low between-block scatter, so those two rates are biased low.

**Completeness: amplitude at 50% and 90% recovery** (any matching trigger, then a trigger of the right type; mag, or mag per century for linear):

| Family | Any trigger | Right type |
|---|---|---|
| Step | 0.54 / not reached | 0.58 / not reached |
| Linear fade | 1.08 / 1.49 | 1.35 / not reached |
| Wave | 0.33 / 0.44 | 0.36 / not reached |
| Dip | 0.64 / not reached | not reached |
| Outburst | not reached | not reached |

Overall 46.2% of the 17,572 injections not planned as expected-missed are recovered by some trigger.

**Outbursts are rarely recoverable.** 430 of 4,332 planted outbursts (10%) are recovered, and even at the largest amplitude (8 mag) only 62 of 361. The plates are too sparse: two plates within 60 days at least 1 mag above the baseline, from two series, is seldom met by a decay of days to months. This is the sampling, not the trigger.

## 2. Findings that change the rules

- **Single-plate outbursts can never survive.** A single point is one plate and one series, so the series rule (`trigger_min_series` 2) always kills it. Of the outburst triggers found on constants, 33 were single-plate, and all were killed. Lifting the series rule for outbursts to 1 gives 36 false alarms in 361 constants (100 per 1,000) and recovers 19.5% of outbursts instead of 10.0%. At the 10 per 1,000 budget only 2 series qualifies.
- **The plan's dip and outburst rules also fire on persistent changes.** 341 of 4,088 expected-missed signals (8%) were recovered, every one a step placed in the Menzel Gap and matched by a `dip` (198) or an `outburst` (143), never by a step. After such a step only a few dozen detections survive (the star drops below plate limits), and they are faint against a baseline dominated by pre-gap data. Noise then chops a persistent fade into short runs of consecutive faint points, and each run reads as a dip. The seam rule only guards steps, so by the Sprint 2 rule this trigger set is **suspect**.
- **Three fixes that do not work.** `outburst_max_days` (365 or 180) changes nothing. `dip_must_return` (a dip may not touch the first or last good point) removes 9 of the 341. A baseline per continuous stretch (`local_baseline`) leaves 326, because the step lies inside the gap stretch itself.
- **One that does, in its first form: require that the star returns to baseline** (`return_points`, `return_fraction`). This first form was later replaced (section 6) because it removed T CrB's eruption. The median of the 5 good points before and after an event must lie within a fraction of the event's amplitude threshold of the baseline. Measured on all 21,660 dev injections:

| Setting | Expected-missed recovered (of 4,088) | Outburst recovered (of 4,332) | Dip recovered, any / right type (of 4,332) | Step (of 2,410) | Linear / wave (of 2,166 / 4,332) | False alarms |
|---|---|---|---|---|---|---|
| Off (plan) | 341 | 431 | 2,015 / 1,126 | 1,072 | 826 / 3,773 | 1 |
| 5 points, fraction 1.0 | 61 | 429 | 1,681 / 773 | 983 | 700 / 3,721 | 1 |
| 3 points, fraction 1.0 | 74 | 429 | 1,692 / 823 | 985 | 745 / 3,731 | 1 |
| 5 points, fraction 0.75 | 1 | 385 | 1,435 / 460 | 945 | 546 / 3,599 | 1 |
| 5 points, fraction 0.5 | 0 | 311 | 1,352 / 340 | 936 | 517 / 3,558 | 1 |
| 3 points, fraction 0.5 | 2 | 269 | 1,287 / 271 | 936 | 522 / 3,556 | 1 |

  The strict versions remove what the plates cannot show and cost real outbursts and dips, which are also less likely to be seen with sparse points on each side. The false-alarm count does not move.
- **Ablations** (default settings, all 21,660 injections):

| | Constants with a surviving trigger | Recovered share | Expected-missed recovered |
|---|---|---|---|
| Default | 1 of 361 | 46.2% | 341 |
| No detrending | 3 of 361 | 48.2% | 367 |
| No seam rule | 1 of 361 | 47.0% | 605 |
| Meteor series kept | 3 of 361 | 47.6% | 380 |

  The seam rule earns its place: switching it off recovers 264 more signals that the plates cannot show. Detrending and the meteor policy are not distinguishable by these numbers (one to two constants, one to two points of completeness, both inside the noise). Detrending does not show the gain the principle asks for in this benchmark, and I would keep it only because it does no measurable harm and cuts the scatter by 7% (record 0006).
- **Sweeps of the starting values** change little where it matters. Outburst amplitude 1.0 to 2.0 moves recovery from 10.0% to 5.5% with no false alarms to remove, `outburst_min_points` 3 costs 1 point and `outburst_sigma` 8 costs 0.1. Dip points 3 to 5 cost 3 points of recovery, and dip depth 0.5 to 1.0 costs 10. Step shift 0.5 to 1.0 costs 5 points, and 20 to 30 points per side costs 2. Long-term span 0.5 to 1.0 costs 6. No setting reduces false alarms below the 0 to 3 per 1,000 already there, so the plan's values are not binding, and the only knob with a visible trade is the series rule.
- **A cluster of apparent disappearances is a processing effect.** The disappearance trigger fires on 20 of the 1,688 patrol stars (2 in M44, 18 in Tabby, none in T CrB). 12 of the Tabby ones share the same last detection (1962.6), yet 86% of the tile's other stars have good detections on the plates that follow. The star list is spread over the whole tile. It is the "missing lightcurve points" signature the plan warns about. A tile-level kill reason (many stars disappearing at the same epoch) would catch it. I have not built it, since it is not in the plan.
- **T CrB's 1946 eruption fires as an outburst and survives** (26 evidence points, 3.6 mag as a lower limit, flagged only `known_variable`). T CrB is not detrended. Its other triggers (1938 to 1951 activity, a step, a long-term span of 1.1 mag, dips) are all real features of the star.

## 3. Rules added in this sprint (all off by default, so the plan's behaviour is the baseline)

`outburst_max_days`, `dip_must_return`, `local_baseline`, `return_points` and `return_fraction` were optional fields of `TriggerParams`, off by default. The first three did not help and were removed. The last two became the recovery check of section 6, and `return_fraction` was replaced there.

## 4. Starting values (section above) are unchanged

None of the 13 thresholds in the table at the top has changed value.

## 5. Choices for you

1. **The return check.** Off, or on with one of the settings in the table. My suggestion is 5 points with fraction 0.75: it leaves 1 expected-missed recovery in 4,088 and costs 11% of outbursts and 29% of dips recovered by any trigger (59% of dips recovered as a dip). Fraction 1.0 keeps outbursts whole, costs 17% of dips (31% as a dip) and leaves 61 (1.5%). Fraction 0.5 reaches 0 and costs 28% of outbursts. This is your call, since it trades sensitivity against what the plates can claim.
2. **The series rule.** Keep 2 for every trigger (no single-plate outburst can survive at L1, 0 false alarms) or lower it to 1 for outbursts (100 per 1,000 false alarms, double the recovery). A single-plate flare is the most defect-prone class, so I suggest keeping 2.
3. **Detrending and the meteor policy.** Keep both as they are (detrending on, meteor `reject`), since neither difference is measurable here.
4. **Whether to build a tile-level kill reason** for common disappearance epochs.

When you have chosen, I write the chosen values here, change this record's status to "chosen", register what is adopted, and the test tile can then be run once.

## 6. What was chosen (2026-11-26)

You said to continue with the defaults, which were my suggestions in section 5. One of them was wrong and I changed it before building on it.

**The correction.** My suggested return check (5 points within 0.75 of the amplitude threshold of the baseline) removed T CrB's 1946 eruption from the outburst triggers. A nova decays slowly, so the points right after it are still 0.8 to 1.1 mag above the baseline, outside the tolerance, and the check read it as a persistent change. T CrB firing as an outburst is the sprint's headline requirement (R11.2). Only a fraction of 1.25 or more kept it, and at that fraction the check no longer rejects persistent shifts of 1 to 1.25 mag. I had recommended the setting without testing it on T CrB.

**The replacement rule: the star must be recovering.** The median of the 5 good detections after an outburst or a dip must have moved back towards the baseline by at least 0.25 of the way from the event's own level. After a nova the next points are fainter than the eruption, so it recovers even when the decay is slow. After a persistent shift they stay at the event's level. Only the side after the event is checked: a persistent change begins at the event. When fewer than 5 good points follow, the recovery cannot be seen and the event does not count.

| Setting | Expected-missed recovered (of 4,088) | Outburst recovered (of 4,332) | Dip recovered, any / right type (of 4,332) | Step (of 2,410) | Linear / wave (of 2,166 / 4,332) | T CrB 1946 eruption |
|---|---|---|---|---|---|---|
| Off (the plan) | 341 | 431 | 2,015 / 1,126 | 1,072 | 826 / 3,773 | fires |
| 5 points, recovery 0.10 | 135 | 431 | 1,977 / 1,102 | 1,055 | 809 / 3,770 | fires |
| **5 points, recovery 0.25 (chosen)** | **35** | **428** | **1,891 / 1,025** | **1,033** | **713 / 3,732** | **fires** |
| 3 points, recovery 0.25 | 58 | 424 | 1,883 / 1,038 | 1,035 | 767 / 3,745 | fires |
| 5 points, recovery 0.50 | not run in full | | | | | lost |
| First form (5 points, within 0.75) | 1 | 385 | 1,435 / 460 | 945 | 546 / 3,599 | lost |

False alarms are 1 of 361 in every row. The chosen setting cuts the expected-missed recoveries from 341 to 35 (0.9% of 4,088; 34 are dips, 1 an outburst), and costs 0.7% of outbursts, 6% of dips and 3.6% of steps. 35 recovered expected-missed signals still make the set formally "suspect" by the Sprint 2 rule, but they are a residue of persistent fades that start inside the gap and then recover in the data, not a rule that ignores the gap.

**Chosen:**
1. **The recovery check is on, with 5 points and recovery 0.25.** Registered as `return_points` (5) and `return_recovery` (0.25), status `decided`, and read by `TriggerParams.from_registry()`.
2. **The series rule stays at 2 for every trigger.** No single-plate outburst can survive L1. They are still found and logged with `single_series_only`.
3. **Detrending stays on and the meteor policy stays `reject`.** Neither difference is measurable (section 2).
4. **The plan's starting values are kept.** Those that the sweeps tested and kept are now status `decided`: `outburst_min_points` (2), `outburst_sigma` (5), `outburst_single_point_mag` (2.0), `trigger_min_series` (2), `dip_min_points` (3) and `long_term_min_decades` (4). The others stay `start`, because no sweep varied them: the outburst window (60 days), the dip span (20 years), the disappearance limits and the three recovery tolerances. The reused amplitude floors and `seam_min_points` stay `start` in record 0002.
5. **Dropped:** `outburst_max_days`, `dip_must_return` and `local_baseline`, which did not help. They are removed from the code.
6. **Not built:** a tile-level kill reason for stars that disappear at the same epoch. The 12 Tabby stars with a last detection at 1962.6 will be in the triggers log of the Tabby run and stay unexplained until someone looks at the plates.

## 7. Final numbers at the chosen defaults (dev split)

From `computers bench triggers`, 361 constants, 21,660 injections, 1,049 s. Report: `data/reports/bench-triggers-chosen.{txt,json}` (not committed).

- **False alarms at the L1 exit:** 1 of 361 (3 per 1,000, interval 0 to 16). Outburst 0 (38 found, all killed: `single_series_only` 38, `non_blue_only` 2), dip 1 (2 found), step 0, long-term 0, disappearance 0.
- **Completeness, amplitude at 50% recovery (any trigger | right type):** step 0.55 | 0.58 mag, wave 0.33 | 0.36 mag, dip 0.71 mag | not reached, linear 1.16 | 1.35 mag, outburst not reached.
- **Expected-missed signals recovered:** 35 of 4,088.
- **Ablations (false alarms of 361, recovered share, expected-missed):** default 1, 44.4%, 35. No detrending 3, 46.0%, 46. No seam rule 1, 45.8%, 530. No return check 1, 46.2%, 341. Meteor `keep` 3, 45.7%, 43. The detrending and meteor differences are 2 false alarms and under 2 points of recovery, within noise.
- **Patrol (`computers l1 run`, `reject`):** M44 444 stars, 65 candidates (dip 43, step 7, outburst 7, long-term 6, disappearance 2). T CrB 186 stars, 26 candidates (dip 13, outburst 10, step 2, long-term 1). Stars stopped: M44 80 by `single_series_only`, 5 `blend_suspect`, 3 `non_blue_only`, 19 `too_few_points`, 22 passed; T CrB 23, 0, 2, 14, 8 passed. Tabby not run.
- **Goldens:** T CrB's 1946 eruption survives from the full curve. The committed excerpt has none under `reject` and one single-series outburst under `keep`, killed by the series rule. KIC 8462852 has no surviving long-term or step trigger.

## What this means for the test tile

The test tile may now be run once, but only after the test split is frozen (`computers bench freeze`). `computers l1 run --tile tabby` checks both and refuses until they hold. After that run, no threshold changes without a new record.
