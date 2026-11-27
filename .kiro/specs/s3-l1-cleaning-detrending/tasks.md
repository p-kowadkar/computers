# Tasks: S3 L1 cleaning, detrending and seams

Status: **approved 2026-11-26; defaults accepted. All 13 tasks done; waiting for your decisions (see `0006` section 4).**

- [ ] 0. **First thing in a new session: confirm the blocking hook through Kiro** (carried over from S1a)

- [x] 1. Moves that keep the Leavitt Set identical
  - Move `jd_to_year` to `data/timeconv.py` and the scatter statistics to `l1/scatter.py`. `bench` re-exports them. Rebuild the set and confirm `manifest.json` and `injections.parquet` are byte-identical to the committed build.
  - _R10.1_

- [x] 2. Series table
  - `scripts/make_series_table.py` (spike group) writes `data/series.py`. Tests: matches daschlab when installed, every series in the three tiles is known, kinds follow the plate-scale boundaries at 400 and 900 on both sides. `NOTICE` entry.
  - _R1_

- [x] 3. Tags and the curve
  - `l1/tags.py`, `l1/curve.py`. Tests: the tag bits are distinct, `usable()` keeps good detections and non-detections only, `with_magnitudes` recomputes `good`, the input is never changed, no row is deleted.
  - _R2_

- [x] 4. Cleaning steps 2 to 6, 8 and 9
  - `l1/clean.py`, one function per step. Tests per step on both sides of its boundary (15 arcsec, colour term -0.53 and 0.0, blend 0.3 mag, deep limit 1.0 mag, `good` against `good_for_outburst`), plus Hypothesis: adding a rejection never increases good points, cleaning twice equals cleaning once.
  - _R3.2 to R3.6, R3.8 to R3.10, R6.1, R6.5_

- [x] 5. **Human step: decide** the meteor policy default, the soft and hard split, and the threshold table. Write `docs/decisions/0006-l1-cleaning-and-detrending.md` and register the thresholds with their lock entries.
  - _R10.3_

- [x] 6. Pull the neighbours and source splitting (step 1)
  - `computers pull --stage neighbours` (21 requests, public API). `clean.merge_sources` with tests on synthetic interleaved and overlapping pairs, and a check on the 18 real stars.
  - _R3.1_

- [x] 7. TileContext and shape outliers (step 7)
  - `l1/context.py`: the matrix, baselines, quiet stars, per-plate shape statistics. Tests on a small synthetic tile. A speed check on Tabby.
  - _R3.7, R10.2_

- [x] 8. Detrending
  - `l1/detrend.py`. Tests: the hand-computed ensemble (5 stars by 6 plates), pool selection at the edges of both windows, `few_comparisons`, `NO_OFFSET`, offsets cached, copies of the target give a constant, and Hypothesis for R6.2 to R6.4.
  - _R4, R6.2 to R6.4_

- [x] 9. Seams
  - `l1/seams.py`. Tests: stretches break at the gap and at a series change, `seam_only` for epochs inside the gap, across a break and with 19 against 20 points, never for a clear mid-stretch step.
  - _R5_

- [x] 10. Benchmark integration and goldens
  - `L1Detector`, the host-aware harness form, L1-mode hosts. Golden tests: T CrB eruption survival, RY Cnc tags, the hand-checked quiet star (**you review the frozen numbers**).
  - _R7_

- [x] 11. The reports
  - `computers bench l1` and `computers bench tabby`. Run on the real data, show the four tables and the Tabby slopes. A component that does not help is reported as such.
  - _R8, R9_

- [x] 12. Decision record, docs, plan
  - Fill `0006` with the numbers, add the data card note, update `plan.md` (6.1 steps 2 and 4, 6.2, 6.3, changelog), `structure.md` and `astro-domain.md` where the facts changed.
  - _R10.3_

- [x] 13. Exit test and report
  - `uv run pytest`, `ruff`, secret scan, schema, thresholds and snapshot checks, `bench check`, `pre-commit run --all-files`. Show: property tests pass; scatter and false alarms before and after detrending; both meteor policies; gap steps 100% `seam_only`; the Tabby slope. Report and wait before Sprint 4.
