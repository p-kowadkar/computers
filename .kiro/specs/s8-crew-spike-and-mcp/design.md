# Design: S8 the crew spike, the LLM gateway and the MCP servers

## Layout

```
src/computers/crew/
  schemas.py      Evidence, ContextPacket, Claim, Argument, Verdict (pydantic)
  packet.py       packet v0 builder and the token count
  llm.py          the client: cache, price, cap, log; the shim (OpenAI-compatible, in process)
  validate.py     the claim validator
  base.py         the ONE module that imports nooa; also the in-house fallback base class
  maury.py        Maury on the base chosen at import time or by argument
src/computers/mcp/
  dasch_server.py    get_lightcurve, list_exposures, get_plate_info
  catalogs_server.py vsx_search
  _cache.py          bounded reads of the local tables
tests/unit/          fast tests with a fake transport and no torch, no nooa
tests/golden/llm/    the 20 recorded responses of the exit run
Dockerfile
docs/decisions/0010-crew-gateway.md
```

`crew` may import everything below it and is the only place that imports an agent framework, and inside `crew` only `base.py` does. `mcp/` imports `data` only. The MCP SDK is imported in `mcp/` only.

## The seam

```
Maury (our methods, our prompts, our schemas)
  |
  base.py:  NooaAgent (subclass of nooa.Agent)      FallbackAgent (plain class)
  |                       |                                   |
  |            litellm -> http://127.0.0.1:<port>/v1          |
  |                       |                                   |
  +------------------>  crew/llm.py: complete(...) <----------+
                          cache -> OpenRouter (provider pinned, fallbacks off)
```

Both bases end in `complete()`. If NOOA can take a base URL (spike question 2), the shim is a few dozen lines of `http.server` that parses a chat-completions request, calls `complete()`, and answers in the same format. The shim runs on a random port on localhost for the length of a run and never listens on another interface. If NOOA cannot take a base URL, the decision is yours (requirements, decision 3).

## The client (R2)

`complete(model, messages, *, settings, cap, transport)` returns `Completion(text, tokens_in, tokens_out, cost_usd, cache_key, hit)`.

- **Request.** `{"model": ..., "messages": ..., "temperature": 0, "provider": {"order": [provider], "allow_fallbacks": false}, ...}`. The provider slug comes from the config, and a model without one is refused, because an unpinned model can be served by anyone. The structured-output settings (a JSON schema for `Argument`) are part of the request and so of the hash.
- **Cache key.** `sha256(canonical_json({model, provider, request_without_key}))`, one file per key under `data/llm_cache/<first 2 hex>/<key>.json` holding the response and the usage. It is written once, atomically (write to a temp file and rename), and never edited. A response that did not parse, or an error, is not written.
- **Price.** OpenRouter returns usage and, when asked, the cost of a call. The client stores what the response says. If a response has no cost, the cost is the token counts times the price table in the config, and the row says which. A hit costs 0.
- **Cap.** A run has a `RunBudget(cap_usd)`. Before each uncached call, `spent >= cap` raises `CapReached`, which is logged. The cap is read from the registry (`llm_cap_run_usd`) and not from an argument, so a call site cannot raise it quietly.
- **Log.** One JSON line per call in `data/llm_log/<run_id>.jsonl`: time, model, provider, cache key, hit, tokens in and out, cost, status. No prompt text and no headers.
- **Key.** `OPENROUTER_API_KEY` is read once from the environment inside the transport. Errors name the variable. The request record that is hashed and cached never contains it.
- **Transport.** A small object with a `post(url, headers, json) -> (status, body)` method. The real one uses `httpx` with a 120 s timeout and no retries (a retry would hide a cost). Tests pass a fake.

## Packet v0 (R1)

`build_packet(candidate_row, l1_result, l2_score) -> ContextPacket` with mode `lightcurve_only`. Evidence ids are assigned in a fixed order, block by block.

| Block | Evidence | Source |
|---|---|---|
| 1 Identity | ref number, tile, position (RA, Dec from the stars table), APASS B, the candidate id, the event type, epoch range, amplitude with the lower-limit flag, `single_plate`, the L2 score | stars table, candidate row |
| 2 Century summary | per decade: median, count, scatter of the good detections; per series: zero-point and count | the cleaned curve |
| 3 The event | for the evidence rows of the trigger (capped at 30, the rest summarised): date, series, plate, magnitude, limiting magnitude, tags | the trigger's evidence rows |
| 9 Checklist | each L1 check: `source_split`, `emulsion`, `astrometry`, `blend`, `seam_only`, `single_series_only`, `known_variable`, `detrended`, with pass or flag and the reason | L1 tags and kill reasons |

Token count rule: characters of the compact JSON divided by 3.5, rounded up. It is documented and tested, and it is an estimate. When the estimate exceeds 2,500, the builder first thins block 3 (keep the extremes and an even sample), then block 2 (merge the quiet decades), and records each cut in `packet.cuts`. Block 9 and block 1 are never cut.

The packet text for an agent is rendered from the model in a role order (Maury: 9, 3, 2, 1). Rendering is deterministic, and the rendered text is what is hashed.

## Maury (R3)

```python
class MauryCore:                      # plain Python: prompts, deterministic methods
    SYSTEM = "..."                    # the class docstring of the NOOA class and the system prompt of the fallback
    def known_issue_hits(self, packet) -> list[IssueHit]: ...   # deterministic, from block 9
```

`NooaMaury(MauryCore, NooaAgent)` and `FallbackMaury(MauryCore, FallbackAgent)` add `argue_artifact(packet, hits) -> Argument`. On NOOA it is an ellipsis method with `@strategy(PredictStrategy())` and the docstring as its prompt. On the fallback base it is a method that renders the same prompt, calls `complete()` with the `Argument` JSON schema, parses the reply, and retries once at most when it does not parse (the retry is an uncached second call and is counted).

`MauryCore` holds the prompts as constants shared by both bases, so the two paths differ only in the machinery. The prompt says: argue only from the packet; cite evidence ids such as E-004; the star's event is a candidate; if the evidence does not support an artifact explanation, say what is missing and do not invent. A test checks that the strings are the same on both.

## The validator (R4)

`validate(packet, argument) -> (Argument, list[Removed])`. A claim stays only when `evidence_ids` is non-empty and every id is in `{e.id for e in packet.evidence}`. Order and text are untouched. The count of removed claims is a number the exit report carries, because a path that needs the validator often is a weaker path.

## MCP servers (R5)

Both are written with the official Python SDK (the major version is decided by spike question 6) and run over stdio.

| Tool | Reads | Bounds |
|---|---|---|
| `dasch.get_lightcurve(ref_number, tile, start_year?, end_year?, series?, limit=200)` | silver measurements | at most `limit` rows, evenly thinned if more, with `n_total`, `n_returned` and `thinned` in the reply |
| `dasch.list_exposures(tile, start_year?, end_year?, series?, limit=200)` | silver exposures | same |
| `dasch.get_plate_info(exposure_id)` | silver exposures and plate table | one record |
| `catalogs.vsx_search(ra, dec, radius_arcsec, tile?)` | silver `vsx` | at most 50 rows |

A call for a tile or star that is not local returns `{"error": "not_local", "missing": ..., "pull_with": "computers pull --tile ..."}`. No tool imports `requests` or `httpx`, and a test patches the socket module to fail on any connection to prove it.

## Container (R7)

`python:3.12-slim`, `uv` copied from its official image, `uv sync --group dev --group crew --frozen`, a non-root user, no key and no data in the image, and `CMD ["uv", "run", "pytest", "-m", "not network and not slow"]`. Code as action stays off because every ellipsis method has `PredictStrategy`, and the container is the isolation the plan asks for in case that changes.

## The exit run (R6)

`computers crew spike --model <id> --provider <slug> --n 10` does, in order: print the cap and the model and ask to proceed; build the 10 packets (the top 10 selected stars by L2 score, plus T CrB's eruption as an 11th reference that is reported but not counted); run Maury on NOOA and on the fallback for each; validate; write `data/reports/crew-spike.json` and the text table; copy the cache files of the run into `tests/golden/llm/`. A second invocation reads the cache and must print the same arguments at zero cost (a test).

## Risks

- **NOOA is Alpha (0.0.12).** An API change or a bug can break the NOOA path. The pin and the fallback are the answer, and "NOOA is not worth it" is an allowed finding.
- **The shim is a second implementation of a chat endpoint.** It must handle exactly what NOOA sends (streaming? tool calls? JSON mode?). The spike checks. If NOOA needs streaming or tool calls for `PredictStrategy`, the shim grows, and that is a cost to report.
- **Prompts that differ in a hidden field defeat the cache.** Question 4 of the spike. A test pins that a repeated call is a hit.
- **A model that ignores the JSON schema.** The retry once rule and the count of invalid replies answer it, and 10 of 10 may not be reached on a cheap model. The report says so, and you can choose a better model.
- **Cost.** 20 calls of about 3,000 tokens in and 600 out is under a dollar for any model on a normal price list, so the cap of 2 US dollars is a guard against a loop and not a budget.
- **The structured-output setting is not the same on every provider.** The pinned provider must support it, and the client refuses a model whose config says it does not.
- **Plan wording.** The plan says the crew's agents "see a bounded summary, not a dump". Packet v0 is bounded by construction, and a test pins the cap.

## Spike results (task 1, 2026-11-27)

Run in a throwaway environment with a local fake chat endpoint. No model was called and no key was used.

1. **Install.** `nooa==0.0.12` installs cleanly on Python 3.12: 254 MB in a fresh environment, with `litellm` 1.111.0, `openai` 3.60.0, `pydantic` 2.14.4, `httpx` 0.29 and the OpenInference and OpenTelemetry packages. It is a heavy dependency, kept in its own `crew` group.
2. **Base URL.** Works. `CompletionClient("openai/<model>", api_base="http://127.0.0.1:<port>/v1", api_key=...)` sends `POST /v1/chat/completions` there with an `Authorization: Bearer` header. The shim design stands.
3. **What NOOA sends.** The body has only `model`, `messages`, `response_format` and `prompt_cache_key`. The `response_format` is a strict `json_schema` built from the return type. There is no `temperature`, no `provider` and no `max_tokens`, so **the shim adds temperature 0 and the provider pin** when it forwards. A Pydantic return type comes back as a validated object.
4. **Repeatability.** Two calls differ in exactly one field, `prompt_cache_key` (a fresh UUID per agent instance plus the strategy name). The cache key must drop it. With it dropped the requests are identical.
5. **A bad reply is expensive.** When the reply is not valid JSON, NOOA retries up to **10 attempts** and then raises `nooa.errors.GenerationError`. That is 10 paid calls for one bad reply. `PredictStrategy(config=PredictConfig(max_retries=N, temperature=0))` sets the limit, and it is still a `PredictStrategy`, so the strategy test is unchanged. The crew sets `max_retries=2`, and the cap is the backstop.
6. **MCP SDK.** `mcp` 2.3.0 renamed `FastMCP` to `MCPServer` and changed other APIs. NOOA's own MCP extra needs `mcp<2`. The servers use **`mcp==1.30.0`** (the latest 1.x), so one SDK serves both.
7. **Trace viewer.** `uv add "nooa[cli,viewer]"` then `nooa start-dev` on port 5001. With the viewer off, tracing is silently disabled, as the plan says. To be run in task 8.
8. **Models.** From OpenRouter's live catalogue, with structured outputs, per million tokens in and out: `anthropic/claude-haiku-5.5` 0.10 and 0.50 (provider `anthropic`, structured outputs supported), `google/gemini-2.5-flash-lite` 0.10 and 0.40 (provider `google-ai-studio`), `qwen/qwen3.5-flash-02-23` 0.065 and 0.26 (provider `alibaba`). **Chosen default: `anthropic/claude-haiku-5.5` on provider `anthropic`**, because it is first party, supports the schema, and costs well under a cent per 20 calls. It is a config value.
9. **Trace viewer (spike question 5, answered).** `nooa[cli,viewer]` installs `nooa start-dev`, which serves on port 5001 and **binds to 0.0.0.0**. A single `argue_artifact` call recorded 4 span kinds (`method.argue_artifact`, `generation`, `acompletion`, `context_snapshot`) and 1 `llm_calls` row per model request, in `~/.config/nooa/traces.db`. With the viewer off nothing is recorded. The store is outside the repository and holds prompts. It was deleted after the spike.

## Findings during the build

- **Ruff deletes the `...` body of an agent method** (PIE790) when a docstring exists, and NOOA needs the `...`. It carries `# noqa: PIE790`, and a test checks the body.
- **The strategy test had to be scoped to `Agent` subclasses.** A Protocol stub elsewhere also ends in `...`.
- **A bad reply is cached.** A reply that is non-empty text is a successful call to the gateway, so a bad JSON reply is cached and replays. Each NOOA retry carries the previous reply, so it is a different request with its own entry.
- **Pydantic constraints stay out of the JSON schema.** `min_length` and `ge`/`le` become `minItems` and `minimum`, which strict structured output on some providers refuses. The rules are validators.

