# 0004. L3 budget cap and plate-disjoint split

Date: 2026-11-25. Status: adopted as defaults. You may overrule either before Sprint 4 (budget) or Sprint 10 (split).

## Context

Sprint 1b measured 1,688 patrol stars in total (decision record 0003). Two plan rules do not fit that size.

## Decision 1: L3 budget (plan 7.5)

The rule "no more than 1 L3 call per 1,000 stars" allows about 1.7 calls over all tiles, so it cannot run a patrol.

New rule: at most 20 candidates per tile and 60 in total reach L3 in one patrol run, plus a dollar cap per run set in `thresholds.py` when the crew exists (Sprint 8). The ranker's threshold is set to meet the cap, and the telemetry report shows what was left out. The benchmark, not the patrol, supplies the case counts.

## Decision 2: plate-disjoint split (plan 9.0)

Train on M44 and T CrB, hold out Tabby. T CrB and Tabby share 3,381 plates, which carry 5.5% of T CrB's and 1.2% of Tabby's measurements for the stars measured.

Rule: when building the held-out set, drop Tabby measurements on any plate that appears in a training tile. This removes about 1 to 6% of measurements and gives training and test sets with no plate in common. `computers plates` already lists the shared plates.

## Consequences

- Plan 7.5 and 9.0 lose their "awaiting decision" marks.
- No threshold value changes today, so `thresholds.lock.json` is untouched. The cap values enter `thresholds.py` with their own record in Sprint 8.
