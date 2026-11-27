# 0010. The crew gateway, Maury and the MCP servers

Date: 2026-11-27. Spec: `.kiro/specs/s8-crew-spike-and-mcp/`. Status: **built, run live once on 2026-11-27, and replayable offline.** The exit test is met on NOOA (10 of 10) and missed on the in-house fallback (9 of 10), for a reason that is now fixed in the default settings. Nothing needs your choice.

## What was decided

1. **One gateway, one place.** Every model call goes through `crew/llm.py`. It pins the provider (`order` and `allow_fallbacks: false`), uses temperature 0, caches every successful response on disk by model, provider and request, logs one row per call without prompt text or key, and refuses a paid call once a run has spent `llm_cap_run_usd`. A cache hit needs no key and costs nothing.
2. **NOOA is routed through a local shim.** NOOA 0.0.12 accepts a base URL, so it calls an OpenAI-compatible endpoint on 127.0.0.1 that answers from the gateway. NOOA gets a placeholder key. The real key stays in the gateway. The fallback base class calls the gateway directly. So both paths share the cache, price log and cap, and a spike question about NOOA's routing is answered yes.
3. **The model.** `anthropic/claude-haiku-5.5` on provider `anthropic`, a config value. It supports structured outputs and costs 0.10 and 0.50 US dollars per million tokens. Two alternatives were priced: `google/gemini-2.5-flash-lite` (0.10 and 0.40, provider `google-ai-studio`) and `qwen/qwen3.5-flash-02-23` (0.065 and 0.26, provider `alibaba`).
4. **Retries are limited.** NOOA retries an unreadable structured reply up to 10 times, each a paid call. The crew sets 2 (`base.PREDICT`). The in-house base retries once.
5. **A reply that is text is cached, valid or not.** Each NOOA retry carries the previous reply, so it is a different request. A rerun reproduces the same failure and the same cost of zero.
6. **MCP SDK 1.30,** not 2.x: 2.x renamed `FastMCP`, and NOOA's MCP extra needs `<2`.
7. **Pydantic constraints are validators,** so the JSON schema has no `minItems` or `minimum` for strict structured output.

## Registered threshold

| Threshold | Value | Status | Meaning |
|---|---|---|---|
| `llm_cap_run_usd` | 2 US dollars | start | The most one run of the crew may spend. The gateway refuses the next uncached call once a run has spent this much. It is a guard against a retry loop and not a budget: 20 calls of about 3,000 tokens in and 600 out cost under a cent on the chosen model. |

## What was measured offline

- The gateway, packet, validator, Maury, spike and MCP tests pass with a scripted model, a fake transport and the in-process MCP client. No test calls a live model.
- Packet v0 for 3 real M44 stars is 2,090 to 2,128 estimated tokens, 70 pieces of evidence each, with block 3 capped at 30 listed points (1,188 to 1,461 in the trigger). The estimate is the characters over 3.5.
- The container image builds and runs the fast suite with no network and no key: 1,132 passed and 14 skipped in 62 s. The image is 2.2 GB.
- T CrB's 1946 eruption is readable through `dasch.get_lightcurve`.

## The live run (2026-11-27, `computers crew spike`)

Model `anthropic/claude-haiku-5.5` on provider `anthropic`. 10 packets (the best-scored stars of the L2 run: M44 and T CrB) and T CrB's 1946 eruption as a reference. `max_tokens` was 1500.

| | NOOA | In-house fallback |
|---|---|---|
| Schema-valid and non-empty after the validator | **10 of 10** | **9 of 10** |
| Replies that needed the validator | 0 | 0 |
| Tokens in, out (the 10 packets) | 60,992 and 6,921 | 59,918 and 11,643 |
| Model calls | 10 | 12 (2 retries) |
| Reference, T CrB 1946 eruption | valid | valid |
| Cost | about $0.010 | about $0.012 |

The whole run, with the reference, is about **$0.023** at the price table. The log said $0.00: see finding 2.

Both paths wrote evidence-cited arguments in which every id exists in its packet (checked by a test over the committed fixtures). The reference argument is the most useful read: for T CrB's real 1946 eruption Maury argues artifact on both paths, citing the soft-rejected points (23 of 26), the single unknown emulsion, a 3-plate `ee` zero-point, and "not detrended and a known variable". That is the Skeptic's job. Its weak point is that the NOOA path calls the event "the apparent dip" in one claim, which is wrong for an outburst, and the validator cannot catch it because it checks ids and not meaning. The judge and the full crew (Sprint 9) have to.

## Findings from the live run

1. **The one miss was `max_tokens`, not the model's judgement.** 3 of the 12 fallback replies stopped at the limit of 1500 tokens. Two were cut-off JSON, which the retry rescued. One came back empty (all 1500 tokens used before any text), so `the reply was empty` failed that packet (`38ecda2a7c6f3e28`). Visible text for these replies is about 1,000 to 2,000 characters, so the rest of the budget went on something that is not in the reply, most likely hidden reasoning. The NOOA path averaged 690 tokens out and never reached the limit. The fallback prompt is plainer, and the model wrote more. The code default is now 4,000 tokens and the setting is on the command line (`--max-tokens`, `--reasoning off`).
2. **OpenRouter reported a cost of 0 for 120,000 tokens** (credits or a bring-your-own-key route can do that). A cap that trusts 0 never fires. The gateway now treats a reported 0 with tokens used as unknown and uses the price table, and the log says which (`cost_source`: reported or estimated). A test pins it.
3. **A failed call can still cost.** The empty reply used 1500 tokens. It is now charged to the run's budget and logged with its cost, and still not cached.
4. **The exit test is not met on the fallback as run,** and I am not rewriting it. The fixtures are the run as it happened (`max_tokens` 1500, 9 of 10), recorded in `tests/golden/llm/run.json`, and they replay offline. A run with the new default writes new cache keys, so it needs a new live run, about two cents. I have not repeated it: the plan's gate is the framework decision, and this answers it.

## The framework decision (what the spike was for)

NOOA is **usable** and I keep both paths. It reached 10 of 10 where the in-house base reached 9, on the same prompts, the same gateway and the same model. The cost of NOOA is a 254 MB dependency, an Alpha version, a retry default of 10 paid calls (set to 2) and a trace viewer that binds to all interfaces. The in-house base is 40 lines and lets us see every request. Neither is clearly better on this evidence, and the seam is cheap, so the plan's choice stands: **agents on NOOA, with the in-house base as the tested fallback.** Re-run the comparison at Sprint 9 when there is a judge and a debate, because that is where a framework would earn or lose its place.

## Next

Sprint 9 builds the full crew, the debate and the claim validator's full form. It needs the packet blocks it lacks (neighbours, Gaia, catalogs, precedents) only as far as they exist. Blocks 4 to 8 are not built and the spec for S9 must say which ones it adds.
