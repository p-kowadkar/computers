# 0003. Tile sizes, request rates, plate overlap and outside services

Date: 2026-11-25. Spec: `.kiro/specs/s1b-thin-client/`. Follows `0001-sprint0-findings.md`.

**Run.** The catalog and exposures stages ran for all three tiles and the lightcurve pilot ran for M44, on the full API with your key (reports in `data/reports/`, not committed). Some numbers come from my own earlier runs on the public API, and they are marked.

**Status of the proposals below.** Sections 2 and 3 end with proposals. They change plan thresholds and the split rule, which are yours to decide. Nothing in the code depends on them yet.

## 1. The three tiles, measured

| | M44 | T CrB | Tabby's Star |
|---|---|---|---|
| Catalog sources in the 2 by 2 degree tile | 7,466 | 6,275 | 47,495 |
| With a magnitude | 6,969 | 5,902 | 40,997 |
| **Patrol stars (B 13.5 or brighter)** | **444** | **186** | **1,058** |
| Seen in more than one query box (merged) | 245 | 184 | 1,267 |
| Exposures touching the tile centre and corners | 15,146 | 17,849 | 21,384 |
| Plates (a plate with several exposures counts once) | 14,766 | 17,646 | 20,947 |

**1,688 patrol stars in all.** The Sprint 0 estimate from one small box per target was 735, 180 and 1,040. M44 was about 65% too high because RY Cnc's box sits on the cluster centre. T CrB and Tabby were close. Patrol stars by 0.5 mag bin are in the reports. They rise steeply toward the faint end: 111 of M44's 444 stars are in the 13.0 to 13.5 bin.

**M44 pilot lightcurves.** 444 stars, 1,684,513 rows, 39 plate series, 1886 to 1989.

| | |
|---|---|
| Detections / non-detections | 932,395 (55%) / 752,118 |
| Detections removed by the standard rejections | 307,332 (33%) |
| Detections per star: median, 5th to 95th percentile | 1,850 (343 to 4,301) |
| Good detections per star (after standard rejections) | 1,281 (207 to 2,839) |
| Stars with fewer than 50 good points (`too_few_points`) | 17 of 444 (3.8%) |

## 2. What the real sizes mean for the L3 budget and the case counts

**The L3 budget rule (plan 7.5) does not work at this scale.** It says at most 1 L3 call per 1,000 stars. With 444, 186 and 1,058 stars that allows 0.44, 0.19 and 1.06 calls, about 1.7 for the whole patrol, which is the same as no L3 at all.

*Proposal:* replace the ratio with the absolute cap the rule already contains (at most 20 candidates per tile per patrol, so at most 60 in all, which is 3.6% of stars), add a dollar cap per patrol in the gateway, and report "share of stars reaching L3" as a number instead of enforcing it. The ratio was written for a whole-sky patrol.

**Case-count targets (plan 13).** The patrol itself cannot supply them. The benchmark has to.

| Target | Patrol alone | Where it comes from instead | Verdict |
|---|---|---|---|
| S9: 50 candidates judged end to end | At most 60 by the cap, and probably far fewer real triggers | Known variables and injected events from the benchmark | Feasible with the benchmark |
| S13: 3,000 to 5,000 packets for Track B | No | Injected events on constant host stars. About 1,400 hosts remain after removing known variables, and 500 are needed | Feasible, but packets made from one host star are not independent, so splits must be by host tile |
| S17: 200 human-confirmed cases for text embeddings | No | VSX known variables (28 of 186 in T CrB's tile; the other two tiles are pending) plus artifacts you label in the review UI | Uncertain. Decide at S16, and be ready for it to slide or shrink |

**Pulling everything.** The 1,244 lightcurves still missing for T CrB and Tabby would take about 5 minutes at the measured full-API rate (section 4), for about 6.4 million rows in total.

## 3. Plate overlap and the train and test split

The plan assumed tiles this far apart share no plates. They do, in one pair.

| Pair | Shared plates | Share of each tile's plates |
|---|---|---|
| T CrB and Tabby | **3,381** | 19.2% and 16.1% |
| M44 and T CrB | 2 | 0.0% |
| M44 and Tabby | 1 | 0.0% |
| In all three | 0 | |

The shared plates are wide-field patrol series: `ai` 2,563, `fa` 619, `ka` 132, `kb` 36, `ke` 17, `bi` 4. These counts are a lower bound, because they cover the exposures that touch the tile centre and its four corners.

**But few measurements sit on them.** For the one star I have in each tile: 5.5% of the T CrB star's detections (144 of 2,605) are on plates the Tabby tile also contains, and 1.2% of the Tabby star's (33 of 2,717) are on plates the T CrB tile contains. In the T CrB star's data, 117 of 565 `ai` and 27 of 197 `fa` detections are on shared plates, and no `ac`, `dnb` or `rh` detection is.

*Proposal (for Sprint 6, plan 9.0):* keep the tile-level split (train M44 and T CrB, hold out Tabby), and add a **plate-disjoint rule**: when a tile is held out, drop from it every measurement on a plate that appears in a training tile. By the figures above this costs about 1 to 6% of the held-out measurements. Check the real figure when the other lightcurves are pulled.

## 4. Request rates

| Call | Full API (your run) | Notes |
|---|---|---|
| `querycat`, 1,850 arcsec | median 17.2 to 17.5 s, 95th percentile 19.8 s | 12 calls, 0 failures |
| `queryexps` | median 16.1 to 19.7 s, 95th percentile 23.1 s, max 23.3 s | 15 calls, 0 failures. The gateway gives up at about 30 s |
| `lightcurve` | median 0.54 s, 95th percentile 0.71 s, max 2.8 s | 415 calls, 0 failures |

- **Full API:** 415 lightcurve requests in 91 s at 3 in flight, which is **4.5 requests per second**, with no HTTP 429 and no retries.
- **Public API:** 444 lightcurve requests took 371 s (**1.2 per second**) and drew **8 HTTP 429 responses**. The client lowered its concurrency from 3 to 1 and finished without failures. Per-request time was the same (median 0.53 s), so the limit is on rate, not speed.
- The key therefore matters: roughly 4 times the throughput, and no throttling at the rate tested. The upper limit of the full API is not known. The email asks.
- No request failed in any run, and no retry was needed on the full API.

## 5. Interrupt and resume, shown on the real API

A pull of 66 M44 stars was stopped with a real SIGINT (exit status 130). `computers snapshot verify` then reported the store clean, no `measurements` table had been written, and the same command run again made exactly 62 requests, which is 66 minus the 4 already stored. An earlier attempt did not interrupt at all, because background jobs in a non-interactive shell start with SIGINT ignored. That is a property of the shell, not of the program.

## 6. Outside services, as checked on 2026-11-25

| Service | State | Notes |
|---|---|---|
| DASCH API | works | health, class table and plate class all answered |
| VSX through VizieR, Strasbourg | **down** | HTTP 404 for the whole `viz-bin/votable` path, on 23 and 25 November |
| VSX through VizieR, CfA mirror | **works, but flaky** | T CrB and RY Cnc answered in 0.8 s. KIC 8462852 and the M44 and Tabby cone searches returned empty, because VizieR sends an error document with HTTP 200 and astroquery turns that into an empty result. T CrB's tile worked (345 VSX entries) |
| VSX through AAVSO's own API | **blocked** | Behind a Cloudflare challenge, so a script cannot use it |
| SIMBAD, Strasbourg | **down** | 404 for the whole application |
| SIMBAD, Harvard mirror | works | T CrB resolved as `Sy*` |
| ZTF (IRSA HTTP API) | works | 759 rows in `zg` and `zi`, 2018 to 2026, for a B=13.2 star. No rows for a B=11.0 star (saturated) |
| Gaia DR3, ESA archive | works for now | Banner: **the archive goes down 25 November 12:00 CET** for DR4 preparation |
| Gaia DR3 through VizieR, CfA | works | The route to use during the ESA downtime |
| Gaia DR4 | **release confirmed for 2 December 12:00 CET** (ESA archive banner) | The schema list has no DR4 yet. Rerun `scripts/s1_access_checks.py` after the release |

**A bug in my access script, fixed.** It first reported VizieR as working when it was down, for two reasons: astroquery cached the earlier answers (the cache is controlled by a `cache=False` argument on each query, not by an attribute), and an empty answer for an object that must exist was counted as a pass. Both are fixed, and tests cover them.

**The known-variable cross-match is incomplete.** Only T CrB's tile completed: 28 of its 186 patrol stars match VSX within 3 arcsec (types such as `ROT`, `ORG`, `CST`, `VAR`, `DSCT`). Several of those types are not real variables (for example `CST`, constant), so the benchmark must filter VSX types. M44 and Tabby are pending until VizieR is stable.

## 7. Findings added by this sprint

Numbered after W1 to W10 in `0001-sprint0-findings.md`.

- **W11. Plate class is empty in `queryexps` for every exposure** (about 49,000 across the three fields). The batch `GET /plates?ids=...` endpoint gives a class for 279 of 300 sampled plates (93%), 100 plates per call in about 2.5 s, and the single-plate endpoint worked for 12 of 12. Values are messy (`L` 251, `Mi`, `D`, `Plate 5`, `w`, `PAN`). The `dnb` series has none.
- **W12. Emulsion should come from the colourterm.** DASCH's Colorterms page recommends `median_colorterm_apass` over plate class: below -1.13 red, above -0.53 blue, otherwise yellow, and exactly 0.0 suspect. For T CrB's detections that gives 95.4% blue, 1.5% red, 1.3% yellow, 1.2% suspect and 0.7% unknown. The red and yellow ones are in the series DASCH names (`dnr`, `dny`). Plate class stays a secondary hint. Plan 6.1 step 5 is updated.
- **W13. Re-scanned plates break the mosaic-based id.** 506 of T CrB's 2,605 detections (19%) name a mosaic that `queryexps` does not list for that plate. daschlab's own `exp_local_id` would be -1 for those. Our `exposure_id` (`{series}{platenum:05d}e{expnum}` plus `s{solnum}`) joins all of them.
- **W14. The API's row order is not stable.** Rows with equal timestamps return in a different order on every call (67 of 4,740 positions differed between two fetches). A re-fetch can therefore give different bytes and a different `digest()` for the same data.
- **W15. Three `queryexps` columns depend on the queried position:** `center_distance`, `edge_distance` and `lim_mag_apass`. Copies of one exposure from the five positions differed only in them (17,254 of 17,849 near T CrB), so they are null in tile tables. Real conflicts between copies: 0.
- **W16. W6 is explained.** `UNCERTAIN_CATALOG_MAG` equals the catalog `v_flag` for all four Sprint 0 stars, and `v_flag` is 1 for 97% of the sources near T CrB.
- **W17. `expdate` has several forms:** with and without a trailing Z, minute-only, and values with 60 seconds (`05:07:60.0`).
- **W18. The public API throttles** at about 1.2 requests per second (section 4).

## 8. Still open

1. **Your decisions** on the two proposals: the L3 budget rule (section 2) and the plate-disjoint split rule (section 3).
2. **Known variables for M44 and Tabby** need VizieR to be reliable. Rerun the cross-match, and the access checks, when it is.
3. **Gaia DR4:** rerun the access checks after 2 December.
4. **Your key's rate limit** is unknown. The email to the DASCH list asks.
5. **The blocking hook** (`block-raw-edits`) still has not been confirmed through Kiro itself. New hooks load only at session start.
6. **The remaining lightcurves** (T CrB and Tabby, 1,244 stars) are not pulled. They are not needed until the benchmark, and take about 5 minutes with your key.
