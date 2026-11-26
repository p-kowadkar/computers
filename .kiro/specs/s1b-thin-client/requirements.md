# Requirements: S1b Thin client, golden test and tile pilot

**Intent.** Replace daschlab for bulk pulls without changing the data, and measure the real tile sizes. Pull catalogs for the three tiles, pull the M44 pilot lightcurves, prove the thin client's tables match daschlab's, and write down what the real numbers mean for the plan.

**Source:** `plan.md` sections 5.2, 5.3, 5.4 and S1 in section 13; `docs/decisions/0001-sprint0-findings.md`. Spec status: **draft, awaiting review**.

## Scope

In: the HTTP client with retries and resume, parsers to our tables, tile geometry, the `pull` and `plates` commands, a golden test against daschlab, the M44 pilot, tile counts for all three tiles, a plate-overlap measurement, access checks for VSX, ZTF, Gaia and SIMBAD, a draft email to the DASCH list, and a decision record.
Out: L1 rejections beyond the five standard bits (Task 6), VSX cross-matching and the benchmark (Task 5), catalog clients and MCP servers (Task 12), cutout pulls (later tasks).

## Facts found while writing this spec

These shape the requirements. Sources are in `design.md`.

- **The catalog box is a true on-sky square.** Its RA extent is stretched by 1/cos(dec). `dra_asec` is already on-sky.
- **T CrB and Tabby's Star share 14.6% and 12.5% of their plates** (2,363 plates, mostly wide-field series `ai`, `fa`, `ka`), measured from one position per field. M44 shares almost none. The plan said tiles this far apart do not share plates. That is wrong for this pair, and it matters for the train and test split (Task 10).
- **`stdmag` is Johnson B** for the APASS catalog, and `num_matches` is the DASCH detection count. Both were assumptions in Sprint 0.
- **The Gaia ESA archive goes down on 25 November 12:00 CET** for DR4 preparation, and **DR4 releases on 2 December 12:00 CET** (ESA archive banner). Gaia DR3 stays reachable through the CfA VizieR mirror (`I/355/gaiadr3`).
- **ZTF saturates on bright stars.** A B=11.0 star returned no rows. A B=13.2 star returned 759 rows from 2018 to 2026 in two bands, with magnitudes 11.9 to 14.3.
- **W6 is explained.** The per-point `UNCERTAIN_CATALOG_MAG` bit equals the catalog's `v_flag` ("uncertain magnitude", ad hoc cutoffs). It is set for 97% of the sources near T CrB, so it carries almost no information there.

## Acceptance criteria (EARS)

**R1. Tiles**
1. `computers.data.tiles` SHALL define the tiles `m44` (RA 130.1, Dec +19.7), `tcrb` (239.87568, +25.92017) and `tabby` (301.56439, +44.45689), each a 2 by 2 degree on-sky square: a star belongs to a tile when `|dec - dec0| <= 1` degree and `|ra - ra0| * cos(dec0) <= 1` degree.
2. THE module SHALL give, for each tile, four catalog query boxes: centres at `dec0 +/- 0.5` degree and `ra0 +/- 0.5/cos(dec_box)` degree, where `dec_box` is that box's own centre declination, each with radius 1,850 arcsec. The 50 arcsec margin over 1,800 SHALL keep adjacent boxes overlapping.
3. THE module SHALL give five exposure query positions per tile: the centre and the four corners (`+/- 1` degree on-sky).

**R2. Catalog pull**
1. WHEN the catalog stage runs for a tile, THE client SHALL make the four `querycat` calls, remove duplicate sources by `ref_number`, drop sources outside the tile, and write the `stars` table for sources with a finite `stdmag` of 13.5 or brighter (Johnson B).
2. THE `stars` table SHALL set `apass_b` from `stdmag`, `n_points` from `num_matches`, and `tile` to the tile name, and SHALL pass `schemas.validate`.
3. THE catalog stage SHALL report the number of sources in the tile, the number with a magnitude, and the number at or below 13.5 in 0.5 mag steps, plus how many sources were seen in more than one box.
4. WHEN a `querycat` call returns HTTP 504, THE client SHALL retry it. WHEN every retry fails, THE stage SHALL fail with the box named, and SHALL NOT write a partial `stars` table.

**R3. Requests, retries and resume**
1. THE client SHALL read the API key only from `STARGLASS_API_KEY`, use the full API when it is set and the public API otherwise, and SHALL NOT print, log or store the key. Error text SHALL be passed through `redact`.
2. THE client SHALL send every request through the snapshot store: it SHALL look the request up first, SHALL make no network call when a stored response exists, and SHALL store each successful response before parsing it.
3. WHEN a response is HTTP 429, 500, 502, 503 or 504, a timeout or a connection error, THE client SHALL retry with exponential backoff and jitter, up to 5 attempts, honouring a `Retry-After` header. IT SHALL NOT retry other 4xx responses.
4. WHEN a 2xx response is not a JSON list of strings whose first record contains the columns the parser needs, THE client SHALL raise a format error naming the missing columns, and SHALL NOT store the response.
5. THE client SHALL run lightcurve requests with a concurrency of 3 by default, and between 1 and 4 when set. WHEN it receives an HTTP 429, IT SHALL lower the concurrency by one, down to 1, and SHALL say so in the report.
6. WHEN a pull is stopped and started again, THE client SHALL make only the requests that have no stored response.
7. WHEN a request fails after all retries, THE pull SHALL carry on with the others, SHALL list the failures in the report, and SHALL exit non-zero.

**R4. Parsing to our tables (parity with daschlab 1.1.0)**
1. THE `measurements` parser SHALL produce the columns in `schemas.SCHEMAS["measurements"]` from a raw lightcurve response, mapping raw names to daschlab names as listed in `design.md`.
2. A row SHALL be a **non-detection** when the raw `gsc_bin_index` and `sextractor_number` are both zero, and SHALL then have nulls in every column daschlab masks for non-detections.
3. THE parser SHALL treat the sentinel values daschlab treats as missing: 99 for `magcal_local_rms` and `drad_rms2`, 0 and 99 for `blended_mag`, and NaN for any float. Missing values SHALL be null in Arrow, not NaN.
4. THE parser SHALL set `ref_number` from the request (and SHALL raise when a non-zero raw `ref_number` differs), and set `exposure_id` as `{series}{platenum:05d}e{expnum}`, plus `s{solnum}` when the exposure has a solution. **[changed during the build, see design.md]** daschlab's mosaic-based id was not usable as a key.
4a. THE parser SHALL sort rows by `time` and break ties with a fixed key (series, plate, solution, exposure, mosaic, limiting magnitude, magnitude, image position), so the table does not depend on the order the API returned tied rows in. **[added during the build]**
5. THE `reject` column SHALL start at zero. `apply_standard_rejections` SHALL set bit 0 (value 1) on every row whose `aflags` has any of `HIGH_BACKGROUND`, `LARGE_ISO_RMS`, `LARGE_LOCAL_SMOOTH_RMS`, `CLOSE_TO_LIMITING` or `BIN_DRAD_UNKNOWN`, and SHALL leave non-detections at zero.
6. THE `exposures` parser SHALL build the `exposures` table from a `queryexps` response with these rules: `exposure_id` follows the same rule as in `measurements` (item 4); `mosnum` and `solnum` are null when `scannum` is -1; `expnum` is null when negative; `ra` 999 or `dec` 99 become null; `has_imaging` is true when `wcssource` is `imwcs`; `series_kind` is null for now.
7. WHEN the parser meets a column value it cannot read, IT SHALL raise an error naming the column and the row, and SHALL NOT substitute a default.

**R5. Golden test against daschlab**
1. THE repo SHALL contain an offline golden test that parses the same raw lightcurve responses with the thin client and with daschlab 1.1.0's own parsing and `apply_standard_rejections()`, and compares every column of every row. Values SHALL match exactly where both are present, and both SHALL be missing together.
2. THE test SHALL cover at least 20 stars and SHALL fail with the star, column and row of the first difference.
3. THE test SHALL also compare the `reject != 0` mask row by row.
4. A network test (marked `network`) SHALL pull the same 20 stars once through daschlab's own client and once through the thin client, and compare the parsed tables.
5. WHEN the golden test cannot be made to pass, THE project SHALL stay on daschlab for pulls and say so in the decision record.

**R6. Exposures and plate overlap**
1. WHEN the exposures stage runs for a tile, THE client SHALL query the five positions, remove duplicate `exposure_id` rows, and write the `exposures` table. THE columns `center_distance`, `edge_distance` and `lim_mag_apass` SHALL be null in that table, because the API reports them relative to the queried position. THE report SHALL count copies that differ only in those columns separately from copies that differ in any other column. **[added during the build]**
2. WHEN a `queryexps` response would exceed the 6 MB limit, THE client SHALL split the request by `midpoint_min` and `midpoint_max` at the middle of the date range and combine the results.
3. `computers plates` SHALL read the exposures tables for every tile that has one and print, for each pair of tiles, the number and share of plates (series plus plate number) that both tiles contain, and the series those shared plates belong to.

**R7. Silver tables**
1. THE pull SHALL write Parquet files `data/silver/<table>/tile=<name>.parquet` for `stars`, `measurements` and `exposures`, each validated against its schema, written to a temporary file and renamed into place.
2. EACH file SHALL carry the snapshot digest and the schema version in its Parquet metadata.

**R8. The pilot**
1. `computers pull --tile m44 --pilot` SHALL run all three stages for M44. WHEN the tile has more stars than `--max-stars` (default 2,000), THE pilot SHALL pick a subset stratified by 0.5 mag bins, with a fixed seed. Otherwise it SHALL use every star.
2. THE pull SHALL write a report to `data/reports/` with: stars per magnitude bin, requests made and stored, retries, failures, concurrency used, median and 95th percentile time per endpoint, bytes stored, and the snapshot digest.
3. `computers pull --tile all --stage catalog` and `--stage exposures` SHALL run those stages for all three tiles.

**R9. Access checks**
1. A script `scripts/s1_access_checks.py` SHALL record, with the time and server used, whether each of these works: VSX through VizieR (both mirrors), SIMBAD (both servers), ZTF for a star between B 12.5 and 14, Gaia DR3 through the ESA archive and through VizieR, and whether the ESA archive lists a DR4 schema.
2. THE script SHALL treat an unreachable service as a result to record, not an error, and SHALL be safe to run again after 2 December.

**R10. Decision record and docs**
1. THE sprint SHALL produce `docs/decisions/0003-tile-sizes-and-access.md` with: real star counts per tile and magnitude bin; what they mean for the L3 budget rule (7.5) and for the case-count targets in S9, S13 and S17; the plate-overlap table and what it means for the train and test split; request times, failure rates and the concurrency used; and the access-check results.
2. `plan.md`, `docs/decisions/0001-sprint0-findings.md` (W6) and the `astro-domain` steering file SHALL be updated where this sprint changes them, with a changelog entry.

**R11. Email to the DASCH list**
1. THE repo SHALL contain `docs/outreach/dasch-email-draft.md`, a draft covering Appendix D of the plan, with the measured request volume, the catalog radius limit that differs from the documentation, the 6 MB response limit, and the specific questions in `design.md`. The user sends it. THE draft SHALL NOT contain a key or any private data.

**R12. Layer rules and safety**
1. THE new modules SHALL pass the existing layer-rule test, and no module SHALL import `daschlab` outside `scripts/` and `tests/`.
2. NO test in the default run SHALL touch the network. Network tests carry the `network` marker.

## Exit tests (from the plan, plus this sprint's)

1. The golden test passes on 20 stars, offline.
2. `computers pull --tile m44 --pilot` is resumable: stop it partway, start it again, and only missing requests are made.
3. A report shows real star counts per tile.
4. `computers plates` shows the plate-overlap table.

## Human steps

- **Run the pilot** in your own terminal with `STARGLASS_API_KEY` set (about 15 to 25 minutes), or approve a public-API run by me.
- **Send the DASCH email** (I draft it).

## Questions for the reviewer

1. **Committed test fixtures.** The offline golden test needs real lightcurve responses. Committing small excerpts (about 150 rows each for 4 stars, a few tens of kilobytes) makes the test run everywhere, but publishes a tiny piece of DASCH data before the Harvard terms are checked. The alternative is to run the golden test only against your local snapshot and keep CI tests on synthetic rows.
2. **Pilot run.** You run it with your key, or I run it on the public API.
3. **Scope of pulls.** Catalog and exposures for all three tiles (about 25 minutes of API time) plus M44 lightcurves, or M44 only.
