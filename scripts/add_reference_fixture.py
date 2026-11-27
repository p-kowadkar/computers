"""Add one named reference star to the golden fixtures, without touching the others.

    uv run python scripts/add_reference_fixture.py            # RY Cnc

`make_golden_fixtures.py` re-selects its four stars by features and rewrites the folder,
so it must not be rerun now that the local store holds whole tiles. This script reads one
stored lightcurve response and writes `star_<ref>.json.gz` with the same excerpt rule
(150 rows, the awkward cases first). The output is deterministic.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "golden"))

from make_golden_fixtures import FIXTURES, excerpt

from computers.data.snapshot import SnapshotStore

STARS = {"RY Cnc": 408395461194919}  # APASS J083954.6+194919, B 14.08, inside the M44 tile


def main(name: str = "RY Cnc") -> int:
    ref = STARS[name]
    store = SnapshotStore(ROOT / "data")
    for source in store.sources():
        for row in store.rows(source):
            if row.endpoint.endswith("/lightcurve") and json.loads(row.request)["ref_number"] == ref:
                lines = json.loads(store.read(row))
                small = excerpt(lines)
                payload = {
                    "name": f"fixture:{ref}", "star": name, "ref_number": ref,
                    "source": "DASCH DR7 lightcurve endpoint, excerpt of a stored response",
                    "excerpt_of_rows": len(lines) - 1, "lines": small,
                }
                path = FIXTURES / f"star_{ref}.json.gz"
                with gzip.GzipFile(path, "wb", mtime=0) as f:
                    f.write(json.dumps(payload).encode())
                print(f"{path.name}: {len(small) - 1} of {len(lines) - 1} rows, {path.stat().st_size} bytes ({name})")
                return 0
    print(f"{name} (ref {ref}) is not in the local store; pull it first", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:2]))
