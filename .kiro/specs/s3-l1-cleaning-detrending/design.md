# Design: S3 L1 cleaning, detrending and seams

## Modules

```
src/computers/
  data/
    series.py        73 series: kind and plate scale, copied from daschlab 1.1.0
    timeconv.py      jd_to_year (moved from bench/inject.py; bench re-exports it)
  l1/
    tags.py          the Tag bit flags and the series-kind codes
    scatter.py       star_stats, tile_stats, reference scatter (moved from bench/constants.py)
    curve.py         Curve: arrays plus tags, with `usable()`, `with_magnitudes()`
    clean.py         clean_star: the nine steps, one function per step
    context.py       TileContext: the per-tile matrices and the comparison pool
    detrend.py       offsets_for, detrend, per-series zero-points
    seams.py         stretches, seam_only
  bench/
    l1_report.py     before and after detrending, meteor policies, seam check
    tabby_check.py   the century-trend check for KIC 8462852
  cli.py             computers bench l1 | tabby
scripts/make_series_table.py   regenerates data/series.py from daschlab (spike group)
tests/{unit,property,golden}/
```

`l1` imports `data` and the top-level `thresholds` only. The scatter statistics move into `l1` because the comparison pool needs the same "quiet star" test as the benchmark, and `l1` may not import `bench`. `bench/constants.py` keeps `select_constants` and imports the statistics from `l1.scatter`, so its tests and the built Leavitt Set stay byte-identical (checked by `bench build` and a diff of the manifest).

## The curve

A `Curve` holds numpy arrays for one star, sorted by time, one entry per measurement row, never fewer rows than the input:

| Array | Meaning |
|---|---|
| `time`, `mag`, `limit` | HJD days, magnitude (NaN for a non-detection), plate limit |
| `exposure_id`, `series` | strings |
| `kind` | uint8 series kind: 0 unknown, 1 narrow, 2 patrol, 3 meteor |
| `tags` | uint64 bit mask |
| `std_reject` | the silver `reject` mask, kept as received |
| `good` | detection that survives every rejecting tag; computed from `tags` |
| `good_for_outburst` | as `good`, but points rejected only by the three soft bits stay |

`usable()` returns the rows that are good detections or non-detections. That is what an injection is applied to, and what detrending reads. `with_magnitudes(new_mag)` swaps in injected magnitudes and recomputes `good`. Tags describe the plate and the measurement, so they are not recomputed after injection.

**Tags.** One `IntFlag`, never raw numbers.

| Tag | Rejects? | Set by |
|---|---|---|
| `STD_HARD`, `STD_SOFT` | yes, both | step 2: the silver `reject` bits split in two groups |
| `ASTROMETRY` | yes | step 3 |
| `METEOR_REJECTED` | only under the `reject` policy | step 4 |
| `NON_BLUE`, `EMULSION_UNKNOWN` | no | step 5 |
| `BLEND_SUSPECT` | no | step 6 |
| `SHAPE_OUTLIER` | no | step 7 |
| `PLATE_EDGE` | no | step 8 |
| `DEEP_LIMIT_SUSPECT` | no | step 9 (non-detections only) |
| `UNKNOWN_SERIES`, `SOURCE_MERGED` | no | steps 1 and 4 |
| `NO_OFFSET` | no, but the point has no detrended value | detrending |

Soft group: `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS`, `BIN_DRAD_UNKNOWN`. Hard group: `HIGH_BACKGROUND`, `CLOSE_TO_LIMITING`. `STD_SOFT` is set when a point carries only soft bits, so `good_for_outburst` is `good` plus those points. This split goes in decision `0006`.

## The cleaning steps

Each is a function `(curve, ...) -> curve` that only ORs tags. The cleaner records a count per tag.

1. **Source splitting.** Input: the target and the neighbour curves within 5 arcsec. Overlap = plates with a detection in both divided by the detection plates of the smaller curve. If the overlap is under `source_split_overlap_max`, the merged curve takes each plate's detection from the source that has one, and every merged point gets `SOURCE_MERGED`. Only 18 patrol stars have a neighbour, so the pull is 21 lightcurves (`computers pull --stage neighbours`, new, public API is enough). The neighbours live in `data/silver/measurements/tile=<tile>-neighbours.parquet` and a small `neighbours` JSON record; no new core table.
2. **Standard rejections.** From the silver `reject` column, split into hard and soft by the bit table above.
3. **Astrometry.** The star's mean position is the median of the measured `ra_deg` and `dec_deg`. A detection farther than 15 arcsec is rejected. Rows with a null position are not tested.
4. **Series.** `kind` from `data/series.py`. Under `meteor_policy="reject"`, meteor rows get `METEOR_REJECTED`. Unknown series get `UNKNOWN_SERIES`.
5. **Emulsion.** Per exposure: `median_colorterm_apass` below -0.53 or exactly 0.0 is `NON_BLUE`, missing is `EMULSION_UNKNOWN`. Red (below -1.13) and yellow share one tag for now.
6. **Blends.** Uses `blended_mag` against the star's catalog `apass_b`. 94% of rows have no `blended_mag`, so this step touches few points.
7. **Shape.** Needs the other stars on the same plate, so it runs inside `TileContext`. For each plate and each 1-mag bin, the median and robust sigma (MAD times 1.4826) of `fwhm_image` and `ellipticity` over the tile's detections. A target detection farther than `shape_outlier_sigma` is tagged. Bins with under 10 stars give no tag.
8. **Plate edge.** The `RADIAL_BIN_9` bit in `aflags`.
9. **Deep limits.** Non-detections only: `limit - median_mag > deep_limit_margin_mag`.

Steps 1 to 6, 8 and 9 need only the star's own rows and the tile's exposures table. Step 7 needs the tile matrices.

## TileContext and detrending

A `TileContext` is built once per tile (and meteor policy) from `stars`, `measurements`, `exposures`, the VSX match and the scatter statistics.

- **Matrix.** `M[n_stars, n_exposures]` float32 of good magnitudes, NaN where a star has no good detection on an exposure. Columns come from `exposure_id`. Tabby: 1,058 by 21,384 is 90 MB, M44 27 MB.
- **Baselines and residuals.** `b = nanmedian(M, axis=1)`, `R = M - b[:, None]`.
- **Quiet stars.** Not matched to VSX within the exclusion radius, and within `constant_scatter_factor` of the reference scatter for their magnitude (`scatter.py`). This is the Leavitt Set's constant test on the raw statistics, so the pool never includes a known variable.
- **Comparison pool for a target.** Quiet stars other than the target with `|dB| <= comparison_mag_window` and `|dC| <= comparison_colour_window`, ranked by `max(|dB| / mag_window, |dC| / colour_window)` with ties broken by reference number, the first `comparison_max`. When the target has no colour, only the magnitude cut applies, and `colour_cut=False` is recorded.
- **Offsets.** `offset[j] = nanmedian(R[pool, j])`, valid when at least `comparison_min_per_plate` rows are not NaN. A pool under `comparison_min` gives the kill reason `few_comparisons` and no detrending.
- **Detrended curve.** `mag - offset[col]`, with `NO_OFFSET` where the offset is invalid. Non-detections get the same shift on `limit`.
- **Zero-points.** The median of the detrended magnitude per series, reported with its count.
- **Cache.** Offsets are cached per target. An injection changes only the target's own rows, so one host's 60 injections share one offset vector. The pool is built from the original matrix, which satisfies "comparison stars are never injected" by construction. If the host is itself in another target's pool, it is there with its original curve.

One pass is used, as in the plan. Re-estimating the baselines after the offsets is a variant the benchmark can test later.

**Cost.** The offsets for one target are a `nanmedian` over about 40 rows of 21k columns, about 10 ms. Cleaning all 1,058 Tabby stars is a set of vectorised passes over 5.7 million rows. Target: under 2 minutes for the tile.

**The invariance bound (R6.2).** Baselines are medians. Moving one plate by a constant moves a median by about half the gap between neighbouring points, so the detrended values change by a little. Hypothesis found 0.0108 mag at noise 0.19 against the 0.01 first written, so the requirement was restated with the measured scaling: at most 15 sigma / N, observed 6 to 10 sigma / N.

## Seams

1. **Stretches.** Points before `menzel_gap_start_year` are one stretch, points inside the gap another, points after `menzel_gap_end_year` a third. Each of these splits further wherever the series mix changes: in consecutive 5-year windows with at least 10 good points each, a series with a share of at least `series_change_share` in one window and under half of that in the next breaks the curve between them.
2. **`seam_only(curve, epoch)`.** True when the epoch lies in the gap, when the nearest good points before and after it fall in different stretches, or when either side holds under `seam_min_points` good points inside that stretch.
3. **Why a gap step is always `seam_only`.** The plan treats a step placed in the gap as one the plates cannot separate from the calibration change, and the benchmark marks it expected to be missed. The epoch test enforces this even where plates exist in the gap.

## Benchmark integration

- **The L1 detector.** `L1Detector(context, meteor_policy, detrend, threshold)` wraps the trivial first-decade, last-decade rule on the cleaned (and optionally detrended) curve. It needs the host's metadata, so the harness gets a second detector form: a detector with `takes_host = True` is called as `(host, mag, limit)`, where `host.curve` is the `Curve` it came from. The old form `(time, mag, limit)` still works.
- **Hosts in L1 mode.** `host = clean_star(raw rows)`, then `usable()`, then injection, then detrending against the cached offsets.
- **The report** (`computers bench l1`): table 1, scatter before and after; table 2, false alarms at 0.2, 0.3 and 0.5 mag on raw and detrended constants; table 3, each meteor policy's false-alarm rate and completeness; table 4, the seam check. Raw means the host with no cleaning beyond what `host_curves` already did.
- **The Tabby check** (`computers bench tabby`): decade medians need at least 20 good points and 4 decades; the slope is a Theil-Sen fit in mag per century.

## Risks

| Risk | Handling |
|---|---|
| Detrending removes real long-term change | The per-series zero-point is reported, not subtracted. The completeness table shows what detrending costs. The result is reported even if it hurts. |
| Constants are chosen quiet, so false-alarm gains look small | Table 2 uses low thresholds (0.2, 0.3 mag), and table 1 shows scatter, which does not depend on a detector. |
| The comparison pool shares the tile's systematics with the target | That is the point of the method. It cannot remove a systematic that differs by position on the plate. Reported as a limit. |
| 53% unknown emulsion | Tagged and kept. The kill reason `non_blue_only` (Sprint 4) will count only plates known to be non-blue. |
| `few_comparisons` for red or bright stars | Counted per tile in the report. Not widened silently. |
| Meteor policy changes the Leavitt Set statistics | The set's constants are computed from the silver `reject` column and do not depend on the policy. The policy applies in L1 only. |
