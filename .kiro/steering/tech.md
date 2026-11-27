---
inclusion: always
---

# Tech stack and rules

## Stack

- **Python 3.12**, managed with **uv**. Lint and format with **ruff**. Tests with **pytest** and **Hypothesis**.
- Core libraries: `numpy`, `pyarrow` (schemas, Parquet), `requests`. DuckDB arrives with the first task that queries tables.
- `daschlab` (MIT) is pinned to an exact version in the `spike` dependency group. It is the reference the thin client is tested against, not a runtime dependency of the pipeline.
- Agents run on **NOOA** (Apache-2.0, 0.0.12, pinned in the `crew` group) and are imported in exactly one module, `crew/base.py`, which also holds the in-house fallback base. A test enforces it. Everything else is plain Python.
- LLM calls go through OpenRouter, wrapped by our own client that logs tokens and cost and caches every response.
- Storage: committed manifests plus `aws s3 sync` for the data. No DVC.

## Commands

```bash
uv sync                                  # core + dev
uv sync --group spike                    # adds daschlab, astropy, matplotlib, astroquery
uv sync --group ml                       # adds PyTorch (E1 training)
uv sync --group crew                     # adds NOOA, pydantic, httpx (the crew)
uv sync --group mcp                      # adds the MCP SDK (the servers)
docker run --rm --network none computers-crew   # the fast suite in the container
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

## The crew gateway (Sprint 8)

- **Every model call goes through `crew/llm.py`** (`Gateway.complete`). It pins the provider with `allow_fallbacks: false`, uses temperature 0, caches every successful response by model, provider and request, logs one row per call (no prompt text, no key) and stops at `llm_cap_run_usd`.
- **NOOA reaches the model through a local shim** (`Shim`, 127.0.0.1) that answers from the gateway, so the cache, price log and cap apply to NOOA as well. NOOA is given a placeholder key. The real key lives only in the gateway's environment.
- **NOOA retries a bad structured reply up to 10 times by default,** and each retry is a paid call. The crew sets `PredictConfig(max_retries=2, temperature=0)` (`base.PREDICT`).
- **The NOOA trace viewer binds to all interfaces** (0.0.0.0:5001) and keeps traces in `~/.config/nooa/traces.db`. Start it only when you need it, on a trusted network, and stop it after. Cost figures come from our gateway, not from traces.
- **MCP:** the SDK is 1.30 (`FastMCP`). Version 2 renamed it and NOOA's MCP extra needs `<2`.

