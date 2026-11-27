# Requirements: S3 L1 cleaning, detrending and seams

**Intent.** Turn a star's raw measurements into a cleaned curve, a detrended curve and a list of stretches that can be compared with each other, using deterministic code only. Show with numbers that detrending and the seam rule earn their place.

**Source:** `plan.md` sections 6.1 to 6.3, 6.7 and S3 in section 13; `docs/decisions/0001` (W4), `0003`, `0005`. Spec status: **draft, awaiting review**.

## Scope

In:
- The nine cleaning steps of 6.1, as tags on a curve.
- Neighbour-ensemble detrending (6.2), with a per-series zero-point.
- The seam rule (6.3): continuous stretches, and a test for whether a change is visible inside one.
- The kill reason `too_few_points`.
- The L1 property tests and golden tests of 6.7.
- A before-and-after report on the Leavitt Set dev constants, and the Tabby's Star check.

Out:
- Triggers, the other kill reasons and thresholds for events (Sprint 4, 6.4 and 6.5). The seam rule is built here as a function a trigger will call.
- Cutouts and the shape classifier (Sprint 11). Shape outliers are flagged here, not judged.
- Any model, any LLM.
- Writing cleaned curves to disk. Curves are computed in memory. Injected curves exist only in memory, so this is the only design that serves the benchmark.

## Facts found while writing this spec

- **`series_kind` is empty** for all 15,146 M44 exposures. daschlab ships a static table of 73 series with kind and plate scale (narrow below 400, patrol 400 to 900, meteor 900 and above, in arcsec/mm). It is needed for step 4, and it is MIT licensed.
- **Meteor series are not rare.** They are 16% of M44's good detections, 21% of T CrB's and 10% of Tabby's. They are 50% of the 1950s detections in M44 and 52% in T CrB, and 0% from 1960 on. `ai` alone is 29% of M44's rows. Rejecting them empties the 1950s, right next to the Menzel Gap.
- **The colour term is missing for 53% of exposures** (8,078 of 15,146 in M44). The emulsion check has three states and "unknown" is large.
- **`blended_mag` is null for 94% of rows,** and the shape columns (`fwhm_image`, `ellipticity`, `plate_dist`) are null for the 45% that are non-detections.
- **Few stars need source splitting.** Only 18 patrol stars (10 in M44, 0 in T CrB, 8 in Tabby) have another catalog source within 5 arcsec, with 21 neighbour sources in all. Their lightcurves are not pulled yet, 21 requests.
- **Enough comparison stars exist** before the colour cut: with constants within 0.5 mag, 82% of M44 stars, 69% of T CrB stars and 95% of Tabby stars have 30 or more.
- **The standard rejections remove most of an outburst** (W4: 25 of 32 T CrB eruption points, mostly through three soft bits). The plan asks for a decision record in this sprint.
- **Detection and non-detection rows both have `reject == 0`.** Rejected detections carry a nonzero `reject`. No good detection is fainter than its own plate limit.

## Acceptance criteria (EARS)

**R1. Series table**
1. `computers.data.series` SHALL hold the kind (narrow, patrol, meteor) and plate scale of every series, copied from daschlab 1.1.0, with a test that compares it with daschlab when daschlab is installed and a test that every series in the three tiles' measurements is in it.
2. WHEN a series is not in the table, THE cleaner SHALL tag its points `unknown_series` and SHALL NOT reject them.
3. `NOTICE` SHALL record the daschlab MIT licence for the copied table.

**R2. The curve**
1. THE cleaner SHALL take a star's measurements and return a `Curve` with, for every row: time, magnitude (NaN for a non-detection), plate limit, series, series kind, exposure id, a 64-bit tag mask and a `good` flag.
2. THE tags SHALL be named bits in one place, never numbers in code. Zero means no tag.
3. THE cleaner SHALL NOT delete rows and SHALL NOT change the input table.
4. THE cleaner SHALL report how many points each tag removed or flagged.

**R3. Cleaning steps** (in the order of plan 6.1; each step has a function, a tag and a test on both sides of its boundary)
1. **Source splitting.** WHEN a neighbour source lies within `source_split_radius_arcsec` of the star and the two curves' plates interleave, THE cleaner SHALL merge them into one curve, taking each plate's detection from whichever source has one. Two curves interleave when the share of the smaller curve's detection plates that the other curve also has is under `source_split_overlap_max`.
2. **Standard rejections.** THE cleaner SHALL keep the five standard bits as the hard set for `good`, and SHALL also give `good_for_outburst`, in which points rejected only by `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS` and `BIN_DRAD_UNKNOWN` stay. THE report SHALL say how many points the standard rejections removed.
3. **Astrometry.** THE cleaner SHALL reject detections farther than `astrometry_max_sep_arcsec` from the star's median measured position.
4. **Plate series.** THE cleaner SHALL tag every point with its series kind, and SHALL reject meteor points WHEN the meteor policy is `reject`. THE policy SHALL be a parameter, never a hidden default.
5. **Emulsion.** THE cleaner SHALL tag a point `non_blue` when its exposure's `median_colorterm_apass` is below -0.53 or exactly 0.0, and `emulsion_unknown` when it is missing. It SHALL NOT reject such points. A point on a blue plate carries neither tag.
6. **Blends.** THE cleaner SHALL tag a detection `blend_suspect` when `blended_mag` differs from the star's catalog magnitude by more than `blend_max_diff_mag`. A null `blended_mag` SHALL NOT be tagged.
7. **Shape.** THE cleaner SHALL tag a detection `shape_outlier` when its `fwhm_image` or `ellipticity` lies more than `shape_outlier_sigma` robust sigma from the median of stars of similar brightness on the same plate. WHEN a plate has fewer than 10 such stars, THE cleaner SHALL NOT tag it.
8. **Plate edge.** THE cleaner SHALL tag a detection `plate_edge` when DASCH's `RADIAL_BIN_9` bit is set.
9. **Deep limits.** THE cleaner SHALL tag a non-detection `deep_limit_suspect` when its plate limit is fainter than the star's median magnitude by more than `deep_limit_margin_mag`. The tag SHALL NOT remove the point.
10. WHEN a curve has fewer than 50 good detections, THE cleaner SHALL set the kill reason `too_few_points`.

**R4. Detrending**
1. FOR each target, THE detrender SHALL choose up to `comparison_max` comparison stars from the same tile that: are within `comparison_mag_window` mag in B, are within `comparison_colour_window` in colour, are constants in the Leavitt Set sense (no VSX entry within the exclusion radius, low scatter), are not the target, and share plates with it. WHEN the colour is missing for the target, THE detrender SHALL drop the colour cut and record it.
2. WHEN fewer than `comparison_min` comparison stars qualify, THE detrender SHALL return the raw curve as the detrended curve, SHALL set the kill reason `few_comparisons` and SHALL NOT widen the windows silently.
3. A comparison star's residual on a plate SHALL be its good magnitude minus its own median over good points. A plate's offset SHALL be the median residual over comparison stars with a measurement on that plate, WHEN at least `comparison_min_per_plate` of them have one. A target point on a plate without an offset SHALL be tagged `no_offset` and left out of the detrended curve.
4. THE detrended magnitude SHALL be the target magnitude minus the plate offset. THE raw curve SHALL be kept alongside it.
5. THE detrender SHALL give each star a per-series zero-point (the median detrended magnitude in that series). THE zero-point SHALL be reported and SHALL NOT be subtracted from the detrended curve, because series cluster in time and subtracting it would remove real long-term change.
6. THE comparison pool SHALL always be built from the original, never injected, curves.
7. A non-detection SHALL keep its plate limit and SHALL have the plate offset applied to it, so limits stay comparable with detrended magnitudes.

**R5. Seams**
1. THE seam module SHALL split a curve into continuous stretches at the Menzel Gap (`menzel_gap_start_year` to `menzel_gap_end_year`) and wherever the dominant series changes. THE second rule SHALL be: the share of a series' points in a 5-year window changes from at least `series_change_share` to under half of that.
2. THE module SHALL provide a test that returns `seam_only` for a change at a given epoch WHEN the epoch lies inside the Menzel Gap, OR the nearest good points before and after it are in different stretches, OR fewer than `seam_min_points` good points lie on either side of the epoch inside that stretch. Otherwise it SHALL return not-`seam_only`.
3. A step placed inside the Menzel Gap SHALL always be `seam_only`, even when plates exist inside the gap, because the plan's rule is that such a step is visible only by comparing before-gap with after-gap data.

**R6. Property tests** (Hypothesis, plan 6.7)
1. Adding a rejection never increases the count of good points.
2. Adding a constant to every star on one plate changes no detrended value by more than `15 sigma / N`, where sigma is the comparison stars' scatter about their medians and N the number of plates (0.0125 mag for sigma 0.1 and 120 plates). The change comes from the medians, which move by about half a gap between neighbouring points; measured over six settings it was 6 to 10 times sigma/N.
3. Adding a constant to every point of every star shifts every detrended value by that constant.
4. Detrending a curve against comparison stars that are exact copies of it returns a constant.
5. Cleaning twice gives the same tags as cleaning once.
6. Every tag comparison on a magnitude has a test on both sides of its boundary.

**R7. Golden tests**
1. T CrB SHALL keep at least 2 eruption points within 60 days in `good_for_outburst` after cleaning, and the detrended curve SHALL keep the eruption at least 1.0 mag above its baseline.
2. A small hand-computed ensemble (5 comparison stars on 6 plates) SHALL give the exact detrended values written by hand.
3. A hand-checked quiet star SHALL give the tags and counts you review and approve before they are frozen.
4. RY Cnc SHALL be checked for the cleaning tags only.

**R8. Before-and-after report** (the exit test, on the dev constants of the Leavitt Set)
1. THE report SHALL give the median within-block and between-block scatter of the dev constants before and after detrending.
2. THE report SHALL give the false-alarm rate of the trivial detector at thresholds 0.2, 0.3 and 0.5 mag on raw and on detrended constants, with 95% intervals.
3. THE report SHALL give the false-alarm rate and the completeness of each meteor policy (reject and keep) on the dev split.
4. THE report SHALL give the share of Menzel Gap steps judged `seam_only` (required: 100%) and the share of other steps, at each amplitude, judged `seam_only`.
5. A component whose number does not improve SHALL be reported as such and left out of the default settings.

**R9. The Tabby's Star check**
1. THE check SHALL fit a straight line to decade medians (4 or more populated decades) for KIC 8462852 and for each constant within 0.5 mag in B in the Tabby tile, on raw and on detrended curves, in mag per century.
2. THE check SHALL report where the star's slope sits in the distribution of the others', on raw and detrended curves. The exit test is that its detrended slope lies inside the central 95% of the detrended distribution. The plan's claim is that the "unique" trend is common, so its raw slope SHOULD also lie inside the raw distribution. Neither result is a claim about the star.

**R10. Layers, thresholds and speed**
1. `l1` SHALL import only from `data`, and its numeric thresholds SHALL be in `thresholds.py` with a decision record.
2. Cleaning and detrending the 1,058 Tabby stars SHALL take under 2 minutes, and each tile's comparison structure SHALL be built once.
3. A new thresholds record `docs/decisions/0006-l1-cleaning-and-detrending.md` SHALL give the numbers, the meteor policy and the soft and hard split of the standard rejections, and `plan.md` SHALL get a changelog entry.

## Thresholds this sprint adds (all status start; you own them)

| Name | Value | Meaning |
|---|---|---|
| `source_split_overlap_max` | 0.1 | Two sources interleave when under 10% of the smaller curve's detection plates have a detection in the other too |
| `blend_max_diff_mag` | 0.3 | Plan 6.1 step 6 |
| `shape_outlier_sigma` | 3.0 | Plan 6.1 step 7 |
| `deep_limit_margin_mag` | 1.0 | Limit this much fainter than the star is suspect |
| `comparison_max` | 40 | Plan: 30 to 50 comparison stars |
| `comparison_min` | 10 | Under this the target is not detrended |
| `comparison_min_per_plate` | 5 | Under this a plate has no offset |
| `comparison_mag_window` | 0.5 mag | Plan 6.2 |
| `comparison_colour_window` | 0.25 | In the catalog colour (B-V), for the target colour plus or minus this |
| `series_change_share` | 0.5 | A series that held at least half of a window and drops under half of that |
| `seam_min_points` | 20 | Plan 6.4: 20 good points on each side |

`astrometry_max_sep_arcsec` (15) and `source_split_radius_arcsec` (5) exist already.

## Decisions for you

1. **Meteor policy.** Proposed: build both, report both (R8.3), and set the default to the policy with the better false-alarm rate that keeps the completeness. Until the numbers are in, the default is the plan's `reject`.
2. **Soft and hard split of the standard rejections** (decision record needed by the plan). Proposed: soft is `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS` and `BIN_DRAD_UNKNOWN`. Hard is `HIGH_BACKGROUND` and `CLOSE_TO_LIMITING`. Everything detrended uses the hard-and-soft `good`.
3. **The threshold table above.**
4. **Neighbour pull for source splitting.** 21 lightcurves on the public API, about 20 seconds. I can run it, since the public API needs no key.
5. **Unknown emulsion** is tagged and kept. Excluding it from triggers would drop 53% of exposures. Say if you want a stricter rule.
