---
inclusion: fileMatch
fileMatchPattern: ["tests/**", "src/computers/l1/**", "src/computers/l2/**", "src/computers/bench/**", "src/computers/thresholds.py"]
---

# Testing rules

## What every change needs

- **Numeric code needs property tests** (Hypothesis). Typical properties for this project:
  - magnitude to flux and back is the identity;
  - combining two sources is always brighter than either;
  - Julian date to calendar date round-trips across 1880 to 1990;
  - adding a constant to every star on a plate leaves every detrended value unchanged;
  - injecting a zero-amplitude signal changes nothing;
  - adding a rejection never increases the count of good points;
  - an injected point fainter than the plate limit always becomes a non-detection.
- **Magnitudes run backwards.** Every comparison that says "brighter" gets a test on both sides of the boundary, including a "fainter must not pass" case.
- **Golden tests** for T CrB, KIC 8462852, RY Cnc and one hand-checked boring star, with frozen raw inputs and reviewed expected outputs. Frozen means committed, small, and never regenerated without a decision record.
- **Acceptance criteria become tests.** Each EARS criterion in a spec maps to at least one test. Ask of every criterion: how would I test this?
- **Negative tests prove a check can fail.** For a rule that forbids something (a layer import, an overwrite, a key in a request), include a case that breaks it and assert it is caught.

## Splits and leakage

- Lightcurve data is split **by tile**. Train on M44 and T CrB, hold out the Tabby tile.
- Cutout crops may split by plate. A model that saw a plate in training is not tested on it.
- Benchmark stars never enter any training set. Thresholds are tuned on a dev split and reported once on the test split. The frozen test split is never tuned against.

## Benchmark delta

No component merges without a benchmark delta: a number from `computers bench` before and after. A component whose number does not improve is cut, and the write-up says so. A negative result that is measured and written up counts as done. (The benchmark itself arrives in Sprint 2.)

## Thresholds and schemas

- A changed threshold needs a new decision record in `docs/decisions/` and an updated `docs/decisions/thresholds.lock.json`. `computers thresholds check` enforces it.
- A changed table needs a version bump and a regenerated lock. `computers schema check` enforces it.

## Running and organising tests

- `tests/unit/` for fast tests, `tests/property/` for Hypothesis, `tests/golden/` for frozen cases.
- Tests that need the network carry `@pytest.mark.network`. They are excluded by default and run with `-m network`. Do not hit the DASCH API from the default suite.
- Tests that need the optional `spike` dependencies use `pytest.importorskip`.
- The fast suite runs in pre-commit and should stay under about 10 seconds. Mark slow tests and move them out when it grows.
- Use `tmp_path` for anything that writes files. Never write under `data/` from a test.
- A test that depends on git state must skip when there is no commit yet, not fail.

## Reproducibility

- LLM-dependent tests and benchmark runs read from the response cache, keyed by model, provider and prompt hash. A test never calls a live model.
- Patrols are idempotent: the same snapshot digest and the same code version give the same output.
