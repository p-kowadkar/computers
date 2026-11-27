---
inclusion: always
---

# Repo structure and layer contracts

## Layout

```
.kiro/
  steering/         these files
  specs/            one folder per sprint deliverable (requirements, design, tasks)
  hooks/            Kiro hooks
plan.md             the build plan, with a changelog at the top
docs/
  decisions/        numbered, dated records: findings, thresholds, design choices
  data-cards/       one per dataset
  model-cards/      one per trained model
  schemas.md        generated from schemas.py. Do not edit by hand
  schemas.lock.json generated. Change with `computers schema lock`
src/computers/
  data/             schemas.py  snapshot.py  (later: dasch.py  catalogs.py)
  l1/               tags, curve, scatter, clean, context, detrend, seams, triggers, kill, analyse, candidates (later: features)
  l2/               features, anomaly, ranker, budget, run (later: embeddings, cutout classifier)
  crew/             schemas, packet, llm (gateway and shim), validate, base (the one NOOA import), maury, spike; later the other agents and the debate
  mcp/              MCP servers: dasch_server, catalogs_server over the local tables (imports data only)
  bench/            the Leavitt Set: injection, metrics, ablations
  ml/               E1 data and evaluation (sequences, dataset, masking, baselines, evaluate); lc_encdec/ (model, train, report); later cutout_clf, verdict_distill, text_embed, direction
  telemetry/        one row per star per layer
  thresholds.py     every numeric threshold
  cli.py            the `computers` command
apps/
  review/           local labelling UI
  observatory/      public static site
tests/
  unit/  property/  golden/
scripts/            one-off tools: spikes, hooks, secret check
infra/aws/          training-job launch scripts, IAM notes
data/               git-ignored, except data/manifests/
```

Only folders that exist today have code in them. The rest are empty until their sprint.

## Layer contracts (plan 4.2)

| Layer | Input | Output | May call an LLM? |
|---|---|---|---|
| Data | API responses | Immutable raw files, typed Parquet tables | No |
| L1 | Measurements for a star and its neighbours | Cleaned, detrended curve; zero or more triggers; kill reasons | No |
| L2 | Triggered stars | A score, an event description, cutout verdicts | No |
| L3 | Top-K candidates as context packets | Arguments, verdict, case file | Yes |

Each layer writes one telemetry row per star: where it stopped, why, and what it cost.

## Who may import whom

```
data  <-  l1  <-  l2  <-  crew  <-  cli
          ^        ^
        bench -----+
```

- `l1` imports only from `data`. Never from `l2` or `crew`.
- `l2` may import `l1` and `data`.
- `crew` may import everything below it, and is the only place that imports an agent framework.
- `bench` scores the layers and may import them. It does not call an LLM client itself.

## Where things go

- A new numeric threshold: `thresholds.py`, plus a decision record.
- A new or changed table: `data/schemas.py`, plus a version bump and `computers schema lock`.
- A new data source: a source name for the snapshot store, and an entry in `NOTICE` if it brings a licence.
- Throwaway exploration: `scripts/`, writing under `data/spike/`. It becomes `src/` code only with tests.
