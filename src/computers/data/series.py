"""The kind and plate scale of every DASCH plate series.

Copied from daschlab 1.1.0 (MIT licence, https://github.com/pkgw/daschlab;
see NOTICE) by `scripts/make_series_table.py`. Do not edit by hand.

The API leaves `series_kind` empty, so L1 reads it here. Kinds are daschlab's own
assignment: "narrow" is 400 arcsec/mm or less, "patrol" is over 400 and under 900, "meteor" is 900 and over,
with the exceptions listed in `KIND_EXCEPTIONS`.
"""

from __future__ import annotations

__all__ = ["KINDS", "KIND_CODES", "KIND_EXCEPTIONS", "SERIES", "kind_from_scale", "kind_of"]

KINDS = ("narrow", "patrol", "meteor")
KIND_CODES = {"unknown": 0, "narrow": 1, "patrol": 2, "meteor": 3}

# series -> (kind, plate scale in arcsec/mm or None, aperture in m or None)
SERIES: dict[str, tuple[str, float | None, float | None]] = {
    "a": ("narrow", 60.0, 0.6),
    "ab": ("patrol", 590.0, 0.0635),
    "ac": ("patrol", 606.4, 0.038),
    "aco": ("patrol", 611.3, 0.0254),
    "ad": ("meteor", None, None),
    "adh": ("narrow", 68.0, 0.76),
    "ai": ("meteor", 1360.0, 0.038),
    "ak": ("patrol", 614.5, 0.038),
    "al": ("meteor", 1200.0, 0.038),
    "am": ("patrol", 610.8, 0.038),
    "an": ("patrol", 574.0, None),
    "ax": ("patrol", 695.7, 0.076),
    "ay": ("patrol", 694.2, 0.066),
    "ayroe": ("narrow", None, 0.152),
    "b": ("narrow", 179.4, 0.2),
    "bc": ("narrow", None, 0.406),
    "bi": ("meteor", 1446.0, 0.038),
    "bm": ("narrow", 384.0, 0.075),
    "bo": ("patrol", 800.0, 0.0635),
    "br": ("narrow", 204.0, 0.2),
    "c": ("narrow", 52.56, 0.3),
    "ca": ("patrol", 596.0, 0.0635),
    "ctio": ("narrow", 18.0, 4.0),
    "darnor": ("meteor", 890.0, None),
    "darsou": ("meteor", 890.0, None),
    "dnb": ("patrol", 577.3, 0.042),
    "dnr": ("patrol", 579.7, 0.042),
    "dny": ("patrol", 576.1, 0.042),
    "dsb": ("patrol", 574.5, 0.042),
    "dsr": ("patrol", 579.7, 0.042),
    "dsy": ("patrol", 581.8, 0.042),
    "ee": ("narrow", 330.0, 0.102),
    "er": ("narrow", 390.0, 0.076),
    "fa": ("meteor", 1298.0, 0.038),
    "h": ("narrow", 59.6, 0.6),
    "hale": ("narrow", 11.06, 5.0),
    "i": ("narrow", 163.3, 0.2),
    "ir": ("narrow", 164.0, 0.2),
    "j": ("narrow", 98.0, 0.6),
    "jdar": ("patrol", 560.0, 0.076),
    "ka": ("meteor", 1200.0, 0.071),
    "kb": ("meteor", 1200.0, 0.071),
    "kc": ("patrol", 650.0, 0.127),
    "kd": ("patrol", 650.0, 0.127),
    "ke": ("meteor", 1160.0, 0.071),
    "kf": ("meteor", 1160.0, 0.071),
    "kg": ("meteor", 1160.0, 0.071),
    "kge": ("meteor", 1160.0, 0.071),
    "kh": ("meteor", 1160.0, 0.071),
    "lwla": ("narrow", 36.687, 1.01),
    "m": ("narrow", None, 0.127),
    "ma": ("narrow", 93.7, 0.3),
    "mb": ("narrow", 390.0, 0.1),
    "mc": ("narrow", 97.9, 0.4),
    "md": ("narrow", 193.0, 0.1),
    "me": ("patrol", 600.0, 0.038),
    "meteor": ("meteor", 1200.0, 0.3),
    "mf": ("narrow", 167.3, 0.25),
    "na": ("narrow", 100.0, 0.191),
    "pas": ("narrow", 95.64, 0.67),
    "poss": ("narrow", 67.19, 1.219),
    "pz": ("meteor", 1553.0, 0.076),
    "r": ("narrow", 390.0, 0.075),
    "rb": ("narrow", 395.5, 0.075),
    "rh": ("narrow", 391.3, 0.075),
    "rl": ("narrow", 290.0, 0.1),
    "ro": ("narrow", 390.0, 0.075),
    "s": ("narrow", 26.3, 1.52),
    "sb": ("narrow", 26.0, 1.5),
    "sh": ("narrow", 26.0, 1.5),
    "x": ("narrow", 42.3, 0.33),
    "y": ("narrow", None, 0.203),
    "yb": ("narrow", 55.0, 0.508),
}



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
