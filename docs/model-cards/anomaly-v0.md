# Model card: anomaly-v0

**What it is.** An isolation forest (200 trees, fixed seed) over 14 hand-made features of a lightcurve: robust amplitude, MAD, skew, von Neumann ratio, reduced chi-square, decade slope and curvature, largest step, longest run, fraction beyond 3 sigma, spread of per-series zero-points, point count, time span and populated decades. NaN is filled with the training median, which is stored with the model. The score is higher for a stranger star.

**Data.** Un-injected dev constants, cross-fitted by host star in the benchmark (the forest that scores a host never saw it).

**Result.** It does not help the ranker. Outburst-only AUC is 0.885 with and without it, and the bootstrap interval of the gain at one false-alarm constant widens (-2.6 to +5.0 points with it, +0.5 to +4.7 without). It is not an input of the ranker on real tiles (`0008`).

**Why it stays.** Plan 7.2 adopts embedding scores only if they beat this baseline, so Sprint 7 needs it. The code is `src/computers/l2/anomaly.py`, and nothing in `l2 run` calls it.

**Limits.** Trained on quiet stars from two tiles. It has no labels and was never scored on real candidates.
