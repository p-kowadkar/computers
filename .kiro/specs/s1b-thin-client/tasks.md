# Tasks: S1b Thin client, golden test and tile pilot

Status: **approved 2026-11-25.** Steps 1-13 done. Step 0 (live hook check) is still open: new hooks load only when a session starts.

- [ ] 0. **First thing in a new session: confirm the blocking hook through Kiro**
  - Ask the agent to write a file under `data/raw/` and confirm Kiro refuses it, then read `data/hooks/block_raw_edits.log`. Left open from S1a because hooks load at session start.

- [x] 1. Tiles
  - `tiles.py`, `tests/unit/test_tiles.py`: membership at the four edges and corners, box centres (RA offset grows with declination), adjacent boxes overlap, exposure positions, and a property test that every point in a tile is inside at least one of its four boxes.
  - _Requirements: R1_

- [x] 2. Parser, part 1: records, catalog, exposures
  - `dasch_parse.py`: `parse_records`, `parse_catalog`, `stars_from_catalog`, `parse_exposures`, with tests on small synthetic responses and on the raw T CrB responses saved in Sprint 0 (`data/spike/raw`, skipped when absent).
  - Cover: ragged rows, sentinel values, `scannum == -1`, negative `expnum`, `ra` 999, empty dates, tile clipping, the brightness cut at exactly 13.5, duplicate sources.
  - _Requirements: R2.1, R2.2, R4.6, R4.7_

- [x] 3. Parser, part 2: lightcurves and rejections
  - `parse_lightcurve`, `apply_standard_rejections`, `STANDARD_BAD_AFLAGS`.
  - Tests: every row of the mapping table, non-detection rule, all sentinels, `ref_number` mismatch raises, sort by time, `exposure_id` format, rejection mask on a hand-built table, and the constants against daschlab's `AFlags` (skipped without daschlab).
  - Property tests: adding a rejection never increases good points; parse then re-serialise round trips the text columns.
  - _Requirements: R4.1 to R4.5, R4.7_

- [x] 4. Golden test, offline
  - `tests/golden/test_lightcurve_parity.py` as in design.md. Run it on the S0 raw lightcurves (4 stars) first.
  - Fix every difference. If a difference cannot be fixed, stop and report (R5.5).
  - _Requirements: R5.1 to R5.3_

- [x] 5. Client
  - `dasch_client.py` with a fake transport. Tests: resume from the store makes zero calls; each retry status retries and 400 does not; `Retry-After` is honoured; backoff and jitter bounds; attempts stop at 5; format drift raises and stores nothing; error text never contains the key; 429 lowers concurrency; a failure does not stop other tasks.
  - _Requirements: R3_

- [x] 6. Silver files and pull stages
  - `silver.py`, `pull.py`. Tests with a fake transport: catalog stage dedupes the overlap, clips to the tile, applies the cut, and refuses to write on a failed box; exposures split on a simulated size failure; pilot stratification is deterministic and keeps every bin; the Parquet files validate and carry the metadata; an interrupted pull (fake transport raising partway) resumes with only the missing requests; the report has every field.
  - _Requirements: R2, R6.1, R6.2, R7, R8_

- [x] 7. Commands
  - `computers pull` and `computers plates`, with tests using the fake transport.
  - `plates.py` tests: shared and unshared plates, multi-exposure plates counted once, shares computed per tile, series grouping.
  - _Requirements: R6.3, R8_

- [x] 8. Access checks script
  - `scripts/s1_access_checks.py`. Unit-test the result handling with stubs (unreachable service recorded, not raised). Run it once for real now, and note that it must be rerun after 2 December.
  - _Requirements: R9_

- [x] 9. Email draft
  - `docs/outreach/dasch-email-draft.md`, with the measured numbers filled in after step 11. Check it contains no key.
  - _Requirements: R11_

- [x] 10. **Human step: run the pilot**
  - In your terminal: `export STARGLASS_API_KEY=...`, then `uv run computers pull --tile all --stage catalog`, `uv run computers pull --tile all --stage exposures`, `uv run computers pull --tile m44 --pilot --stage lightcurves`. Press Ctrl-C partway through the last one and run it again to show the resume.
  - Or approve a run on the public API by me.

- [x] 11. Live golden test and plate overlap
  - Run the network parity test on 20 stars. Run `computers plates`. Run the offline golden test over the pilot's lightcurves.
  - _Requirements: R5.4, R6.3_

- [x] 12. Decision record and doc updates
  - Write `docs/decisions/0003-tile-sizes-and-access.md` from the real numbers. Update `plan.md` (5.2, 5.4, 9.0 splits, 7.5 budget, changelog), W6 in the Sprint 0 findings, and `astro-domain.md`.
  - **Human step:** you send the DASCH email.
  - _Requirements: R10_

- [x] 13. Exit test and report
  - `uv run pytest`, `ruff`, secret scan, `computers schema check`, `computers thresholds check`, `computers snapshot verify`.
  - Show the four exit tests. Report to the user and wait before Task 5.
