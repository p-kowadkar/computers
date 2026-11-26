---
inclusion: always
---

# The Computers: product

A crew of agents, named after the women of the Harvard Observatory, that patrols a century of digitized glass plates (DASCH, about 1880 to 1990) for stars that faded or flared, and measures how reliable it is. The full design is in `plan.md`.

## What it is

A **triage tool with measured reliability**. It takes a patch of sky, pulls each star's 100-year brightness history, and produces a short ranked list of changes the plates can actually resolve. Each one comes with a case file: the evidence, the argument that it is real, the argument that it is a plate defect, a verdict, and the full chain of how the system got there.

The public face is the Observatory site, where anyone can read case files and blink between a 1920s plate and a modern image.

## What it is not

- Not "AI discovers new stars". A real candidate is a bonus, not the goal.
- Not a vanished-star hunt. The largest search of that kind traced 600 million sources and confirmed none. "Vanished" is a rare class with the strictest evidence bar.
- Not whole-sky. Three 2 by 2 degree tiles: M44, the Tabby's Star tile, the T CrB tile.
- Not a framework demo. Every capability needs a job the pipeline cannot do without, and a number showing it helped. If a capability cannot produce its number, it is cut.

## Claims policy

- The word is **"candidate"** until an astronomer says otherwise.
- Every case file shows the argument against.
- The Methods page states the false-alarm rate and what the system cannot see.
- **"Vanished" never appears in a headline.** A disappearance needs cutouts from 3 or more plates, absence from Gaia, and a human sign-off before it can reach "real".
- Reliability claims come from injection-recovery completeness curves, not from single recovered events.

## Principles (plan section 2)

1. The topology is the reliability: L1 deterministic code, L2 classical ML and small models, L3 an LLM used only for judgment on the few survivors.
2. Benchmark before pipeline. No component merges without a benchmark delta.
3. Injection-recovery over anecdotes.
4. Every documented DASCH failure mode maps to a named L1 check.
5. Snapshot everything. DASCH may not stay up. Every pull is cached, hashed and versioned.
6. Split by tile. A model that saw a plate in training is not tested on it.
7. Calibrated claims.
8. Patterns over frameworks. Agent logic is plain Python that happens to run on NOOA.

## How we work

- One sprint at a time. Each sprint starts as a spec in `.kiro/specs/<name>/` (requirements, design, tasks). The user reviews the acceptance criteria before code is written, and after each sprint we stop and report.
- These stay with the user: science thresholds and the amplitude floor, every gold label, the frozen benchmark test split, and go or no-go at each gate.
- Findings that change the plan are written to `docs/decisions/` and folded into `plan.md` with a changelog entry.
- Licence: AGPL-3.0-or-later. DASCH must be acknowledged (see `README.md`).
