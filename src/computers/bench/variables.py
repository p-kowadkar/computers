"""Which VSX entries count as known variables for the benchmark, and the reference targets.

The type list is a science choice made by default in the Sprint 2 review (decision
record 0005). It keeps variables that a century of plates can resolve as an event or a
long trend, and leaves out short-period pulsators and rotators, which only add scatter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from computers.thresholds import THRESHOLDS

__all__ = ["REFERENCE_TARGETS", "ReferenceTarget", "base_types", "classify", "sexagesimal"]

# VSX type codes grouped by why they matter to a plate search.
LONG_PERIOD = {"M", "SR", "SRA", "SRB", "SRC", "SRD", "L", "LB", "LC"}
RV_TAURI = {"RV", "RVA", "RVB"}
ERUPTIVE = {
    "N", "NA", "NB", "NC", "NL", "NR", "UG", "UGSS", "UGSU", "UGZ", "ZAND", "I", "IA", "IB",
    "IN", "INA", "INB", "INT", "INAT", "INS", "FU", "GCAS", "BE", "SDOR", "RCB", "DYPER",
}  # fmt: skip
ECLIPSING = {"E", "EA", "EB", "EW"}  # kept only with a known amplitude at the long-term floor

_SPLIT = re.compile(r"[/|+]")


def base_types(vsx_type: str | None) -> list[str]:
    """Plain type codes from a VSX type string such as `EA/SD`, `SRB:` or `DSCT|GDOR`."""
    if not vsx_type:
        return []
    return [p.strip().rstrip(":").upper() for p in _SPLIT.split(vsx_type) if p.strip()]


def classify(vsx_type: str | None, amplitude_mag: float | None) -> str | None:
    """The known-variable class of a VSX entry, or None when it is not one we keep.

    A known amplitude below the long-term floor rules an entry out, whatever its type: the
    plates cannot resolve it. An unknown amplitude is kept for the long-period and
    eruptive classes (T CrB's is a nova outburst, VSX gives it as 8.8 mag) and ruled out for
    eclipsing binaries, which are only useful when deep.
    """
    floor = THRESHOLDS["amplitude_floor_long_term_mag"].value
    known = amplitude_mag is not None
    if known and amplitude_mag < floor:
        return None
    codes = base_types(vsx_type)
    for code in codes:
        if code in LONG_PERIOD:
            return "long_period"
        if code in RV_TAURI:
            return "rv_tauri"
        if code in ERUPTIVE:
            return "eruptive"
    if known and any(c in ECLIPSING for c in codes):
        return "eclipsing"
    return None


def sexagesimal(ra_hms: str, dec_dms: str) -> tuple[float, float]:
    """Degrees from `hh:mm:ss.s` and `+dd:mm:ss`."""
    h, m, s = (float(x) for x in ra_hms.split(":"))
    sign = -1.0 if dec_dms.strip().startswith("-") else 1.0
    d, dm, ds = (abs(float(x)) for x in dec_dms.split(":"))
    return 15.0 * (h + m / 60 + s / 3600), sign * (d + dm / 60 + ds / 3600)


@dataclass(frozen=True)
class ReferenceTarget:
    name: str
    ra: float
    dec: float
    why: str


_J0830 = sexagesimal("08:30:38.5", "+14:07:13")
_J0754 = sexagesimal("07:54:45.9", "+16:41:41")
_J0736 = sexagesimal("07:36:06.5", "+21:14:11")

# Positions of the first three come from Sprint 0, the Tang giants from their names
# (Tang et al. 2010, arXiv:1001.1395, section 2.2). The giants lie outside the three tiles.
REFERENCE_TARGETS = (
    ReferenceTarget("T CrB", 239.87568, 25.92017, "recurrent nova; the 1946 eruption must be real"),
    ReferenceTarget("KIC 8462852", 301.56439, 44.45689, "must not be called a century-long fade"),
    ReferenceTarget("RY Cnc", 129.97758, 19.82192, "eclipsing binary inside the M44 tile"),
    ReferenceTarget("DASCH J083038.5+140713", *_J0830, "K giant, 1 mag decline in a century"),
    ReferenceTarget("DASCH J075445.9+164141", *_J0754, "K giant, sharp dip near 1930"),
    ReferenceTarget("DASCH J073606.5+211411", *_J0736, "K giant, 1 mag dip in the 1930s to 1950s"),
)
