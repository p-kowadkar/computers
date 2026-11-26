# The Computers

A crew of agents, named after the women of the Harvard Observatory, that patrols a century of digitized glass plates for stars that faded or flared, and measures how reliable it is.

This is a triage tool with a published false-alarm rate. It is not a discovery claim. Any event it flags is a **candidate** until an astronomer says otherwise.

The full design is in [`plan.md`](plan.md).

## Status

Early development. Sprint 0 (data spike, see [`docs/decisions/0001-sprint0-findings.md`](docs/decisions/0001-sprint0-findings.md)) and the foundations of Sprint 1 (schemas, snapshot store, steering, hooks) are done. The DASCH client and the first tile pull come next. See the roadmap in section 13 of the plan.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run pytest
uv run computers --version
```

Install the pre-commit checks:

```bash
uv run pre-commit install
```

Every commit then runs the secret scan, ruff, `computers schema check`, `computers thresholds check` and the fast test suite. Datasets that need the optional spike tools (`daschlab`, `astropy`, `matplotlib`) use `uv sync --group spike`.

## Secrets

API keys come from environment variables only. Never commit them.

| Variable | Used for |
|---|---|
| `STARGLASS_API_KEY` | DASCH "full" API (higher rate limits). Create a free Starglass account and copy the key from the profile menu. Scripts pass it to daschlab as `DASCHLAB_API_KEY` for their own process only. |
| `OPENROUTER_API_KEY` | LLM gateway for the crew (from Sprint 8) |

## Data and snapshots

Every API response is stored exactly as received, named by the hash of its bytes, and recorded in a manifest. Nothing is ever overwritten.

| Path | What | Committed |
|---|---|---|
| `data/raw/<source>/<hh>/<sha256>.gz` | The response bytes, gzipped, read-only | No |
| `data/manifests/<source>.jsonl` | One line per response: request, hash, time, client | **Yes** |
| `data/silver/`, `data/gold/` | Typed tables and derived products | No |

Anyone can rebuild the raw data from the committed manifests, because each row holds the request that produced it. Copies of `data/` go to S3 with `aws s3 sync`. There is no DVC.

```bash
uv run computers snapshot probe     # one request to the public /health endpoint, stored and shown
uv run computers snapshot verify    # re-hash every raw file against its manifest row
uv run computers snapshot digest    # one id for the whole stored set (the snapshot id in case files)
uv run computers snapshot status    # rows, requests and bytes per source
```

Table definitions are in `src/computers/data/schemas.py` and documented in [`docs/schemas.md`](docs/schemas.md). A table can only change with a version bump and a regenerated lock (`computers schema lock`). Numeric thresholds live in `src/computers/thresholds.py` and change only with a decision record in `docs/decisions/`.

## Checks, and what enforces them

Kiro hooks can only prompt the agent, because command hooks on file save cannot fail loudly. The real enforcement is the test suite and pre-commit. The hooks are a convenience on top.

| Rule | Enforced by (fails a commit) | Kiro hook (prompts or blocks) |
|---|---|---|
| Table changes need a version bump and a lock | `computers schema check`, `tests/unit/test_schemas.py` | `schemas-on-change` (on save of `schemas.py` or `data-contracts.md`) |
| Threshold changes need a decision record | `computers thresholds check`, `tests/unit/test_thresholds.py` | `thresholds-on-change` (on save of `thresholds.py`) |
| Numeric code has passing property tests | the fast test suite | `props-on-numeric-change` (on save under `l1/` or `bench/`) |
| No LLM client below L3, and L1 does not import L2 or the crew | `tests/unit/test_layer_rules.py` | none |
| Raw data is never edited | the snapshot store (no overwrite, read-only files) and `computers snapshot verify` | `block-raw-edits` blocks the agent's file tools on `data/raw/` |
| No API key in the repo | `scripts/check_secrets.py` | none |

The blocking hook does not cover shell commands that write into `data/raw/`. Kiro loads new hooks at session start, so hooks created during a session take effect in the next one.

## Licence

[GNU Affero General Public License v3.0 or later](LICENSE). Third-party components and their licences are listed in [`NOTICE`](NOTICE).

If you run a modified version of any networked component of this project (the MCP servers or the review UI), the AGPL requires you to offer its source to the users of that service.

## Acknowledgements and citations

This work has made use of data provided by Digital Access to a Sky Century @ Harvard (DASCH), which has been partially supported by NSF grants AST-0407380, AST-0909073, and AST-1313370. Work on DASCH Data Release 7 received support from the Smithsonian American Women's History Initiative Pool.

Principal DASCH citation: Grindlay et al. (2012), *Opening the 100-Year Window for Time-Domain Astronomy*, [doi:10.1017/S1743921312000166](https://doi.org/10.1017/S1743921312000166), arXiv:1211.1051.

Photometry pipeline: Laycock et al. (2010), [doi:10.1088/0004-6256/140/4/1062](https://doi.org/10.1088/0004-6256/140/4/1062); Tang et al. (2013), [doi:10.1086/671760](https://doi.org/10.1086/671760), arXiv:1304.7504.

Data access uses the [daschlab](https://github.com/pkgw/daschlab) toolkit (MIT). Cite the exact version you run, using the DOI from `daschlab.get_version_doi()`.

Source: <https://dasch.cfa.harvard.edu/citing/>
