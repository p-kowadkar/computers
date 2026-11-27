# Tasks: S8 the crew spike, the LLM gateway and the MCP servers

- [x] 1. Spike: answer the six questions, price the models
  - In a throwaway environment (not the project's), install `nooa==0.0.12` and answer the six spike questions of the requirements: install, base URL, validated return and failure behaviour, repeatability of the request, the trace viewer, the MCP SDK major version. List three candidate models with structured-output support, the provider slug and the price per million tokens. Write the answers into `design.md`. **Stop and report if NOOA cannot be routed through our client**, with the choices of requirements decision 3. You pick the model before any paid call.
  - _spike questions, decisions 2 and 3_

- [x] 2. Dependencies, layer rules and the registry
  - A `crew` dependency group with `nooa`, `pydantic` and `httpx` pinned exactly, and the `mcp` SDK pinned in its own group. Layer tests: `nooa` and `litellm` imported only in `crew/base.py`; `mcp/` imports `data` only; both with a case that breaks the rule and is caught. Register `llm_cap_run_usd` (2.0) with record `0010` and the lock.
  - _R3.4, R5.6, R8.2, R8.3_

- [x] 3. Schemas and the packet
  - `crew/schemas.py` and `crew/packet.py`: packet v0, evidence ids, the token estimate and the cuts. Tests: same input gives the same packet and ids, ids are unique, every block's facts have ids, the budget is met and the cuts are recorded, block 1 and 9 are never cut, no raw dump (block 3 capped), no "vanished" or discovery wording, `Claim` rejects empty evidence ids.
  - _R1_

- [x] 4. The LLM client and the shim
  - `crew/llm.py`: request, cache, price, cap, log, key handling, the fake transport. Tests: the provider is pinned in the request, a repeat is a hit with no transport call and zero cost, a failure is logged and not cached, the cap stops the next call (both sides of the boundary), a missing key names the variable and not a value, no key or prompt text in the log, an atomic cache write. A test plants a fake key and searches every file the run wrote.
  - _R2, R8.1_

- [x] 5. The validator, Maury and the two bases
  - `crew/validate.py`, `crew/base.py`, `crew/maury.py`. Tests with a scripted fake model: valid and invalid replies, a reply with an unknown id (stripped and counted), a reply with no valid claims (empty, not an error), the same prompts on both bases, the same deterministic `known_issue_hits`, the strategy rule on every ellipsis method with a breaking case, the retry-once rule on the fallback.
  - _R3, R4_

- [x] 6. MCP servers
  - `mcp/dasch_server.py`, `mcp/catalogs_server.py`, the bounded reads. In-process client tests: each tool, a bounded reply with the cut reported, a not-local error, no network (the socket is patched to fail), and no tool registered for data that is not local.
  - _R5_

- [x] 7. The container
  - Dockerfile; build it and run the crew tests inside with no network and no key. Record the image size and the time.
  - _R7.1, decision 8_

- [x] 8. The live run (needs your key, cap and model)
  - `computers crew spike`: 10 packets on both paths, the T CrB reference, the cost table. Copy the cache files to `tests/golden/llm/`. Rerun from the cache and show zero cost and identical arguments. Bring up the trace viewer once and write down what it shows.
  - _R6, R7.2_

- [x] 9. Decision record and docs
  - `0010` with the NOOA answers, the schema-valid rates per path, the validator's removals, tokens and cost; `plan.md` (8.2, 8.5, changelog, S8 row), `tech.md` and `structure.md` if rules changed, the README snippet for Kiro's MCP config.
  - _plan changelog_

- [x] 10. Exit test and report
  - `uv run pytest` (with and without the `crew` group), `ruff`, secret scan, schema, thresholds, snapshot and `bench check`, `pre-commit run --all-files`, the container test. Show: 10 of 10 on each path or the honest count, cost, the NOOA findings. Report and wait before Sprint 9.
