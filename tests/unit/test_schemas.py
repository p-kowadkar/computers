import datetime as dt
import json
from pathlib import Path

import pyarrow as pa
import pytest

from computers.cli import main
from computers.data import schemas

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "stars", "measurements", "exposures", "cutouts",
    "candidates", "verdicts", "labels", "telemetry", "vsx", "vsx_match",
}  # fmt: skip


def _value_for(typ: pa.DataType):
    if pa.types.is_integer(typ):
        return 1
    if pa.types.is_floating(typ):
        return 1.5
    if pa.types.is_string(typ):
        return "x"
    if pa.types.is_boolean(typ):
        return True
    if pa.types.is_timestamp(typ):
        return dt.datetime(1946, 2, 9, tzinfo=dt.UTC)
    if pa.types.is_list(typ):
        return []
    raise AssertionError(f"no sample value for {typ}")


def good_table(name: str) -> pa.Table:
    schema = schemas.SCHEMAS[name].schema
    cols = {f.name: pa.array([_value_for(f.type)], type=f.type) for f in schema}
    return pa.table(cols, schema=schema)


# ------------------------------------------------------------------- definition


def test_all_tables_exist_at_version_one():
    assert set(schemas.SCHEMAS) == EXPECTED_TABLES
    assert all(s.version == 1 for s in schemas.SCHEMAS.values())


def test_primary_keys_are_non_nullable_columns():
    for spec in schemas.SCHEMAS.values():
        for key in spec.primary_key:
            assert not spec.schema.field(key).nullable, (spec.name, key)


def test_measurements_follow_the_sprint0_findings():
    m = schemas.SCHEMAS["measurements"].schema
    assert "plate_quality_flag" not in m.names  # W3: it does not exist in the API
    assert "exposure_id" in m.names
    assert m.field("magcal_magdep").nullable  # a non-detection has no magnitude
    assert not m.field("limiting_mag_local").nullable
    assert m.field("reject").type == pa.uint64()
    for daschlab_name in ("fwhm_image", "plate_dist", "image_x", "mosnum", "solnum"):
        assert daschlab_name in m.names  # daschlab names, not raw API names
    for raw_name in ("date_jd", "plate_number", "fwhm_pix", "plate_center_dist_deg"):
        assert raw_name not in m.names


def test_bad_primary_key_is_rejected_at_construction():
    with pytest.raises(ValueError, match="primary key"):
        schemas.TableSpec("t", 1, ("nope",), pa.schema([pa.field("a", pa.int32())]), "")


# --------------------------------------------------------------------- validate


@pytest.mark.parametrize("name", sorted(EXPECTED_TABLES))
def test_conforming_table_validates(name):
    schemas.validate(name, good_table(name))


@pytest.mark.parametrize("name", sorted(EXPECTED_TABLES))
def test_empty_table_validates(name):
    schemas.validate(name, schemas.SCHEMAS[name].schema.empty_table())


def test_unknown_table():
    with pytest.raises(schemas.SchemaError, match="unknown table"):
        schemas.validate("nope", good_table("stars"))


def test_missing_column_is_named():
    t = good_table("stars").drop_columns(["tile"])
    with pytest.raises(schemas.SchemaError, match=r"stars: missing column 'tile'"):
        schemas.validate("stars", t)


def test_extra_column_is_named():
    t = good_table("stars").append_column("surprise", pa.array([1]))
    with pytest.raises(schemas.SchemaError, match=r"unexpected column 'surprise'"):
        schemas.validate("stars", t)


def test_wrong_type_is_named_and_not_coerced():
    t = good_table("stars")
    i = t.schema.get_field_index("ra_deg")
    t = t.set_column(i, "ra_deg", pa.array([1.5], type=pa.float32()))
    with pytest.raises(schemas.SchemaError, match=r"stars\.ra_deg: type is float"):
        schemas.validate("stars", t)


def test_null_in_non_nullable_column():
    t = good_table("stars")
    i = t.schema.get_field_index("tile")
    t = t.set_column(i, t.schema.field("tile"), pa.array([None], type=pa.string()))
    with pytest.raises(schemas.SchemaError, match=r"stars\.tile: 1 null"):
        schemas.validate("stars", t)


def test_null_in_nullable_column_is_fine():
    t = good_table("measurements")
    i = t.schema.get_field_index("magcal_magdep")
    t = t.set_column(i, t.schema.field("magcal_magdep"), pa.array([None], type=pa.float32()))
    schemas.validate("measurements", t)


def test_first_problem_is_reported_in_documented_order():
    # Missing column beats wrong type.
    t = good_table("stars").drop_columns(["tile"])
    i = t.schema.get_field_index("ra_deg")
    t = t.set_column(i, "ra_deg", pa.array([1.5], type=pa.float32()))
    with pytest.raises(schemas.SchemaError, match="missing column"):
        schemas.validate("stars", t)


def test_column_order_does_not_matter():
    t = good_table("stars")
    schemas.validate("stars", t.select(list(reversed(t.column_names))))


# ----------------------------------------------------------------- fingerprints


def test_fingerprint_is_stable_and_distinct():
    fps = {n: schemas.fingerprint(s) for n, s in schemas.SCHEMAS.items()}
    assert fps == {n: schemas.fingerprint(s) for n, s in schemas.SCHEMAS.items()}
    assert len(set(fps.values())) == len(fps)
    assert all(len(v) == 64 for v in fps.values())


def _variant(spec: schemas.TableSpec, **kw) -> schemas.TableSpec:
    args = {
        "name": spec.name,
        "version": spec.version,
        "primary_key": spec.primary_key,
        "schema": spec.schema,
        "summary": spec.summary,
    }
    args.update(kw)
    return schemas.TableSpec(**args)


def test_fingerprint_changes_with_each_kind_of_edit():
    spec = schemas.SCHEMAS["stars"]
    base = schemas.fingerprint(spec)
    added = spec.schema.append(pa.field("extra", pa.int32()))
    retyped = spec.schema.set(
        spec.schema.get_field_index("n_points"), pa.field("n_points", pa.int64(), True)
    )
    renullable = spec.schema.set(
        spec.schema.get_field_index("tile"), pa.field("tile", pa.string(), True)
    )
    assert schemas.fingerprint(_variant(spec, schema=added)) != base
    assert schemas.fingerprint(_variant(spec, schema=retyped)) != base
    assert schemas.fingerprint(_variant(spec, schema=renullable)) != base
    assert schemas.fingerprint(_variant(spec, primary_key=("ref_number", "tile"))) != base
    assert schemas.fingerprint(_variant(spec, version=2)) != base
    reordered = pa.schema(list(reversed(list(spec.schema))))
    assert schemas.fingerprint(_variant(spec, schema=reordered)) != base


def test_summary_text_does_not_change_the_fingerprint():
    spec = schemas.SCHEMAS["stars"]
    assert schemas.fingerprint(_variant(spec, summary="reworded")) == schemas.fingerprint(spec)


# ------------------------------------------------------------- the committed lock


def test_committed_lock_matches_the_code():
    committed = (ROOT / "docs" / "schemas.lock.json").read_text(encoding="utf-8")
    assert committed == schemas.lock_text(), (
        "docs/schemas.lock.json is stale. If you changed a table, raise its version "
        "in schemas.py, then run `computers schema lock`."
    )


def test_committed_docs_match_the_code():
    committed = (ROOT / "docs" / "schemas.md").read_text(encoding="utf-8")
    assert committed == schemas.docs_text(), "docs/schemas.md is stale: `computers schema docs`"


def test_lock_detects_a_change_without_a_bump(monkeypatch):
    """The failure the lock exists for: a column added, version left at 1."""
    spec = schemas.SCHEMAS["stars"]
    changed = _variant(spec, schema=spec.schema.append(pa.field("extra", pa.int32(), True)))
    monkeypatch.setitem(schemas.SCHEMAS, "stars", changed)
    committed = (ROOT / "docs" / "schemas.lock.json").read_text(encoding="utf-8")
    assert committed != schemas.lock_text()


# -------------------------------------------------------------- bump violations


def _lock(version=1, structure="a", fp="f"):
    return {"t": {"version": version, "fingerprint": fp, "structure": structure}}


def test_bump_violation_when_structure_changes_at_same_version():
    problems = schemas.bump_violations(_lock(1, "a"), _lock(1, "b"))
    assert len(problems) == 1 and "raise it" in problems[0]


def test_no_violation_when_version_is_raised():
    assert schemas.bump_violations(_lock(1, "a"), _lock(2, "b")) == []


def test_no_violation_when_nothing_changed():
    assert schemas.bump_violations(_lock(3, "a"), _lock(3, "a")) == []


def test_version_decrease_is_a_violation():
    assert "went down" in schemas.bump_violations(_lock(2, "a"), _lock(1, "a"))[0]


def test_new_and_removed_tables_are_not_violations():
    assert schemas.bump_violations({}, _lock()) == []
    assert schemas.bump_violations(_lock(), {}) == []


# --------------------------------------------------------------------- cli check


def _write_repo(root: Path, lock: str | None = None, docs: str | None = None):
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "schemas.lock.json").write_text(lock or schemas.lock_text())
    (root / "docs" / "schemas.md").write_text(docs or schemas.docs_text())


def test_cli_check_passes_on_fresh_files(tmp_path, capsys):
    _write_repo(tmp_path)
    assert main(["schema", "check", "--root", str(tmp_path)]) == 0
    assert "ok" in capsys.readouterr().out


def test_cli_check_fails_on_stale_lock(tmp_path, capsys):
    stale = json.loads(schemas.lock_text())
    stale["stars"]["fingerprint"] = "0" * 64
    _write_repo(tmp_path, lock=json.dumps(stale, indent=2, sort_keys=True) + "\n")
    assert main(["schema", "check", "--root", str(tmp_path)]) == 1
    assert "schemas.lock.json is stale" in capsys.readouterr().err


def test_cli_check_fails_on_missing_files(tmp_path, capsys):
    assert main(["schema", "check", "--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "lock" in err and "docs" in err


def test_cli_lock_and_docs_write_files(tmp_path):
    assert main(["schema", "lock", "--root", str(tmp_path)]) == 0
    assert main(["schema", "docs", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "docs" / "schemas.lock.json").read_text() == schemas.lock_text()
    assert (tmp_path / "docs" / "schemas.md").read_text() == schemas.docs_text()
