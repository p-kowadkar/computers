# Requirements: S8 the crew spike, the LLM gateway and the MCP servers

**Intent.** Take one agent, Maury the Skeptic, from a context packet to a schema-valid argument, end to end, on a real LLM, with every call cached, priced and capped. Do it on NOOA and on a small in-house base class behind the same interface, so the choice of framework is a measured one. Build the first two MCP servers over data that already exists. This sprint is where L3 starts, so it is where the plan's rules about cost, reproducibility and safety are first tested against a real model.

**Source:** `plan.md` sections 8.1 to 8.6, 4.2 and S8 in section 13; Appendix B; `.kiro/steering/tech.md` rules 1, 2, 3, 7 and 8; `docs/decisions/0004`, `0008`, `0009`. Spec status: **draft, awaiting review**. S7 was cut (`0009`), so this follows S6.

## Scope

In:
- Pydantic schemas for `Evidence`, `ContextPacket`, `Claim` and `Argument` (Appendix B). `Verdict` is declared and not used until Sprint 9.
- A packet builder for **packet v0**, mode `lightcurve_only`: blocks 1, 2, 3 and 9, built deterministically from the L1 and L2 outputs for a candidate, with an evidence id on every fact and a token budget.
- Our own LLM client (`crew/llm.py`): OpenRouter, provider pinned with fallbacks off, temperature 0, an on-disk cache keyed by model, provider and prompt hash, token and cost logging, a hard dollar cap per run, and a refusal to run without a key.
- Maury, with a deterministic `known_issue_hits` and one ellipsis method `argue_artifact`, on NOOA, and the same agent on the fallback base class.
- A deterministic validator that strips claims whose evidence ids are not in the packet.
- Two MCP servers: `dasch` and `catalogs`, with the tools whose data already exists locally.
- A container that runs the crew and its tests, with code-as-action off.
- A decision record `0010` with what was measured: the NOOA answers, the cost, the schema-valid rate on both paths.

Out:
- Payne, Leavitt, Cannon, Hoffleit, the debate loop and the judge (Sprint 9).
- Packet blocks 4 to 8: neighbours, classifier A scores, Gaia and ZTF, catalog types and precedents. They need data and models that do not exist yet.
- Any verdict, any claim that an event is real or an artifact beyond Maury's argued side, and any evaluation of whether Maury is right. There are no gold labels yet.
- MCP tools whose data is not pulled: `get_cutout`, `get_plate_photo_url`, `simbad_lookup`, `gaia_source`, `gaia_timeseries`, `ztf_lightcurve`. They are not registered, so a caller never meets a stub that returns nothing.
- Editing `.kiro/settings/mcp.json`. You asked for that only when you want it (see decision 7).
- The Tabby tile.

## Facts found while drafting this spec

- **NOOA is on PyPI** as `nooa` 0.0.12 (Apache-2.0, Alpha), requires Python 3.12 or 3.13 (the project uses 3.12), and depends on `litellm`, `httpx`, `pydantic`, `anyio` and the OpenInference tracing packages. Its optional extras include `tracing`, `viewer` and `mcp`. So the crew layer will import `nooa` and `litellm`, and the layer rules already forbid both below L3.
- **Because NOOA calls LLMs through litellm,** its calls do not pass through our client by default. The plan wants every response cached by model, provider and prompt hash, and cost logged from our own wrapper. The spike's first question is whether NOOA can be pointed at an endpoint of our choosing. The default design is a small in-process OpenAI-compatible shim that is our client: NOOA, the fallback class and the tests all call one function, so cache, price and cap sit in one place whichever framework runs.
- **OpenRouter pins a provider** with `provider: {"order": [slug], "allow_fallbacks": false}` in the request body (checked against its docs today). Without it the same model name can be served by different providers.
- **The MCP SDK is at 2.3.0, and NOOA's `mcp` extra needs `mcp<2`.** If the crew wants NOOA's MCP client, the servers must be written against 1.x. The spike checks which one we need.
- **`OPENROUTER_API_KEY` is not set in the shell I can see.** I cannot read your profile. Docker 29.7 is installed.
- **The inputs exist.** 17 stars were selected for L3 across the dev tiles (14 on M44, 3 on T CrB), each with a candidate row, an L2 score and L1 kill and flag data. Ten of them are the 10 packets of the exit test. T CrB's 1946 eruption is a known variable, takes no place in the budget, and can still be made into a packet by hand as a reference.
- **There are no gold labels.** The exit test measures validity of form (schema, evidence ids), cost and reproducibility. It says nothing about whether an argument is right.

## Decisions for you (defaults in bold)

1. **The gateway and its cap.** **Default: OpenRouter through our own client, with a hard cap of 2 US dollars per run (`llm_cap_run_usd`, registered in `thresholds.py` with `0010`).** You also set a spend limit on the key itself in the OpenRouter dashboard (the plan's "gateway spend cap before S8"). You set `OPENROUTER_API_KEY` in the environment. I never see or store it.
2. **The model.** The model id is a config value, not a constant. **Default: task 1 lists three candidates that support structured output with their prices per million tokens, and you pick one before the first paid call.** I do not choose a model id from memory.
3. **How NOOA reaches the model.** **Default: through the in-process shim, if NOOA can take a base URL.** If it cannot, the spike says so and you choose: NOOA through litellm with a litellm-level cache and cost callback (a second place that must be kept in step), or drop NOOA and keep the in-house base class. The spec treats the in-house class as the fallback the plan already names, not as a failure.
4. **Scope of packet v0.** **Default: blocks 1, 2, 3 and 9 only, mode `lightcurve_only`, about 2,500 tokens.** Maury sees block 9 (the known-issue checklist) first, as in 8.4.
5. **The claim validator comes now,** in its minimal form (strip any claim whose evidence ids are empty or not in the packet), because "schema-valid" in the exit test needs it. The plan puts it in Sprint 9 with the full crew.
6. **Cache fixtures.** **Default: the 10 recorded responses per path are committed under `tests/golden/llm/`** (small files, no key, no personal data) so the exit test reruns from the cache. The working cache under `data/llm_cache/` is git-ignored.
7. **MCP in Kiro.** **Default: write the config snippet in the README and do not edit `.kiro/settings/mcp.json`.** Say so and I will register both servers.
8. **The container.** **Default: a Dockerfile that installs the project and runs the crew tests, with no key in the image and no network needed for the tests.** I build it and run the tests in it once. It is not pushed anywhere.

## Spike questions (task 1 answers them before anything else is built)

1. Does `uv add --group crew nooa==0.0.12` install cleanly on Python 3.12 next to the existing environment, and what does it pull in (size, `litellm` version)?
2. Can NOOA take a base URL and an API key from us, so that all its calls go to the shim?
3. Does an ellipsis method with `@strategy(PredictStrategy())` return a validated pydantic object, and what happens when the model's reply does not validate (retry, raise)?
4. Does NOOA add anything to the request that changes the prompt hash (trace ids, timestamps) and so defeats the cache? Is a call repeatable?
5. What does the trace viewer record, and what does it need to be running?
6. Which `mcp` major version do the servers use, 1.x or 2.x, and does the 2.x server API differ in a way that matters?

## Acceptance criteria (EARS)

**R1. Schemas and the packet**
1. THE schemas SHALL be pydantic models matching Appendix B. `Claim.evidence_ids` SHALL be non-empty, and `Argument.side` SHALL be `artifact` or `real`.
2. THE packet builder SHALL read a candidate row, the L1 result of its star and its L2 score, and SHALL build a `ContextPacket` of mode `lightcurve_only` containing block 1 (identity), block 2 (century summary and per-series zero-points), block 3 (the points involved in the event) and block 9 (the result of every L1 check, pass or flag).
3. EVERY fact SHALL have an evidence id of the form `E-001`, unique within the packet, and the same candidate and code version SHALL give the same ids and the same packet.
4. THE packet SHALL fit the token budget of about 2,500 tokens, counted by a documented rule. A packet that exceeds it SHALL be reduced by dropping the least important evidence first, never by cutting a fact in half, and the cut SHALL be recorded in the packet.
5. THE packet SHALL NOT contain a raw lightcurve dump. A block 3 point list SHALL be capped, and the cap SHALL be recorded.
6. THE packet SHALL NOT say "vanished", "discovery" or any claim of reality. The word for the star's event is "candidate".

**R2. The LLM client**
1. EVERY call SHALL pass through one function that takes the model, the messages and the settings and returns the text, the token counts and the cost.
2. THE request SHALL pin the provider with `order` and `allow_fallbacks: false` and use temperature 0.
3. THE client SHALL cache every response on disk, keyed by model, provider and a hash of the full request. A repeated request SHALL return the cached response, SHALL NOT touch the network, and SHALL cost nothing.
4. THE client SHALL write one log row per call: the time, the model, the provider, the key of the cache entry, whether it was a hit, tokens in and out, and the cost. THE log SHALL NOT contain the key, the Authorization header or the prompt text.
5. THE client SHALL refuse to start a call when the cost of the run so far has reached `llm_cap_run_usd`, with an error that says so, and SHALL record the refusal.
6. THE client SHALL refuse to run when `OPENROUTER_API_KEY` is not set, with a message that names the variable and not its value.
7. A failed call (network error, an HTTP error, an unreadable reply) SHALL be recorded in the log with its reason and SHALL NOT be cached as a success.
8. TESTS SHALL NEVER call a live model. They SHALL run the client against a fake transport and against the committed cache.

**R3. Maury**
1. THE agent SHALL have a deterministic method `known_issue_hits(packet)` that returns the L1 checks flagged in block 9, without any LLM, and a method `argue_artifact(packet, hits)` completed by the LLM that returns an `Argument` with side `artifact`.
2. THE agent SHALL be implemented twice with the same methods and the same prompts, once on NOOA and once on the in-house base class. A test SHALL show that the two classes have the same public method names and the same deterministic behaviour.
3. EVERY ellipsis method SHALL carry `@strategy(PredictStrategy())`. A test SHALL walk the crew classes and fail when one does not, and SHALL include a class that breaks the rule and is caught.
4. `nooa`, `litellm` and any other agent framework SHALL be imported in exactly one module of `crew/`. A test SHALL enforce that, with a case that breaks it and is caught.
5. THE agent's prompt SHALL tell it to argue only from evidence in the packet and to cite evidence ids, and SHALL say that the star's event is a candidate.

**R4. The validator**
1. THE validator SHALL remove from an `Argument` every claim with no evidence ids or with an id that is not in the packet, and SHALL return the removed claims and the reason for each.
2. WHEN every claim is removed, THE result SHALL be an `Argument` with no claims, marked as empty, and SHALL NOT be an error.
3. THE validator SHALL NOT change the text of a claim it keeps.

**R5. MCP servers**
1. THE `dasch` server SHALL register `get_lightcurve`, `list_exposures` and `get_plate_info`, reading only the local snapshot and silver tables. THE `catalogs` server SHALL register `vsx_search`, reading the local VSX tables.
2. NO tool SHALL touch the network. A tool called for data that is not local SHALL return a structured error that says what is missing and which command pulls it.
3. A tool result SHALL be bounded in size and SHALL say when it was cut and what is left out. It SHALL NOT return a whole 2,900-row lightcurve in one reply.
4. NO tool SHALL be registered for data that is not pulled yet.
5. THE servers SHALL be tested in-process with an MCP client, including a call that fails.
6. `mcp/` SHALL import `data` only, and a test SHALL enforce that, with a case that breaks it and is caught.

**R6. The exit test**
1. WHEN run on 10 packets of the selected stars, THE NOOA path and the fallback path SHALL EACH return a schema-valid `Argument` for 10 of 10, valid meaning that it parses as `Argument`, that every claim cites at least one evidence id, and that every cited id is in the packet. A reply that needs the validator to strip claims SHALL be counted, and SHALL NOT count as valid unless the claims that remain are non-empty.
2. THE live run SHALL be one command, SHALL print the cost against the cap before it starts, and SHALL ask for the model you chose.
3. THE responses of that run SHALL be committed as fixtures, and a second run from the cache SHALL give identical arguments at zero cost.
4. THE report SHALL give tokens in and out and cost per call and in total, per path.

**R7. The container and tracing**
1. THE container SHALL run the crew tests with no network and no key, and SHALL have code-as-action off.
2. THE NOOA trace viewer SHALL be brought up once, and the record SHALL say what it showed. THE cost figures SHALL come from our client and not from the viewer (plan 8.2).

**R8. Rules**
1. NO key, token or Authorization header SHALL appear in a log, a cache file, a fixture, a trace or a record. A test SHALL plant a fake key in the environment and search for it in every file the run writes.
2. `llm_cap_run_usd` SHALL be in `thresholds.py` with a record and the lock updated. `computers thresholds check` SHALL pass.
3. THE fast suite SHALL run without the `crew` dependency group (tests that need it use `importorskip`).
