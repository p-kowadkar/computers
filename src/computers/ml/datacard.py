"""The data card of the E1 dataset, computed from the stars (spec S6 R1.7)."""
from __future__ import annotations

import numpy as np

from computers.ml import sequences as S

__all__ = ["card_text", "stats"]

KIND_NAMES = {0: "unknown", 1: "narrow", 2: "patrol", 3: "meteor"}


def stats(stars: list[S.Star]) -> dict:
    """Counts and shares for the stars of one tile."""
    n = np.array([len(s) for s in stars])
    det = np.array([s.n_detections for s in stars])
    year = np.concatenate([s.year for s in stars])
    kind = np.concatenate([s.kind for s in stars])
    lim = np.concatenate([s.is_limit for s in stars])
    decades = sorted({int(d) for d in np.floor(year / 10) * 10})
    mix = {}
    for d in decades:
        sel = (year >= d) & (year < d + 10)
        mix[d] = {KIND_NAMES[k]: float((kind[sel] == k).mean()) for k in sorted(set(kind[sel].tolist()))}
    return {
        "stars": len(stars),
        "rows": int(n.sum()),
        "detections": int(det.sum()),
        "rows_per_star": [float(x) for x in np.percentile(n, [0, 10, 50, 90, 100])],
        "limit_share": float(lim.mean()),
        "limits_deeper_than_star": float((np.concatenate([s.value[s.is_limit] for s in stars]) > 0).mean()) if lim.any() else float("nan"),
        "detrended_share": float(np.mean([s.detrended for s in stars])),
        "series_kind_by_decade": mix,
        "series_ids": len({int(x) for s in stars for x in np.unique(s.series)}),
    }


def card_text(by_tile: dict[str, list[S.Star]], *, digest: str) -> str:
    lines = [
        f"# Data card: lc-sequences-v{S.SPEC_VERSION}",
        "",
        f"**What it is.** Per-star sequences of observations for the lightcurve encoder-decoder (Track E1). Sequence spec version {S.SPEC_VERSION}, hash `{S.spec_hash()}`, snapshot digest `{digest}`. Written by `computers ml build`.",
        "",
        "**Rows.** The curve L1 read (detrended when the star could be detrended, raw otherwise), for stars with at least 50 good detections. Each row is a good detection, or a non-detection that no cleaning tag removed and that has a finite plate limit. The magnitude is stored as an offset from the star's median, and a non-detection's limit as a depth relative to it. Nothing is divided by scatter.",
        "",
        "**What it leaves out.** Injected curves (the builder never reads the benchmark). The Tabby tile, which is held out. Stars with fewer than 50 good detections. Detections without a plate offset after detrending. Rows that a rejecting tag removed.",
        "",
        "| Tile | Stars | Rows | Detections | Rows per star (min, p10, median, p90, max) | Limit share | Limits deeper than the star | Detrended share |",
        "|---|---|---|---|---|---|---|---|",
    ]
    all_stats = {t: stats(s) for t, s in by_tile.items() if s}
    for t, st in all_stats.items():
        r = st["rows_per_star"]
        lines.append(f"| {t} | {st['stars']} | {st['rows']:,} | {st['detections']:,} | {r[0]:.0f}, {r[1]:.0f}, {r[2]:.0f}, {r[3]:.0f}, {r[4]:.0f} | {100 * st['limit_share']:.1f}% | {100 * st['limits_deeper_than_star']:.0f}% | {100 * st['detrended_share']:.0f}% |")
    for t, st in all_stats.items():
        lines += ["", f"**Series kind by decade, {t}** (share of rows).", "", "| Decade | " + " | ".join(KIND_NAMES.values()) + " |", "|---|---|---|---|---|"]
        for d, mix in st["series_kind_by_decade"].items():
            lines.append(f"| {d}s | " + " | ".join(f"{100 * mix.get(k, 0):.0f}%" for k in KIND_NAMES.values()) + " |")
    lines += [
        "",
        "**Known limits.**",
        f"- {sum(len(v) for v in by_tile.values())} stars, mostly constant. The set is small for self-supervised training, and a model trained on it may learn sampling and plate effects.",
        "- Stars of one tile share plates. Splits are by tile (`0004`).",
        "- 361 of the dev stars are Leavitt Set hosts. An encoder never scores a host of a tile it was trained on.",
        "- Meteor series are 10 to 21% of the raw rows. The default meteor policy rejects them, so the dataset has none (`0006`). The 1950s are thin as a result.",
        "- **Most limits are not upper limits of a faint star.** A limit deeper than the star's own median magnitude (offset above 0) means the star should have been detected and was not. That is 60 to 80% of limit rows here, which fits the known DASCH issue of missing points faking deep limits (`astro-domain.md`). The one-sided loss still treats them as limits, and the report counts them.",
        "- Non-detections are 6 to 12% of rows here, against 34 to 45% of raw rows, because cleaning removes most of them.",
        "",
    ]
    return "\n".join(lines)
