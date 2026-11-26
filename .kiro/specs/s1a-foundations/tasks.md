# Tasks: S1a Steering, hooks, schemas and snapshot store

Status: **requirements approved 2026-11-24 (all recommended answers).** Tasks 1-10 done, with one open item: the live hook check below.

- [x] 1. Add dependencies
  - `uv add pyarrow requests`. Record versions. Keep `uv sync` clean from a fresh copy.

- [x] 2. Schemas, lock and docs
  - `src/computers/data/schemas.py`: the eight tables from design.md, `SCHEMAS`, `fingerprint()`, `validate()`, `SchemaError`.
  - `computers schema lock | docs | check`. Commit `docs/schemas.lock.json` and `docs/schemas.md`.
  - `tests/unit/test_schemas.py`: validate passes on a conforming table and fails on a missing column, an extra column, a wrong type and a null in a non-nullable column; fingerprints are stable and change when a field changes; the lock and the doc match the code; a table changed without a version bump fails.
  - _Requirements: R2, R3_

- [x] 3. Snapshot store
  - `src/computers/data/snapshot.py`: `Request`, `ManifestRow`, `SnapshotStore` with `put`, `lookup`, `read`, `verify`, `digest`.
  - Unit tests: first put writes a read-only file and one row; repeat put returns the same row with no duplicate; changed response adds a row and `lookup` returns it; overwrite attempt with different content raises and leaves the file untouched; non-2xx is refused; key-like request body is rejected; torn manifest tail is skipped, reported by `verify`, and not merged with the next row; stray temp file reported.
  - Property tests (Hypothesis): manifest row round-trips exactly; the request key ignores dict key order; the hash and the file are identical across runs for the same bytes; the digest does not depend on put order; 8 threads putting 50 distinct bodies each leave 400 valid lines.
  - _Requirements: R4, R5, R6, R7, R8_

- [x] 4. Snapshot command line
  - `computers snapshot probe | verify | digest | status`. The probe uses the public `/health` endpoint.
  - Tests with a stubbed HTTP layer: success writes one row; a connection error exits non-zero and writes nothing; `verify` exits non-zero on a corrupted file.
  - _Requirements: R9_

- [x] 5. Thresholds and the first decision record
  - `src/computers/thresholds.py`, `docs/decisions/thresholds.lock.json`, `docs/decisions/0002-initial-thresholds.md`.
  - `tests/unit/test_thresholds.py`: value change fails, missing decision file fails, threshold missing from the lock fails.
  - _Requirements: R10_

- [x] 6. Layer rules test
  - `tests/unit/test_layer_rules.py`, AST based. Include a negative test on a temporary module that imports `openai`.
  - _Requirements: R11_

- [x] 7. Steering files
  - Write the six files from design.md. Apply the inclusion modes agreed in question 1. Keep each under 150 lines.
  - Run the secret scanner on them.
  - _Requirements: R1_

- [x] 8. Hooks
  - `scripts/hooks/block_raw_edits.py` and `tests/unit/test_block_raw_hook.py`.
  - First, create a temporary logging hook and trigger one real `PreToolUse` event to record the true input format. Record it in design.md, delete the temporary hook, and adjust the script's key names if needed.
  - Create the four hooks with the `createHook` tool.
  - Demonstrate: a write under `data/raw/` is blocked, a write elsewhere (including a file whose text mentions `data/raw/`) passes. **Done on the script with real payloads. Not yet done through Kiro itself:** new hooks only load at the next session start. **Open item for the next session:** ask the agent to write a file under `data/raw/` and confirm Kiro refuses it, then check `data/hooks/block_raw_edits.log` shows the payload keys were read.
  - _Requirements: R12_

- [x] 9. Pre-commit and README
  - Add the fast-test hook to `.pre-commit-config.yaml`.
  - README: a "Data and snapshots" section, and the hook table from design.md.
  - _Requirements: R12.5, R13_

- [x] 10. Exit test and report
  - Run `computers snapshot probe` for real and show the row.
  - Show the hook blocking an edit under `data/raw/`.
  - Change a schema field temporarily, show the failure, revert.
  - Corrupt a copy of a raw file, show `verify` flagging it.
  - `uv run pytest`, `ruff`, secret scan.
  - Report to the user and wait before Task 4.
