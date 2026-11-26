"""Turn raw DASCH API responses into our typed tables.

The API returns a JSON list of comma-separated strings, header first. These parsers
reproduce daschlab 1.1.0's reading of those responses (the golden test in
tests/golden proves it), but write Arrow tables with our column names and with nulls
where daschlab masks.

Rules that matter (each was checked against real responses, see
docs/decisions/0001-sprint0-findings.md and .kiro/specs/s1b-thin-client/):

* A **non-detection** is a lightcurve row whose `gsc_bin_index` and `sextractor_number`
  both read as 0. In raw data they are empty strings, and an empty integer reads as 0.
* Missing floats are empty strings or NaN. Both become null.
* Sentinel values daschlab masks (99, 999, 0 in specific columns) become null.
* Nothing is guessed. A value that cannot be read raises `ParseError` naming the
  column and the row.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence

import numpy as np
import pyarrow as pa

from computers.data import schemas
from computers.data.tiles import Tile, in_tile

__all__ = [
    "BIT_STANDARD_AFLAGS",
    "CATALOG_COLUMNS",
    "EXPOSURE_COLUMNS",
    "LIGHTCURVE_COLUMNS",
    "STANDARD_BAD_AFLAGS",
    "ParseError",
    "apply_standard_rejections",
    "exposure_id",
    "merge_catalogs",
    "parse_catalog",
    "parse_exposures",
    "parse_lightcurve",
    "parse_records",
    "stars_from_catalog",
]


class ParseError(ValueError):
    """A response is not in the shape the parser expects."""


# AFLAGS bits (daschlab 1.1.0, `daschlab.photometry.AFlags`). A test checks these
# against daschlab's own enum whenever daschlab is installed.
STANDARD_BAD_AFLAGS = (1 << 6) | (1 << 11) | (1 << 12) | (1 << 13) | (1 << 15)
BIT_STANDARD_AFLAGS = 1  # bit 0 of `reject`: rejected by the standard AFLAGS set

# Columns each parser reads. A response missing one of these has drifted.
CATALOG_COLUMNS = (
    "ref_text", "ref_number", "gsc_bin_index", "ra_deg", "dec_deg", "pos_epoch", "stdmag",
    "color", "pm_ra_masyr", "u_pm_ra_masyr", "pm_dec_masyr", "u_pm_dec_masyr", "class",
    "v_flag", "mag_flag", "num_matches", "dra_asec", "ddec_asec",
)  # fmt: skip
EXPOSURE_COLUMNS = (
    "series", "platenum", "scannum", "mosnum", "expnum", "solnum", "class", "ra", "dec",
    "exptime", "expdate", "wcssource", "centerdist", "edgedist", "limMagApass",
    "medianColortermApass",
)  # fmt: skip
LIGHTCURVE_COLUMNS = (
    "date_jd", "series", "plate_number", "mosaic_number", "solution_number",
    "exposure_number", "limiting_mag_local", "ref_number", "gsc_bin_index",
    "sextractor_number", "magcal_magdep", "magcal_local_rms", "aflags", "a2flags", "bflags",
    "b2flags", "reject_flag", "fwhm_pix", "ellipticity", "plate_center_dist_deg",
    "blended_mag", "ra_deg", "dec_deg", "x_image", "y_image", "time_accuracy_days",
    "drad_rms2", "extinction",
)  # fmt: skip

_NAN = float("nan")


# ------------------------------------------------------------------------ records


def parse_records(
    lines: Sequence[str], required: Sequence[str] = ()
) -> tuple[list[str], list[list[str]]]:
    """Split a response into its header and rows, checking the shape.

    Raises `ParseError` when the response is empty, a row has a different number of
    fields from the header, or a required column is missing.
    """
    if not lines:
        raise ParseError("empty response")
    header = lines[0].split(",")
    if len(set(header)) != len(header):
        raise ParseError("duplicate column names in the header")
    missing = [c for c in required if c not in header]
    if missing:
        raise ParseError(f"response is missing column(s): {', '.join(missing)}")
    rows = []
    for n, line in enumerate(lines[1:], start=1):
        fields = line.split(",")
        if len(fields) != len(header):
            raise ParseError(f"row {n} has {len(fields)} fields, the header has {len(header)}")
        rows.append(fields)
    return header, rows


def _column(header: list[str], rows: list[list[str]], name: str) -> list[str]:
    i = header.index(name)
    return [r[i] for r in rows]


def _floats(values: list[str], name: str, *, required: bool = False) -> np.ndarray:
    """float64 array. An empty string is NaN, or an error when `required`. Unreadable text raises."""
    out = np.empty(len(values), dtype=np.float64)
    for i, v in enumerate(values):
        if v == "":
            if required:
                raise ParseError(f"column {name!r}, row {i + 1}: empty value in a required column")
            out[i] = _NAN
            continue
        try:
            out[i] = float(v)
        except ValueError:
            raise ParseError(f"column {name!r}, row {i + 1}: cannot read {v!r} as a number") from None
    return out


def _ints(values: list[str], name: str, default: int | None = 0) -> np.ndarray:
    """int64 array. An empty string reads as `default`; with None it raises."""
    out = np.empty(len(values), dtype=np.int64)
    for i, v in enumerate(values):
        if v == "":
            if default is None:
                raise ParseError(f"column {name!r}, row {i + 1}: empty value")
            out[i] = default
            continue
        try:
            out[i] = int(v)
        except ValueError:
            raise ParseError(f"column {name!r}, row {i + 1}: cannot read {v!r} as an integer") from None
    return out


def _array(values: np.ndarray, typ: pa.DataType, null: np.ndarray | None = None) -> pa.Array:
    """Arrow array of `typ`. `null` is a boolean mask of rows to make null; NaN is always null."""
    mask = np.zeros(len(values), dtype=bool) if null is None else null.copy()
    if values.dtype.kind == "f":
        mask |= np.isnan(values)
    if pa.types.is_floating(typ):
        values = values.astype(np.float32 if typ == pa.float32() else np.float64)
    elif pa.types.is_integer(typ):
        info = np.iinfo(typ.to_pandas_dtype())
        too_big = np.zeros(len(values), dtype=bool) if info.max > np.iinfo(np.int64).max else values > info.max
        bad = (~mask) & ((values < info.min) | too_big)
        if bad.any():
            raise ParseError(f"value {values[bad][0]} does not fit in {typ}")
        values = np.where(mask, 0, values).astype(typ.to_pandas_dtype())
    return pa.array(values, type=typ, mask=mask)


def _strings(values: list[str], null_if_empty: bool = False) -> pa.Array:
    return pa.array([None if (null_if_empty and v == "") else v for v in values], type=pa.string())


# ------------------------------------------------------------------ exposure ids


def exposure_id(series: str, platenum: int, expnum: int | None, solnum: int | None) -> str:
    """A stable id for one exposure, the same in `measurements` and `exposures`.

    `{series}{platenum:05d}e{expnum}`, plus `s{solnum}` when the exposure has a WCS
    solution. A missing or negative `expnum` counts as 0.

    daschlab's own id uses the mosaic number (`m{mosnum}s{solnum}`). That is not safe
    as a key: re-scanned plates change mosaic, so 19% of T CrB's detections point at a
    mosaic the exposure list does not have, and scanned plates with no solution give
    duplicate ids (`me01112m1s-1` appears 4 times near T CrB). This form is unique
    across all 16,326 exposures near T CrB and joins every lightcurve row.
    """
    e = expnum if expnum is not None and expnum >= 0 else 0
    return f"{series}{platenum:05d}e{e}" + (f"s{solnum}" if solnum is not None and solnum >= 0 else "")


# ---------------------------------------------------------------------- catalog

_CATALOG_SCHEMA = pa.schema(
    [
        pa.field("ref_text", pa.string()),
        pa.field("ref_number", pa.uint64()),
        pa.field("gsc_bin_index", pa.uint32()),
        pa.field("ra_deg", pa.float64()),
        pa.field("dec_deg", pa.float64()),
        pa.field("pos_epoch", pa.float64()),
        pa.field("stdmag", pa.float32()),
        pa.field("color", pa.float32()),
        pa.field("pm_ra_masyr", pa.float32()),
        pa.field("pm_dec_masyr", pa.float32()),
        pa.field("u_pm_ra_masyr", pa.float32()),
        pa.field("u_pm_dec_masyr", pa.float32()),
        pa.field("class", pa.uint16()),
        pa.field("v_flag", pa.uint16()),
        pa.field("mag_flag", pa.uint16()),
        pa.field("num_matches", pa.uint32()),
        pa.field("dra_asec", pa.float64()),
        pa.field("ddec_asec", pa.float64()),
    ]
)


def parse_catalog(lines: Sequence[str]) -> pa.Table:
    """A `querycat` response as an Arrow table (not yet one of our schema tables).

    `stdmag` and `color` of 99 or more mean "no value", as in daschlab, and become null.
    """
    header, rows = parse_records(lines, CATALOG_COLUMNS)

    def col(name: str) -> list[str]:
        return _column(header, rows, name)

    def f(name: str) -> np.ndarray:
        return _floats(col(name), name)

    stdmag, color = f("stdmag"), f("color")
    data = {
        "ref_text": _strings(col("ref_text")),
        "ref_number": _array(_ints(col("ref_number"), "ref_number", None), pa.uint64()),
        "gsc_bin_index": _array(_ints(col("gsc_bin_index"), "gsc_bin_index", None), pa.uint32()),
        "ra_deg": _array(f("ra_deg"), pa.float64()),
        "dec_deg": _array(f("dec_deg"), pa.float64()),
        "pos_epoch": _array(f("pos_epoch"), pa.float64()),
        "stdmag": _array(stdmag, pa.float32(), stdmag >= 99),
        "color": _array(color, pa.float32(), color >= 99),
        "pm_ra_masyr": _array(f("pm_ra_masyr"), pa.float32()),
        "pm_dec_masyr": _array(f("pm_dec_masyr"), pa.float32()),
        "u_pm_ra_masyr": _array(f("u_pm_ra_masyr"), pa.float32()),
        "u_pm_dec_masyr": _array(f("u_pm_dec_masyr"), pa.float32()),
        "class": _array(_ints(col("class"), "class", 0), pa.uint16()),
        "v_flag": _array(_ints(col("v_flag"), "v_flag", 0), pa.uint16()),
        "mag_flag": _array(_ints(col("mag_flag"), "mag_flag", 0), pa.uint16()),
        "num_matches": _array(_ints(col("num_matches"), "num_matches", 0), pa.uint32()),
        "dra_asec": _array(f("dra_asec"), pa.float64()),
        "ddec_asec": _array(f("ddec_asec"), pa.float64()),
    }
    return pa.table(data, schema=_CATALOG_SCHEMA)


def merge_catalogs(tables: Sequence[pa.Table]) -> tuple[pa.Table, int]:
    """Combine catalogs from overlapping boxes, keeping the first row of each `ref_number`.

    Returns the merged table and how many sources appeared in more than one input.
    """
    if not tables:
        return _CATALOG_SCHEMA.empty_table(), 0
    combined = pa.concat_tables(list(tables))
    refs = combined.column("ref_number").to_numpy()
    _, first_index, counts = np.unique(refs, return_index=True, return_counts=True)
    keep = np.sort(first_index)
    return combined.take(pa.array(keep)), int((counts > 1).sum())


def stars_from_catalog(
    catalog: pa.Table, tile: Tile, *, refcat: str = "apass", max_b: float = 13.5
) -> pa.Table:
    """The `stars` table: sources in the tile with a magnitude of `max_b` or brighter.

    `max_b` is Johnson B for the APASS catalog. `n_points` is the DASCH detection count.
    """
    ra = catalog.column("ra_deg").to_numpy()
    dec = catalog.column("dec_deg").to_numpy()
    mag = catalog.column("stdmag").to_numpy(zero_copy_only=False)  # nulls become NaN
    keep = np.asarray(in_tile(tile, ra, dec)) & np.isfinite(mag) & (mag <= max_b)
    sub = catalog.filter(pa.array(keep))
    n = sub.num_rows
    data = {
        "ref_number": sub.column("ref_number"),
        "refcat": pa.array([refcat] * n, type=pa.string()),
        "ref_text": sub.column("ref_text"),
        "gsc_bin_index": sub.column("gsc_bin_index"),
        "ra_deg": sub.column("ra_deg"),
        "dec_deg": sub.column("dec_deg"),
        "pm_ra_masyr": sub.column("pm_ra_masyr"),
        "pm_dec_masyr": sub.column("pm_dec_masyr"),
        "apass_b": sub.column("stdmag"),
        "color": sub.column("color"),
        "num_matches": sub.column("num_matches"),
        "tile": pa.array([tile.name] * n, type=pa.string()),
        "n_points": sub.column("num_matches"),
    }
    table = pa.table(data, schema=schemas.SCHEMAS["stars"].schema)
    return table.sort_by("ref_number")


# -------------------------------------------------------------------- exposures


_TIMESTAMP = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?Z?$"
)


def _parse_timestamp(value: str, row: int) -> dt.datetime | None:
    """`expdate` as UTC. Real data has several forms: with or without a trailing Z,
    minute-only (`1930-01-01T03:30`), and logbook slips such as `05:07:60.0`, which
    astropy reads as a leap second (05:08:00). The time of day is added to the date as a
    duration, so a 60 is carried over instead of rejected. Empty is null. A date that
    does not exist, or text that is not a timestamp, raises.
    """
    if value == "":
        return None
    m = _TIMESTAMP.match(value)
    if not m:
        raise ParseError(f"column 'expdate', row {row}: cannot read {value!r} as a date")
    year, month, day, hour, minute = (int(m.group(k)) for k in range(1, 6))
    seconds = float(m.group(6)) if m.group(6) else 0.0
    if hour > 24 or minute > 59 or seconds >= 120:
        raise ParseError(f"column 'expdate', row {row}: {value!r} is not a plausible time of day")
    try:
        base = dt.datetime(year, month, day, tzinfo=dt.UTC)
    except ValueError:
        raise ParseError(f"column 'expdate', row {row}: {value!r} is not a real date") from None
    return base + dt.timedelta(hours=hour, minutes=minute, seconds=seconds)


def parse_exposures(lines: Sequence[str]) -> pa.Table:
    """A `queryexps` response as the `exposures` table.

    `mosnum` and `solnum` are null when the plate is unscanned (`scannum` -1) or the
    value is negative; `expnum` is null when negative. `series_kind` is not available
    from this endpoint and stays null until Sprint 3.
    """
    header, rows = parse_records(lines, EXPOSURE_COLUMNS)

    def col(name: str) -> list[str]:
        return _column(header, rows, name)

    def f(name: str) -> np.ndarray:
        return _floats(col(name), name)

    series = col("series")
    platenum = _ints(col("platenum"), "platenum", None)
    scannum = _ints(col("scannum"), "scannum", None)
    mos = _ints(col("mosnum"), "mosnum", None)
    sol = _ints(col("solnum"), "solnum", None)
    exp = _ints(col("expnum"), "expnum", None)
    unscanned = scannum == -1
    mos_null, sol_null, exp_null = unscanned | (mos < 0), unscanned | (sol < 0), exp < 0

    ids = [
        exposure_id(
            series[i],
            int(platenum[i]),
            None if exp_null[i] else int(exp[i]),
            None if sol_null[i] else int(sol[i]),
        )
        for i in range(len(rows))
    ]
    ra, dec = f("ra"), f("dec")
    bad_pos = (ra == 999) | (dec == 99)
    colorterm = f("medianColortermApass")
    colorterm[~np.isfinite(colorterm)] = _NAN
    wcs = col("wcssource")
    dates = [_parse_timestamp(v, i + 1) for i, v in enumerate(col("expdate"))]

    data = {
        "exposure_id": pa.array(ids, type=pa.string()),
        "series": _strings(series),
        "series_kind": pa.array([None] * len(rows), type=pa.string()),
        "platenum": _array(platenum, pa.uint32()),
        "mosnum": _array(mos, pa.uint32(), mos_null),
        "solnum": _array(sol, pa.uint32(), sol_null),
        "expnum": _array(exp, pa.int32(), exp_null),
        "plate_class": _strings(col("class"), null_if_empty=True),
        "exptime": _array(f("exptime"), pa.float32()),
        "obs_date": pa.array(dates, type=pa.timestamp("us", tz="UTC")),
        "wcssource": _strings(wcs, null_if_empty=True),
        "has_imaging": pa.array([w.lower() == "imwcs" for w in wcs], type=pa.bool_()),
        "lim_mag_apass": _array(f("limMagApass"), pa.float32()),
        "median_colorterm_apass": _array(colorterm, pa.float32()),
        "center_distance": _array(f("centerdist"), pa.float32()),
        "edge_distance": _array(f("edgedist"), pa.float32()),
        "ra_deg": _array(ra, pa.float64(), bad_pos),
        "dec_deg": _array(dec, pa.float64(), bad_pos),
    }
    return pa.table(data, schema=schemas.SCHEMAS["exposures"].schema)


# ------------------------------------------------------------------ lightcurves

# (our column, raw column, arrow type, extra null rule). The rule says which values
# become null besides non-detections (always null) and NaN (always null).
_NONDET_NULL = "nondet"  # null on non-detections
_ALWAYS = "always"  # present on every row
_LC_FLOATS = (
    ("magcal_magdep", "magcal_magdep", pa.float32(), _NONDET_NULL, ()),
    ("magcal_local_rms", "magcal_local_rms", pa.float32(), _NONDET_NULL, (99.0,)),
    ("limiting_mag_local", "limiting_mag_local", pa.float32(), _ALWAYS, ()),
    ("fwhm_image", "fwhm_pix", pa.float32(), _NONDET_NULL, ()),
    ("ellipticity", "ellipticity", pa.float32(), _NONDET_NULL, ()),
    ("plate_dist", "plate_center_dist_deg", pa.float32(), _NONDET_NULL, ()),
    ("blended_mag", "blended_mag", pa.float32(), _NONDET_NULL, (0.0, 99.0)),
    ("ra_deg", "ra_deg", pa.float64(), _NONDET_NULL, ()),
    ("dec_deg", "dec_deg", pa.float64(), _NONDET_NULL, ()),
    ("image_x", "x_image", pa.float32(), _NONDET_NULL, ()),
    ("image_y", "y_image", pa.float32(), _NONDET_NULL, ()),
    ("time_accuracy", "time_accuracy_days", pa.float32(), _NONDET_NULL, ()),
    ("drad_rms2", "drad_rms2", pa.float32(), _NONDET_NULL, (99.0,)),
    ("extinction", "extinction", pa.float32(), _NONDET_NULL, ()),
)
_LC_UINTS = (
    ("aflags", "aflags"),
    ("a2flags", "a2flags"),
    ("bflags", "bflags"),
    ("b2flags", "b2flags"),
    ("local_bin_reject_flag", "reject_flag"),
)


# Rows are sorted by time, then by these columns. The API returns rows that share a
# timestamp in a different order on every call (67 of 4,740 positions differ between two
# fetches of one star), so a full key makes the table the same whichever order was stored.
# Nulls sort last.
_ROW_ORDER = (
    "time", "series", "platenum", "solnum", "expnum", "mosnum", "limiting_mag_local",
    "magcal_magdep", "image_x", "image_y",
)  # fmt: skip


def parse_lightcurve(lines: Sequence[str], ref_number: int) -> pa.Table:
    """A `lightcurve` response as the `measurements` table, in a fixed row order.

    Sorted by time, with ties broken by `_ROW_ORDER`, so the result does not depend on the
    order the API happened to return tied rows in.

    `ref_number` comes from the request, because non-detection rows have none. A row
    carrying a different non-zero `ref_number` raises.
    """
    header, rows = parse_records(lines, LIGHTCURVE_COLUMNS)
    n = len(rows)

    def col(name: str) -> list[str]:
        return _column(header, rows, name)

    raw_ref = _ints(col("ref_number"), "ref_number", 0)
    wrong = (raw_ref != 0) & (raw_ref != ref_number)
    if wrong.any():
        raise ParseError(
            f"row {int(np.flatnonzero(wrong)[0]) + 1} has ref_number {int(raw_ref[wrong][0])}, "
            f"but the request was for {ref_number}"
        )

    nondet = (_ints(col("gsc_bin_index"), "gsc_bin_index", 0) == 0) & (
        _ints(col("sextractor_number"), "sextractor_number", 0) == 0
    )
    series = col("series")
    platenum = _ints(col("plate_number"), "plate_number", None)
    mos = _ints(col("mosaic_number"), "mosaic_number", None)
    sol = _ints(col("solution_number"), "solution_number", None)
    expnum = _ints(col("exposure_number"), "exposure_number", 0)  # empty reads as 0, as in daschlab

    data: dict[str, pa.Array] = {
        "ref_number": pa.array(np.full(n, ref_number, dtype=np.uint64), type=pa.uint64()),
        "exposure_id": pa.array(
            [
                exposure_id(series[i], int(platenum[i]), int(expnum[i]), int(sol[i]))
                for i in range(n)
            ],
            type=pa.string(),
        ),
        "time": _array(_floats(col("date_jd"), "date_jd", required=True), pa.float64()),
        "series": _strings(series),
        "platenum": _array(platenum, pa.uint32()),
        "mosnum": _array(mos, pa.uint32()),
        "solnum": _array(sol, pa.uint32()),
        "expnum": _array(expnum, pa.int32()),
        "reject": pa.array(np.zeros(n, dtype=np.uint64), type=pa.uint64()),
    }
    for ours, raw, typ, rule, sentinels in _LC_FLOATS:
        values = _floats(col(raw), raw, required=(rule == _ALWAYS))
        null = nondet.copy() if rule == _NONDET_NULL else np.zeros(n, dtype=bool)
        for s in sentinels:
            null |= values == s
        data[ours] = _array(values, typ, null)
    for ours, raw in _LC_UINTS:
        data[ours] = _array(_ints(col(raw), raw, 0), pa.uint32(), nondet)

    table = pa.table(data, schema=schemas.SCHEMAS["measurements"].schema)
    return table.sort_by([(name, "ascending") for name in _ROW_ORDER])


# ------------------------------------------------------------------- rejections


def apply_standard_rejections(table: pa.Table) -> pa.Table:
    """Set bit 0 of `reject` on rows with any of the five standard bad AFLAGS.

    Non-detections have null `aflags` and are never rejected, as in daschlab. Returns
    a new table. Rows already rejected keep their other bits.
    """
    aflags = table.column("aflags").to_numpy(zero_copy_only=False)
    aflags = np.where(np.isnan(aflags.astype(float)), 0, aflags).astype(np.int64)
    bad = (aflags & STANDARD_BAD_AFLAGS) != 0
    reject = table.column("reject").to_numpy().astype(np.uint64)
    reject = np.where(bad, reject | np.uint64(BIT_STANDARD_AFLAGS), reject).astype(np.uint64)
    i = table.schema.get_field_index("reject")
    return table.set_column(i, table.schema.field(i), pa.array(reject, type=pa.uint64()))

