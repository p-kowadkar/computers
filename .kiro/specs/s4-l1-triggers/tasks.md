# Tasks: S4 L1 triggers, kill reasons and the first real completeness curves

Status: **approved 2026-11-26; defaults accepted. Tasks 1 to 8 done.** Task 9 is yours: the choices are in `docs/decisions/0007-l1-trigger-thresholds.md`, section 5. Tasks 10 to 13 follow your choice.

- [ ] 0. **First thing in a new session: confirm the blocking hook through Kiro** (carried over from S1a)

- [x] 1. Trigger type, baseline and noise
  - `l1/triggers.py`: `Trigger`, baseline and noise from a curve and its magnitudes. Tests: baseline on both curves (detrended and raw), noise fallback, magnitudes-run-backwards cases, no input changed.
  - _R1_

- [x] 2. Outburst and dip
  - Both functions with their evidence. Tests on both sides of every boundary (1.0 and 0.99 mag, 5 sigma, 60 and 61 days, 2 and 1 points, 2.0 mag single point, 3 and 2 consecutive points, 20 and 21 years, a non-detection between dip points), hand-built curves, and a property test that a curve with no point beyond the amplitude never fires.
  - _R2, R3_

- [x] 3. Step
  - The coarse-to-fine scan with stretch-aware splits and seam candidates. Tests: a clean step is found at its epoch, a step in the gap and across a break is found and gets `seam_only` through the seam rule, 19 against 20 points on a side, shift 0.49 against 0.5, coarse and fine agree with a full scan, speed under 10 ms. Prototype the false-alarm rate on the dev constants and report it.
  - _R4_

- [x] 4. Long-term variation and disappearance
  - Both functions. Tests: a linear fade and a wave fire, a flat curve does not, 3 against 4 decades, span 0.49 against 0.5, a decade with 19 points is dropped, the disappearance synthetic case (fires at 10 plates from 2 series, not at 9 or from 1 series), and the word "vanished" appears nowhere in the output. Prototype the false-alarm rates and count disappearance triggers on the 1,688 stars.
  - _R5, R6_

- [x] 5. Kill reasons and `analyse_star`
  - `l1/kill.py`, `l1/analyse.py`. Tests per reason on both sides: 1 against 2 series, all against not all evidence `NON_BLUE`, unknown emulsion not counted as non-blue, `known_variable` kept, `too_few_points` stops before triggers, a killed trigger is never dropped.
  - _R7_

- [x] 6. Candidates, telemetry and `computers l1 run`
  - `l1/candidates.py` and the command. Tests: schema validation of the `candidates` and `telemetry` tables, stable `candidate_id`, one telemetry row per star, the triggers log holds killed ones, `tabby` refused before `0007` exists and the split is frozen. Run it on M44 and T CrB.
  - _R8_

- [x] 7. Benchmark recovery
  - `bench/recovery.py` and the trigger detector adapter. Tests: spans of each family, the tolerance on both sides, "not also on the un-injected host", right-type against any-type, the confusion matrix, an expected-missed recovery flagged.
  - _R9_

- [x] 8. The reports
  - `bench/trigger_bench.py`, `computers bench triggers`. False alarms by kill reason, completeness both ways, the sweeps, the ablations, the known variables and reference stars. Run on the dev split and show every table.
  - _R10.1, R10.2, R11, R12, R13_

- [ ] 9. **Human step: choose the thresholds**
  - I propose a value for each from the sweeps, with its cost. You decide. I write `docs/decisions/0007-l1-trigger-thresholds.md`, register the thresholds and lock them.
  - _R10.3_

- [x] 10. Goldens and regressions
  - T CrB fires a surviving outburst (committed excerpt and full curve). KIC 8462852 produces no surviving long-term or step trigger at the chosen thresholds. The synthetic disappearance case. Frozen counts of the triggers on the five fixtures.
  - _R11.2, R11.3_

- [x] 11. Patrol the tiles
  - `computers l1 run` on M44 and T CrB, then (after you freeze the split) on Tabby, once. Candidate counts, the kill reasons, and the disappearance count for review. The test-split completeness and false-alarm report is run once, and no threshold changes after it.
  - _R8.5, R10.4_

- [x] 12. Decision record, docs, plan
  - Fill `0007` with the numbers, update `plan.md` (6.4, 6.5, changelog, S4 row), `astro-domain.md` and `structure.md`.

- [x] 13. Exit test and report
  - `uv run pytest`, `ruff`, secret scan, schema, thresholds and snapshot checks, `bench check`, `pre-commit run --all-files`. Show: T CrB fires as an outburst; the first real completeness curves per family; false alarms per kill reason; the ablations; and what the chosen thresholds cost. Report and wait before Sprint 5.
