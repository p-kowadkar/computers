# 0002. Initial thresholds

Date: 2026-11-24. Spec: `.kiro/specs/s1a-foundations/`.

## Decision

Four numeric thresholds are recorded in `src/computers/thresholds.py`, taken unchanged from `plan.md`. All four have status **start**: they are values to tune, not values chosen from data. The benchmark (Sprint 4) sets the real ones, each with its own decision record.

| Threshold | Value | Plan | Where the number comes from |
|---|---|---|---|
| `amplitude_floor_long_term_mag` | 0.5 mag | 3.2 | Plan's own starting value for long-term events |
| `amplitude_floor_single_epoch_mag` | 1.0 mag | 3.2 | Plan's own starting value for single-epoch events |
| `astrometry_max_sep_arcsec` | 15 arcsec | 6.1 step 3 | The reduction guide's example (`sep_above(15 * u.arcsec)`). The guide says the separation where data go bad is typically 10 to 20 arcsec. |
| `source_split_radius_arcsec` | 5 arcsec | 6.1 step 1 | Plan's own starting value |

## Not included

The brightness cut, the blend threshold (0.3 mag), the shape-outlier limit (3 sigma), the comparison-star counts and the trigger thresholds are also marked as starting values in the plan. They are added to the registry in the sprints that implement them (Sprints 3 and 4), each with a record.

## How to change one

1. Write a new decision file in `docs/decisions/` that says what changed and why, with the benchmark numbers behind it.
2. In `docs/decisions/thresholds.lock.json`, set the threshold's `decision` to the new file and its `value` to the new number.
3. Change the value in `src/computers/thresholds.py`.

The tests fail when the three disagree. `computers thresholds check` also fails when a value changed against git HEAD and the lock still cites the same decision file.
