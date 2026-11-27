# 0006. L1 cleaning, detrending and seams

Date: 2026-11-26. Spec: `.kiro/specs/s3-l1-cleaning-detrending/`. Status: **built; one decision is waiting for you** (the meteor policy default, section 4). Reports: `computers bench l1` and `computers bench tabby`, which write `data/reports/bench-l1.json` and `bench-tabby.json`.

## Decisions taken by default in the Sprint 3 review

1. **Meteor policy.** Both policies are built and reported. The default is `reject` (the plan's rule) until the benchmark gives a better answer, and a change of default needs a new record. Meteor series are 10 to 21% of good detections, 50% of the 1950s ones in M44 and T CrB, and none after 1960.
2. **The standard rejections are split in two groups.** DASCH's five bad AFLAGS bits are the standard set.
   - Hard: `HIGH_BACKGROUND` (64) and `CLOSE_TO_LIMITING` (8192).
   - Soft: `LARGE_ISO_RMS` (2048), `LARGE_LOCAL_SMOOTH_RMS` (4096) and `BIN_DRAD_UNKNOWN` (32768).
   - A point with any hard bit is `STD_HARD`. A point with only soft bits is `STD_SOFT`. Both leave the good set. `good_for_outburst` keeps the `STD_SOFT` points, because the standard rejections removed 25 of 32 T CrB eruption points (W4), mostly through the soft bits.
3. **Unknown emulsion** (no colour term, 53% of M44's exposures) is tagged `EMULSION_UNKNOWN` and kept. Only plates known to be non-blue are tagged `NON_BLUE`.
4. **Source splitting** pulls the 21 neighbour lightcurves of the 18 patrol stars that have a neighbour within 5 arcsec.
5. **The colour-term boundary is inclusive at the blue end.** DASCH says "above -0.53 is blue", so -0.53 itself is yellow and a point on such a plate is `NON_BLUE`. Exactly 0.0 is the pipeline's default (unset) and is also `NON_BLUE`.

## Thresholds recorded here

All are status **start**: values to tune, not values chosen from data.

| Threshold | Value | Plan | Meaning |
|---|---|---|---|
| `min_good_points` | 50 points | 6.5 | Plan: a curve with fewer good detections is killed as `too_few_points`. Added after Sprint 3 closed, see the note at the end. |
| `source_split_overlap_max` | 0.1 | 6.1 step 1 | Two sources interleave when under this share of the smaller curve's detection plates also has a detection in the other curve |
| `blend_max_diff_mag` | 0.3 mag | 6.1 step 6 | Plan's starting value |
| `shape_outlier_sigma` | 3.0 | 6.1 step 7 | Plan's starting value, in robust sigma |
| `deep_limit_margin_mag` | 1.0 mag | 6.1 step 9 | A non-detection with a limit this much fainter than the star's median is suspect |
| `colorterm_blue_min` | -0.53 | 6.1 step 5 | DASCH's boundary between yellow and blue (reduction guide, Colorterms page) |
| `comparison_max` | 40 stars | 6.2 | Plan: 30 to 50 comparison stars |
| `comparison_min` | 10 stars | 6.2 | Fewer and the target is not detrended |
| `comparison_min_per_plate` | 5 stars | 6.2 | Fewer on a plate and that plate has no offset |
| `comparison_mag_window` | 0.5 mag | 6.2 | Plan |
| `comparison_colour_window` | 0.25 | 6.2 | Plus or minus, in the catalog colour (B-V). The catalog colour spans 0.02 to 1.5 |
| `series_change_share` | 0.5 | 6.3 | A series holding at least this share of a window and under half of it in the next breaks the curve |
| `seam_min_points` | 20 points | 6.3 | Plan 6.4: 20 good points on each side of a change |

`astrometry_max_sep_arcsec` (15) and `source_split_radius_arcsec` (5) are in `0002`.

## Findings

- **Source splitting: the overlap is measured against the smaller curve.** The first definition (plates with a detection in both, over plates with a detection in either) merged all 20 real pairs, including a neighbour with 14 detections of which 11 were on plates where the target also had one. A long curve hides a short one in that ratio. The share of the smaller curve's plates that the other also has separates the cases: a split source puts its detections where the other entry has none (overlap 0), two real stars detected together overlap heavily (0.25 to 1.0 in 18 of the 20 pairs). With it, 2 of 20 pairs merge (a neighbour with 14 and one with 3 detections, both at overlap 0). The 20 pairs are the 10 neighbours of M44 and the 10 of Tabby. T CrB has none. The unit tests carry both sides of the 0.1 boundary.
- **The neighbours stage pulled 20 lightcurves** (89,154 rows): 10 for M44 and 10 for Tabby. They are stored as `stars` and `measurements` tables under the tile labels `m44-neighbours` and `tabby-neighbours`. Neighbours with no catalog magnitude cannot be kept in a `stars` table and are left out.

## What L1 does to the benchmark (dev split: 361 constants, M44 and T CrB)

Run with `computers bench l1`. The detector is the trivial first-decade, last-decade rule, so these numbers measure L1 and say nothing about the triggers of Sprint 4. "Raw" is the Leavitt Set's own host curves. "Clean" is L1 cleaning only. "Detrended" is cleaning plus the comparison pool. `reject` and `keep` are the meteor policies.

**Scatter of the constants** (median over hosts, mag; within-block / between-block):

| | raw | reject clean | reject detrended | keep clean | keep detrended |
|---|---|---|---|---|---|
| within | 0.122 | 0.118 | 0.115 | 0.120 | 0.118 |
| between | 0.029 | 0.029 | 0.027 | 0.029 | 0.027 |
| good points per host | 1,170 | 1,063 | 1,032 | 1,130 | 1,112 |

**False alarms** on the 361 constants (count, with a 95% interval per 1,000):

| Threshold | raw | reject clean | reject detrended | keep clean | keep detrended |
|---|---|---|---|---|---|
| 0.2 mag | 18 (32-77) | 14 (23-64) | 6 (8-36) | 15 (25-67) | 8 (11-43) |
| 0.3 mag | 7 (9-39) | 6 (8-36) | 1 (0-16) | 6 (8-36) | 1 (0-16) |
| 0.5 mag | 0 (0-11) | 0 | 0 | 0 | 0 |

**Completeness at 0.5 mag:** the 50% and 90% crossings are the same in every configuration to within 0.01: linear 0.63 and 0.83 mag per century, step 0.55 to 0.56 mag (90% not reached), wave 0.88 to 0.90 mag, dips and outbursts not reached. Detrending costs no sensitivity.

**Reading it.**
- Detrending reduces the between-block scatter of the constants by 7% and the within-block scatter by 3%. Constants were chosen for low raw scatter, so a large gain was not to be expected.
- It cuts false alarms at 0.2 mag from 14 to 6 and at 0.3 mag from 6 to 1 (reject policy). The intervals overlap, so this is suggestive, not shown. At 0.5 mag there are no false alarms to remove.
- Cleaning alone moves false alarms from 18 to 14 and 7 to 6, within noise.
- **The meteor policy cannot be told apart by these numbers.** `keep` retains 8% more good points per host (1,112 against 1,032 once detrended). False alarms (8 against 6 at 0.2 mag) and every crossing agree within noise.
- 49 of 361 hosts (14%) could not be detrended (`few_comparisons`) under either policy.

## The seam rule on injected steps

Run on the dev steps (`reject` policy, 4,332 steps):

| Group | Judged `seam_only` |
|---|---|
| Steps placed inside the Menzel Gap | 1,922 of 1,922 (100%, as required) |
| All other steps | 30% (26.5% to 32.9% by amplitude, with no trend in amplitude) |

For the other steps the reasons are `too_few_points` 686, `across_break` 49 and `no_data_one_side` 4; 1,671 (69%) are visible inside a stretch. By epoch decade in M44, steps are 95% visible in the 1930s and 1940s, 72 to 86% in the 1900s to 1920s, about 78% in the 1970s and 1980s, but only 23% in the 1890s and 0 to 28% in the 1950s and 1960s. The rule is conservative where the plates are sparse (the first years, the gap edges), which is what it is for. `seam_min_points` (20) sets how conservative: lowering it would let more steps near the ends of the data count.

## The Tabby's Star check (not what the plan expected)

KIC 8462852 against its look-alikes in the Tabby tile: slope of decade medians, mag per century, a positive number is a fade.

| Look-alike set | | star | look-alikes: median | central 95% | where the star sits |
|---|---|---|---|---|---|
| 210 quiet stars within 0.5 mag | raw | +0.156 | -0.007 | -0.150 to +0.144 | outside, 98th percentile |
| | detrended | +0.179 | +0.007 | -0.153 to +0.165 | outside, 98th percentile |
| 290 non-VSX stars within 0.5 mag | raw | +0.156 | -0.051 | -0.671 to +0.144 | outside, 98th percentile |
| | detrended | +0.179 | -0.024 | -0.693 to +0.163 | outside, 98th percentile |

- **The plan's expectation is not met.** The plan said the star's trend would sit inside the spread of its comparison stars after detrending. It sits just outside the upper edge in both look-alike sets, before and after, and detrending leaves it slightly larger. The look-alikes show no common trend to remove (median close to zero), so there is nothing for the ensemble to subtract. About four of 207 look-alikes have a larger detrended slope.
- **This is not a claim about the star.** It says that this method, with 40 comparison stars of the same brightness and colour, does not explain the DASCH fade as a common plate systematic. The star is a mild outlier (top 2%), not an extreme one.
- **The system still must not call it a fade, and it would not.** 0.18 mag per century is under the fade floor of 0.3 mag per century (`fade_floor_mag_per_century`) and under the long-term amplitude floor of 0.5 mag. The protection comes from the floors, not from detrending.
- **Nothing was tuned on this tile.** The Tabby tile is the held-out test tile and is not yet frozen. This check reads it as a diagnostic of the method on one named star and sets no threshold.

## Other findings

- **The meteor policy and the T CrB eruption.** In the 150-row committed excerpt, six of the eight 1946 points are on meteor plates and the 7.73 mag peak carries three rejecting tags (soft standard bit, astrometry, meteor), so only two points reach `good_for_outburst`. The excerpt is enriched with awkward rows. In the full curve, 25 or more eruption points survive `good_for_outburst` under both policies, spread over series `ac`, `ee` and `rh`.
- **Bright and red stars cannot be detrended.** T CrB (B 11.43, colour 1.32) has a comparison pool of 2, so it gets `few_comparisons` and the raw curve. The share of stars with the 10 comparison stars needed is 82% (M44), 75% (T CrB) and 93% (Tabby). By brightness it is 0 to 23% for B 7 to 10, 0 to 75% for B 10 to 11, 63 to 94% for B 11 to 12 and 91 to 98% fainter than B 12 (the T CrB tile has no detrendable star brighter than B 11). The Tang giants are red K giants and will have the same problem. KIC 8462852 (B 12.36, colour 0.51) has the full pool of 40, and 99.7% of its good detections get an offset.
- **Invariance bound.** The plan's "adding a constant to every star on a plate changes no detrended value" holds only to about `15 sigma / N`, because the baselines are medians. Measured 6 to 10 sigma / N, worst ratio 8.2 over 400 adversarial examples.
- **Series.** `series_kind` is empty in the API, so `data/series.py` holds daschlab's table. Seven series have a kind that does not follow the scale boundaries (`ad`, `ayroe`, `bc`, `darnor`, `darsou`, `m`, `y`); the table copies daschlab's assignment.
- **Speed and memory.** Building the Tabby context takes 5.4 s and about 1.8 GB at peak (mostly the table). The dev report takes 28 s.
- **The fixture script is unsafe to rerun.** `scripts/make_golden_fixtures.py` re-selects stars by features and the store now holds whole tiles, so it would replace the four committed fixtures. I ran it by mistake once, restored them from a copy, and checked git showed no change. RY Cnc was added with `scripts/add_reference_fixture.py`, and the README now says not to rerun the first script.

## 4. Decision for you: the meteor policy default

The benchmark cannot separate the two policies (false alarms, completeness and the T CrB eruption all agree within noise). What differs is data: `keep` retains 8% more good points and about half of the 1950s detections in M44 and T CrB, which `reject` removes just before the Menzel Gap. The plan's reason for rejecting them is that low-resolution plates cluster in time and fake trends. The Tabby check is the nearest test of that, and it does not separate them either (slope +0.179 against +0.177). My suggestion is to keep `reject` as the default until the Sprint 4 triggers can tell the policies apart, and to treat the answer as open. Changing the default needs a new decision record.

## Correction after the sprint report

Requirement R3.10 (the kill reason `too_few_points` for a curve with fewer than 50 good detections) was in the approved spec and was not implemented in the first pass. I found it while drafting Sprint 4 and added it: `Curve.kill_reason`, the threshold `min_good_points` (50), and tests on both sides of 49 and 50. Under the default meteor policy it kills 19 of 444 M44 stars, 14 of 186 T CrB stars and 7 of 1,058 Tabby stars.
