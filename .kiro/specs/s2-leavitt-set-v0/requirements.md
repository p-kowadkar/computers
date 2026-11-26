# Requirements: S2 Leavitt Set v0

**Intent.** Build the benchmark before the pipeline it scores: a versioned set of known variables, constant stars and injected signals, a metrics harness, and a trivial detector that proves the harness can draw a completeness curve.

**Source:** `plan.md` sections 10.1 to 10.6 and S2 in section 13; `docs/decisions/0003` and `0004`. Spec status: **draft, awaiting review**.

## Scope

In:
- VSX cross-match of the patrol stars.
- Constant-star selection.
- Dev and test split.
- Injection engine for the five families.
- Metrics: completeness curves, 50% and 90% points, false alarms per 1,000 constants.
- A trivial threshold detector.
- `computers bench --smoke`.
- A versioned set manifest.

Out:
- Real L1 cleaning, detrending and triggers (Tasks 6 to 8).
- Gold labels and the review UI (Task 11).
- Gaia variability cuts. DR4 is not out until 2 Dec, so v0 uses DASCH scatter and VSX only. Gaia enters in v1.
- Layer-3 scoring, economics and the ablation matrix (built as the layers appear).

## Facts that shape the requirements

- Lightcurves exist for M44 only (444 stars). T CrB (186) and Tabby (1,058) need 1,244 more requests, about 5 minutes on the full API. That pull is a human step.
- VSX through the CfA VizieR mirror is flaky: M44 and Tabby cone searches returned empty on 2026-11-25, T CrB worked (345 entries, 28 of 186 stars matched). An empty answer for a field that has variables is a failure, not a result.
- The tiles are small, so 500 constants is not available. M44 has 444 stars before any cut. The plan's size (500 constants, 15,000 curves) must shrink or reuse hosts.
- Injection hosts are real curves, so a host must not be a known variable and must have enough good points.
- Test split is frozen once and belongs to you.

## Acceptance criteria (EARS)

**R1. VSX cross-match**
1. THE cross-match SHALL query VSX by position for each tile, through a server list that falls back from the CfA mirror to others, and SHALL store each raw response with the snapshot store.
2. WHEN a server returns zero rows for a tile and a reference variable known to be there (RY Cnc for M44, T CrB for its tile) is absent, THE cross-match SHALL treat the answer as a failure and try the next server.
3. WHEN every server fails, THE command SHALL fail naming the tile, and SHALL NOT write a partial table.
4. THE result SHALL record, per patrol star, the VSX id, type, period and amplitude when matched within 3 arcsec **[start]**, and a null otherwise.

**R2. Known variables**
1. THE set SHALL include as known variables the matched stars whose VSX type is on a listed set of long-timescale or large-amplitude types (the list lives in `thresholds.py` or a documented constant, not in code).
2. THE set SHALL list the reference targets (T CrB, KIC 8462852, RY Cnc, the three Tang K giants) as their own part, marked golden, whether or not they are patrol stars.

**R3. Constants**
1. A patrol star SHALL qualify as a constant when it has no VSX match within 30 arcsec, has at least 200 good points after the standard rejections **[start]**, and its robust scatter of magnitude against time is at or below a limit chosen from the tile's own scatter-versus-magnitude trend.
2. THE selection SHALL write the reason each star was excluded.
3. WHEN fewer constants qualify than the target size, THE report SHALL say so and the set SHALL use what exists. It SHALL NOT relax a threshold silently.

**R4. Split**
1. THE set SHALL split stars into dev and test by tile, following `0004`: dev is M44 and T CrB, test is Tabby.
2. THE test split SHALL be written once with a content hash, and any later change SHALL fail `computers bench check` until you record a decision.
3. Test-tile measurements on plates that also appear in a dev tile SHALL be marked and excluded from test scoring.

**R5. Injection**
1. THE engine SHALL plant a signal into a host curve in flux, convert back to magnitude, and turn any point pushed below its plate's `limiting_mag_local` into a non-detection.
2. THE engine SHALL add no noise and SHALL leave non-detections as non-detections unless the signal brings them above their limit.
3. THE engine SHALL implement the five families of plan 10.2 with the stated parameter ranges, and SHALL be deterministic for a given seed.
4. THE engine SHALL never inject into a comparison star and never into a known variable.
5. THE injection set SHALL record, for each curve, the host, family, amplitude, epoch range and seed, so a recovery can be scored against the truth.
6. THE set SHALL place some steps on the Menzel Gap and include fades under 0.3 mag per century, and SHALL mark them `expected_missed`.

**R6. Metrics**
1. THE harness SHALL compute completeness per family and amplitude bin, the amplitude at 50% and 90% recovery with a binomial confidence interval, and false alarms per 1,000 constants.
2. WHEN a bin holds fewer than 10 injections, THE report SHALL mark its completeness as unreliable.
3. WHEN a detector recovers an `expected_missed` case, THE report SHALL flag the detector as suspect.

**R7. Trivial detector and smoke run**
1. THE trivial detector SHALL flag a curve when the difference between the median of its first and last decade of good points exceeds a threshold in mag. It SHALL be marked a test fixture, not a pipeline stage.
2. `computers bench --smoke` SHALL run a fixed subset in under 2 minutes, print the completeness table and write a JSON result.
3. THE exit test SHALL show the harness drawing a completeness curve for the trivial detector, where completeness rises with amplitude for the linear and step families.

**R8. Versioning and layers**
1. THE set SHALL carry a version, a manifest of the stars, injections and seeds, and the hash of every input table.
2. `bench` SHALL import `l1` and `data` only, and SHALL NOT call an LLM. The layer test SHALL cover it.
3. Any new numeric threshold SHALL be in `thresholds.py` with a decision record.

**R9. Records**
1. THE sprint SHALL write `docs/decisions/0005-leavitt-set-v0.md` with the counts, the constant scatter limit and what v0 cannot see, and fold findings into `plan.md` with a changelog entry.

## Decisions for you

1. **Injection size.** Proposed: all qualifying constants in the dev tiles as hosts, 5 families, 6 amplitudes, 2 seeds each. With about 300 hosts that is about 18,000 curves. Cheap, since it is numpy.
2. **Known-variable types.** Proposed start list: Mira, semi-regular, RV Tau, eruptive and nova types, eclipsing binaries with amplitude of at least 0.5 mag, and the reference targets.
3. **Pull T CrB and Tabby lightcurves** (human step, about 5 minutes).
4. **Frozen test split** is yours to approve once built, per the product rules.
