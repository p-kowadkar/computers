"""Regenerate src/computers/data/series.py from daschlab 1.1.0's series table.

Needs the spike group: `uv run --group spike python scripts/make_series_table.py`.
The kind is copied as daschlab assigns it, not recomputed from the plate scale: two
series at 890 arcsec/mm are listed as meteor, and four have no scale at all.
"""

from __future__ import annotations

import math
from pathlib import Path

import daschlab
import daschlab.series as S

OUT = Path("src/computers/data/series.py")


def main() -> None:
    rows = []
    for name in sorted(S.SERIES):
        v = S.SERIES[name]
        scale = "None" if math.isnan(v.plate_scale) else repr(float(v.plate_scale))
        aperture = "None" if v.aperture is None or math.isnan(v.aperture) else repr(float(v.aperture))
        rows.append(f'    "{name}": ("{str(v.kind).split(".")[-1].lower()}", {scale}, {aperture}),')
    OUT.write_text(
        f'''"""The kind and plate scale of every DASCH plate series.

Copied from daschlab {daschlab.__version__} (MIT licence, https://github.com/pkgw/daschlab;
see NOTICE) by `scripts/make_series_table.py`. Do not edit by hand.

The API leaves `series_kind` empty, so L1 reads it here. Kinds are daschlab's own
assignment: "narrow" is 400 arcsec/mm or less, "patrol" is over 400 and under 900, "meteor" is 900 and over,
with the exceptions listed in `KIND_EXCEPTIONS`.
"""

from __future__ import annotations

__all__ = ["KINDS", "KIND_CODES", "KIND_EXCEPTIONS", "SERIES", "kind_from_scale", "kind_of"]

KINDS = ("narrow", "patrol", "meteor")
KIND_CODES = {{"unknown": 0, "narrow": 1, "patrol": 2, "meteor": 3}}

# series -> (kind, plate scale in arcsec/mm or None, aperture in m or None)
SERIES: dict[str, tuple[str, float | None, float | None]] = {{
{chr(10).join(rows)}
}}



def kind_from_scale(scale: float) -> str:
    """The kind a plate scale (arcsec/mm) implies: 400 or less narrow, under 900 patrol."""
    return "narrow" if scale <= 400 else "patrol" if scale < 900 else "meteor"


# Series whose kind does not follow the plate-scale boundaries, as daschlab lists them.
KIND_EXCEPTIONS = frozenset(
    name
    for name, (kind, scale, _) in SERIES.items()
    if scale is None or kind != kind_from_scale(scale)
)


def kind_of(series: str) -> str:
    """"narrow", "patrol" or "meteor"; "unknown" for a series not in the table."""
    entry = SERIES.get(series)
    return entry[0] if entry else "unknown"
'''
    )
    print(f"wrote {OUT} ({len(rows)} series)")


if __name__ == "__main__":
    main()
