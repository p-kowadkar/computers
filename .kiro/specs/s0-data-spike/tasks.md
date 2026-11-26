# Tasks: S0 Data spike

Status: **requirements approved 2026-11-23.** All tasks done 2026-11-24. Findings: docs/decisions/0001-sprint0-findings.md.

- [x] 1. Add the `spike` dependency group and install it
  - Add `daschlab`, `astropy`, `matplotlib`, `numpy`, `requests`, `astroquery` under `[dependency-groups] spike`.
  - Run `uv sync --group spike`. Record the daschlab version.
  - If install fails, stop and report. (R10)

- [x] 2. Write the pure functions and their tests
  - `src/computers/data/spike.py` with the six functions in design.md.
  - `tests/unit/test_spike.py`, synthetic data, no network. Cover: brighter-means-smaller-magnitude direction, 1946 boundary dates, a curve with no 1946 points, non-detections never counted as detections, rejected points excluded, tie-break determinism.
  - Register the `network` marker in `pyproject.toml`, excluded by default. (R11)
  - _Requirements: R3, R5, R6.5, R7, R11_

- [x] 3. Write the driver script, target by target
  - `scripts/s0_spike.py` with `--refresh` and `--targets`.
  - Credentials handling first, with a test that the key never appears in output. (R1)
  - RY Cnc first, to confirm the setup against the tutorial. Then T CrB, KIC 8462852, then the quiet star. (R2, R3)
  - _Requirements: R1, R2, R3_

- [x] 4. Add rejections, plots and the T CrB check
  - Standard then extra rejections, reported separately. (R3.2)
  - Raw and cleaned PNGs. (R4)
  - T CrB check with non-zero exit on failure. (R5)

- [x] 5. Add cutouts
  - One per target, the 1946 one for T CrB, the defect one for RY Cnc, offsets reported. (R6)

- [x] 6. Add timing, raw capture and column comparison
  - Ten uncached lightcurve requests, one raw `querycat` and `lightcurve`. (R7, R8)
  - Column comparison against plan 4.5 and DASCH's "medium" set. (R9)

- [x] 7. **Human step: provide the Starglass key**
  - Create a free Starglass account, copy the key from the profile menu, and `export STARGLASS_API_KEY=...` in the shell that runs the script. Do not paste it into chat or any file.
  - If skipped, the spike runs on the public API (R1.2).

- [x] 8. Run the spike and review the plots
  - Run all four targets. Open the T CrB plot and confirm the 1946 eruption by eye.
  - Look at the cutouts: a star, and the RY Cnc defect.

- [x] 9. Write `docs/decisions/0001-sprint0-findings.md`
  - Fill every section from R10 using the numbers the script produced.
  - Mark each tested **[verify]** item as held or not held.
  - Update `plan.md` where findings contradict it, and add a changelog line.
  - Update the README secrets table to say `STARGLASS_API_KEY` maps to `DASCHLAB_API_KEY`.

- [x] 10. Exit test and report
  - `uv run pytest`, `ruff`, secret scan.
  - Confirm: eruption visible, L1 columns listed, request times measured.
  - Report to the user and wait before Task 3.
