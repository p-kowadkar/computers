# Tasks: S5 L2 features, ranker and the first topology report

- [x] 1. Features
  - `l2/features.py` for the 14 features of R1. Hypothesis tests for invariance under a constant magnitude shift, for chi-square near 1 on constants, for NaN and not zero when undefined, and a "fainter must not pass" case for each sign-sensitive feature.
  - _R1_

- [x] 2. Dependency and layer test
  - Add `scikit-learn` pinned exactly. Extend the layer test to cover `l2/` and a negative case that imports an LLM client and is caught.
  - _R9.1_

- [x] 3. Anomaly score
  - `l2/anomaly.py`: fit, save, load, score. Test reproducibility and that the saved model gives the same scores.
  - _R2, R9.2_

- [x] 4. The operating-point arms
  - `bench/l2_bench.py`: run L1 strict and relaxed (`trigger_min_series` 1) on the dev constants and injections with the same signals, and collect trigger rows with their features, labels and host stars.
  - _R4.1, R4.3_

- [x] 5. Ranker
  - `l2/ranker.py`: logistic regression, calibration, grouped cross-validation by host star. Pooled held-out scores, AUC and calibration bins with intervals. Compare with the amplitude baseline and with and without the anomaly score.
  - _R2.3, R3_

- [x] 6. Decide: the numbers for your review
  - Run the three-arm comparison. Show decision 5's test, the cost, and the calibration. You choose the threshold and whether L2 stays. Write `docs/decisions/0008-l2-ranker.md`, the thresholds in `thresholds.py` and the lock, and the model cards.
  - _R4.2, R4.4, R10_

- [x] 7. Budget
  - `l2/budget.py` with the caps from the registry and property tests (never above either cap, a worse candidate never displaces a better one, known variables take no place).
  - _R5_

- [x] 8. `computers l2 run`
  - Read L1 candidates, write scores and L2 telemetry, refuse the test tile before the freeze. Idempotence test.
  - _R6, R9.3_

- [x] 9. Topology report
  - Per-tile stages and costs, what the cap left out, T CrB's eruption and the known variables with their scores. Run M44 and T CrB. Tabby only after you freeze the split.
  - _R7, R8_

- [x] 10. Docs and plan
  - Update `plan.md` (7.1, 7.4, 7.5, changelog, S5 row), `astro-domain.md` and `structure.md`, and the model cards.

- [x] 11. Exit test and report
  - `uv run pytest`, `ruff`, secret scan, schema, thresholds, snapshot and `bench check`, and `pre-commit run --all-files`. Show: false alarms at equal recovery for the three arms, the calibration, the topology report, and what the cap left out. Report and wait before Sprint 6.
