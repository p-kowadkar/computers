"""Compare our lightcurve parser with daschlab 1.1.0's own, column by column.

daschlab's parsing functions are private (`_tabulate_phot_data`, `_postproc_phot_table`),
which is acceptable because its version is pinned and this test fails loudly if they
change. The comparison rules:

* values must be equal where both sides have one (float32 columns compare as float32);
* "missing" must agree: daschlab's masked or NaN is our null;
* rows are aligned by a total ordering on the data, because daschlab sorts by time with
  an unstable sort and ties may come out in a different order;
* `exposure_id` is not compared: ours is deliberately different (see dasch_parse);
* rejections compare as `reject != 0`, because bit positions are each side's own.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa

from computers.data import dasch_parse as P
from computers.data.snapshot import SnapshotStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# our column -> daschlab column (same name unless listed)
FLOAT_COLUMNS = (
    "magcal_magdep", "magcal_local_rms", "limiting_mag_local", "fwhm_image", "ellipticity",
    "plate_dist", "blended_mag", "image_x", "image_y", "time_accuracy", "drad_rms2", "extinction",
)  # fmt: skip
INT_COLUMNS = (
    "platenum", "mosnum", "solnum", "expnum", "aflags", "a2flags", "bflags", "b2flags",
    "local_bin_reject_flag",
)  # fmt: skip


# ---------------------------------------------------------------------- daschlab


def daschlab_table(lines: list[str]):
    """daschlab 1.1.0's reading of a raw response, standard rejections applied."""
    from daschlab.lightcurves import Lightcurve
    from daschlab.photometry import _postproc_phot_table, _tabulate_phot_data

    lc = _postproc_phot_table(Lightcurve, _tabulate_phot_data(lines))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        lc.apply_standard_rejections()
    return lc


def _their_column(lc, name: str) -> tuple[np.ndarray, np.ndarray]:
    """(values, missing) for one daschlab column."""
    col = lc[name]
    if name == "time":
        return np.asarray(col.jd, dtype=float), np.zeros(len(lc), dtype=bool)
    mask = getattr(col, "mask", None)
    missing = np.zeros(len(lc), dtype=bool) if mask is None else np.broadcast_to(np.asarray(mask), len(lc)).copy()
    data = getattr(col, "unmasked", col)
    data = getattr(data, "value", data)
    values = np.asarray(np.ma.getdata(data))
    if values.dtype.kind == "f":
        missing |= np.isnan(values)
    return values, missing


def _their_position(lc, which: str) -> tuple[np.ndarray, np.ndarray]:
    deg = np.asarray(getattr(lc["pos"], which).deg, dtype=float)
    return deg, np.isnan(deg)


def _our_column(table: pa.Table, name: str) -> tuple[np.ndarray, np.ndarray]:
    col = table.column(name)
    missing = np.asarray(col.is_null().to_numpy(zero_copy_only=False), dtype=bool)
    values = col.to_numpy(zero_copy_only=False)
    values = np.asarray(values)
    if values.dtype.kind == "f":
        missing = missing | np.isnan(values)
    return values, missing


def _order(time, platenum, solnum, expnum, limit, mag) -> np.ndarray:
    mag = np.where(np.isnan(mag), np.inf, mag)
    return np.lexsort((mag, limit, expnum, solnum, platenum, time))


# ---------------------------------------------------------------------- compare


def compare(ours: pa.Table, theirs, *, max_reports: int = 8) -> list[str]:
    """Differences between our table and daschlab's, empty when they agree."""
    problems: list[str] = []
    n = ours.num_rows
    if len(theirs) != n:
        return [f"row count: ours {n}, daschlab {len(theirs)}"]
    if n == 0:
        return []

    sort_columns = ("time", "platenum", "solnum", "expnum", "limiting_mag_local", "magcal_magdep")
    oo = _order(*[_our_column(ours, c)[0].astype(float) for c in sort_columns])
    to = _order(*[_their_column(theirs, c)[0].astype(float) for c in sort_columns])

    def check(name, our, their, exact=True):
        (ov, om), (tv, tm) = our, their
        ov, om, tv, tm = ov[oo], om[oo], tv[to], tm[to]
        if not np.array_equal(om, tm):
            bad = np.flatnonzero(om != tm)
            problems.append(
                f"{name}: missing-ness differs in {len(bad)} rows, first at sorted row {bad[0]}: "
                f"ours {'missing' if om[bad[0]] else ov[bad[0]]!r}, daschlab "
                f"{'missing' if tm[bad[0]] else tv[bad[0]]!r}"
            )
            return
        present = ~om
        a, b = ov[present], tv[present]
        if a.dtype.kind in "fiu" and b.dtype.kind in "fiu":
            a, b = a.astype(np.float64), b.astype(np.float64)
            same = (a == b) if exact else np.isclose(a, b, rtol=1e-12, atol=0)
        else:
            same = a == b
        if not np.all(same):
            bad = np.flatnonzero(~same)
            row = np.flatnonzero(present)[bad[0]]
            problems.append(
                f"{name}: {len(bad)} values differ, first at sorted row {row}: ours {a[bad[0]]!r}, "
                f"daschlab {b[bad[0]]!r}"
            )

    check("time", _our_column(ours, "time"), _their_column(theirs, "time"))
    for name in FLOAT_COLUMNS + INT_COLUMNS:
        check(name, _our_column(ours, name), _their_column(theirs, name))
    check("series", (np.array(ours.column("series").to_pylist(), dtype=object), np.zeros(n, bool)),
          (np.array([str(s) for s in theirs["series"]], dtype=object), np.zeros(n, bool)))  # fmt: skip
    check("ra_deg", _our_column(ours, "ra_deg"), _their_position(theirs, "ra"))
    check("dec_deg", _our_column(ours, "dec_deg"), _their_position(theirs, "dec"))

    # daschlab masks ref_number on non-detections; ours is always the requested star.
    tv, tm = _their_column(theirs, "ref_number")
    ours_ref = _our_column(ours, "ref_number")[0]
    mism = (~tm[to]) & (tv[to].astype(np.uint64) != ours_ref[oo].astype(np.uint64))
    if mism.any():
        problems.append(f"ref_number: {int(mism.sum())} detections differ")

    # Rejections: the same rows, whatever each side calls the bit.
    our_rej = _our_column(ours, "reject")[0][oo] != 0
    their_rej = np.asarray(theirs["reject"])[to] != 0
    if not np.array_equal(our_rej, their_rej):
        bad = np.flatnonzero(our_rej != their_rej)
        problems.append(f"reject: {len(bad)} rows differ, first at sorted row {bad[0]}")
    return problems[:max_reports]


# ------------------------------------------------------------------------ inputs


def _read_fixture(path: Path) -> tuple[str, int, list[str]]:
    with gzip.open(path) as f:
        data = json.loads(f.read())
    return data["name"], int(data["ref_number"]), data["lines"]


def fixture_inputs() -> Iterator[tuple[str, int, list[str]]]:
    for path in sorted(FIXTURES.glob("*.json.gz")):
        yield _read_fixture(path)


LOCAL_SAMPLE = 40  # the local store holds thousands of stars; the fast suite checks a sample


def local_inputs(data_dir: Path | None = None) -> Iterator[tuple[str, int, list[str]]]:
    """Raw lightcurve responses in the local snapshot store, plus the Sprint 0 capture.

    At most `LOCAL_SAMPLE` stores are returned, spaced evenly through the stars sorted by
    reference number, so the choice is stable. Set COMPUTERS_PARITY_ALL=1 for every one.
    """
    store_dir = data_dir or ROOT / "data"
    seen: set[int] = set()
    found = {}
    if (store_dir / "manifests").exists():
        store = SnapshotStore(store_dir)
        for source in store.sources():
            for row in store.rows(source):
                if row.endpoint.endswith("/lightcurve"):
                    found.setdefault(int(json.loads(row.request)["ref_number"]), (store, row))
    refs = sorted(found)
    if not os.environ.get("COMPUTERS_PARITY_ALL") and len(refs) > LOCAL_SAMPLE:
        refs = [refs[i * len(refs) // LOCAL_SAMPLE] for i in range(LOCAL_SAMPLE)]
    for ref in refs:
        store, row = found[ref]
        seen.add(ref)
        yield f"store:{ref}", ref, json.loads(store.read(row))
    s0 = store_dir / "spike" / "raw" / "tcrb_lightcurve.json.gz"
    if s0.exists() and 415593021255513 not in seen:
        with gzip.open(s0) as f:
            yield "sprint0:T CrB", 415593021255513, json.loads(f.read())


def parse_ours(lines: list[str], ref: int) -> pa.Table:
    return P.apply_standard_rejections(P.parse_lightcurve(lines, ref))
