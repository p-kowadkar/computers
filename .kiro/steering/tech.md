---
inclusion: always
---

# Tech stack and rules

## Stack

- **Python 3.12**, managed with **uv**. Lint and format with **ruff**. Tests with **pytest** and **Hypothesis**.
- Core libraries: `numpy`, `pyarrow` (schemas, Parquet), `requests`. DuckDB arrives with the first task that queries tables.
- `daschlab` (MIT) is pinned to an exact version in the `spike` dependency group. It is the reference the thin client is tested against, not a runtime dependency of the pipeline.
- Agents run on **NOOA** (Apache-2.0), pinned, and imported in exactly one place (Sprint 8). Everything else is plain Python.
- LLM calls go through OpenRouter, wrapped by our own client that logs tokens and cost and caches every response.
- Storage: committed manifests plus `aws s3 sync` for the data. No DVC.

## Commands

```bash
uv sync                                  # core + dev
uv sync --group spike                    # adds daschlab, astropy, matplotlib, astroquery
uv run pytest                            # fast suite (network tests are excluded)
uv run pytest -m network                 # tests that need the network
uv run ruff check
uv run computers schema check            # lock and docs up to date, no missing version bump
uv run computers thresholds check        # values match their decision records
uv run computers snapshot verify         # raw files still match their manifests
```

## Rules

1. **No LLM calls below L3.** Nothing under `data/`, `l1/`, `l2/`, `bench/` or `telemetry/` imports `nooa`, `litellm`, `openai` or `anthropic`. A test enforces this. L1 also may not import from `l2/` or `crew/`.
2. **Every NOOA method with an `...` body carries `@strategy(PredictStrategy())`.** NOOA's default strategy writes and runs Python, and we keep that off for v1. A test checks it (added with the crew).
3. **Secrets live only in environment variables.** `STARGLASS_API_KEY` and `OPENROUTER_API_KEY`. Never print, log, commit or write a key, and never put one in a request record. A pre-commit check scans for key patterns.
4. **Raw data is immutable.** Nothing edits files under `data/raw/`. The snapshot store creates them once, read-only. See `data-contracts.md`.
5. **Numeric thresholds live in `src/computers/thresholds.py`.** A changed value needs a new decision record. Do not scatter magic numbers through L1.
6. **Table schemas live in `src/computers/data/schemas.py`.** A changed table needs a version bump and a regenerated lock.
7. **LLM results are reproducible.** Cache every response keyed by model, provider and prompt hash. Pin the OpenRouter provider and disable fallbacks.
8. **Failures are recorded, not swallowed.** A broad `except Exception` needs a comment saying why, and the failure must reach a log, a report or a result file.
9. **Keep it simple.** No unrequested refactors or features. Prefer a small function with a test over a framework.

## DASCH API access

- **Public API:** about 1.2 requests per second, then HTTP 429. Use concurrency 1.
- **Full API** (needs `STARGLASS_API_KEY`): 4.5 requests per second at concurrency 3 with no 429s. `pull` accepts `--concurrency 1` to `4`.
- `querycat` takes about 17 s and `queryexps` 16 to 20 s (95th percentile 23 s), against a 30 s gateway timeout. Lightcurves take about 0.5 s.
- Pull a whole tile (1,688 stars in all three) in about 5 minutes. Details: `docs/decisions/0003-tile-sizes-and-access.md`.

## Hooks and enforcement

Kiro command hooks on file save cannot fail loudly, so the real enforcement is tests and pre-commit. Hooks only prompt. See `README.md` for the table.

## Style

- Type hints on public functions. Docstrings say what and why, not how.
- Commit messages say what changed and why. Do not skip pre-commit hooks.
- Prose in docs: plain, specific, no filler. Numbers carry units.
