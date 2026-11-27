# Requirements: S5 L2 features, ranker and the first topology report

**Intent.** Rank what L1 lets through, so the crew sees at most 20 candidates per tile and 60 in all, and show by a number that the ranking helps. L2 has one job it can be measured on: L1's two-series rule kills every single-plate outburst and costs outburst recovery (9.9% with the rule against 19.3% without, at 100 false alarms per 1,000 constants). L2 should buy some of that back. If it cannot, it is cut (principle 2).

**Source:** `plan.md` sections 7.1, 7.4, 7.5 (with the rev of 7.5 in `0004`), 4.5 and S5 in section 13; `docs/decisions/0004`, `0007`. Spec status: **draft, awaiting review**.

## Scope

In:
- The hand-made feature baseline of 7.1, computed per star on the curve L1 read.
- An isolation-forest anomaly score over those features.
- A calibrated logistic ranker (7.4) over trigger strength, the anomaly score and the L1 flags.
- The budget rule of 7.5: top candidates per tile and in total, with what was left out reported.
- An operating point where L1 runs with `trigger_min_series` 1 and L2 decides, measured against L1 alone (decision 1 below).
- `candidates.l2_score`, an L2 telemetry row per star, and a first topology report: stars in, stars out and cost at each layer.
- A `computers l2 run` command.

Out:
- Embedding scores (7.2, Sprint 7) and cutout scores (7.3, Sprint 14 or later). The ranker's inputs have a place for them and nothing more.
- Any change to the L1 thresholds in the registry. The relaxed series rule is a parameter of the benchmark, not a change of the default.
- The crew, packets and case files.
- The test split. It is scored once, after you freeze it, and no threshold changes afterwards.

## Facts found while drafting this spec

- **There is a real ranking job already.** `computers l1 run` leaves 65 candidates on M44 and 26 on T CrB: 91 against a cap of 60 (20 per tile). 13 of M44's and 16 of T CrB's are `known_variable`.
  **Corrected after the build:** those are candidate rows. One trigger per star leaves 22 stars on M44 and 8 on T CrB, and the cap does not bind (`0008`).
- **L1 has almost no false alarms left to cut.** At the exit it raises 1 in 361 constants (3 per 1,000). "L2 cuts false alarms at equal recovery" cannot be shown on that. It can be shown at the relaxed operating point, where L1 raises 36 outbursts on 361 constants (100 per 1,000) and recovers 19.3% of outbursts.
- **Negatives are scarce.** The dev split has 361 constants, so the relaxed L1 gives about 40 false triggers to learn from. A model with many parameters would fit them by memory. The ranker therefore stays a logistic regression with a handful of inputs, and the report gives intervals.
- **Positives are injections.** Every injected signal that L1 recovers is a labelled true event. Real stars have no gold labels yet, so nothing here is scored on real candidates, only listed.
- **scikit-learn is not a dependency yet.** It brings isolation forest, logistic regression and calibration, and adds a core dependency (decision 3).
- **Features of a 100-year curve have gaps.** The Menzel Gap and the sparse 1950s and 1960s leave features such as the longest run and the von Neumann ratio sensitive to sampling. Features must be computed within stretches where the plan's definition crosses a seam.

## Decisions for you (defaults in bold)

1. **The operating point.** L1 stays at the registry's `trigger_min_series` 2 and the recovery checks. For the L2 test, the benchmark also runs L1 with `trigger_min_series` 1 and measures "L1 relaxed, then L2" against "L1 strict alone". **Default: yes, as a benchmark comparison. The registry default is not changed here.** If L2 wins by your rule, changing the L1 default is a separate decision record.
2. **The unit of ranking.** A candidate row is one trigger, and one star can have several. **Default: rank the triggers, then keep only the best trigger per star within the budget**, because the crew judges a star.
3. **The dependency.** **Default: add `scikit-learn` pinned to an exact version in the core group.** The alternative is to write the logistic regression and isolation forest by hand, which costs more than it teaches.
4. **What "equal recovery" means.** **Default: false alarms per 1,000 constants compared at the same outburst, dip and step recovery as L1 strict alone, with the ranker threshold chosen on the dev split, by cross-validation grouped by star.**
5. **The cut-off for keeping the ranker.** **Default: L2 stays only if, at equal recovery to L1 strict, it cuts the relaxed false alarms to at most those of L1 strict, with a recovery gain of at least 3 points on outbursts (9.9% to 12.9% or more).** If it misses, the sprint reports a negative result and the topology stays L1 strict, then budget by score.
6. **Budget.** **Default: at most 20 stars per tile and 60 in all, and no dollar figure yet because L3 does not exist.** `0004` sets it.

## Acceptance criteria (EARS)

**R1. Features**
1. THE feature table SHALL have one row per patrol star with these columns: robust amplitude (5th to 95th percentile), median absolute deviation, skew, von Neumann ratio, reduced chi-square against a constant, slope and curvature of decade-binned medians, largest change-point shift, longest run above or below the median, fraction of points beyond 3 sigma, spread of per-series zero-points, point count, time span and populated decades.
2. THE features SHALL be computed on the good detections of the curve L1 read (detrended when the star could be detrended, raw otherwise) and SHALL record which.
3. WHEN a feature cannot be defined (too few points, one series), IT SHALL be NaN and SHALL NOT be filled with zero. The model's imputer SHALL be explicit and its fill values recorded.
4. A magnitude shift applied to a whole curve SHALL leave every shape feature unchanged. A star that is constant plus noise SHALL give a reduced chi-square near 1.

**R2. Anomaly score**
1. THE anomaly score SHALL be an isolation forest fitted on features of dev stars only, with a fixed seed and the fit saved with its feature list and the snapshot digest.
2. THE score SHALL be higher for a more anomalous star, and SHALL be reproducible: the same features and model give the same score.
3. THE score SHALL be compared with a trivial baseline (rank by trigger amplitude, then by significance). IF the anomaly score does not improve the ranker's held-out result, THEN it SHALL be dropped from the ranker and the report SHALL say so.

**R3. Ranker**
1. THE ranker SHALL be a logistic regression over trigger strength, the anomaly score (if kept), the trigger type and the L1 flags. THE inputs SHALL be listed in the model card.
2. THE training labels SHALL be: positive for a recovered injected signal, negative for a surviving false trigger on a constant. THE known variables SHALL NOT be training labels.
3. THE ranker SHALL be calibrated, and the report SHALL show the calibration of held-out scores in bins.
4. THE ranker SHALL be evaluated by cross-validation grouped by host star, so no star is in training and test together. THE test tile SHALL NOT be used.
5. THE report SHALL give, with 95% intervals, the area under the ROC curve and the false-alarm rate at the chosen threshold.
6. A model card in `docs/model-cards/` SHALL give the inputs, the data, the counts of positives and negatives, and the limits.

**R4. The operating-point comparison**
1. THE benchmark SHALL run L1 with `trigger_min_series` 1 and the other registry values, with no change to the registry.
2. THE report SHALL give, for "L1 strict alone", "L1 relaxed alone" and "L1 relaxed then L2": completeness per family, false alarms per 1,000 constants per trigger type, and the recovered share of outbursts.
3. THE comparison SHALL use the same signals and the same constants for every arm.
4. THE sprint SHALL state whether L2 meets decision 5 and SHALL give its cost: what it removes that L1 strict would have kept.

**R5. Budget**
1. THE budget rule SHALL keep at most 20 stars per tile and 60 in total, ranked by the ranker's score, with one trigger per star.
2. A candidate `known_variable` SHALL be kept in the table and SHALL NOT take a place in the budget.
3. THE report SHALL list what was left out: the number of stars below the cap with their scores, so a threshold that is too tight is visible.
4. THE budget constants SHALL be registered as `budget_per_tile` (20) and `budget_total` (60) in `thresholds.py`, with `0004` and the new record `0008` as their source. They are in a decision record but not yet in the registry.

**R6. Candidates, telemetry and the CLI**
1. `computers l2 run --tile m44|tcrb|tabby` SHALL read the L1 candidates, write `l2_score` into a new candidates file, and write one L2 telemetry row per star that reached L2: `stop_layer` `l2` with a reason (`below_budget`, `known_variable`, `not_ranked`) or `l3` when selected.
2. THE command SHALL refuse the test tile until the split is frozen and the thresholds are chosen, as `l1 run` does.
3. EVERY L1 telemetry row SHALL stay unchanged.
4. A schema change SHALL need a version bump and a regenerated lock. IF no column is added, no bump SHALL be made.

**R7. The topology report**
1. THE report SHALL give for each tile the stars in, the stars stopped at each layer with the reason, the seconds at each layer, and the cost in dollars (zero for L1 and L2).
2. THE report SHALL say what the cap left out.

**R8. Reference stars**
1. T CrB's 1946 eruption SHALL still be in the budget for the T CrB tile, or the report SHALL say why not.
2. THE known variables SHALL be listed with their score, so a ranker that gives a known outburst a low score is visible.

**R9. Layer rules and reproducibility**
1. Nothing under `l2/` SHALL import `nooa`, `litellm`, `openai` or `anthropic`, and `l2` SHALL NOT import `crew`. A test SHALL enforce it.
2. A fitted model SHALL be saved with its seed, feature list, imputer and snapshot digest, and SHALL load to give the same scores.
3. THE same snapshot digest and code version SHALL give the same candidates and scores.

**R10. Thresholds**
1. EVERY new number (ranker threshold, budget, isolation-forest settings) SHALL be in `thresholds.py` with a record `0008`, and `computers thresholds check` SHALL pass.
2. THE dev split SHALL set the values. THE test split SHALL be scored once, after you freeze it, and no value SHALL change afterwards.
