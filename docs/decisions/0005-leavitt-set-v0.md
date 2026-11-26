# 0005. Leavitt Set v0

Date: 2026-11-26. Spec: `.kiro/specs/s2-leavitt-set-v0/`. Status: **built; the test split is waiting for your approval** (`computers bench freeze`).

The set is built from the committed snapshot manifests with `computers bench build` and checked with `computers bench check`. The data card is `docs/data-cards/leavitt-v0.md`.

## 1. What is in it

| | M44 (dev) | T CrB (dev) | Tabby (test) |
|---|---|---|---|
| Patrol stars (B at most 13.5) | 444 | 186 | 1,058 |
| Constants | 247 | 114 | 536 |
| Known variables kept | 4 | 1 | 2 |
| Injections (60 per constant) | 14,820 | 6,840 | 32,160 |
| Of which expected to be missed | 2,783 | 1,305 | 6,074 |

Why constants are excluded, per tile (M44 / T CrB / Tabby):

| Reason | M44 | T CrB | Tabby |
|---|---|---|---|
| VSX entry within 30 arcsec | 114 | 33 | 302 |
| Fewer than 200 good points | 15 | 13 | 7 |
| Within-block scatter above 1.5 times the reference | 26 | 5 | 54 |
| Between-block scatter above 1.5 times the reference | 42 | 21 | 159 |

The dev split has 361 constants. Of the 27 family and amplitude bins that remain after the expected-missed signals are set aside, most hold 722 injections (2 replicates) and the step bins hold 385 to 412. All are far above the 10 needed for a bin to count as reliable. The three weakest linear fades (under 0.3 mag per century) are expected to be missed, so linear has 3 bins.

## 2. Thresholds recorded here

All are status **start**: values to tune, not values chosen from data.

| Threshold | Value | Plan | Where the number comes from |
|---|---|---|---|
| `vsx_match_radius_arcsec` | 3 arcsec | 10.1 | Spec R1.4. DASCH positions scatter by a few arcsec, VSX positions are mostly from Gaia. |
| `vsx_exclude_radius_arcsec` | 30 arcsec | 10.1 | Spec R3.1. A star with any VSX entry this close is not a constant, because DASCH blends sources at plate resolution. |
| `menzel_gap_start_year` | 1953 | 3.1 | Plan: coverage thins "from about 1953 to 1969". Sources disagree on the dates. |
| `menzel_gap_end_year` | 1969 | 3.1 | Same. Steps placed inside the gap are marked expected to be missed. |
| `fade_floor_mag_per_century` | 0.3 mag per century | 10.2 | Plan: fades under 0.3 mag per century sit inside the systematics (constants drift up to about 0.2). |
| `constant_min_good_points` | 200 points | 10.1 | Spec R3.1. Enough points for at least four 5-year blocks of 10 or more, after the standard rejections. |
| `constant_scatter_factor` | 1.5 (ratio) | 10.1 | A constant's within-block and between-block scatter must each be at most 1.5 times the median of the 41 stars nearest in magnitude in its tile. |

## 3. Choices made by default in the review

- **Injection size:** every constant in a tile is a host, 5 families, 6 log-spaced amplitudes, 2 replicates. The plan's 500 constants and 15,000 curves do not fit tiles of this size.
- **Known-variable types:** long-period (M, SR*, L*), RV Tauri, eruptive (novae, dwarf novae, symbiotics, FU Ori, Gamma Cas, R CrB and similar) and eclipsing binaries. A known amplitude under 0.5 mag rules any entry out. Eclipsing binaries need a known amplitude. Short-period pulsators and rotators are left out: they add scatter, not events.
- **Non-detections:** a signal can lift a non-detection above its plate limit. The host's median magnitude stands in for the unseen true magnitude, and the point becomes a detection. Points are counted as converted. A point is lost only when the signal made it fainter and it now lies below its limit, which keeps a zero-amplitude injection an exact identity.
- **A rejected detection is bad data, not a faint star.** Host curves drop it. Non-detections always have `reject == 0` in the data.
- **Recovery** means the detector flags the injected curve and did not already flag the host.

## 4. Findings

- **A median absolute deviation cannot see a step that splits the century in half.** The median of the block medians then lies on one side of the step. The between-block scatter is a 10th-to-90th percentile range instead. A test caught this on synthetic data. Without the change the constants would have included stars with real steps.
- **VSX is full of Gaia-derived low-amplitude entries.** 99 of 444 M44 stars and 159 of 1,058 Tabby stars match a VSX entry within 3 arcsec, most of them rotational variables and delta Scuti stars. They are excluded from the constants, and only 7 stars across the tiles survive as known variables with an amplitude of 0.5 mag or more. The benchmark's real-image true positives are few. Its main evidence is injection.
- **T CrB, KIC 8462852 and RY Cnc.** T CrB and KIC 8462852 are patrol stars and both carry a VSX entry, so neither is a constant. T CrB would also fail the scatter cut (within 0.31, reference 0.15). KIC 8462852 is quiet by the statistics (between-block 0.031 against a reference of 0.04), which matches its reputation. RY Cnc is not a patrol star (B is fainter than 13.5), and the three Tang K giants lie outside all three tiles. They are listed in the manifest with coordinates and need their own pull.
- **The three Tang giants** are DASCH J083038.5+140713, J075445.9+164141 and J073606.5+211411 (arXiv:1001.1395, section 2.2).
- **The trivial detector behaves as designed** (section 5).
- **The fast test suite had grown to 140 seconds.** The golden parity test compared every lightcurve in the local store, and the Sprint 2 pull added 1,244. It now compares an evenly spaced sample of 40, and `COMPUTERS_PARITY_ALL=1` runs the rest. The full run covered all 1,688 stars (1,698 cases) and every one matched daschlab.
- **The tile-coverage property test claimed dec 60. It holds to about 55.** At 58 the four catalog boxes miss tile corners. All three real tiles are at dec 44.5 or lower. The test is now bounded at 55.

## 5. The trivial detector on the dev split

It flags a star when the median magnitude of its first decade differs from its last decade by more than 0.5 mag. Plot: `docs/decisions/assets/s2/completeness_trivial_dev.png`. Run with `computers bench run --split dev`.

| Family | 50% recovery | 90% recovery | Plateau |
|---|---|---|---|
| Linear fade or brightening | 0.63 mag per century (0.63 to 0.64) | 0.83 (0.82 to 0.84) | 98% |
| Step | 0.56 mag (0.54 to 0.57) | not reached | 82% |
| Slow wave | 0.89 mag (0.77 to 1.02) | not reached | 61% at 1.5 mag |
| Dip | not reached | not reached | 5% |
| Outburst | not reached | not reached | 0% |

- **False alarms:** 0 of 361 constants (95% interval 0 to 10.5 per 1,000).
- **Suspect:** it recovers 1,001 of 4,088 signals that were expected to be missed. These are steps placed in the Menzel Gap (the weak fades are missed, as they should be). The detector has no seam rule, so the harness flags it. A detector that is not suspect has to pass this check.
- **The step plateau at 82%** is the first-decade and last-decade blind spot: a step in the first or last decade does not separate the two windows.

This shows that the harness measures what it is meant to. It says nothing about the pipeline.

## 6. What v0 cannot see

- **No Gaia cuts.** DR4 arrives on 2 December, so the constants are chosen from DASCH scatter and VSX only. A constant that is variable at a level DASCH cannot resolve is still a good host. A constant that hides a variable the plates can see would show up as a false alarm.
- **Hosts share their tile's plate systematics.** Injection into a real host keeps real noise and sampling, but a signal is the same in every host of a tile.
- **The set has no real outburst, dip or disappearance in the test tile.** Tabby has 2 known variables and neither is an event.
- **Outbursts are mostly invisible by sampling.** With tens of days of decay and plates a few times a year, most planted outbursts fall between plates.
- **Constants are chosen on a statistic that costs the set its noisiest stars.** The factor of 1.5 removes 14 to 20% of the stars in each tile for scatter alone (15% in M44, 14% in T CrB, 20% in Tabby). A star at 1.5 times the reference is still included, so the hosts are not all quiet.
- **The Tang giants and RY Cnc are not pulled.** They need their own lightcurve pulls before the golden tests.

## 7. The test split

Tabby is the test tile: 1,058 stars, with 3,382 plates dropped from its measurements because they also appear in M44 or T CrB. The split depends only on the catalogs and exposures, so tuning a threshold cannot change it. Its hash is `67758fff157a6ab5...` (full hash in `data/gold/leavitt/v0/test_split.json`).

To freeze it, after your review:

```
uv run computers bench freeze --decision docs/decisions/0005-leavitt-set-v0.md
```

That writes `docs/decisions/leavitt-test-split.lock.json`, which you commit. From then on `computers bench check` and `bench build` fail if the test split changes, and `bench run --split test` works only on a frozen, consistent set. Score the test split once, when a detector is ready to be reported.
