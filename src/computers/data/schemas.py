"""Typed schemas for the core tables (plan 4.5).

PyArrow schemas are the single source of truth, because they are the objects used
to write Parquet. Column names follow daschlab's names, not the raw API's
(docs/decisions/0001-sprint0-findings.md, W2).

Changing any table means raising its ``version`` and regenerating the lock with
``computers schema lock``. A test fails otherwise, so a change is always deliberate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pyarrow as pa

__all__ = [
    "SCHEMAS",
    "SchemaError",
    "TableSpec",
    "docs_text",
    "fingerprint",
    "lock_dict",
    "lock_text",
    "validate",
]


class SchemaError(ValueError):
    """A table does not match its schema."""


@dataclass(frozen=True)
class TableSpec:
    name: str
    version: int
    primary_key: tuple[str, ...]
    schema: pa.Schema
    summary: str

    def __post_init__(self) -> None:
        names = self.schema.names
        if len(set(names)) != len(names):
            raise ValueError(f"{self.name}: duplicate column names")
        missing = [k for k in self.primary_key if k not in names]
        if missing:
            raise ValueError(f"{self.name}: primary key columns not in schema: {missing}")


def _f(name: str, typ: pa.DataType, nullable: bool = False) -> pa.Field:
    return pa.field(name, typ, nullable=nullable)


_U64, _U32, _I32, _I64 = pa.uint64(), pa.uint32(), pa.int32(), pa.int64()
_F64, _F32, _STR, _BOOL = pa.float64(), pa.float32(), pa.string(), pa.bool_()
_TS = pa.timestamp("us", tz="UTC")
_STRLIST = pa.list_(_STR)

_TABLES = [
    TableSpec(
        "stars",
        1,
        ("ref_number",),
        pa.schema(
            [
                _f("ref_number", _U64),
                _f("refcat", _STR),
                _f("ref_text", _STR),
                _f("gsc_bin_index", _U32),
                _f("ra_deg", _F64),
                _f("dec_deg", _F64),
                _f("pm_ra_masyr", _F32, True),
                _f("pm_dec_masyr", _F32, True),
                _f("apass_b", _F32, True),
                _f("color", _F32, True),
                _f("num_matches", _U32),
                _f("tile", _STR),
                _f("n_points", _U32, True),
            ]
        ),
        "One catalog source.",
    ),
    TableSpec(
        "measurements",
        1,
        ("ref_number", "exposure_id"),
        pa.schema(
            [
                _f("ref_number", _U64),
                _f("exposure_id", _STR),
                _f("time", _F64),
                _f("magcal_magdep", _F32, True),
                _f("magcal_local_rms", _F32, True),
                _f("limiting_mag_local", _F32),
                _f("series", _STR),
                _f("platenum", _U32),
                _f("mosnum", _U32, True),
                _f("solnum", _U32, True),
                _f("expnum", _I32, True),
                _f("aflags", _U32, True),
                _f("a2flags", _U32, True),
                _f("bflags", _U32, True),
                _f("b2flags", _U32, True),
                _f("local_bin_reject_flag", _U32, True),
                _f("fwhm_image", _F32, True),
                _f("ellipticity", _F32, True),
                _f("plate_dist", _F32, True),
                _f("blended_mag", _F32, True),
                _f("ra_deg", _F64, True),
                _f("dec_deg", _F64, True),
                _f("image_x", _F32, True),
                _f("image_y", _F32, True),
                _f("time_accuracy", _F32, True),
                _f("drad_rms2", _F32, True),
                _f("extinction", _F32, True),
                _f("reject", _U64),
            ]
        ),
        "One star on one exposure. `time` is HJD in days. A non-detection has a null "
        "`magcal_magdep` and keeps its `limiting_mag_local`. `exposure_id` is "
        "`{series}{platenum:05d}e{expnum}` plus `s{solnum}` when there is a solution, for "
        "example `ac33494e0s0`; it joins to `exposures` (daschlab's mosaic-based id does not).",
    ),
    TableSpec(
        "exposures",
        1,
        ("exposure_id",),
        pa.schema(
            [
                _f("exposure_id", _STR),
                _f("series", _STR),
                _f("series_kind", _STR, True),
                _f("platenum", _U32),
                _f("mosnum", _U32, True),
                _f("solnum", _U32, True),
                _f("expnum", _I32, True),
                _f("plate_class", _STR, True),
                _f("exptime", _F32, True),
                _f("obs_date", _TS, True),
                _f("wcssource", _STR, True),
                _f("has_imaging", _BOOL),
                _f("lim_mag_apass", _F32, True),
                _f("median_colorterm_apass", _F32, True),
                _f("center_distance", _F32, True),
                _f("edge_distance", _F32, True),
                _f("ra_deg", _F64, True),
                _f("dec_deg", _F64, True),
            ]
        ),
        "One exposure. Some plates carry several exposures. `center_distance`, "
        "`edge_distance` (cm on the plate) and `lim_mag_apass` are relative to the position "
        "queried, so they are null in tile tables; use `plate_dist` and `limiting_mag_local` "
        "in `measurements`. `median_colorterm_apass` diagnoses the "
        "emulsion (below -1.13 red, above -0.53 blue, else yellow; exactly 0.0 is suspect). "
        "`plate_class` is empty in `queryexps`, so it is null until filled from `/plates`.",
    ),
    TableSpec(
        "cutouts",
        1,
        ("cutout_id",),
        pa.schema(
            [
                _f("cutout_id", _STR),
                _f("ref_number", _U64),
                _f("exposure_id", _STR),
                _f("path", _STR),
                _f("ra_deg", _F64),
                _f("dec_deg", _F64),
                _f("offset_dx", _F32, True),
                _f("offset_dy", _F32, True),
                _f("offset_sigma", _F32, True),
                _f("classifier_a_score", _F32, True),
                _f("classifier_version", _STR, True),
            ]
        ),
        "One star on one plate. Offsets are in pixels from the cutout centre.",
    ),
    TableSpec(
        "candidates",
        1,
        ("candidate_id",),
        pa.schema(
            [
                _f("candidate_id", _STR),
                _f("ref_number", _U64),
                _f("tile", _STR),
                _f("event_type", _STR),
                _f("epoch_start", _F64),
                _f("epoch_end", _F64),
                _f("amplitude_mag", _F32),
                _f("single_plate", _BOOL),
                _f("l1_flags", _STRLIST),
                _f("l2_score", _F32, True),
                _f("snapshot_digest", _STR),
                _f("code_version", _STR),
            ]
        ),
        "One triggered event. Epochs are decimal years.",
    ),
    TableSpec(
        "verdicts",
        1,
        ("verdict_id",),
        pa.schema(
            [
                _f("verdict_id", _STR),
                _f("candidate_id", _STR),
                _f("verdict", _STR),
                _f("cause", _STR),
                _f("confidence", _F32),
                _f("evidence_ids", _STRLIST),
                _f("rationale", _STR),
                _f("model_id", _STR),
                _f("prompt_hash", _STR),
                _f("tokens_in", _I64, True),
                _f("tokens_out", _I64, True),
                _f("cost_usd", _F64, True),
                _f("created_at", _TS),
            ]
        ),
        "One judged candidate.",
    ),
    TableSpec(
        "labels",
        1,
        ("label_id",),
        pa.schema(
            [
                _f("label_id", _STR),
                _f("target_type", _STR),
                _f("target_id", _STR),
                _f("label", _STR),
                _f("defect_type", _STR, True),
                _f("labeller", _STR),
                _f("labelled_at", _TS),
                _f("note", _STR, True),
            ]
        ),
        "One human judgement of a cutout or a verdict.",
    ),
    TableSpec(
        "telemetry",
        1,
        ("run_id", "ref_number"),
        pa.schema(
            [
                _f("run_id", _STR),
                _f("ref_number", _U64),
                _f("stop_layer", _STR),
                _f("reason", _STR),
                _f("wall_seconds", _F32),
                _f("tokens", _I64, True),
                _f("cost_usd", _F64, True),
            ]
        ),
        "One star per patrol run: where it stopped, why, and what it cost.",
    ),
    TableSpec(
        "vsx",
        1,
        ("tile", "oid"),
        pa.schema(
            [
                _f("tile", _STR),
                _f("oid", _U64),
                _f("name", _STR),
                _f("var_flag", _I32, True),
                _f("ra_deg", _F64),
                _f("dec_deg", _F64),
                _f("vtype", _STR, True),
                _f("max_mag", _F32, True),
                _f("min_mag", _F32, True),
                _f("min_is_amplitude", _BOOL),
                _f("amplitude_mag", _F32, True),
                _f("period_d", _F64, True),
            ]
        ),
        "One entry of AAVSO's Variable Star Index inside a tile. `min_is_amplitude` is true "
        "when VSX gives the amplitude in the minimum column (flag `)`); `amplitude_mag` is "
        "then `min_mag`, otherwise `min_mag - max_mag`.",
    ),
    TableSpec(
        "vsx_match",
        1,
        ("ref_number",),
        pa.schema(
            [
                _f("ref_number", _U64),
                _f("tile", _STR),
                _f("vsx_oid", _U64, True),
                _f("vsx_name", _STR, True),
                _f("vsx_type", _STR, True),
                _f("vsx_period_d", _F64, True),
                _f("vsx_amplitude_mag", _F32, True),
                _f("sep_arcsec", _F32, True),
            ]
        ),
        "The nearest VSX entry for each patrol star, null when none lies within the search "
        "radius. Whether it counts as a match is decided by the reader against the match and "
        "exclusion radii in `thresholds.py`.",
    ),
]

SCHEMAS: dict[str, TableSpec] = {t.name: t for t in _TABLES}


# ------------------------------------------------------------------ fingerprints


def _canonical(spec: TableSpec) -> dict:
    return {
        "name": spec.name,
        "version": spec.version,
        "primary_key": list(spec.primary_key),
        "fields": [[f.name, str(f.type), f.nullable] for f in spec.schema],
    }


def fingerprint(spec: TableSpec) -> str:
    """sha256 of the table's canonical definition, field order included.

    The version is part of the definition. The lock stores the fingerprint
    *without* the version too (see `lock_dict`) so a bump alone is detectable.
    """
    text = json.dumps(_canonical(spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _structure_fingerprint(spec: TableSpec) -> str:
    """Fingerprint of the structure only (no version): what a version bump must follow."""
    canon = _canonical(spec)
    canon.pop("version")
    text = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def lock_dict() -> dict:
    return {
        name: {
            "version": spec.version,
            "fingerprint": fingerprint(spec),
            "structure": _structure_fingerprint(spec),
        }
        for name, spec in sorted(SCHEMAS.items())
    }


def lock_text() -> str:
    return json.dumps(lock_dict(), indent=2, sort_keys=True) + "\n"


def bump_violations(old: dict, new: dict) -> list[str]:
    """Tables whose structure changed without a higher version than in ``old``.

    ``old`` is an earlier lock (for example the one in git HEAD), ``new`` the
    current one. Regenerating the lock does not hide a missing bump from this check.
    Tables only present in one of the two are not violations.
    """
    problems = []
    for name in sorted(set(old) & set(new)):
        o, n = old[name], new[name]
        if n["version"] < o["version"]:
            problems.append(f"{name}: version went down ({o['version']} to {n['version']})")
        elif n["structure"] != o["structure"] and n["version"] == o["version"]:
            problems.append(
                f"{name}: columns or key changed but version is still {n['version']}; "
                "raise it"
            )
    return problems


# ---------------------------------------------------------------------- validate


def validate(name: str, table: pa.Table) -> None:
    """Raise `SchemaError` on the first problem. Never coerces.

    Order of checks: unknown table, missing columns, extra columns, column types,
    nulls in non-nullable columns.
    """
    spec = SCHEMAS.get(name)
    if spec is None:
        raise SchemaError(f"unknown table {name!r}; known: {', '.join(sorted(SCHEMAS))}")

    have = {f.name: f for f in table.schema}
    want = {f.name: f for f in spec.schema}

    for col in spec.schema.names:
        if col not in have:
            raise SchemaError(f"{name}: missing column {col!r}")
    for col in table.schema.names:
        if col not in want:
            raise SchemaError(f"{name}: unexpected column {col!r}")
    for col, field in want.items():
        if not have[col].type.equals(field.type):
            raise SchemaError(
                f"{name}.{col}: type is {have[col].type}, expected {field.type}"
            )
    for col, field in want.items():
        if not field.nullable and table.column(col).null_count:
            raise SchemaError(
                f"{name}.{col}: {table.column(col).null_count} null value(s) in a "
                "non-nullable column"
            )


# -------------------------------------------------------------------------- docs


def docs_text() -> str:
    lines = [
        "# Table schemas",
        "",
        (
            "Generated by `computers schema docs` from `src/computers/data/schemas.py`. "
            "Do not edit by hand. To change a table, raise its version in the schema file "
            "and run `computers schema lock`."
        ),
        "",
    ]
    for name, spec in sorted(SCHEMAS.items()):
        lines += [
            f"## `{name}` (version {spec.version})",
            "",
            spec.summary,
            "",
            (
                f"Primary key: {', '.join(f'`{k}`' for k in spec.primary_key)}. "
                f"Fingerprint: `{fingerprint(spec)[:16]}`."
            ),
            "",
            "| Column | Type | Nullable |",
            "|---|---|---|",
        ]
        for f in spec.schema:
            lines.append(f"| `{f.name}` | `{f.type}` | {'yes' if f.nullable else 'no'} |")
        lines.append("")
    return "\n".join(lines)
