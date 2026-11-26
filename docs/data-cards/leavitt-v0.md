# Data card: Leavitt Set v0

**What it is.** The benchmark that the pipeline is scored against: constant stars, known variables, planted signals and a frozen test split. Built by `computers bench build` from the DASCH snapshot and VSX. Record: `docs/decisions/0005-leavitt-set-v0.md`.

**Where it came from.**
- Lightcurves and catalogs: DASCH DR7 through the Starglass API (snapshot digests in `data/gold/leavitt/v0/manifest.json`, one per input file, with the SHA-256 of each silver table).
- Known variables: AAVSO VSX through the CfA VizieR mirror (`B/vsx/vsx`, version 2026-10-18).
- Nothing in the set is a label made by a person yet. Gold labels start in Sprint 11.

**Contents** (all under `data/gold/leavitt/v0/`, not in git):

| File | Rows | What |
|---|---|---|
| `constants.parquet` | 1,688 | One per patrol star: statistics, the reason it is or is not a constant, split |
| `variables.parquet` | 7 | Patrol stars matching a kept VSX type with an amplitude of at least 0.5 mag |
| `injections.parquet` | 53,820 | Every planned injection: host, family, amplitude, epoch, truth flags, split |
| `test_split.json` | 1,058 stars | The held-out stars, the plates dropped from them, and a hash |
| `manifest.json` | | Version, seed, thresholds, counts and input hashes |

**Splits.** Dev is M44 and T CrB, test is Tabby's Star, with the 3,382 Tabby plates that also appear in a dev tile dropped from test measurements (decision 0004). The split depends on the catalogs and exposures only.

**Weaknesses.**
- Only 7 known variables, none of them an event in the test tile. Injection carries the benchmark.
- Constants are selected on DASCH scatter and VSX. A star that is variable below what DASCH can resolve is still a constant here.
- One signal shape per family, with random epoch and scale. Real events are more varied.
- No Gaia information in v0.
- Steps in the Menzel Gap, fades under 0.3 mag per century and outbursts that fall between plates are in the set and are expected to be missed.

**Licence and terms.** Derived from DASCH data and VSX. The Harvard terms and VSX terms have not been checked for redistribution of derived tables. Do not publish `data/gold/` until that is done (plan 5.5). The code is AGPL-3.0-or-later.
