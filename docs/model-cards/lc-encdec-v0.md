# Model card: lc-encdec-v0

**What it is.** A transformer encoder (4 layers, width 128, 4 heads) and a lighter decoder (2 layers), about 1.19 million parameters, trained to reconstruct masked magnitude offsets of a star's 100-year history (Track E1, plan 9.1). The decoder is used in training only. Code: `src/computers/ml/lc_encdec/`.

**Inputs.** Up to 200 tokens per star from `docs/data-cards/lc-sequences-v1.md`: magnitude offset from the star's median, measurement error, an is-limit flag, a hidden flag, the time as sine and cosine at 5 scales (a day to a century), and learned embeddings of series id and series kind. 30 to 50% of tokens are hidden, half as random points and half as one decade. Loss: squared error over the measurement error (floored at 0.05 mag) on hidden detections, and a one-sided squared penalty (scale 0.15 mag) on hidden limits for predictions brighter than the limit.

**Training.** AdamW at 1e-3 with a 2-epoch warm-up and cosine decay, batch 64, 20 fresh draws per star per epoch, early stopping on 10% of stars set aside, on the Apple GPU. Encoder M: M44 (383 stars). Encoder T: T CrB (154 stars). Configs in `configs/e1/`.

**Result (`docs/decisions/0009-e1-training.md`).** Encoder M predicts the star median on T CrB: RMSE 0.1885 mag against 0.1885 (difference -0.0001, interval -0.0004 to +0.0003). It beats linear interpolation and the nearest-5 same-series median, which are both worse than the median. Encoder T is 0.21 mag worse than the median on M44. **The pass mark was not met.**

**Failures.**
- It learned nothing beyond the median. Predictions have a standard deviation of 0.012 mag.
- It is no better than the median even on its own training stars (0.003 mag) or on same-tile stars set aside (no difference), so this is not a leak that the tile split removed.
- Encoder T extrapolates badly across tiles (2.25 mag RMSE on M44 in 1953 to 1969), and I have not found the cause.
- 60 to 80% of limit tokens are deeper than the star, so they contradict a detection that should have been made. The model treats them as limits.

**Limits.** 596 stars, mostly constant. No variable star with structure in the evaluation except T CrB. No embedding has been extracted or tested (Sprint 7). No third encoder on both tiles was trained. Not for use in L2.
