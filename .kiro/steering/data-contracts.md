---
inclusion: fileMatch
fileMatchPattern: ["src/computers/data/**", "data/**", "docs/schemas*", "docs/data-cards/**"]
---

# Data contracts

## Storage (plan 4.4)

| Path | What | In git? |
|---|---|---|
| `data/raw/<source>/<hh>/<sha256>.gz` | API responses exactly as received, gzipped | No |
| `data/manifests/<source>.jsonl` | One row per raw file: request, hash, time, client | **Yes** |
| `data/silver/` | Typed Parquet tables, queried with DuckDB | No |
| `data/gold/` | Candidates, verdicts, case files, training sets | No |

A remote copy goes to S3 with `aws s3 sync`. There is no DVC.

## The rules

1. **Raw is immutable.** Never edit, replace or delete anything under `data/raw/`. The snapshot store creates each file once, named by the hash of its bytes, and makes it read-only. An existing file with different content is an error, never overwritten.
2. **Manifests are append-only.** No operation rewrites or removes an existing row. A torn line is skipped and reported by `computers snapshot verify`.
3. **Only successful (2xx) responses are stored.** An error page is not data.
4. **No credential ever appears in a request record.** `Request` has no headers field and rejects a body with a key-like name. Keys come from environment variables.
5. **The snapshot id is `SnapshotStore.digest()`**: one hash over the newest response of every request. Case files cite it for provenance. It does not depend on fetch order.
6. **Lightcurves in the tables keep non-detections** as rows with a null `magcal_magdep`. Nothing is deleted. Rejections are a bit-field in `reject` (zero means good).
7. **Names follow daschlab**, not the raw API (`time`, `platenum`, `fwhm_image`). The thin client maps raw names to ours. `exposure_id` is ours, not daschlab's: `{series}{platenum:05d}e{expnum}` plus `s{solnum}` when there is a solution.
7a. **A re-fetch of the same request can return different bytes** (tied rows come back in another order). Compare stored responses as multisets of rows, and do not read a changed `digest()` as a data change by itself.
8. **Splits are by tile** for lightcurve data. All stars in a tile share nearly the same plates, so a split by star does not separate plates. Track A cutout crops can split by plate.

## Tables

Ten tables: `stars`, `measurements`, `exposures`, `cutouts`, `candidates`, `verdicts`, `labels`, `telemetry`, and from Sprint 2 `vsx` and `vsx_match`. Defined in `src/computers/data/schemas.py` as PyArrow schemas and documented in the generated file below. Use `schemas.validate(name, table)` before writing Parquet.

#[[file:docs/schemas.md]]

## Changing a table

1. Edit the schema in `schemas.py` and **raise that table's `version`**.
2. Run `computers schema lock` and `computers schema docs`.
3. Run `computers schema check`. It fails if the lock or docs are stale, or if a table's columns changed against git HEAD without a higher version.
4. Say in the commit message why the table changed, and what reads it.

Adding a nullable column is still a change. There is no "small enough to skip".

## Data cards

Every dataset gets a card in `docs/data-cards/`: where it came from (the snapshot digest), how many rows, the share of upper limits, the series mix, known weaknesses, and the licence. Do not publish derived data (cutouts, labelled sets) before reading the Harvard terms and asking the DASCH list.
