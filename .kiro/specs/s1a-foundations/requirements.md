# Requirements: S1a Steering, hooks, schemas and snapshot store

**Intent.** Put in place the rules every later sprint depends on: standing project rules for Kiro, typed table schemas with version control, an immutable snapshot store with committed manifests, and hooks that stop the easy mistakes. No network pulls beyond one probe request.

**Source:** `plan.md` sections 4.4, 4.5, 12.1, 12.4, and `docs/decisions/0001-sprint0-findings.md`. Spec status: **draft, awaiting review**.

## Scope

In: six steering files, `schemas.py` with a version lock, `snapshot.py` with manifests and a small CLI, a thresholds registry with a decision lock, hooks, tests.
Out: the thin DASCH client and any real pull (Task 4), the `bench` hook and `computers bench` (Task 5), S3 sync and AWS (Task 16), reject-bit names (Task 6).

## Design constraint found while writing this spec

Kiro command hooks on file-save events cannot block or show failures: any non-zero exit other than 2 is silent, and only `PreToolUse` and a few other triggers can block. So **enforcement is by tests and pre-commit.** Hooks on save are prompts to the agent, and the one blocking hook (raw data) is a `PreToolUse` hook.

## Acceptance criteria (EARS)

**R1. Steering files**
1. THE repo SHALL contain `.kiro/steering/{product,tech,structure,astro-domain,data-contracts,testing}.md`, each with front matter stating its inclusion mode, and each under 150 lines.
2. `product.md` SHALL state what the project is and is not (plan 1.1, 1.2) and the claims policy (11.5).
3. `tech.md` SHALL state: Python 3.12 with `uv`, no LLM calls below L3, NOOA pinned behind one import seam, every ellipsis method uses `@strategy(PredictStrategy())`, secrets only in environment variables, and the commands to run tests and lint.
4. `structure.md` SHALL give the repo layout (plan 4.3) and the layer contracts (4.2).
5. `astro-domain.md` SHALL state: magnitudes run backwards, APASS-referenced curves only, the known-issues table (3.3), the amplitude floors, and the Sprint 0 findings that change L1 (W1 radius, W4 rejections, W5 Tabby trend, W6 `UNCERTAIN_CATALOG_MAG`).
6. `data-contracts.md` SHALL state the table names, that raw data is immutable, that manifests are committed, the naming rule (daschlab names, not raw API names), and the schema version rule.
7. `testing.md` SHALL state: property tests for numeric code, golden tests, split by tile, a benchmark delta to merge, and that a threshold change needs a decision record.
8. THE steering files SHALL NOT contain anything matching the secret scanner.

**R2. Table schemas**
1. `computers.data.schemas` SHALL define the eight tables in plan 4.5 (`stars`, `measurements`, `exposures`, `cutouts`, `candidates`, `verdicts`, `labels`, `telemetry`), each with typed columns, nullability, a primary key, and an integer version.
2. THE `measurements` table SHALL use daschlab's column names as established in the Sprint 0 findings, SHALL NOT contain `plate_quality_flag`, SHALL carry `exposure_id` (the daschlab exposure id, such as `ac33494m0s0`) to join to `exposures`, and SHALL store non-detections as a null `magcal_magdep`.
3. WHEN a table is given to `validate(table_name, arrow_table)`, THE function SHALL raise an error naming the first problem found if a column is missing, extra, or of the wrong type, or if a non-nullable column holds a null.
4. THE schemas SHALL be defined as PyArrow schemas, so they are the same objects used to write Parquet.

**R3. Schema version rule**
1. THE repo SHALL commit `docs/schemas.lock.json` holding each table's version and a fingerprint (sha256 of its canonical field list).
2. WHEN a table's fingerprint differs from the lock and its version is unchanged, THE test suite SHALL fail with a message naming the table.
3. WHEN a table's version is raised, THE test SHALL still fail until the lock is regenerated with `computers schema lock`, so a change is always a deliberate two-step.
4. THE repo SHALL commit `docs/schemas.md`, generated from the schemas, and a test SHALL fail if it is out of date. `computers schema docs` regenerates it.

**R4. Snapshot store: writing**
1. `SnapshotStore.put(request, body)` SHALL write the response bytes under `data/raw/<source>/<hh>/<sha256>.gz`, where `<sha256>` is the hash of the uncompressed body and `<hh>` its first two hex digits, and SHALL append one manifest row.
2. THE gzip output SHALL be deterministic (fixed header time), so the same body always gives the same file.
3. THE manifest row SHALL hold: sha256, source, endpoint, request (as canonical JSON), request key, relative path, byte count, fetched-at (UTC, ISO 8601), client version, and API base (`public` or `full`).
4. WHEN the same request and the same response already have a row, THE store SHALL return the existing row and SHALL NOT append a duplicate.
5. WHEN the same request returns a different response, THE store SHALL append a new row, and `lookup` SHALL return the newest.
6. THE store SHALL only record successful responses. A caller reporting a non-2xx status SHALL get an error and no file or row.

**R5. Snapshot store: immutability**
1. THE store SHALL create raw files without replacing any existing file, so an existing path is never overwritten.
2. WHEN a file exists at the target path with different content than the hash implies, THE store SHALL raise an error and SHALL NOT modify it.
3. THE store SHALL set each raw file read-only after writing it.
4. THE manifest SHALL be append-only: no operation of the store SHALL rewrite or delete an existing row.

**R6. Snapshot store: reading and resuming**
1. `lookup(source, endpoint, request)` SHALL return the newest row for that request whose raw file exists, or `None`. THE store SHALL NOT need the network to answer.
2. THE request key SHALL be the same for equal requests whatever the order of their dictionary keys.
3. `read(row)` SHALL return the original bytes and SHALL raise an error if the file's content no longer matches the row's hash.
4. `verify()` SHALL re-hash every raw file named in the manifests and report missing files, hash mismatches, and raw files with no manifest row.
5. `digest()` SHALL return one sha256 over the sorted (request key, sha256) pairs of the newest row per request. It SHALL be identical for the same set of responses whatever the order of fetching. This is the snapshot id for case-file provenance.

**R7. Safety under concurrency**
1. WHEN several threads call `put` at once, THE manifest SHALL contain whole, valid JSON lines and no row SHALL be lost.
2. WHEN a process is stopped partway through a write, THE store SHALL NOT leave a raw file whose content does not match its name.

**R8. Secrets**
1. THE store SHALL NOT accept, record or write any API key. Request headers are not part of a record.
2. A test SHALL assert that a request object carrying a key-like header is rejected, and that no manifest field can hold one.

**R9. Command line**
1. `computers snapshot probe` SHALL make one request to the public `GET /health` endpoint, store it, and print the manifest row. **Deviation from the plan's exit test:** the plan names this `computers pull --dry-run`. `pull` is built in Task 4 and `--dry-run` would mislead for a command that writes, so the probe has its own name.
2. `computers snapshot verify`, `computers snapshot digest` and `computers snapshot status` SHALL call the matching store operations. `status` SHALL list rows per source.
3. `computers schema lock` and `computers schema docs` SHALL regenerate the lock and the schema doc.
4. WHEN the probe cannot reach the API, THE command SHALL exit non-zero and SHALL NOT write a row.

**R10. Thresholds and decision records**
1. `computers.thresholds` SHALL be the single place for numeric thresholds, each with a name, value, unit, a status (`start` or `decided`), and the plan section it comes from.
2. THE repo SHALL commit `docs/decisions/thresholds.lock.json` holding each threshold's value and the decision file that set it.
3. WHEN a threshold's value differs from the lock, THE test suite SHALL fail, naming the threshold.
4. WHEN the lock names a decision file that does not exist, THE test SHALL fail.
5. THE initial registry SHALL hold only values the plan states: the amplitude floors (0.5 and 1.0 mag), the 15 arcsec astrometry limit, and the 5 arcsec source-split radius, recorded in `docs/decisions/0002-initial-thresholds.md`.

**R11. Layer rules**
1. A test SHALL fail if any module under `data/`, `l1/`, `l2/`, `bench/` or `telemetry/` imports an LLM client or agent framework (`nooa`, `litellm`, `openai`, `anthropic`).
2. THE test SHALL also check that no module under `l1/` imports from `l2/` or `crew/`.

**R12. Hooks**
1. THE repo SHALL contain a `PreToolUse` hook that exits with status 2 and a clear message when an agent file tool targets a path under `data/raw/`. Writes elsewhere SHALL pass, including files whose text merely mentions `data/raw/`.
2. THE blocking hook SHALL read the tool's path from the hook's JSON input, and THE spec SHALL record the real input format observed from one live event.
3. THE repo SHALL contain `PostFileSave` agent hooks that ask the agent to run (a) the property tests after a change under `src/computers/l1/` or `src/computers/bench/`, (b) the schema tests after a change to `schemas.py` or `data-contracts.md`, and (c) the threshold tests after a change to `thresholds.py`.
4. THE `computers bench --smoke` hook SHALL NOT be created yet, because the command does not exist. Task 5 creates it.
5. THE README SHALL list each hook, its trigger, and what enforces the same rule in tests.

**R13. Pre-commit**
1. THE pre-commit config SHALL run the fast test suite (`pytest -q -x`), so R3, R10 and R11 fail a commit even when no Kiro hook ran.

## Exit test

1. `computers snapshot probe` writes a raw file and a manifest row.
2. The hook blocks an edit to a file under `data/raw/` and allows a write elsewhere.
3. Changing a schema field without a version bump fails the tests, shown once with a temporary edit.
4. `computers snapshot verify` flags a corrupted raw file, shown once on a copy.

## Questions for the reviewer

1. **Steering inclusion.** Always-included files cost tokens on every message. I propose `product`, `tech` and `structure` always included, and `astro-domain`, `data-contracts` and `testing` included only when matching files are open (`fileMatch`). Fewer tokens, but a rule might be missed when the matching file isn't open.
2. **Probe name (R9.1).** `computers snapshot probe` instead of `pull --dry-run`. OK?
3. **Initial thresholds (R10.5).** Seeding four values from the plan so the mechanism has real entries. The alternative is an empty registry tested with temporary entries, with values added as sprints decide them.
