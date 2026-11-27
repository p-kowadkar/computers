# Design: S4 L1 triggers

## Modules

```
src/computers/
  l1/
    triggers.py    Trigger, baseline and noise, the five trigger functions
    kill.py        the kill reasons, applied to a trigger and its evidence
    analyse.py     analyse_star: cleaned curve, detrending, triggers, kill reasons, stop reason
    candidates.py  candidate rows, ids, telemetry rows, the triggers log, the writers
  bench/
    recovery.py    signal epoch spans, the recovery rule, the confusion matrix
    trigger_bench.py   false alarms by kill reason, sweeps, ablations, known variables
  cli.py           computers l1 run --tile ... ; computers bench triggers
tests/{unit,property,golden}/
```

`l1` still imports only `data` and the top-level `thresholds`. Everything a trigger reads comes from the `Curve` and the `Detrended` result of Sprint 3.

## One trigger

```python
@dataclass(frozen=True)
class Trigger:
    kind: str                  # outburst, dip, step, long_term, disappearance
    epoch_start: float         # decimal years
    epoch_end: float
    amplitude_mag: float       # positive; a lower limit for outbursts
    single_plate: bool
    evidence: tuple[int, ...]  # row indices into the curve the trigger read
    detrended: bool            # False when the star had too few comparison stars
    kill_reasons: tuple[str, ...] = ()
```

The evidence rows are what the kill reasons read: their series, and the `NON_BLUE` and `BLEND_SUSPECT` tags. A trigger never carries data of its own beyond its epochs, amplitude and evidence.

## Baseline and noise

The magnitudes a trigger reads are `Detrended.mag` when the star was detrended and the raw curve otherwise, and the trigger says which. The baseline is the median of the good detections of that array. The noise is `scatter_within` of the good detections (Sprint 3's statistic), or 1.4826 times the median absolute deviation when too few blocks exist. Magnitudes run backwards: brighter than the baseline means `baseline - mag` is positive.

## The five triggers

All five are functions `(curve, mags, baseline, noise, stretches) -> list[Trigger]`, so each is tested on its own.

- **Outburst.** Evidence candidates are the rows of `good_for_outburst` with a finite magnitude at least `amplitude_floor_single_epoch_mag` and `outburst_sigma` noise units brighter than the baseline. A sliding window over their times finds `outburst_min_points` inside `outburst_window_days`. One trigger per cluster, taking the largest cluster in a window. A lone point at least `outburst_single_point_mag` brighter makes a trigger with `single_plate` true.
- **Dip.** Walk the good detections in time order, ignoring non-detections. A run of `dip_min_points` or more consecutive points all at least `amplitude_floor_long_term_mag` fainter than the baseline and spanning at most `dip_max_years` is a dip. Overlapping runs merge.
- **Step.** The good detections are sorted, with a stretch id from `seams.stretches`. For each stretch, the admissible splits have `seam_min_points` points of that stretch on each side. For each seam between stretches, one candidate compares the two neighbouring stretches. The shift of a split is the difference of the medians on either side. The scan is coarse to fine: about 64 evenly spaced splits, then every split within one coarse step of the best, so a curve costs a few milliseconds. The best split of a stretch fires when its shift reaches `amplitude_floor_long_term_mag`, with its epoch at the split. Every step then goes through `seam_only(curve, epoch)`, which gives `seam_only` to anything in the gap or across a break, so those are logged and not absent.
- **Long-term variation.** Per stretch, the good detections are binned by decade, decades with fewer than `seam_min_points` points are dropped, and the trigger fires when at least `long_term_min_decades` remain and their medians span `amplitude_floor_long_term_mag`. The span is the amplitude.
- **Disappearance.** Let L be the last good detection. The evidence rows are the non-detections after L carrying `DEEP_LIMIT_SUSPECT` (limit fainter than the baseline by `deep_limit_margin_mag`). The trigger fires when there are `disappearance_min_plates` of them from `disappearance_min_series` series. It carries `needs_cutouts` as a flag and nothing in its output uses the word "vanished".

A star may have several triggers, and they are all kept. A large step also produces a long-term trigger; the report counts both.

## Kill reasons

`kill.py` has one function per reason, each taking a trigger, the curve and the star's VSX match, and returning the reason or nothing.

| Reason | Rule |
|---|---|
| `seam_only` | `seam_only(curve, epoch).seam_only` for steps and long-term triggers that cross a break |
| `single_series_only` | the evidence rows come from fewer than `trigger_min_series` series |
| `non_blue_only` | every evidence row has `NON_BLUE` (a row with `EMULSION_UNKNOWN` does not count) |
| `blend_suspect` | every evidence row has `BLEND_SUSPECT` |
| `known_variable` | the star has a VSX entry within `vsx_match_radius_arcsec`. Kept, flagged, not escalated |
| `too_few_points` | the curve (Sprint 3), before any trigger runs |

A trigger survives when its kill reasons are empty or only `known_variable`. For a step the seam test needs the whole curve, so the order is: triggers, then kill reasons.

## The star's result

`analyse_star(ctx, ref, vsx)` returns the cleaned curve, the `Detrended` result, the list of triggers with their kill reasons, and a stop reason: `too_few_points`, `no_trigger`, the first kill reason when every trigger was killed, or `passed` when one survives. `ctx` is the `TileContext` of Sprint 3.

## Candidates and telemetry

- A surviving trigger becomes a `candidates` row. The `candidates` schema (version 1) already has every column needed; flags go in `l1_flags`: `known_variable`, `not_detrended`, `lower_limit`, `needs_cutouts`. The event type is the trigger kind. `candidate_id` is the first 16 hex characters of a SHA-256 over the star, kind and epochs rounded to 0.01 year, so a rerun gives the same ids. `snapshot_digest` and `code_version` come from the store and the package.
- A `telemetry` row per star: `stop_layer` is `l1` for a killed or trigger-less star and `l2` for a survivor (the next layer will replace it), with the reason and the seconds.
- Killed triggers go to `data/reports/l1-triggers-<tile>.json`, one record each with its kill reasons.
- `computers l1 run --tile m44|tcrb|tabby` writes `candidates/tile=<tile>.parquet`, `telemetry/tile=<tile>.parquet` and the log. For `tabby` it refuses to run until the record `0007` exists and the test split is frozen.

## Recovery in the benchmark

`Signal.span(first, last)` returns the epoch range of a planted signal: outburst from its start to three decay times, dip from start to end, step at its epoch, linear and wave the whole curve. The recovery rule: the injected host has a surviving trigger that is not also on the un-injected host (same kind and overlapping epochs), whose epoch range overlaps the signal span widened by the tolerance of its family (`recovery_tolerance_*`; the whole curve for linear and wave).

Two completeness curves per family: any matching trigger, and a trigger of the right type. The confusion matrix counts the type of the first matching trigger against the injected family. Recovery of an `expected_missed` signal flags the set as suspect.

## Sweeps

One parameter at a time around the plan's values, on one replicate of the dev injections (10,830) for speed, with the false alarms on all 361 constants. The full dev run (21,660) is for the chosen settings. A setting changes which points qualify, so triggers are recomputed for each; sweep runs are independent and could be run in parallel later.

| Trigger | Swept |
|---|---|
| Outburst | amplitude 1.0, 1.5, 2.0; points 2, 3; series 1, 2; sigma 5, 8 |
| Dip | points 3, 4, 5; amplitude 0.5, 0.7, 1.0 |
| Step | shift 0.5, 0.7, 1.0; points 20, 30 |
| Long-term | span 0.5, 0.7, 1.0; decades 4, 5 |

For each trigger, the proposal is the setting with the highest completeness at the false-alarm budget you choose (decision 2), with the cost of the next-looser and next-tighter settings beside it.

## Ablations

Detrending on and off, the seam rule on and off, `reject` and `keep` for meteor series. Each is a full dev run at the chosen thresholds. The seam rule off means no `seam_only` kill reason, which the report should show recovering gap steps (the expected-missed flag).

## Risks

| Risk | Handling |
|---|---|
| The outburst false alarms depend on `single_series_only` | The sweep gives the completeness cost of 1 against 2 series. Reported, then your choice. |
| Step and long-term false-alarm rates are unknown | Measured in task 3 and 4 before the spec's budget is applied. If they are high the budget moves or the rule changes, with your decision. |
| Triggers read detrended curves that are raw for 14% of hosts | Every candidate says `not_detrended`. The report splits the numbers by it. |
| Injected outbursts fall between plates | Many planted outbursts cover zero or one plate. The completeness curve is the honest answer, and 0 is a possible one. |
| Sweeps are slow | One replicate, and the step scan is coarse to fine. A time budget of 5 minutes per full run is a requirement (R13). |
| A rare class gets no benchmark | Disappearance has no injection family. It is counted on all stars and checked by eye. |

## Findings during the build (see `docs/decisions/0007`)

- **A single-plate outburst can never survive a two-series rule.** R2.2 and R7.3 together kill every single-plate trigger. The report shows it and the cost of lifting it.
- **The plan's dip and outburst rules fire on persistent changes,** so gap steps were recovered as dips and outbursts (341 of 4,088 expected-missed signals). Four optional parameters were tried and dropped (`outburst_max_days`, `dip_must_return`, `local_baseline`, and a return fraction that lost T CrB's eruption). The recovery check (`return_points` 5, `return_recovery` 0.25) is on by default and cuts it to 35. See `0007` section 6.
- **Evidence from the benchmark that the seam rule earns its place:** switching it off recovers 495 more expected-missed signals (530 against 35).
- **The disappearance trigger fires on a processing cluster** (12 Tabby stars with the same last detection, 1962.6). A tile-level kill reason would catch it; it is not in the plan and is not built.
