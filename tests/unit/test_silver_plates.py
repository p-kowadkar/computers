import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from computers.data import dasch_parse as P
from computers.data import plates, schemas, silver

DIGEST = "sha256:" + "ab" * 32


def stars_table(n=3):
    s = schemas.SCHEMAS["stars"].schema
    cols = {
        "ref_number": [1, 2, 3][:n], "refcat": ["apass"] * n, "ref_text": ["a", "b", "c"][:n],
        "gsc_bin_index": [10, 11, 12][:n], "ra_deg": [1.0] * n, "dec_deg": [2.0] * n,
        "pm_ra_masyr": [None] * n, "pm_dec_masyr": [0.5] * n, "apass_b": [12.0] * n,
        "color": [0.4] * n, "num_matches": [5] * n, "tile": ["t"] * n, "n_points": [5] * n,
    }  # fmt: skip
    return pa.table({f.name: pa.array(cols[f.name], type=f.type) for f in s}, schema=s)


# ----------------------------------------------------------------------- silver


def test_silver_path_layout(tmp_path):
    assert silver.silver_path(tmp_path, "stars", "m44") == tmp_path / "silver" / "stars" / "tile=m44.parquet"
    with pytest.raises(ValueError, match="unknown table"):
        silver.silver_path(tmp_path, "nope", "m44")


def test_write_and_read_round_trip_with_metadata(tmp_path):
    path = silver.write_table("stars", stars_table(), tmp_path / "s" / "stars.parquet", snapshot_digest=DIGEST)
    back = silver.read_table(path)
    assert back.equals(stars_table(), check_metadata=False)
    assert back.schema.field("ref_number").type == pa.uint64()  # unsigned types survive
    meta = silver.read_metadata(path)
    assert meta["computers.snapshot_digest"] == DIGEST
    assert meta["computers.schema_version"] == "1"
    assert meta["computers.written_by"].startswith("computers/")


def test_invalid_table_is_rejected_before_anything_is_written(tmp_path):
    bad = stars_table().drop_columns(["tile"])
    target = tmp_path / "out" / "stars.parquet"
    with pytest.raises(schemas.SchemaError, match="missing column 'tile'"):
        silver.write_table("stars", bad, target, snapshot_digest=DIGEST)
    assert not target.exists() and not (tmp_path / "out").exists()


def test_a_failed_write_leaves_no_temp_file_and_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "stars.parquet"
    silver.write_table("stars", stars_table(2), target, snapshot_digest=DIGEST)
    before = target.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pq, "write_table", boom)
    with pytest.raises(OSError):
        silver.write_table("stars", stars_table(3), target, snapshot_digest=DIGEST)
    assert target.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["stars.parquet"]


def test_overwriting_replaces_the_file_whole(tmp_path):
    target = tmp_path / "stars.parquet"
    silver.write_table("stars", stars_table(2), target, snapshot_digest=DIGEST)
    silver.write_table("stars", stars_table(3), target, snapshot_digest=DIGEST)
    assert silver.read_table(target).num_rows == 3


def test_columns_are_written_in_schema_order(tmp_path):
    t = stars_table()
    shuffled = t.select(list(reversed(t.column_names)))
    path = silver.write_table("stars", shuffled, tmp_path / "s.parquet", snapshot_digest=DIGEST)
    assert silver.read_table(path).column_names == schemas.SCHEMAS["stars"].schema.names


def test_metadata_of_a_file_without_ours_is_empty(tmp_path):
    path = tmp_path / "plain.parquet"
    pq.write_table(pa.table({"a": [1]}), path)
    assert silver.read_metadata(path) == {}


# ----------------------------------------------------------------------- plates


def exposures(plates_list):
    """An exposures table from (series, platenum, expnum) triples."""
    header = list(P.EXPOSURE_COLUMNS)
    rows = []
    for series, num, exp in plates_list:
        d = {"series": series, "platenum": num, "scannum": 0, "mosnum": 0, "expnum": exp,
             "solnum": 0, "class": "", "ra": 1.0, "dec": 2.0, "exptime": "60", "expdate": "1930-01-01T00:00:00Z",
             "wcssource": "imwcs", "centerdist": 1, "edgedist": 1, "limMagApass": "", "medianColortermApass": ""}  # fmt: skip
        rows.append([d[h] for h in header])
    return P.parse_exposures([",".join(header)] + [",".join(str(v) for v in r) for r in rows])


def write(tmp_path, tile, plates_list):
    silver.write_table(
        "exposures", exposures(plates_list), silver.silver_path(tmp_path, "exposures", tile),
        snapshot_digest=DIGEST,
    )  # fmt: skip


def test_plate_set_counts_a_multi_exposure_plate_once():
    assert plates.plate_set(exposures([("ai", 7, 0), ("ai", 7, 1), ("ac", 7, 0)])) == {"ai00007", "ac00007"}


def test_overlap_between_two_tiles(tmp_path):
    write(tmp_path, "tcrb", [("ai", 1, 0), ("ai", 2, 0), ("ac", 3, 0), ("fa", 4, 0)])
    write(tmp_path, "tabby", [("ai", 1, 0), ("ai", 2, 0), ("ai", 2, 1), ("kb", 9, 0)])
    r = plates.load_report(tmp_path, ["tcrb", "tabby"])
    assert r.plates == {"tcrb": 4, "tabby": 3} and r.exposures == {"tcrb": 4, "tabby": 4}
    (pair,) = r.pairs
    assert (pair.a, pair.b, pair.shared) == ("tcrb", "tabby", 2)
    assert pair.share_of_a == pytest.approx(0.5) and pair.share_of_b == pytest.approx(2 / 3)
    assert pair.series == {"ai": 2}
    assert r.in_all == 2


def test_three_tiles_give_three_pairs_and_the_common_plates(tmp_path):
    write(tmp_path, "m44", [("ai", 1, 0), ("ai", 5, 0)])
    write(tmp_path, "tcrb", [("ai", 1, 0), ("ai", 6, 0)])
    write(tmp_path, "tabby", [("ai", 1, 0), ("ai", 7, 0)])
    r = plates.load_report(tmp_path)
    assert len(r.pairs) == 3 and all(p.shared == 1 for p in r.pairs) and r.in_all == 1


def test_no_shared_plates(tmp_path):
    write(tmp_path, "m44", [("ai", 1, 0)])
    write(tmp_path, "tcrb", [("ai", 2, 0)])
    r = plates.load_report(tmp_path)
    assert r.pairs[0].shared == 0 and r.in_all == 0
    assert "series: none" in plates.report_text(r)


def test_missing_tiles_are_skipped(tmp_path):
    write(tmp_path, "m44", [("ai", 1, 0)])
    r = plates.load_report(tmp_path)
    assert list(r.plates) == ["m44"] and r.pairs == []
    assert "only one tile" in plates.report_text(r)


def test_no_tables_says_what_to_run(tmp_path):
    assert "computers pull --stage exposures" in plates.report_text(plates.load_report(tmp_path))


def test_report_text_shows_counts_percentages_and_the_lower_bound_note(tmp_path):
    write(tmp_path, "tcrb", [("ai", i, 0) for i in range(10)])
    write(tmp_path, "tabby", [("ai", i, 0) for i in range(5, 15)])
    text = plates.report_text(plates.load_report(tmp_path, ["tcrb", "tabby"]))
    assert "5 shared (50.0% of tcrb, 50.0% of tabby)" in text and "ai 5" in text
    assert "lower bound" in text
