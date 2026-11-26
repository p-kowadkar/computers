---
inclusion: fileMatch
fileMatchPattern: ["src/computers/data/**", "src/computers/l1/**", "src/computers/l2/**", "src/computers/bench/**", "scripts/**"]
---

# Astronomy and DASCH facts that bind the code

Sources: `plan.md` sections 3 and 6, and `docs/decisions/0001-sprint0-findings.md` (the Sprint 0 spike, cited below as W1 to W10).

## Magnitudes

- **Magnitudes run backwards.** A smaller number is brighter. "Brighter by 2 mag" means `baseline - mag >= 2`. 5 magnitudes is a factor of 100 in light and 0.1 mag is about 10%.
- Inject signals in **flux**, then convert back to magnitude.
- **APASS-referenced curves only.** The ATLAS reference has a filter mismatch that makes false long-term trends. This is a hard rule.
- A **non-detection** has a null `magcal_magdep` and keeps `limiting_mag_local`. The limit is a lower bound on the star's magnitude (the star is fainter than that). It is not a measurement.
- Time is HJD in days, taken from handwritten logbooks and good to minutes at best. Do not attempt short-period analysis.

## What the plates can and cannot resolve

| Event | Detectable? |
|---|---|
| Outburst or nova (several mag, days to months) | Yes, if 2 or more plates catch it |
| Long-term variable (about 1 mag over 10 to 100 yr) | Yes |
| Deep dip or step (0.5 mag or more, years) | Yes, with care |
| Slow fade under 0.3 mag per century | **No.** Inside the systematics |
| Short flare on a single plate | Rarely. The most defect-prone class |
| Vanished star | Only with image proof |

Amplitude floors (starting values, in `thresholds.py`): 0.5 mag for long-term events, 1.0 mag for single-epoch events. A single measurement is good to about 0.15 mag RMS. Constant stars can drift up to about 0.2 mag per century.

## Known DASCH issues and the L1 check for each

| Issue | L1 handling |
|---|---|
| Source splitting: one star's points spread over several catalog entries | Neighbour check and merge within about 5 arcsec |
| Undetected plate defects (dust, scratches, annotations) | Shape checks, then the cutout classifier, then Maury |
| Undetected blends on low-resolution plates, clustered in the 1950s to 70s | Drop or model "meteor" series separately; use `blended_mag` |
| Missing lightcurve points, which fake a deep upper limit | "Artificially deep limit" check. A vanished claim needs the cutout |
| Unspecified emulsions: red or yellow plates make red stars look brighter | Emulsion check with three states: blue, non-blue, **unknown**. The API's plate class is empty, so it reads `median_colorterm_apass`: below -1.13 red, above -0.53 blue, otherwise yellow, exactly 0.0 suspect |
| Incorrect astrometry | Reject points more than 15 arcsec from the mean position |
| Timing from logbooks | No short-period analysis |
| Seams: calibration and plate mix change across the Menzel Gap (about 1953 to 1969) | A change visible only across the gap is `seam_only` and cannot trigger |

## Sprint 0 findings that change the code

- **W1, catalog radius.** `querycat` rejects 3,600 arcsec and times out from 3,000. A 2 by 2 degree tile is **four calls at radius 1,800 arcsec** (about 17 to 19 s each), with a retry on HTTP 504.
- **W2, names.** The raw API names differ from daschlab's (`date_jd` is `time`, `plate_number` is `platenum`, `fwhm_pix` is `fwhm_image`). `querycat` and `lightcurve` are snake_case, `queryexps` is camelCase. Our tables use daschlab's names.
- **W3.** There is no `plate_quality_flag`. The per-point `BAD_PLATE_QUALITY` AFLAGS bit carries it.
- **W4, standard rejections eat outbursts.** `apply_standard_rejections()` rejects five AFLAGS bits (`HIGH_BACKGROUND`, `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS`, `CLOSE_TO_LIMITING`, `BIN_DRAD_UNKNOWN`). It removes 30 to 44% of detections, and 25 of 32 T CrB eruption points. Points rejected only by `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS` or `BIN_DRAD_UNKNOWN` must stay visible to the outburst trigger, and a case file reports how many points were rejected.
- **Saturation.** T CrB's 1946 peak reads about mag 7.7, far fainter than its real peak near mag 2. An outburst amplitude is a **lower limit**.
- **W5.** Tabby's Star drifts 0.18 mag fainter over the century with no detrending. This is the test for ensemble detrending, not a result to report.
- **W6, explained.** The per-point `UNCERTAIN_CATALOG_MAG` bit equals the catalog's `v_flag` ("uncertain magnitude", ad hoc cutoffs). It is set for 97% of the sources near T CrB. It is not a variability flag and carries almost no information. Do not use it as a known-variable cue.
- **Emulsion comes from the colourterm, not plate class.** `queryexps` leaves `class` empty for every exposure. DASCH recommends `median_colorterm_apass` (in `exposures`): below -1.13 is red, above -0.53 is blue, otherwise yellow, and exactly 0.0 is suspect (the pipeline's default). Plate class and the series `dny`, `dnr`, `dsy`, `dsr` are secondary hints. For T CrB, 95.4% of detections are on blue plates, 1.5% red, 1.3% yellow, 1.2% suspect and 0.7% unknown.
- **Join measurements to exposures on `exposure_id`**, which is `{series}{platenum:05d}e{expnum}` plus `s{solnum}`. Never on the mosaic number: 19% of T CrB's detections name a mosaic that `queryexps` no longer lists (re-scanned plates).
- **Row order is not stable.** The API orders rows that share a timestamp differently on each call. Never rely on it. The parser sorts by a fixed key.
- `TOO_BRIGHT` marks individual measurements. Check its meaning before using it.
- **Off-centre cutouts.** The star can sit 20 or more pixels from the cutout centre (1.44 arcsec per pixel). Never assume it is centred.
- **Response size.** The API's buffered responses cap at 6 MB. A lightcurve of 2,900 rows is 1.1 MB. `queryexps` for 16,000 exposures is 2.9 MB.
- **Mirrors.** The Strasbourg SIMBAD and VizieR servers were down on 2026-11-23. Catalog clients take a list of servers and fall back to `simbad.harvard.edu` and `vizier.cfa.harvard.edu`.

## Reference targets

T CrB (1946 eruption must be flagged real), KIC 8462852 (must **not** be called a century-long fade), RY Cnc (a known eclipsing binary inside the M44 tile), and the three K giants from Tang et al. 2010. Coordinates and the reasons are in `plan.md` 3.4.
