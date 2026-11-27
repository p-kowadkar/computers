# Data card: lc-sequences-v1

**What it is.** Per-star sequences of observations for the lightcurve encoder-decoder (Track E1). Sequence spec version 1, hash `29aa9a41b57d899b`, snapshot digest `sha256:2b1f4be89017864e0c33ca9c2be6553994e71aa69fed31d753d6040dd089dc06`. Written by `computers ml build`.

**Rows.** The curve L1 read (detrended when the star could be detrended, raw otherwise), for stars with at least 50 good detections. Each row is a good detection, or a non-detection that no cleaning tag removed and that has a finite plate limit. The magnitude is stored as an offset from the star's median, and a non-detection's limit as a depth relative to it. Nothing is divided by scatter.

**What it leaves out.** Injected curves (the builder never reads the benchmark). The Tabby tile, which is held out. Stars with fewer than 50 good detections. Detections without a plate offset after detrending. Rows that a rejecting tag removed.

| Tile | Stars | Rows | Detections | Rows per star (min, p10, median, p90, max) | Limit share | Limits deeper than the star | Detrended share |
|---|---|---|---|---|---|---|---|
| m44 | 425 | 575,457 | 508,009 | 212, 888, 1327, 1946, 2200 | 11.7% | 80% | 83% |
| tcrb | 171 | 144,646 | 135,760 | 440, 525, 751, 1228, 1641 | 6.1% | 61% | 74% |

**Series kind by decade, m44** (share of rows).

| Decade | unknown | narrow | patrol | meteor |
|---|---|---|---|---|
| 1880s | 0% | 100% | 0% | 0% |
| 1890s | 0% | 84% | 16% | 0% |
| 1900s | 0% | 32% | 68% | 0% |
| 1910s | 0% | 27% | 73% | 0% |
| 1920s | 0% | 28% | 72% | 0% |
| 1930s | 0% | 61% | 39% | 0% |
| 1940s | 0% | 57% | 43% | 0% |
| 1950s | 0% | 29% | 71% | 0% |
| 1960s | 0% | 25% | 75% | 0% |
| 1970s | 0% | 21% | 79% | 0% |
| 1980s | 0% | 2% | 98% | 0% |

**Series kind by decade, tcrb** (share of rows).

| Decade | unknown | narrow | patrol | meteor |
|---|---|---|---|---|
| 1890s | 0% | 30% | 70% | 0% |
| 1900s | 0% | 5% | 95% | 0% |
| 1910s | 0% | 1% | 99% | 0% |
| 1920s | 0% | 26% | 74% | 0% |
| 1930s | 0% | 42% | 58% | 0% |
| 1940s | 0% | 23% | 77% | 0% |
| 1950s | 0% | 11% | 89% | 0% |
| 1960s | 0% | 29% | 71% | 0% |
| 1970s | 0% | 0% | 100% | 0% |
| 1980s | 0% | 0% | 100% | 0% |

**Known limits.**
- 596 stars, mostly constant. The set is small for self-supervised training, and a model trained on it may learn sampling and plate effects.
- Stars of one tile share plates. Splits are by tile (`0004`).
- 361 of the dev stars are Leavitt Set hosts. An encoder never scores a host of a tile it was trained on.
- Meteor series are 10 to 21% of the raw rows. The default meteor policy rejects them, so the dataset has none (`0006`). The 1950s are thin as a result.
- **Most limits are not upper limits of a faint star.** A limit deeper than the star's own median magnitude (offset above 0) means the star should have been detected and was not. That is 60 to 80% of limit rows here, which fits the known DASCH issue of missing points faking deep limits (`astro-domain.md`). The one-sided loss still treats them as limits, and the report counts them.
- Non-detections are 6 to 12% of rows here, against 34 to 45% of raw rows, because cleaning removes most of them.
