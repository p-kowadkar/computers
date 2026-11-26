# Requirements: S0 Data spike

**Intent.** Look at real DASCH data before building anything on it. Pull four stars, plot them, fetch cutouts, time the API, and write down what matches the plan and what doesn't. This is exploration with a reproducible script, not production code.

**Source:** `plan.md` section 13 (S0), Appendix C. Spec status: **draft, awaiting review**.

## Scope

In: four targets, one script, one findings record, a few small pure functions with unit tests.
Out: the thin client, the snapshot store, any benchmark, any model. Those start in Task 3 and Task 4.

## Targets

| Name | Why | Name passed to daschlab |
|---|---|---|
| RY Cnc | The official tutorial star. Checks our setup against known-good steps. | `V* RY Cnc` |
| T CrB | Recurrent nova. The 1946 eruption must be visible. | `V* T CrB` |
| KIC 8462852 | Tabby's Star. The canonical false-fade case. | `KIC 8462852` |
| One quiet star | A boring star of similar brightness, for the false-alarm baseline. Chosen by rule (design.md, "Quiet star"). | by coordinates |

## Acceptance criteria (EARS)

**R1. Credentials**
1. THE spike script SHALL read the Starglass key only from the environment variable `STARGLASS_API_KEY`, and SHALL pass it to daschlab as `DASCHLAB_API_KEY` for its own process only.
2. WHEN `STARGLASS_API_KEY` is not set, THE script SHALL use the public API, SHALL print a notice saying so, and SHALL record "public API" in the findings.
3. THE script SHALL NOT print, log or write the key to any file.

**R2. Lightcurve pulls**
1. FOR EACH target, THE script SHALL open a daschlab session in its own directory under `data/spike/<target>/`, select the APASS refcat, and fetch the lightcurve of the catalog source nearest the target.
2. WHEN the nearest catalog source is more than 5 arcsec from the target position, THE script SHALL report the separation and SHALL print the nearest three sources, so a wrong match is visible.
3. WHEN a session directory already holds cached data, THE script SHALL reuse it. THE script SHALL accept `--refresh` to delete and refetch.

**R3. Per-star summary**
1. FOR EACH target, THE script SHALL write `summary.json` with: row count, detections, non-detections, first and last date, the series present with a count each, points rejected by `apply_standard_rejections()`, and the median detected magnitude.
2. THE script SHALL report the same numbers after the two extra rejections the RY Cnc tutorial applies: points farther than 15 arcsec from the mean position, and points with the `SUSPECTED_DEFECT` flag. This tells us what the plan's rejection list (6.1) adds beyond the standard call.

**R4. Plots**
1. FOR EACH target, THE script SHALL save a PNG of the raw lightcurve and a PNG of the cleaned lightcurve, magnitude axis inverted (brighter is up), non-detections drawn as limits.
2. THE PNGs SHALL be saved under `docs/decisions/assets/s0/`, each under 500 KB.

**R5. T CrB known-answer check**
1. THE check SHALL pass WHEN at least one point that survives the standard rejections is dated in 1946 and is at least 2.0 mag brighter than the median of detections from 1900 to 1945. The 2.0 mag margin is a **[start]** value.
2. THE script SHALL report the raw-data result and the cleaned-data result separately. WHEN the eruption is visible only in raw data, THE script SHALL say so, because that means a standard rejection removes a real event.
3. WHEN the check fails, THE script SHALL exit with a non-zero status and print the 1946 points it found.

**R6. Cutouts**
1. FOR EACH target, THE script SHALL fetch one cutout from a scanned exposure with a precise WCS solution, save the FITS file, and save a PNG rendering.
2. FOR T CrB, THE cutout SHALL come from an exposure dated in 1946 when one exists.
3. FOR RY Cnc, THE script SHALL also fetch the cutout for one point carrying `SUSPECTED_DEFECT`, if one exists, so a defect and a star can be compared side by side.
4. WHEN no scanned exposure exists for a target, THE script SHALL record the reason instead of failing.
5. THE script SHALL report where the target lands in each cutout, as a pixel offset from the centre, so we can see how often the star is off-centre.

**R7. Timing**
1. THE script SHALL time lightcurve requests for the ten catalog sources nearest T CrB, with no cached data, and record per request: seconds, response size, success or failure.
2. THE script SHALL report the median, the slowest, and the failure count, and which API base was used.
3. THE script SHALL record the largest response size seen and compare it with the 6 MB buffered-response limit that daschlab's client documents.

**R8. Raw API format**
1. THE script SHALL make one direct `querycat` call and one direct `lightcurve` call for T CrB, save both responses gzipped under `data/spike/raw/`, and record the column names and the first data row.
2. THE findings SHALL state whether the observed format matches the description in `plan.md` section 5.3: CSV strings in a JSON list, camelCase names, header in the first record.

**R9. Columns**
1. THE script SHALL list every column in a daschlab lightcurve table and compare it with the `measurements` table in plan 4.5 and with DASCH's "medium" column set.
2. THE findings SHALL list any planned column that is absent, and any column L1 will read.

**R10. Findings record**
1. THE spike SHALL produce `docs/decisions/0001-sprint0-findings.md` with sections: What matched the plan; What did not; Numbers (table per target); Timing; Columns L1 will read; Effects on Task 4; Open questions.
2. WHERE a plan statement marked **[verify]** is tested, THE findings SHALL say whether it held.

**R11. Pure logic is tested**
1. THE T CrB check, the summary calculation and the quiet-star rule SHALL be pure functions in `src/computers/data/spike.py`, with unit tests that use synthetic tables and no network.
2. Tests that need the network SHALL carry a `network` marker and SHALL NOT run in the default `pytest` run.

## Exit test (from the plan)

The 1946 T CrB eruption is visible in the plot, the columns L1 will read are listed, and request times are measured.

## Questions for the reviewer

1. **R5 margin.** Is 2.0 mag the right bar? T CrB's 1946 peak was far brighter than that in the literature, so 2.0 is deliberately loose. Tighter is fine if you prefer.
2. **R1 variable name.** I use `STARGLASS_API_KEY` as the project's name and map it to daschlab's `DASCHLAB_API_KEY`. The README will be updated to say so.
3. **Public API fallback (R1.2).** The public API works today without a key (its health endpoint answers). Do you want the spike to run on the public API now and again once you have the key, so we can measure the difference? This costs a second run.
