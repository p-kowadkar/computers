# 0001. Sprint 0 findings

Date: 2026-11-24. Spec: `.kiro/specs/s0-data-spike/`. Plan: `plan.md` section 13 (S0) and Appendix C.

**Run:** `scripts/s0_spike.py --refresh`, started 2026-11-24T17:56Z on the **full API** (Starglass key set). Python 3.12, daschlab 1.1.0, macOS. Results file: `data/spike/results.json` (not committed). Plots and cutouts: [`assets/s0/`](assets/s0/).

Four stars were pulled: RY Cnc, T CrB, KIC 8462852 (Tabby's Star), and one quiet star chosen by rule.

## Exit test: passed

| Exit test from the plan | Result |
|---|---|
| The 1946 eruption is visible | Yes. Raw peak is mag 7.73 on 1946-02. After the standard rejections it is mag 8.59. Both are at least 2.8 mag above the 11.4 baseline. See `assets/s0/tcrb_raw.png` and `tcrb_clean.png`. |
| The columns L1 will read are listed | Yes. See "Columns L1 will read". |
| Request times are measured | Yes. Median 2.2 s per lightcurve, 0 failures in 10. See "Timing". |

The T CrB check (requirement R5) passed on raw data, after the standard rejections, and after the extra rejections. But see finding W4: the standard rejections remove most of the eruption points.

## What matched the plan

| # | Plan statement | Result |
|---|---|---|
| M1 | `apply_standard_rejections()` rejects five AFLAGS bits (6.1 step 2) | Held, read in the installed 1.1.0 source. The bits are `HIGH_BACKGROUND`, `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS`, `CLOSE_TO_LIMITING`, `BIN_DRAD_UNKNOWN`. |
| M2 | A single measurement is good to about 0.15 mag RMS (3.1) | Held. Robust scatter of the cleaned curves is 0.12 mag (Tabby) and 0.16 mag (quiet star). |
| M3 | Cutouts are 835 by 835 px | Held, for all five cutouts. Each took 5 to 7 s. |
| M4 | VSX is reachable through VizieR (5.1, **[verify]**) | Held, with a caveat. `astroquery.vizier` with catalog `B/vsx/vsx` returned T CrB with its type and period from the CfA mirror (`vizier.cfa.harvard.edu`). The Strasbourg server failed on 2026-11-23 (finding W9). This check ran during a debug run on 2026-11-23, not in the official run. |
| M5 | ZTF, Gaia and SIMBAD cross-matches | Not tested. Task 4. |
| M6 | Coordinates in 3.4 | Held. T CrB resolved to 239.8757, +25.9202 and Tabby's Star to 301.5644, +44.4569. |
| M7 | Tabby's Star: the crew must not call a century-long fade | Needs care. See W5. |
| M8 | Tiles are small (a few hundred stars brighter than B 13.5 for M44 and T CrB) | Roughly held. See "Tile sizes". |

## What did not match the plan

**W1. The catalog radius limit is wrong in the DASCH docs.** The docs say `querycat` takes up to 3,600 arcsec. I tested the public API:

| Radius (arcsec) | Result |
|---|---|
| 900 to 2,400 | Works. Time grows with the box area: 5.8 s, 13.8 s (1,500), 17 s (1,800), 30.5 s (2,400, sparse T CrB field). |
| 3,000 and 3,599 | HTTP 504 "Endpoint request timed out" after about 30 s. |
| 3,600 and 3,601 | HTTP 400 "illegal radius_arcsec parameter". |

Time follows the box area, not the star count. At 1,800 arcsec (a 1 by 1 degree box) T CrB's sparse field took 17 s for 1,473 sources and Tabby's dense field took 19 s for 11,594 sources (1.6 MB). So **a 2 by 2 degree tile is four calls of 1,800 arcsec**, not one. 2,400 is too close to the gateway timeout to use. Plan 3.4, 5.1 and 5.2 are updated.

**W2. Raw column names are snake_case for two endpoints and camelCase for the third.** The plan (5.3) said camelCase for all.

| Endpoint | Columns | Style |
|---|---|---|
| `querycat` | 18 | snake_case (`ref_text`, `ref_number`, `gsc_bin_index`, `ra_deg`, `stdmag`, `num_matches`, ...) |
| `lightcurve` | 64 | snake_case (`date_jd`, `plate_number`, `magcal_magdep`, `aflags`, ...) |
| `queryexps` | 27 | camelCase (`limMagApass`, `resultIdApass`, `medianColortermApass`, ...) |

Responses are JSON lists of comma-separated strings, header first, as the plan said. The raw names also differ from the names daschlab shows in its tables, so the thin client needs a mapping. Confirmed from daschlab's source: `date_jd` becomes `time`; `plate_number`, `mosaic_number`, `solution_number` and `exposure_number` become `platenum`, `mosnum`, `solnum` and `expnum`; `x_image` and `y_image` become `image_x` and `image_y`; `fwhm_pix` becomes `fwhm_image`; `reject_flag` becomes `local_bin_reject_flag`; `iso_area_sqdeg` becomes `iso_area_world`. Probably `plate_center_dist_deg` becomes `plate_dist`, but I did not read that line.

**W3. `plate_quality_flag` does not exist.** Plan 4.5 lists it in `measurements`. The raw lightcurve response has no such column, and daschlab's table has it commented out. The exposures table has no per-plate quality column either (it has `class`, `center_distance`, `edge_distance`, `lim_mag_apass`, `median_colorterm_apass`). The per-point `BAD_PLATE_QUALITY` AFLAGS bit (bit 8) carries this information. Plan 4.5 is updated.

**W4. The standard rejections remove most of the eruption.** This matters for L1.
- They remove 30 to 44% of all detections: RY Cnc 40%, T CrB 40%, Tabby 30%, quiet star 44%.
- On T CrB, 32 points from 1946 are at least 2 mag brighter than baseline. The standard rejections remove 25 of them, which is 78%. Seven survive.
- The bits responsible are `LARGE_ISO_RMS` (18 of the 25 removed points), `LARGE_LOCAL_SMOOTH_RMS` (19) and `BIN_DRAD_UNKNOWN` (12). A point can carry more than one bit. These flags describe images with large measurement scatter, which is what a very bright, saturated nova looks like.
- The same bits flag a lot of ordinary points: `LARGE_ISO_RMS` flags 207 of the 1,788 baseline detections (12%) and `BIN_DRAD_UNKNOWN` flags 529 (30%).
- The raw peak (mag 7.73) is far fainter than T CrB's real peak, which the literature puts near mag 2. The plates saturate and calibration breaks down, and `TOO_BRIGHT` (not in the standard set) is set on 15 of the 32 eruption points. The amplitude is a lower limit.

The check still passed because seven points survive. If the eruption had been sampled less densely, a pipeline using the standard rejections would have lost it silently.

**W5. Tabby's Star shows a century trend before any detrending.** Decade medians of the cleaned curve rise steadily: 12.27 (1890s), 12.32 (1900s to 1920s), 12.34 (1930s and 1940s), 12.37 (1950s and 1960s), 12.41 (1970s), 12.45 (1980s). That is 0.18 mag fainter over about a century. The quiet star shows no such trend (decade medians span 0.15 mag and end 0.11 mag *brighter*). The plan says constant stars can drift up to about 0.2 mag per century from systematics, so this is a single star's trend measured without any comparison stars. It is the test case for Task 7: after ensemble detrending, this trend must sit inside the spread of comparison stars. It must not be reported as a fade before then.

**W6. `UNCERTAIN_CATALOG_MAG` is not a variability flag.** I expected it to mark variables. It is set on every detection of RY Cnc, T CrB *and the quiet star* (SIMBAD type `*`), and on none of Tabby's Star's. It is a property of the catalog entry. L1 must not use it as a known-variable cue. **[corrected 2026-11-25, see W16 in `0003`]** The bit equals the catalog `v_flag` for all four stars checked, and `v_flag` is 1 for 97% of the sources near T CrB, so it marks almost every source and carries no information about variability.

**W7. The reduction guide's import path is wrong.** `from daschlab.lightcurves import AFlags` fails in 1.1.0. The class is in `daschlab.photometry`.

**W8. RY Cnc is inside the M44 tile.** The plan said only "same constellation". RY Cnc sits at 129.98, +19.82, which is about 0.17 degrees from the M44 tile centre (130.1, +19.7). It is a known eclipsing binary in the patrol field, so it is a benchmark true positive.

**W9. The Strasbourg CDS servers were down on 2026-11-23.** `simbad.cds.unistra.fr` returned 404 for the whole application, and the VizieR endpoints there failed (`viz-bin/votable` 404, `tapvizier` timed out). The mirrors `simbad.harvard.edu` and `vizier.cfa.harvard.edu` worked. Task 4 should make the server configurable, with a fallback list.

**W10. Full and public APIs gave the same speed, one request at a time.** Median lightcurve time was 2.36 s on the public API (debug run) and 2.20 s on the full API. The difference between them is the rate limit, and sequential requests never reach it. The full API's higher limit is untested.

## Numbers per target

| | RY Cnc | T CrB | Tabby's Star | Quiet star |
|---|---|---|---|---|
| Catalog id | APASS_J083954.6+194919 | APASS_J155930.2+255513 | APASS_J200615.5+442725 | APASS_J155828.4+255631 |
| Catalog B mag (`stdmag`) | 14.08 | 11.43 | 12.36 | 11.02 |
| Match separation | 0.09" | 0.02" | 0.02" | 0.00" |
| Rows | 3,653 | 2,882 | 5,332 | 3,404 |
| Detections | 1,067 | 2,605 | 2,717 | 3,307 |
| Non-detections | 2,586 | 277 | 2,615 | 97 |
| Date range | 1886 to 1990 | 1891 to 1990 | 1890 to 1990 | 1891 to 1990 |
| Plate series present | 37 | 29 | 33 | 29 |
| Median detected mag | 13.30 | 11.29 | 12.36 | 10.93 |
| Rejected, standard | 425 | 1,035 | 805 | 1,458 |
| Rejected, standard + 15" + defect flag | 471 | 1,195 | 930 | 1,644 |
| Good detections, standard | 642 | 1,570 | 1,912 | 1,849 |
| Good detections, after extra | 596 | 1,410 | 1,787 | 1,663 |
| Exposures in the field | 13,431 | 16,326 | 19,227 | 16,255 |
| Cutout: star's offset from centre | 18 px (5.2 sigma) | 10 px (13 sigma) | 2 px (9.8 sigma) | 1 px (19.8 sigma) |

Notes:
- Offsets are the strongest blob within 24 pixels (about 35 arcsec) of the centre, so they are approximate. RY Cnc's second cutout, the `SUSPECTED_DEFECT` point (year 1910.95), lands 24 px away at 8.6 sigma. It shows a large, extended smudge, not a point source, and is a good example of what the flag marks (`assets/s0/rycnc_defect.png`).
- The quiet star was chosen by the rule in the design: B between 11 and 13, at least 5 arcmin from T CrB, top quartile of detections, and SIMBAD type `*`. Two candidates qualified out of 333 sources, and the lower catalog id won. SIMBAD type is only a proxy for "not variable" until VSX arrives in Task 4.
- `TOO_BRIGHT` marks individual measurements, not stars: it is set on 6 RY Cnc detections (a 14th-magnitude star) and on 35 of the quiet star's 3,307. In both cases it likely marks measurements that came out much brighter than the star's normal level, and on T CrB it is set on 15 of the 32 eruption points. Task 6 should check this before using it.
- T CrB's check: raw baseline 11.40 from 1,788 detections, brightest 1946 point 3.67 mag above it; after the standard rejections 2.80 mag above; after the extra rejections 2.82 mag above.

## Timing

Ten uncached lightcurve requests for the catalog sources nearest T CrB (sources 1 to 10; source 0 is T CrB itself). Full API, one at a time.

| | Result |
|---|---|
| Median / slowest | 2.20 s / 2.42 s |
| Failures | 0 of 10 |
| Rows per lightcurve | 1,970 to 2,014 |
| Saved size of the largest | 499 KB (ECSV) |

Direct raw calls for T CrB:

| Call | Time | Response | Records |
|---|---|---|---|
| `querycat`, 850" | 5.3 s | 47 KB | 333 |
| `lightcurve` | 2.4 s | 1.13 MB | 2,882 |
| `queryexps` | 18.8 s | 2.87 MB | 16,326 |

**The 6 MB response limit.** daschlab's client documents a 6 MB cap per response. The lightcurve (1.13 MB for 2,882 rows) has 5.6 times headroom, so about 390 bytes per row puts the limit near 16,000 rows. No star here comes close. `queryexps` has only 2.2 times headroom, so a field with more than about 35,000 exposures could hit the cap. `queryexps` also takes 19 to 21 s, close to the 30 s gateway timeout, and it accepts `midpoint_min` and `midpoint_max` to narrow the result.

## Tile sizes (early estimate for Task 4)

**Superseded 2026-11-25.** Task 4 measured the real counts of stars with B at most 13.5: M44 444, T CrB 186, Tabby 1,058, total 1,688 (see `0003-tile-sizes-and-access.md`). The M44 estimate below was 65% too high, as the note warned. The T CrB and Tabby estimates were within 6%. The estimate is kept for the record.

Each session catalog box covers 0.22 square degrees, one eighteenth of a tile. Counts of catalog sources with B at or below 13.5, scaled to a tile:

| Field | In box | Per tile (about) | Note |
|---|---|---|---|
| RY Cnc (M44 tile) | 41 | 735 | M44 is an open cluster, so this may be high |
| T CrB | 10 | 180 | Same count in the quiet star's box |
| Tabby's Star | 58 | 1,040 | |

Every source in these boxes at B 13.5 or brighter has at least 50 detections, so the `too_few_points` kill reason would rarely fire at this brightness. Each box holds only 10 to 58 stars, so these figures are uncertain to at least 30%. Task 4's pilot gives the real counts (1,688 in total, not roughly 2,000). The case-count targets in S9, S13 and S17 must still come from the benchmark, as the review concluded, and the case-count targets in S9, S13 and S17 must come from the benchmark, as the review concluded.

## Columns L1 will read

**Lightcurve (64 columns present).** Plan 4.5's `measurements` columns all exist except `plate_quality_flag` (W3). Those used by L1:

| Purpose | Columns |
|---|---|
| Time and photometry | `time` (HJD), `magcal_magdep`, `magcal_local_rms`, `limiting_mag_local`, `time_accuracy` |
| Identity and join to exposures | `ref_number`, `series`, `platenum`, `mosnum`, `solnum`, `expnum`, `exp_local_id` (added by daschlab) |
| Quality flags | `aflags`, `bflags`, `a2flags`, `b2flags`, `local_bin_reject_flag`, `reject` (added by daschlab) |
| Shape checks (6.1 step 7) | `fwhm_image`, `ellipticity`, `iso_area_world`, `kron_radius`, `theta_j2000` |
| Position and plate edge | `pos` (`ra_deg`, `dec_deg` raw), `image_x`, `image_y`, `plate_dist`, `drad_rms2` |
| Blends and calibration | `blended_mag`, `npoints_local`, `extinction`, `magcor_local` |

**Exposures (27 columns).** Used by L1 and the later detrending: `class` (plate class, for the emulsion check), `exptime`, `obs_date`, `wcssource` (`imwcs` means imaging with a WCS solution), `lim_mag_apass`, `median_colorterm_apass` (a per-plate colour term, useful for colour-matched detrending in 6.2), `center_distance`, `edge_distance`.

**AFLAGS bits seen on these detections** (18 distinct named bits occurred across the four stars): `HIGH_BACKGROUND`, `BAD_PLATE_QUALITY`, `UNCERTAIN_DATE`, `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS`, `CLOSE_TO_LIMITING`, `RADIAL_BIN_9`, `BIN_DRAD_UNKNOWN`, `UNCERTAIN_CATALOG_MAG`, `CASE_B_BLEND`, `CASE_C_BLEND`, `CASE_BC_BLEND`, `LARGE_DRAD`, `SUSPECTED_DEFECT`, `SXT_BLEND`, `LARGE_SMOOTHING_CORRECTION`, `TOO_BRIGHT`, `LOW_ALTITUDE`. The plan's blend (6.1 step 6), plate edge (step 8) and defect checks each have a matching bit already set by DASCH, which L1 can use alongside its own tests.

## Effects on Task 4 and later tasks

1. **Catalog pulls:** four calls per tile at `radius_arcsec` 1,800, never above about 2,000. Add a retry on 504.
2. **Thin client:** map raw names to daschlab names (W2), create `reject`, `local_id`, `exp_local_id` and `plate_local_id` itself, and add the retries, status checks and rate-limit handling that daschlab's client lacks. Handle the 6 MB cap on `queryexps` by splitting on `midpoint_min` and `midpoint_max`.
3. **Golden test:** compare against daschlab 1.1.0 (pinned). Its standard rejections are the five bits in M1.
4. **L1 cleaning (Task 6):** the standard rejections must not silently decide outburst candidates (W4). The design for Task 6 needs a second path: points rejected only by `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS` or `BIN_DRAD_UNKNOWN` stay visible to the outburst trigger, and the case file reports how many points the standard rejections removed. This costs a decision record when Task 6 starts.
5. **Detrending (Task 7):** Tabby's trend (W5) is the first check. Use `median_colorterm_apass` and `stdmag` colour for the colour window.
6. **Server mirrors:** the catalog clients (SIMBAD, VizieR) take a list of servers (W9).
7. **Tile sizes:** write the decision record on the L3 budget and case counts once the Task 4 pilot gives real counts.

## Open questions

- What does `UNCERTAIN_CATALOG_MAG` mean, and why is it set on the quiet star and not on Tabby's Star? Ask the DASCH list (Task 4 email).
- What are the values of the exposure `class` column? Task 6 needs them for the emulsion check, and DASCH says the data are incomplete.
- Does the 30 s gateway timeout depend on load? The 2,400" call took 30.5 s and succeeded, so the limit is not a fixed 30 s. Task 4 should measure it again.
- What are the full API's real rate limits? These tests were sequential and did not reach them.
- Gaia DR4's release date is still unconfirmed (Task 4).
- The isolated mag-5.7 point on T CrB in 1951 (visible in the raw plot) looks like a defect. Task 6 can use it as a test case.
