# Tasks: S2 Leavitt Set v0

Status: **approved 2026-11-26. Built.** One human step is open: freeze the test split (task 9). Step 0 (live hook check) is also still open.

- [ ] 0. **First thing in a new session: confirm the blocking hook through Kiro** (carried over from S1a and S1b)

- [x] 1. VSX access check and client
  - Live check of the CfA, then other VizieR servers. Choose `requests` or `astroquery`. `data/vsx.py` with a fake server, tests for the empty-answer failure, fallback order, snapshot reuse and parsing.
  - Done: plain `requests` on the CfA mirror's `asu-tsv` (no new dependency; TAP is 404 there). Tables `vsx` and `vsx_match` added (version 1). `computers vsx pull --tile all` ran live: 363 / 345 / 2,630 entries, and 99 / 28 / 159 patrol stars within 3 arcsec.
  - _R1_

- [x] 2. **Human step: pull T CrB and Tabby lightcurves**
  - Done by the user 2026-11-26 on the public API: 186 and 1,058 stars, no failures, 493,324 and 5,741,179 rows. `snapshot verify` ok (1,719 manifest rows).

- [x] 3. Injection engine
  - `bench/inject.py`. Property tests: zero amplitude returns the input, brighter signals never create non-detections, a point below the limit is a non-detection, the same seed gives the same curve, flux round trip within 1e-9 mag, no mutation of the input.
  - Golden: one hand-computed step and one linear injection.
  - Done: `bench/inject.py`. Points the signal leaves unchanged are bit-identical. A point is lost only when the signal made it fainter and it now lies below its limit. Added thresholds for the gap and the fade floor (record 0005).
  - _R5_

- [x] 4. Statistics and constants
  - `bench/constants.py`. Tests on synthetic curves: a flat curve qualifies, a step and a sinusoid do not, too few points gives a recorded reason, a known variable is excluded.
  - Done: `bench/constants.py`. Between-block scatter is a 10th-to-90th percentile range, not a MAD, because a MAD cannot see a step that splits the century in half (found by a test). Real tiles: 247 / 114 / 534 constants (M44 / T CrB / Tabby). Thresholds added: `constant_min_good_points` 200, `constant_scatter_factor` 1.5 (record 0005).
  - _R3_

- [x] 5. Metrics and trivial detector
  - `bench/metrics.py`, `bench/trivial.py`. Tests: Wilson intervals against known values, 50% and 90% interpolation on a made-up curve, "not reached", the under-10 rule, and the `expected_missed` suspect flag with a detector that is deliberately too good.
  - Done: `bench/metrics.py`, `bench/trivial.py`, `bench/harness.py`. A signal counts as recovered only when the detector flags the injected curve and did not already flag the host. Crossings are interpolated in log amplitude on a monotone envelope, with intervals from the Wilson bounds.
  - _R6, R7.1_

- [x] 6. Set builder, split and manifest
  - `bench/set.py`. Tests: split by tile, shared-plate marking, test split hash stable across runs and failing after a change, manifest lists every input hash.
  - Done: `bench/leavitt.py` (named so, not `set.py`, to keep the builtin free) and `bench/variables.py`. The manifest is reproducible byte for byte. `freeze` writes the lock; `check` and `build` fail when the split drifts.
  - _R2, R4, R8.1_

- [x] 7. Commands
  - `computers vsx pull`, `computers bench build|check|--smoke`. Smoke run under 2 minutes on the real M44 data.
  - Done: `computers bench build|check|freeze|run|--smoke` and `computers vsx pull`. Smoke takes 0.4 s on the real data.
  - _R7.2, R7.3_

- [x] 8. Layer test and thresholds
  - Extend the layer test to `bench`. Add the scatter factor and match radii to `thresholds.py` with `0005`.
  - Done: layer test now forbids `bench` from importing crew, cli, mcp or ml, and `data` from importing anything above it (each with a negative case). The seven thresholds are in `0005`.
  - _R8.2, R8.3_

- [x] 9. Build the real v0 and review
  - Run `bench build` on the three tiles. Show the counts, the constant list reasons, the completeness table and the plot of completeness for the trivial detector. **You approve the frozen test split.**
  - Built on the real data. Completeness plot: `docs/decisions/assets/s2/completeness_trivial_dev.png`. **Open for you:** approve and run `uv run computers bench freeze --decision docs/decisions/0005-leavitt-set-v0.md`, then commit `docs/decisions/leavitt-test-split.lock.json`.
  - _R4.2, R7.3_

- [x] 10. Decision record and docs
  - `docs/decisions/0005-leavitt-set-v0.md`, a data card in `docs/data-cards/leavitt-v0.md`, `plan.md` changelog.
  - _R9_

- [x] 11. Exit test and report
  - `uv run pytest`, `ruff`, secret scan, `computers schema check`, `computers thresholds check`, `computers snapshot verify`, `pre-commit run --all-files`. Report and wait before Task 6.
