"""Build the golden-test fixtures: small excerpts of real lightcurve responses.

Reads the local snapshot store (and the Sprint 0 capture), picks a few stars, and writes
up to 150 rows of each, in the raw response format, to tests/golden/fixtures/. The rows
are chosen to include the awkward cases: non-detections, blended points, missing values
and points the standard rejections remove, so the parser is tested where it is most
likely to differ from daschlab.

    uv run python scripts/make_golden_fixtures.py

The selection is deterministic. Re-running gives the same files while the store is the same.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "golden"))

from _parity import FIXTURES, local_inputs

from computers.data.dasch_parse import STANDARD_BAD_AFLAGS

MAX_ROWS = 150
QUOTAS = {"nondetection": 25, "blended": 15, "missing_value": 25, "standard_rejected": 30}


def categorize(header: list[str], fields: list[str]) -> set[str]:
    row = dict(zip(header, fields, strict=True))
    nondet = row["gsc_bin_index"] in ("", "0") and row["sextractor_number"] in ("", "0")
    cats = set()
    if nondet:
        cats.add("nondetection")
        return cats
    if row["blended_mag"] != "":
        cats.add("blended")
    if row["magcal_local_rms"] == "" or row["drad_rms2"] == "":
        cats.add("missing_value")
    if (int(row["aflags"] or 0) & STANDARD_BAD_AFLAGS) != 0:
        cats.add("standard_rejected")
    return cats


def excerpt(lines: list[str]) -> list[str]:
    header = lines[0].split(",")
    rows = [r.split(",") for r in lines[1:]]
    cats = [categorize(header, r) for r in rows]
    chosen: set[int] = set()
    for name, quota in QUOTAS.items():
        idx = [i for i, c in enumerate(cats) if name in c and i not in chosen]
        if idx:
            pick = np.linspace(0, len(idx) - 1, min(quota, len(idx))).round().astype(int)
            chosen.update(idx[j] for j in pick)
    rest = [i for i in range(len(rows)) if i not in chosen]
    room = MAX_ROWS - len(chosen)
    if rest and room > 0:
        pick = np.linspace(0, len(rest) - 1, min(room, len(rest))).round().astype(int)
        chosen.update(rest[j] for j in pick)
    return [lines[0]] + [lines[1 + i] for i in sorted(chosen)]


def features(lines: list[str]) -> dict[str, float]:
    header = lines[0].split(",")
    cats = [categorize(header, r.split(",")) for r in lines[1:]]
    n = max(len(cats), 1)
    return {
        "rows": len(cats),
        "nondet": sum("nondetection" in c for c in cats) / n,
        "blended": sum("blended" in c for c in cats) / n,
        "rejected": sum("standard_rejected" in c for c in cats) / n,
    }


def main() -> int:
    inputs = [(n, r, lines) for n, r, lines in local_inputs()]
    if len(inputs) < 4:
        print("need at least 4 stars in the local store", file=sys.stderr)
        return 1
    feats = {name: features(lines) for name, _, lines in inputs}
    by_name = {name: (ref, lines) for name, ref, lines in inputs}

    wanted: list[str] = []
    if "sprint0:T CrB" in by_name:
        wanted.append("sprint0:T CrB")  # a nova: the largest amplitude, the T CrB check
    for key in ("nondet", "blended", "rejected"):
        for name in sorted(feats, key=lambda n: (-feats[n][key], n)):
            if name not in wanted:
                wanted.append(name)
                break
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for old in FIXTURES.glob("*.json.gz"):
        old.unlink()
    for name in wanted[:4]:
        ref, lines = by_name[name]
        small = excerpt(lines)
        slug = f"star_{ref}"
        payload = {
            "name": f"fixture:{ref}",
            "ref_number": ref,
            "source": "DASCH DR7 lightcurve endpoint, excerpt of a stored response",
            "excerpt_of_rows": len(lines) - 1,
            "lines": small,
        }
        path = FIXTURES / f"{slug}.json.gz"
        with gzip.GzipFile(path, "wb", mtime=0) as f:
            f.write(json.dumps(payload).encode())
        print(f"{path.name}: {len(small) - 1} of {len(lines) - 1} rows, {path.stat().st_size} bytes  ({name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
