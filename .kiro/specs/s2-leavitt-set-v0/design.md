# Design: S2 Leavitt Set v0

## Modules

```
src/computers/
  data/vsx.py          VSX cone query by tile, server list, snapshot store, parse to a table
  bench/
    set.py             build the set: variables, constants, split, manifest, version
    constants.py       scatter statistics and the constant selection with reasons
    inject.py          the five families, flux injection, limit re-application
    metrics.py         completeness, 50/90 points, Wilson intervals, false alarms
    trivial.py         the fixture detector (decade-median difference)
    report.py          text and JSON output
  cli.py               computers bench --smoke | build | check ; computers vsx pull
tests/{unit,property,golden}/
```

`bench` imports `data` and `l1` only. `data/vsx.py` uses `astroquery` through a small function so tests inject a fake. `astroquery` moves from the `spike` group to core only if the pull needs it. Otherwise plain `requests` against the VizieR TAP or votable endpoint is used (decide in task 1 after a live check).

## Data flow

1. `vsx pull` stores the raw VizieR answer per tile and writes `vsx_<tile>.parquet` under `data/silver/`.
2. `bench build` reads `measurements` and `stars` for tiles that have lightcurves, applies the standard rejections, computes per-star statistics and writes under `data/gold/leavitt/v0/`: `variables.parquet`, `constants.parquet`, `injections.parquet`, `manifest.json`.
3. `bench --smoke` loads a fixed subset (first 40 hosts by id, 3 families, 3 amplitudes), injects, runs the trivial detector and prints the table.

## Injection maths

For a host with detections `m_i` at times `t_i`:

- Flux `f_i = 10^(-0.4 m_i)`. A signal is a multiplicative factor `g(t) = 10^(-0.4 dm(t))`, where `dm` is the magnitude offset (negative means brighter). Injected flux is `f_i * g(t_i)`, converted back to magnitude.
- A detection whose new magnitude is fainter than `limiting_mag_local` becomes a non-detection. A non-detection becomes a detection only if the signal lifts it above its limit, in which case its magnitude is the limit minus the lift. v0 keeps non-detections unchanged unless this occurs and records how many were converted. **[open: confirm this rule in review]**
- Families as `dm(t)`:
  - linear: slope in mag per century, zero at the first epoch
  - step: `dm` constant after epoch `t0`
  - dip: a smooth box (cosine taper) of depth and duration
  - outburst: instant rise, exponential decay in days to months. Because plate sampling is sparse, most outbursts hit zero or one plate, which is why completeness for them is measured and not assumed.
  - wave: `A sin(2 pi (t - t0) / P)`
- The amplitude grid per family is log-spaced across the plan ranges. Seeds come from `numpy.random.SeedSequence` keyed by host id and family, so reruns match.

## Constants

For each star with enough good points: robust scatter (1.4826 times the median absolute deviation) of detections against a running median over 5 years, to remove slow trends, plus the raw scatter. A trend of scatter against magnitude is fitted per tile with a median in 0.5 mag bins. A star is "quiet" when its scatter is at or below the bin median times a factor. The factor starts at 1.5 **[start]** and goes to `thresholds.py` with a decision record, since it is a science threshold you own.

## Metrics

- Completeness in a bin is recovered / injected. The interval is Wilson at 95%.
- The 50% and 90% points use linear interpolation between bin centres on a monotone envelope. If completeness never reaches the level, the report says "not reached".
- False alarms per 1,000 constants is flagged constants / constants times 1,000, run on the un-injected hosts.

## Risks

| Risk | Handling |
|---|---|
| VSX unreachable for some tiles | Fail loudly. Build `variables` from the tiles that worked and say which are missing in the manifest. |
| Too few constants in Tabby (dense field, blends) | Report it. Test split may be small. Ask you before relaxing anything. |
| Hosts that are really variable | Scatter cut plus VSX. v0 admits residual contamination and the report estimates it from the false-alarm rate on un-injected hosts. |
| Injection hides real noise structure | Flux injection on real curves keeps sampling and noise. The trivial detector shows the harness works but not that it is representative. |
