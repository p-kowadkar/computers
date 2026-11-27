"""Helpers for the L1 golden tests: the fixtures cleaned, and an independent oracle.

The oracle re-implements the one-star cleaning rules with plain Python loops over rows, so
a vectorisation mistake in `computers.l1.clean` shows up as a difference. It reads the
same parsed table, so it does not test the parser (the parity test does that).
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from pathlib import Path

from computers.data import dasch_parse as P
from computers.data.series import kind_of
from computers.l1 import clean as K
from computers.l1.tags import Tag
from computers.thresholds import THRESHOLDS

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = Path(__file__).parent / "expected" / "l1_clean.json"

# APASS B of each fixture star, from the `stars` table of its tile (RY Cnc is fainter than the cut).
CATALOG_MAG = {
    112312223618: 13.31,
    408395461194919: 14.08,
    408402551192833: 10.32,
    415593021255513: 11.43,
    5139526051: 7.06,
}


def load(path: Path):
    with gzip.open(path) as f:
        d = json.loads(f.read())
    return d, P.parse_lightcurve(d["lines"], d["ref_number"])


def fixtures():
    return sorted(FIXTURES.glob("*.json.gz"))


def clean(table, ref, policy="reject"):
    """Clean a fixture. No exposure colour terms exist for an excerpt, so every row is emulsion-unknown."""
    return K.clean_star(table, K.StarInfo(ref, CATALOG_MAG.get(ref)), K.Exposures({}), meteor_policy=policy)


def oracle_tags(table, ref, policy="reject") -> list[int]:
    """Tag masks row by row, in (time, exposure) order, from plain Python."""
    t = table.to_pydict()
    rows = sorted(range(table.num_rows), key=lambda i: (t["time"][i], t["exposure_id"][i]))
    mag = [t["magcal_magdep"][i] for i in rows]
    aflags = [int(t["aflags"][i] or 0) for i in rows]
    detected = [m is not None and not math.isnan(m) for m in mag]
    series = [t["series"][i] for i in rows]
    ra = [t["ra_deg"][i] for i in rows]
    dec = [t["dec_deg"][i] for i in rows]
    blended = [t["blended_mag"][i] for i in rows]
    limit = [t["limiting_mag_local"][i] for i in rows]
    hard_mask, soft_mask = 64 | 8192, 2048 | 4096 | 32768
    tags = [0] * len(rows)
    pos = [(ra[i], dec[i]) for i in range(len(rows)) if detected[i] and ra[i] is not None and dec[i] is not None]
    ra0 = statistics.median(p[0] for p in pos) if pos else None
    dec0 = statistics.median(p[1] for p in pos) if pos else None
    max_sep = THRESHOLDS["astrometry_max_sep_arcsec"].value
    blend_max = THRESHOLDS["blend_max_diff_mag"].value
    cat = CATALOG_MAG.get(ref)
    for i in range(len(rows)):
        if detected[i]:
            if aflags[i] & hard_mask:
                tags[i] |= int(Tag.STD_HARD)
            elif aflags[i] & soft_mask:
                tags[i] |= int(Tag.STD_SOFT)
            if aflags[i] & (1 << 14):
                tags[i] |= int(Tag.PLATE_EDGE)
            if ra[i] is not None and dec[i] is not None:
                dra = (ra[i] - ra0 + 180.0) % 360.0 - 180.0
                sep = math.hypot(dra * math.cos(math.radians(dec0)), dec[i] - dec0) * 3600.0
                if sep > max_sep:
                    tags[i] |= int(Tag.ASTROMETRY)
            if cat is not None and blended[i] is not None and abs(blended[i] - cat) > blend_max:
                tags[i] |= int(Tag.BLEND_SUSPECT)
        kind = kind_of(series[i])
        if kind == "meteor" and policy == "reject":
            tags[i] |= int(Tag.METEOR_REJECTED)
        if kind == "unknown":
            tags[i] |= int(Tag.UNKNOWN_SERIES)
        tags[i] |= int(Tag.EMULSION_UNKNOWN)
    rejecting = int(Tag.STD_HARD | Tag.STD_SOFT | Tag.ASTROMETRY | Tag.METEOR_REJECTED)
    survivors = [mag[i] for i in range(len(rows)) if detected[i] and not tags[i] & rejecting]
    if survivors:
        median = statistics.median(survivors)
        margin = THRESHOLDS["deep_limit_margin_mag"].value
        for i in range(len(rows)):
            if not detected[i] and limit[i] is not None and limit[i] - median > margin:
                tags[i] |= int(Tag.DEEP_LIMIT_SUSPECT)
    return tags


def summary(curve) -> dict:
    counts = {k: v for k, v in curve.tag_counts().items() if v}
    return {
        "rows": len(curve),
        "detections": int(curve.detected.sum()),
        "good": curve.n_good,
        "good_for_outburst": int(curve.good_for_outburst.sum()),
        "tags": counts,
    }


def current() -> dict:
    out = {}
    for path in fixtures():
        d, table = load(path)
        out[str(d["ref_number"])] = {"star": d.get("star") or d["name"], **summary(clean(table, d["ref_number"]))}
    return out
