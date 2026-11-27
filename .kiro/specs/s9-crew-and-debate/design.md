# Design: S9 the full crew, the debate and the case files

## Layout

```
src/computers/crew/
  packet.py        packet v1: adds block 4 and block 7 and the not-available list      (changed)
  schemas.py       adds CaseFile, Rebuttal, PolicyEvent                                  (changed)
  validate.py      adds the forbidden-word rule and two report-only checks              (changed)
  agents.py        MethodSpec, the prompts of every role as constants, and the cores
  base.py          adds a generic in-house `call(spec, **inputs)` (still the only framework import)
  nooa_agents.py   the NOOA classes: Leavitt, Maury, Payne, Cannon, Hoffleit ellipsis methods
  debate.py        the debate loop, the policy rules, the failure handling
  casefile.py      Hoffleit's deterministic assembly, JSON and Markdown writers, the review queue
  fleming.py       candidate selection and packet building, no LLM
  run.py           `computers crew run`, the report and the comparison of the two bases
  maury.py, maury_nooa.py   replaced by the generic cores in agents.py; kept as thin aliases for the Sprint 8 tests
configs/crew/crew.json    the model and settings of each role
tests/golden/llm/s9/      the recorded responses of the run (if under 5 MB)
docs/decisions/0012-crew-debate.md
```

## Roles and their methods

| Role | Deterministic | LLM method (one per role, `PredictStrategy`) |
|---|---|---|
| Fleming | `select(n)`, `build_packet(c)` | none |
| Leavitt | `describe(packet)`: kind, epoch range, amplitude and lower-limit flag, duration, series count, with evidence ids | `describe_shape(packet_text, description) -> ShapeNote`, only when unusual |
| Maury | `known_issue_hits(packet)` | `argue_artifact(...) -> Argument`, `rebut_artifact(...) -> Rebuttal` |
| Payne | `context_hits(packet)`: VSX entry, brightness and colour, detrending, series coverage, all with ids | `argue_real(...) -> Argument`, `rebut_real(...) -> Rebuttal` |
| Cannon | `evidence_table(packet)` | `judge(table, maury_claims, payne_claims, rebuttals) -> Verdict` |
| Hoffleit | `assemble(...)` the case file | `write_summary(case_json) -> Summary` (at most 80 words) |

Eight ellipsis methods in all. Every one is `async`, takes strings or small pydantic models, returns a pydantic model, carries `@strategy(PredictStrategy(config=PREDICT))` and has the literal prompt as its docstring. The prompts are constants in `agents.py` (`SPECS: dict[str, MethodSpec]`, where a `MethodSpec` holds the system text, the task text, the return type and the argument names). A test compares each NOOA docstring with its spec, so the two bases cannot drift.

## The two bases

- **NOOA:** `nooa_agents.py` has one class per role, built from the cores. The shim and gateway of Sprint 8 are unchanged.
- **In-house:** `base.FallbackAgent.call(spec, **inputs)` renders the task text and the inputs the way Sprint 8's `FallbackMaury` does, asks the gateway for JSON that matches `spec.returns`, parses, retries once. One generic method replaces a hand-written method per role.
- `debate.run(candidate, agents, ...)` takes an `Agents` object that has the same method names on either base. The debate never knows which base it runs on.

## Packet v1 (R1)

Block 4, **neighbours**, from `detrend.offsets_for(ctx, ref)` and the curve: `pool_size`, `colour_cut`, `detrended`, and for the plates of the event the plate offset (median of comparison residuals) and the comparison count, plus the median and the 95th percentile of the absolute plate offsets over all plates for scale. Evidence kinds: `ensemble` (one item) and `event_plate_offsets` (capped at 15 plates, the largest offsets and an even sample). A star with `few_comparisons` gets one `ensemble` item that says why it was not detrended. The builder needs the tile context, so `build_packet` takes an optional `ensemble` argument built by Fleming.

Block 7, **catalogs**, from `vsx_match` (the local table): name, type, period, amplitude and separation in arcsec, or `{"vsx": "no match within <radius> arcsec"}`.

`packet.unavailable = ["5 image evidence", "6 modern era", "8 precedents"]` and a line in the rendering. `packet_version` becomes `v1`; the model accepts `v0` and `v1`.

Order of reading: Maury 9, 4, 3, 2, 1 (the plan says 9, 5, 4 first, and 5 does not exist). Payne 3, 7, 4, 2, 1 (the plan says 3, 6, 7). Cannon reads an evidence table (id, block, kind, a one-line fact) plus the validated claims. Block 1, 4, 7 and 9 are never cut by the budget; the budget is raised to 3,000 tokens for v1 because two blocks were added, and recorded as such.

## The debate (R3)

```
packet = fleming.build(candidate)
desc   = leavitt.describe(packet)                      # deterministic
note   = await leavitt.describe_shape(...)             # only if unusual
a_m    = await maury.argue_artifact(...)               # packet text, hits
a_p    = await payne.argue_real(...)                   # packet text, context hits
v_m, v_p = validate(packet, a_m), validate(packet, a_p)     # strips; records
if shared_ids(v_m.argument, v_p.argument):             # the only trigger for a rebuttal
    r_m = await maury.rebut_artifact(v_m.argument, v_p.argument)
    r_p = await payne.rebut_real(v_p.argument, v_m.argument)
    r_m, r_p = validate_rebuttals(...)
verdict = await cannon.judge(table, v_m.argument, v_p.argument, rebuttals)
verdict, events = policy(packet, verdict)              # disappearance cap, class and cause, unknown ids
case    = hoffleit.assemble(...); summary = await hoffleit.write_summary(case)
```

- **Shared ids** is a deterministic conflict test: two sides citing the same fact are arguing about it. If they cite disjoint ids there is nothing to rebut.
- **Cannon never sees removed claims.** The removed claims are shown in the case file, struck through, as plan 11.3 asks.
- **Policy rules** are a pure function `policy(packet, verdict) -> (verdict, [PolicyEvent])`. The class and cause table: `real_event` with `real_outburst`, `real_dip` or `real_longterm`; `artifact` with any other cause except `unknown`; `inconclusive` with `unknown`. A mismatch costs one repair call to Cannon with the error text, then a fallback to `inconclusive`.
- **Failures:** each agent call is wrapped; the exception type and a 160-character message go into the case file's `failure` field, the debate stops for that candidate, the run goes on. A cap stops the whole run cleanly after writing what it has.

## The validator (R4)

`validate(packet, argument, *, checks=Checks())` where `Checks` has `forbidden_words: bool = True`, `numeric: Literal["off","report","remove"] = "report"`, `kind: Literal["off","report","remove"] = "report"`.

- **Numeric grounding.** Extract numbers from the claim with a regular expression (an optional sign, digits, an optional decimal part; ignore evidence ids like `E-012` and years written as four digits that appear in the claim's own epoch words). A number is grounded when, in the payload of at least one cited evidence item, some value or a tuple of values rounds to the same number at the precision written. A claim with an ungrounded number would be removed. Counts of items ("29 of 40") are grounded by `flagged` and `of` fields.
- **Kind mismatch.** The words `outburst`, `dip`, `step`, `fade`, `fading`, `long-term`, `disappearance` are matched case-insensitively as the *subject* "event ... is a(n) X" is hard to parse, so the rule is the simple one: if the claim names one event kind word and the packet's kind is a different one and the claim does not also name the packet's kind, it is mismatched. Report-only by default.
- Both checks return the claims they would remove with a reason, so the 50-candidate report can count them and Sprint 10 can switch them on if they help. A check that would remove a claim an annotator calls good is a cost, and Sprint 10 weighs it.

## Case files (R5)

`CaseFile` (pydantic) holds: `headline`, `candidate` (packet identity and event), `evidence` (the table), `arguments` (Maury, Payne, rebuttals), `validator` (kept and removed, with reasons, and the report-only findings), `verdict`, `policy_events`, `summary`, `provenance`, `unavailable` and `failure`. `casefile.write(case, dir)` writes `<candidate_id>.json` and `<candidate_id>.md`. The Markdown follows plan 11.3 in order, with the removed claims struck through and a line at the top that says "candidate, not a result". The headline is built from fields, never from a model reply, and a test scans every file for "vanished". The review queue is a JSON-lines file with one row per `inconclusive` budget-selected candidate.

Provenance: snapshot digest from the snapshot store, code version, packet version, per call the model, provider, cache key, prompt hash (the cache key), tokens, cost and whether it was a hit.

## The run (R7)

`computers crew run --n 50 --base nooa|fallback [--yes] [--cache-dir] [--fixtures]`. Fleming picks the candidates (17 selected, then the best-scored others, labelled `reference`), builds the packets once (a local, deterministic step that also writes `data/gold/packets/`), then judges them with at most 4 candidates in flight. The gateway is thread-safe already, and the shim handles concurrent requests. The report is `data/reports/crew-run-<base>.json` plus a text table, and `crew compare` reads both. Fixtures: the cache entries of both runs and the packets are copied to `tests/golden/llm/s9/` when under 5 MB.

## Risks

- **Verdicts are not scored.** A reader will want to read "the system judged 50 candidates" as "the system is right about 50 candidates". The report and the case files say they measure form and cost. The reference list (T CrB and the other known variables) is shown as a reading aid, and a debate that calls T CrB's eruption an artifact is a finding to carry to Sprint 10, not a pass or a fail here.
- **Eight LLM methods on two bases is a lot of surface.** The generic spec table and the docstring-equality test keep it to one definition per method. If NOOA's ellipsis methods cannot take a pydantic argument cleanly, the inputs are strings, as in Sprint 8.
- **Rebuttals may loop or drift.** One round, and a rebuttal that cites an unknown id is stripped by the same validator.
- **Cannon's repair call is a paid retry.** It happens at most once per candidate, and it is counted.
- **Payne with no modern data.** Payne may have little to argue from for a quiet-looking event. The prompt says to state what is missing. A weak Payne argument is information.
- **Numeric grounding is fragile.** Rounding and units vary. That is why it runs in report-only mode first.
- **Cost.** Estimated 5 to 8 calls per candidate, 50 candidates, two bases: 500 to 800 calls, well under $1. The per-run cap of $2 stays.
- **Two sources of nondeterminism:** NOOA's `prompt_cache_key` (dropped from the cache key already) and concurrency order. The cache key does not depend on order, and the report sorts by candidate id.
