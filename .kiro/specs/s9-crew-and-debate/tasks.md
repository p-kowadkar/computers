# Tasks: S9 the full crew, the debate and the case files

- [ ] 1. Packet v1
  - Block 4 (ensemble behaviour) and block 7 (VSX), the unavailable list, the 3,000-token budget and the never-cut blocks. A v0 packet must still load. Tests: determinism, ids, blocks present, a star with `few_comparisons`, no VSX match, a cut that spares blocks 1, 4, 7 and 9, and the unavailable line in the rendering.
  - _R1_

- [ ] 2. Registry and layer rules
  - Register `debate_max_rebuttals` (1) and `leavitt_unusual_min_triggers` (3) with `0012` and the lock. Extend the strategy test to the new class files.
  - _R8.3, R2.2_

- [ ] 3. Prompts and the generic fallback call
  - `agents.py` with `MethodSpec` and the eight specs, and `FallbackAgent.call(spec, **inputs)`. Tests: every spec names a pydantic return type, the prompts say candidate, packet-only and "modern photometry and images are not available", and the fallback parses, retries once and fails after.
  - _R2.1, R2.8_

- [ ] 4. The NOOA agents and the cores
  - `nooa_agents.py`: the eight ellipsis methods with docstrings equal to the specs (a test), and the deterministic methods on the cores (`describe`, `context_hits`, `evidence_table`, `known_issue_hits`). Keep the Sprint 8 Maury tests green.
  - _R2_

- [ ] 5. The validator in full
  - Forbidden words, numeric grounding and kind mismatch, each with `off`, `report` and `remove`. Tests on both sides of each rule, a passing case for each, report-only removes nothing, text is unchanged.
  - _R4_

- [ ] 6. The debate and the policy rules
  - `debate.py`: the order, the shared-id rebuttal rule, Cannon seeing only validated claims, the policy function (disappearance cap, class and cause table, unknown ids), the repair call, failure handling. Tests with a scripted model for every branch, including a cap mid-run.
  - _R3_

- [ ] 7. Case files and the review queue
  - `casefile.py`: the schema, the JSON and Markdown writers following plan 11.3, the review queue, the "vanished" scan. Tests: every section present, removed claims struck through, reference marked, provenance complete, no key.
  - _R5, R6_

- [ ] 8. Fleming and the run command
  - `fleming.py`, `computers crew run` and the comparison report. Tests on saved packets with a scripted model: the same run twice from the cache gives the same files at zero cost, and a failed candidate does not stop the run.
  - _R2.3, R7_

- [ ] 9. The live runs (needs the key)
  - 50 candidates on NOOA, then on the in-house base, then the comparison. Commit the responses if under 5 MB. Write the numbers into `0012`.
  - _R7_

- [ ] 10. Decision record and docs
  - `0012`, `plan.md` (8.1, 8.3, 8.4, changelog, S9 row), `tech.md`, `structure.md`, `astro-domain.md` if findings change code, the README.
  - _plan changelog_

- [ ] 11. Exit test and report
  - `uv run pytest` (with and without the groups), `ruff`, secret scan, schema, thresholds, snapshot and `bench check`, `pre-commit run --all-files`, the container test. Show: 50 case files, the verdict and cause counts, the rebuttal share, what the validator and its report-only checks removed, the two-base comparison, cost. Report and go on to Sprint 10.
