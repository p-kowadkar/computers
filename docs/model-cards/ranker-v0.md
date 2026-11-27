# Model card: ranker-v0

**What it is.** A logistic regression with sigmoid calibration (scikit-learn 1.9.1). It scores one L1 trigger and orders surviving candidates for the L3 budget. It is not a filter: on the dev benchmark it missed the pass mark for that (`docs/decisions/0008-l2-ranker.md`).

**Inputs (the "plain" set, 9 columns).** Trigger amplitude (mag), log of the evidence count, `single_plate`, one-hot trigger type (outburst, dip, step, long_term, disappearance), and `not_detrended`. The series count and the anomaly score were tested and left out. Inputs are standardised with the training mean and scale, which are stored in the model.

**Data.** The dev split of the Leavitt Set (M44 and T CrB): 361 constant stars and 21,660 planted signals. Training rows are the surviving triggers of L1 run with `trigger_min_series` 1. Positives: one trigger per recovered planted signal that was not planned as expected-missed (8,958). Negatives: surviving triggers on un-injected constants (43: 41 outbursts and 2 dips). Known variables are not labels. The test tile (Tabby) is not used.

**Evaluation.** Five folds grouped by host star, so no star is in training and test together. Pooled held-out AUC 0.979 and outburst-only AUC 0.885. The amplitude-only baseline has outburst-only AUC 0.370.

**Limits.**
- 43 negatives. Rates have wide intervals, and the filter test is inside the noise.
- The probability is relative to the training mix (208 positives per negative). It orders triggers and is not a rate of real events. Held-out scores sit between 0.98 and 1.00.
- The positives are injected signals, so the model may learn the injection engine. Real candidates are scored but have no gold labels yet.
- It has not seen a trigger type that L1 does not raise on constants (disappearance has no positives and no negatives; its score comes from the other inputs).
- Fitted file: `data/models/ranker-v0.joblib` (not committed). Rebuild it with `uv run computers bench l2`.
