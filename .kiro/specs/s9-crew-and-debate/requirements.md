# Requirements: S9 the full crew, the debate and the case files

**Intent.** Turn one candidate into a case file: a packet, an event description, the Skeptic's argument, the Advocate's argument, the claims the validator removed, a verdict with a cause and a rationale, and its provenance. Run it on 50 real candidates from the dev tiles, on NOOA and on the in-house base, and measure form, cost and reproducibility. This sprint does **not** measure whether the verdicts are right: there are no gold labels yet, and that is Sprint 10.

**Source:** `plan.md` sections 8.1 to 8.4, 8.6, 11.3 to 11.5, Appendix B and S9 in section 13; `docs/decisions/0004`, `0008`, `0010`. Spec status: **draft, going ahead under the standing instruction to follow the plan** (nothing here departs from it; the departures are listed and small).

## Scope

In:
- **Packet v1**: packet v0 plus block 4 (neighbours: how the comparison ensemble behaved on the event's plates) and block 7 (catalogs: the local VSX match). Blocks 5, 6 and 8 are not available and the packet says so.
- **Six agents** as plan 8.1: Fleming (patrol, no LLM), Leavitt (event description, one LLM method used only for unusual shapes), Maury (done), Payne, Cannon, Hoffleit (case file, one light LLM method).
- **The debate** (`crew/debate.py`, plain Python): Leavitt, Maury, Payne, the validator, at most one rebuttal each when the arguments cite the same evidence, Cannon, Hoffleit.
- **The validator in full**: ids as in Sprint 8, plus a forbidden-words rule, plus two optional checks that are measured and left off by default (numeric grounding, event-kind mismatch).
- **Case files**: JSON and Markdown, one per judged candidate, with every item of plan 11.3 that exists today.
- **A review queue**: every `inconclusive` verdict goes to a file for the review UI of a later sprint.
- **The run**: 50 candidates on NOOA and on the in-house base, with cost, verdict counts and agreement.
- A decision record `0012`.

Out:
- Gold labels, verdict accuracy, the debate ablation, the packet-block ablation, calibration of confidence (Sprint 10 and later).
- Packet blocks 5 (classifier A), 6 (Gaia and ZTF), 8 (precedents).
- The student verdict model (Track B). Cannon is the same LLM as the others until it exists.
- The Observatory site, plots, the blink comparator and the plate photo (Sprint 18). The case file lists them as not yet available.
- Changing L1 or L2, or the benchmark.
- The Tabby tile. It is frozen and has not been run. No Tabby candidate is judged here.

## Facts found while drafting this spec

- **There are 91 candidate rows on the dev tiles** (M44 65, T CrB 26) and 30 stars. 17 stars were selected for L3 by the budget. 50 candidates therefore means all 17 selected ones plus 33 more rows by L2 score (known variables and second triggers of selected stars). The extra rows are labelled `reference` in the case file, because the budget would not send them. This is a stand-in until the review UI produces gold.
- **Sprint 8 found a semantic slip the id check cannot catch:** the NOOA Maury called T CrB's outburst "the apparent dip". Wording of that kind is the judge's problem, and a deterministic kind-mismatch check can report how often it happens.
- **Cost is small.** A Maury call costs about $0.001. Five to seven calls per candidate on 50 candidates on two bases is well under $1, against a $2 per-run cap.
- **Gaia and ZTF are not in the packet.** DR4 is due 2 Dec and its tables are unchecked. Payne's "astrophysical context" is therefore the VSX entry, the star's colour and brightness, the detrending behaviour and the series coverage. The packet and the prompts say that modern photometry is not available, so an agent never assumes it.
- **Payne's role is not a rubber stamp.** Both advocates are told to argue from evidence only and to say what is missing. The debate has to justify itself by an ablation in Sprint 10, and may be cut.
- **The test split is frozen** (`0005`). Nothing in this sprint touches it.

## Decisions (defaults, taken)

1. **Which candidates.** The 17 budget-selected stars first, then the best-scored remaining candidate rows of the dev tiles, 50 in all.
2. **One model for every role** for now: `anthropic/claude-haiku-5.5` on provider `anthropic`, `max_tokens` 4,000, as a config value per agent. A different model for Cannon is a Sprint 10 question.
3. **Rebuttals.** One round of at most one rebuttal each, only when the two validated arguments cite at least one evidence id in common. Registered as `debate_max_rebuttals` (1, start).
4. **When Leavitt calls the LLM.** Only when the event kind is `long_term`, `step` or `disappearance`, or the star has `leavitt_unusual_min_triggers` (3, start) or more surviving triggers. Otherwise the description is deterministic.
5. **Disappearance is capped.** A disappearance candidate can never get `real_event` here: the plan requires cutouts from 3 or more plates, absence from Gaia and a human sign-off first (6.6). A deterministic rule overrides Cannon to `inconclusive`, logs the override and says why in the case file. The word "vanished" never appears in a headline.
6. **Validator extras are measured and left off.** Numeric grounding and kind-mismatch run in "report only" mode on all 50 candidates (they count what they would remove) and are switched on only if Sprint 10 shows a benefit. This follows principle 2: no component merges without a benchmark delta.
7. **The review queue** takes every `inconclusive` verdict of a budget-selected candidate. No new threshold is needed.
8. **The base.** NOOA is the primary base, and the same 50 candidates run again on the in-house base. The report compares them (this is the re-comparison `0010` promised).

## Acceptance criteria (EARS)

**R1. Packet v1**
1. THE packet SHALL add block 4 for a candidate: the size of the comparison pool, whether a colour cut was applied, and for the plates of the event the plate offset applied and the number of comparison stars that measured each plate, with the spread of offsets over all plates for scale. WHEN the star could not be detrended, block 4 SHALL say so and give the reason.
2. THE packet SHALL add block 7: the VSX match (name, type, period, amplitude, separation) or an explicit statement that there is no match within the radius.
3. THE packet SHALL list the blocks that are not available (5, 6 and 8) in a field and in its rendering, so no agent assumes them.
4. THE packet SHALL stay within the token budget, and blocks 1, 9, 4 and 7 SHALL never be cut. THE version SHALL be `v1`, and a packet of version `v0` SHALL still load.
5. THE same candidate and code version SHALL give the same packet (as in Sprint 8).

**R2. Agents**
1. EACH LLM agent SHALL have its prompts as constants in a framework-free core, and a NOOA class and an in-house class that use the same constants. A test SHALL show the prompts are equal, as for Maury.
2. EVERY ellipsis method SHALL carry `@strategy(PredictStrategy(...))`, and the strategy test of Sprint 8 SHALL cover the new classes.
3. **Fleming** SHALL select the candidates of a run, build their packets and hand them on, with no LLM call.
4. **Leavitt** SHALL describe the event deterministically (kind, epoch range, amplitude with the lower-limit flag, duration, number of series) and SHALL call `describe_shape` only under decision 4. The description SHALL cite evidence ids.
5. **Payne** SHALL argue that the event is real from the packet only, SHALL have a deterministic `context_hits(packet)` (VSX match, brightness and colour, detrending, series coverage) with no LLM, and SHALL cite evidence ids.
6. **Cannon** SHALL read the evidence table and the validated claims of both sides, and SHALL return a `Verdict` with a verdict class, a cause from the Appendix B list, a confidence, the evidence ids relied on and a rationale of at most 80 words.
7. **Hoffleit** SHALL assemble the case file deterministically and SHALL use one LLM method only for a plain-language summary of at most 80 words.
8. EVERY prompt SHALL call the event a candidate, SHALL tell the agent to argue only from the packet, and SHALL state that modern photometry and images are not available in this packet.

**R3. The debate**
1. THE debate SHALL run in this order: Leavitt, Maury, Payne, the validator on both arguments, the rebuttals (if any), the validator on the rebuttals, Cannon, the deterministic policy rules, Hoffleit.
2. THE rebuttal rule SHALL be: WHEN the validated arguments cite at least one evidence id in common, each side SHALL write one rebuttal that sees the other side's validated claims. OTHERWISE no rebuttal SHALL be written.
3. CANNON SHALL see the evidence table and the validated claims only. Claims the validator removed SHALL NOT reach Cannon.
4. THE policy rules SHALL apply after Cannon, in this order, and each SHALL be recorded when it fires: (a) a disappearance event cannot be `real_event`; (b) a verdict class and its cause must agree (`real_event` takes a `real_*` cause, `artifact` takes a non-`real_*` cause, `inconclusive` takes `unknown`); a disagreement SHALL be repaired once by asking Cannon again with the error, and if it persists the verdict SHALL become `inconclusive` with cause `unknown`; (c) every evidence id Cannon cites must exist in the packet, and an unknown id SHALL be removed from the verdict.
5. A failure of any agent (an error, an empty reply, a cap) SHALL end that candidate's debate with a recorded failure and a case file that says so. It SHALL NOT stop the run.
6. THE total number of model calls for a candidate SHALL be recorded.

**R4. The validator**
1. THE validator SHALL keep the Sprint 8 rule (a claim must cite at least one id and every id must be in the packet).
2. THE validator SHALL remove any claim whose text contains "vanished", "discovery", "discovered" or "confirmed" (case-insensitive) and SHALL record the reason `forbidden_word`.
3. THE validator SHALL have two checks that run in report-only mode by default: **numeric grounding** (every number of two or more digits or with a decimal point in a claim must appear, to the precision written, in the payload of at least one cited evidence item) and **kind mismatch** (a claim that names an event kind, such as outburst, dip, step, fade or disappearance, must name the packet's own kind). In report-only mode the check SHALL list the claims it would remove and SHALL remove none.
4. EACH check SHALL be switchable by a setting, SHALL be counted per run, and SHALL have tests on both sides of its rule, including a case that passes.
5. THE validator SHALL NOT change the text of a kept claim.

**R5. Case files**
1. EACH judged candidate SHALL have a case file in JSON and in Markdown under `data/gold/case_files/`, with: the headline (star, event type, dates, amplitude as a lower limit where it is one, verdict and confidence); the evidence table with ids; Maury's and Payne's arguments and rebuttals; the claims the validator removed, with reasons; Cannon's verdict and rationale; the policy rules that fired; the provenance; and the list of items not yet available (century plot, blink comparator, plate photo).
2. THE provenance SHALL give the snapshot digest, the code version, the packet version, the model id and provider of every agent, the prompt hash of every call, the number of calls, tokens and cost, and which base ran.
3. THE case file SHALL show the argument against (Maury's) and the argument for (Payne's) whatever the verdict is.
4. THE word "candidate" SHALL be used. "Vanished" SHALL NOT appear in any headline or summary, and a test SHALL scan every generated case file.
5. A candidate that was not sent by the budget SHALL be marked `reference` and SHALL say so in the headline.

**R6. The review queue**
1. EVERY `inconclusive` verdict of a budget-selected candidate SHALL be appended to `data/gold/review_queue.jsonl` with the candidate id, the star, the L2 score, the verdict and the open questions Cannon named.

**R7. The run and the report**
1. `computers crew run --n 50 --base nooa|fallback` SHALL judge the candidates and write the case files and a report. IT SHALL print the model, the cap and the number of candidates before any paid call.
2. THE report SHALL give, per base: candidates judged, failures with reasons, verdict counts, cause counts, the share of debates with a rebuttal, claims removed by the validator and by each report-only check, calls, tokens and cost, and the policy rules that fired.
3. THE report SHALL compare the two bases on the same candidates: the share with the same verdict class, the share with the same class and cause, and the cost of each.
4. THE report SHALL state that it measures form, cost and reproducibility and not accuracy, and SHALL list T CrB's 1946 eruption and each known variable among the candidates with their verdicts, as a reading aid and not as a score.
5. THE responses SHALL be cached as in Sprint 8, a second run from the cache SHALL give the same case files at zero cost, and the recorded responses of the 100 runs SHALL be committed as fixtures if their size is under 5 MB.

**R8. Rules**
1. `crew/` SHALL keep one module that imports an agent framework. THE layer tests SHALL still pass.
2. NO key, token or Authorization header SHALL appear in a case file, a log, a cache file, a fixture or a report. A test SHALL plant a key and search.
3. EVERY new number SHALL be in `thresholds.py` with `0012` and the lock updated (`debate_max_rebuttals`, `leavitt_unusual_min_triggers`).
4. THE fast suite SHALL run without the `crew` group, and tests that need it SHALL skip.
