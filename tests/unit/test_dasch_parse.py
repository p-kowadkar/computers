import datetime as dt
import gzip
import json
import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from computers.data import dasch_parse as P
from computers.data import schemas
from computers.data.tiles import TILES, Tile

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "spike" / "raw"
HAVE_RAW = (RAW / "tcrb_lightcurve.json.gz").exists()
NAN = float("nan")


def load_raw(name):
    return json.loads(gzip.open(RAW / f"{name}.json.gz").read())


# ----------------------------------------------------------------- builders


def response(header, rows):
    return [",".join(header)] + [",".join(str(v) for v in r) for r in rows]


def catalog_response(rows):
    header = list(P.CATALOG_COLUMNS)
    full = []
    for r in rows:
        d = {
            "ref_text": "APASS_X", "ref_number": 415593021255513, "gsc_bin_index": 121096954,
            "ra_deg": 239.9, "dec_deg": 25.9, "pos_epoch": 2000.0, "stdmag": 12.0, "color": 0.5,
            "pm_ra_masyr": 0, "u_pm_ra_masyr": 0, "pm_dec_masyr": 0, "u_pm_dec_masyr": 0,
            "class": 0, "v_flag": 1, "mag_flag": 0, "num_matches": 100, "dra_asec": 1.0,
            "ddec_asec": 2.0,
        }  # fmt: skip
        d.update(r)
        full.append([d[h] for h in header])
    return response(header, full)


def exposure_response(rows):
    header = list(P.EXPOSURE_COLUMNS)
    full = []
    for r in rows:
        d = {
            "series": "ai", "platenum": 100, "scannum": 0, "mosnum": 0, "expnum": 0, "solnum": 0,
            "class": "", "ra": 239.9, "dec": 25.9, "exptime": "60.00",
            "expdate": "1930-05-01T03:30:30.2Z", "wcssource": "imwcs", "centerdist": 5.0,
            "edgedist": 4.0, "limMagApass": "12.5", "medianColortermApass": "0.1",
        }  # fmt: skip
        d.update(r)
        full.append([d[h] for h in header])
    return response(header, full)


LC_DEFAULT = {
    "date_jd": "2420000.5", "series": "ac", "plate_number": 225, "mosaic_number": 0,
    "solution_number": 0, "exposure_number": 0, "limiting_mag_local": "13.1",
    "ref_number": 415593021255513, "gsc_bin_index": 121096954, "sextractor_number": 77,
    "magcal_magdep": "11.5", "magcal_local_rms": "0.15", "aflags": 0, "a2flags": 0,
    "bflags": 0, "b2flags": 0, "reject_flag": 0, "fwhm_pix": "5.0", "ellipticity": "0.1",
    "plate_center_dist_deg": "3.0", "blended_mag": "", "ra_deg": "239.87", "dec_deg": "25.92",
    "x_image": "100.5", "y_image": "200.5", "time_accuracy_days": "0.01", "drad_rms2": "1.2",
    "extinction": "0.05",
}  # fmt: skip
NONDET = {
    "ref_number": "", "gsc_bin_index": "", "sextractor_number": "", "exposure_number": "",
    "magcal_magdep": "", "magcal_local_rms": "", "aflags": "", "a2flags": "", "bflags": "",
    "b2flags": "", "reject_flag": "", "fwhm_pix": "", "ellipticity": "",
    "plate_center_dist_deg": "", "blended_mag": "", "ra_deg": "", "dec_deg": "", "x_image": "",
    "y_image": "", "time_accuracy_days": "", "drad_rms2": "", "extinction": "",
}  # fmt: skip
REF = 415593021255513


def lc_response(rows):
    header = list(P.LIGHTCURVE_COLUMNS)
    full = []
    for r in rows:
        d = dict(LC_DEFAULT)
        d.update(r)
        full.append([d[h] for h in header])
    return response(header, full)


def lc(rows):
    return P.parse_lightcurve(lc_response(rows), REF)


def row(table, i):
    return table.slice(i, 1).to_pylist()[0]


# ------------------------------------------------------------------ records


def test_parse_records_splits_header_and_rows():
    header, rows = P.parse_records(["a,b,c", "1,2,3", "4,,6"])
    assert header == ["a", "b", "c"] and rows == [["1", "2", "3"], ["4", "", "6"]]


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "empty response"),
        (["a,a", "1,2"], "duplicate column"),
        (["a,b", "1,2,3"], "row 1 has 3 fields"),
        (["a,b", "1,2", "3"], "row 2 has 1 fields"),
    ],
)
def test_parse_records_rejects_malformed_responses(lines, message):
    with pytest.raises(P.ParseError, match=message):
        P.parse_records(lines)


def test_parse_records_names_missing_required_columns():
    with pytest.raises(P.ParseError, match="missing column.*stdmag.*color"):
        P.parse_records(["a,b", "1,2"], required=["a", "stdmag", "color"])


def test_header_only_response_is_valid_and_empty():
    _, rows = P.parse_records(["a,b"])
    assert rows == []
    assert P.parse_catalog(response(P.CATALOG_COLUMNS, [])).num_rows == 0


def test_unreadable_number_names_the_column_and_row():
    bad = catalog_response([{}, {"ra_deg": "12.x"}])
    with pytest.raises(P.ParseError, match="column 'ra_deg', row 2: cannot read '12.x'"):
        P.parse_catalog(bad)


def test_unreadable_integer_names_the_column_and_row():
    with pytest.raises(P.ParseError, match="column 'gsc_bin_index', row 1"):
        P.parse_catalog(catalog_response([{"gsc_bin_index": "1.5"}]))


# ------------------------------------------------------------------ catalog


def test_catalog_values_and_types():
    cat = P.parse_catalog(catalog_response([{"stdmag": 12.34, "num_matches": 7}]))
    assert cat.schema.field("ref_number").type == pa.uint64()
    assert cat.schema.field("stdmag").type == pa.float32()
    r = row(cat, 0)
    assert r["stdmag"] == pytest.approx(12.34, abs=1e-6) and r["num_matches"] == 7
    assert r["ref_number"] == REF


def test_catalog_sentinel_magnitudes_and_colours_become_null():
    cat = P.parse_catalog(
        catalog_response([{"stdmag": 99.0, "color": 99.0}, {"stdmag": 99.99, "color": 0.5}])
    )
    assert cat.column("stdmag").null_count == 2 and cat.column("color").null_count == 1


def test_empty_num_matches_reads_as_zero_like_daschlab():
    cat = P.parse_catalog(catalog_response([{"num_matches": ""}]))
    assert row(cat, 0)["num_matches"] == 0


def test_empty_ref_number_raises():
    with pytest.raises(P.ParseError, match="ref_number"):
        P.parse_catalog(catalog_response([{"ref_number": ""}]))


def test_merge_catalogs_keeps_first_and_counts_overlap():
    a = P.parse_catalog(catalog_response([{"ref_number": 1}, {"ref_number": 2}]))
    b = P.parse_catalog(catalog_response([{"ref_number": 2, "ref_text": "dup"}, {"ref_number": 3}]))
    c = P.parse_catalog(catalog_response([{"ref_number": 2}, {"ref_number": 4}]))
    merged, multi = P.merge_catalogs([a, b, c])
    assert sorted(merged.column("ref_number").to_pylist()) == [1, 2, 3, 4]
    assert multi == 1  # only ref_number 2 was seen more than once
    assert "dup" not in merged.column("ref_text").to_pylist()  # first occurrence wins
    assert P.merge_catalogs([])[0].num_rows == 0


def test_stars_from_catalog_applies_tile_and_brightness_cut():
    tile = Tile("t", 100.0, 20.0)
    rows = [
        {"ref_number": 1, "ra_deg": 100.0, "dec_deg": 20.0, "stdmag": 13.5},  # on the cut: kept
        {"ref_number": 2, "ra_deg": 100.0, "dec_deg": 20.0, "stdmag": 13.51},  # just fainter
        {"ref_number": 3, "ra_deg": 100.0, "dec_deg": 22.5, "stdmag": 10.0},  # outside the tile
        {"ref_number": 4, "ra_deg": 100.0, "dec_deg": 20.5, "stdmag": 99.0},  # no magnitude
        {"ref_number": 5, "ra_deg": 100.2, "dec_deg": 19.5, "stdmag": 8.0},  # bright, inside
    ]
    stars = P.stars_from_catalog(P.parse_catalog(catalog_response(rows)), tile)
    schemas.validate("stars", stars)
    assert stars.column("ref_number").to_pylist() == [1, 5]  # sorted by ref_number
    r = row(stars, 0)
    assert r["tile"] == "t" and r["refcat"] == "apass"
    assert r["apass_b"] == pytest.approx(13.5) and r["n_points"] == r["num_matches"] == 100


def test_stars_from_catalog_with_nothing_in_the_tile():
    cat = P.parse_catalog(catalog_response([{"stdmag": 20.0}]))
    stars = P.stars_from_catalog(cat, TILES["tcrb"])
    assert stars.num_rows == 0
    schemas.validate("stars", stars)


def test_stars_ra_wraparound_is_handled():
    tile = Tile("w", 359.95, 10.0)
    rows = [{"ref_number": 1, "ra_deg": 0.3, "dec_deg": 10.0, "stdmag": 12.0}]
    assert P.stars_from_catalog(P.parse_catalog(catalog_response(rows)), tile).num_rows == 1


# ----------------------------------------------------------------- exposure id


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("ai", 30093, 0, 0), "ai30093e0s0"),
        (("a", 7, 2, 1), "a00007e2s1"),
        (("ai", 30093, 0, None), "ai30093e0"),  # no WCS solution
        (("ai", 30093, None, 0), "ai30093e0s0"),  # missing exposure number counts as 0
        (("ai", 30093, -1, -1), "ai30093e0"),
    ],
)
def test_exposure_id(args, expected):
    assert P.exposure_id(*args) == expected


# ----------------------------------------------------------------- exposures


def test_exposure_basic_row():
    t = P.parse_exposures(exposure_response([{}]))
    schemas.validate("exposures", t)
    r = row(t, 0)
    assert r["exposure_id"] == "ai00100e0s0" and r["has_imaging"] is True
    assert r["obs_date"] == dt.datetime(1930, 5, 1, 3, 30, 30, 200000, tzinfo=dt.UTC)
    assert r["exptime"] == pytest.approx(60.0) and r["center_distance"] == pytest.approx(5.0)
    assert r["lim_mag_apass"] == pytest.approx(12.5) and r["series_kind"] is None
    assert r["plate_class"] is None  # empty class is null


def test_unscanned_plate_has_null_mosaic_and_solution_and_the_e_form_id():
    t = P.parse_exposures(
        exposure_response([{"scannum": -1, "mosnum": -1, "solnum": -1, "wcssource": "logbook"}])
    )
    r = row(t, 0)
    assert r["mosnum"] is None and r["solnum"] is None and r["has_imaging"] is False
    assert r["exposure_id"] == "ai00100e0"


def test_scanned_plate_without_a_solution_gets_a_null_solnum_not_minus_one():
    r = row(P.parse_exposures(exposure_response([{"solnum": -1}])), 0)
    assert r["solnum"] is None and r["mosnum"] == 0 and r["exposure_id"] == "ai00100e0"


def test_negative_expnum_is_null_and_does_not_break_the_id():
    r = row(P.parse_exposures(exposure_response([{"expnum": -1}])), 0)
    assert r["expnum"] is None and r["exposure_id"] == "ai00100e0s0"


def test_sentinel_positions_become_null():
    t = P.parse_exposures(exposure_response([{"ra": 999, "dec": 99}, {"ra": 10.0, "dec": 99}]))
    assert t.column("ra_deg").null_count == 2 and t.column("dec_deg").null_count == 2


def test_empty_optional_numbers_are_null():
    r = row(
        P.parse_exposures(
            exposure_response([{"exptime": "", "limMagApass": "", "medianColortermApass": ""}])
        ),
        0,
    )
    assert r["exptime"] is None and r["lim_mag_apass"] is None
    assert r["median_colorterm_apass"] is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1932-06-03T03:30:30.2Z", dt.datetime(1932, 6, 3, 3, 30, 30, 200000)),
        ("1924-08-22T01:13:13.8", dt.datetime(1924, 8, 22, 1, 13, 13, 800000)),  # no Z
        ("1930-01-01T03:30", dt.datetime(1930, 1, 1, 3, 30)),  # minute only
        ("1970-06-03T05:07:60.0Z", dt.datetime(1970, 6, 3, 5, 8, 0)),  # a logbook slip
        ("1900-12-31T23:59:59", dt.datetime(1900, 12, 31, 23, 59, 59)),
        ("1886-01-01T00:00:00.000", dt.datetime(1886, 1, 1)),
    ],
)
def test_exposure_dates(text, expected):
    got = row(P.parse_exposures(exposure_response([{"expdate": text}])), 0)["obs_date"]
    assert got == expected.replace(tzinfo=dt.UTC)


def test_empty_date_is_null():
    assert P.parse_exposures(exposure_response([{"expdate": ""}])).column("obs_date").null_count == 1


@pytest.mark.parametrize(
    "text", ["yesterday", "1930-13-01T00:00:00", "1930-02-30T00:00:00", "1930-01-01T25:00:00", "1930-01-01"]
)
def test_unreadable_or_impossible_dates_raise_with_the_row(text):
    with pytest.raises(P.ParseError, match="column 'expdate', row 2"):
        P.parse_exposures(exposure_response([{}, {"expdate": text}]))


def test_wcssource_is_case_insensitive_and_empty_is_null():
    t = P.parse_exposures(
        exposure_response([{"wcssource": "IMWCS"}, {"wcssource": ""}, {"wcssource": "catalog"}])
    )
    assert t.column("has_imaging").to_pylist() == [True, False, False]
    assert t.column("wcssource").to_pylist() == ["IMWCS", None, "catalog"]


def test_plate_class_is_kept_when_present():
    assert row(P.parse_exposures(exposure_response([{"class": "B2"}])), 0)["plate_class"] == "B2"


def test_exposure_ids_are_unique_across_a_multi_exposure_plate():
    rows = [{"expnum": 0, "solnum": 0}, {"expnum": 1, "solnum": 0}, {"expnum": 1, "solnum": 1}]
    ids = P.parse_exposures(exposure_response(rows)).column("exposure_id").to_pylist()
    assert len(set(ids)) == 3


# ---------------------------------------------------------------- lightcurves


def test_detection_row_values():
    t = lc([{}])
    schemas.validate("measurements", t)
    r = row(t, 0)
    assert r["ref_number"] == REF and r["series"] == "ac" and r["platenum"] == 225
    assert r["time"] == 2420000.5
    assert r["magcal_magdep"] == np.float32(11.5) and r["limiting_mag_local"] == np.float32(13.1)
    assert r["aflags"] == 0 and r["reject"] == 0
    assert r["exposure_id"] == "ac00225e0s0"
    assert r["blended_mag"] is None  # empty means no blend


def test_float32_columns_match_numpy_float32_of_the_text_exactly():
    r = row(lc([{"magcal_magdep": "12.962232", "fwhm_pix": "5.123456789"}]), 0)
    assert r["magcal_magdep"] == float(np.float32(12.962232))
    assert r["fwhm_image"] == float(np.float32(5.123456789))


def test_non_detection_rule_and_nulls():
    t = lc([{**NONDET, "date_jd": "2420000.0"}, {"date_jd": "2420001.0"}])
    nd, det = row(t, 0), row(t, 1)
    for col in ("magcal_magdep", "magcal_local_rms", "aflags", "a2flags", "bflags", "b2flags",
                "local_bin_reject_flag", "fwhm_image", "ellipticity", "plate_dist", "ra_deg",
                "dec_deg", "image_x", "image_y", "time_accuracy", "drad_rms2", "extinction"):  # fmt: skip
        assert nd[col] is None, col
        assert det[col] is not None, col
    assert nd["limiting_mag_local"] is not None  # always present
    assert nd["ref_number"] == REF  # taken from the request
    assert nd["expnum"] == 0  # an empty exposure number reads as 0, as in daschlab


@pytest.mark.parametrize(
    ("gsc", "sxt", "is_nondetection"),
    [("", "", True), (0, 0, True), ("0", "", True), ("", 5, False), (5, "", False), (5, 5, False)],
)
def test_a_row_is_a_non_detection_only_when_both_ids_read_as_zero(gsc, sxt, is_nondetection):
    r = row(lc([{"gsc_bin_index": gsc, "sextractor_number": sxt}]), 0)
    assert (r["magcal_magdep"] is None) == is_nondetection


@pytest.mark.parametrize(
    ("column", "ours", "sentinel"),
    [
        ("magcal_local_rms", "magcal_local_rms", "99"),
        ("magcal_local_rms", "magcal_local_rms", "99.0"),
        ("drad_rms2", "drad_rms2", "99"),
        ("blended_mag", "blended_mag", "0"),
        ("blended_mag", "blended_mag", "99"),
    ],
)
def test_sentinels_become_null(column, ours, sentinel):
    assert row(lc([{column: sentinel}]), 0)[ours] is None


def test_values_next_to_the_sentinels_are_kept():
    r = row(lc([{"magcal_local_rms": "98.9", "blended_mag": "0.01", "drad_rms2": "98"}]), 0)
    assert r["magcal_local_rms"] is not None and r["blended_mag"] is not None
    assert r["drad_rms2"] is not None


def test_nan_and_empty_floats_are_null_not_nan():
    t = lc([{"ellipticity": ""}, {"ellipticity": "nan"}, {"ellipticity": "0.2"}])
    assert t.column("ellipticity").null_count == 2
    assert not any(math.isnan(v) for v in t.column("ellipticity").to_pylist() if v is not None)


@pytest.mark.parametrize("column", ["date_jd", "limiting_mag_local"])
def test_required_columns_raise_when_empty(column):
    with pytest.raises(P.ParseError, match=f"column '{column}', row 1: empty value"):
        lc([{column: ""}])


def test_unreadable_values_raise():
    with pytest.raises(P.ParseError, match="column 'magcal_magdep', row 2"):
        lc([{}, {"magcal_magdep": "abc"}])
    with pytest.raises(P.ParseError, match="column 'plate_number'"):
        lc([{"plate_number": ""}])  # required integer


def test_ref_number_mismatch_raises_but_empty_and_zero_do_not():
    with pytest.raises(P.ParseError, match="row 2 has ref_number 999"):
        lc([{}, {"ref_number": 999}])
    assert lc([{"ref_number": 0}, {"ref_number": ""}, {}]).num_rows == 3


def test_rows_are_sorted_by_time():
    t = lc(
        [
            {"date_jd": "2420005.0", "plate_number": 3},
            {"date_jd": "2420001.0", "plate_number": 1},
            {"date_jd": "2420003.0", "plate_number": 2},
        ]
    )
    assert t.column("platenum").to_pylist() == [1, 2, 3]


def test_tied_timestamps_are_ordered_by_a_fixed_key_whatever_the_input_order():
    """The API returns rows with equal timestamps in a different order on each call."""
    rows = [
        {"date_jd": "2420003.0", "plate_number": 9, "magcal_magdep": "11.0"},
        {"date_jd": "2420003.0", "plate_number": 2, "magcal_magdep": "12.0"},
        {"date_jd": "2420003.0", "plate_number": 2, "magcal_magdep": "10.0"},
        {"date_jd": "2420003.0", "plate_number": 2, "magcal_magdep": ""},  # a null sorts last
        {"date_jd": "2420001.0", "plate_number": 5},
    ]
    a = lc(rows)
    assert a.equals(lc(list(reversed(rows)))) and a.equals(lc(rows[2:] + rows[:2]))
    assert a.column("platenum").to_pylist() == [5, 2, 2, 2, 9]
    assert a.column("magcal_magdep").to_pylist()[1:4] == [10.0, 12.0, None]


@given(
    rows=st.lists(
        st.fixed_dictionaries(
            {
                "date_jd": st.sampled_from(["2420001.0", "2420002.0", "2420003.0"]),
                "plate_number": st.integers(1, 4),
                "magcal_magdep": st.sampled_from(["10.0", "11.5", ""]),
                "limiting_mag_local": st.sampled_from(["12.0", "13.0"]),
            }
        ),
        min_size=1,
        max_size=12,
    ),
    seed=st.randoms(use_true_random=False),
)
@settings(max_examples=60, deadline=None)
def test_property_row_order_in_the_response_never_changes_the_table(rows, seed):
    shuffled = list(rows)
    seed.shuffle(shuffled)
    assert lc(rows).equals(lc(shuffled))


def test_exposure_id_ignores_the_mosaic_number():
    a = lc([{"mosaic_number": 0}]).column("exposure_id")[0]
    b = lc([{"mosaic_number": 1}]).column("exposure_id")[0]
    assert a == b  # re-scans change the mosaic, not the exposure


def test_empty_lightcurve_is_valid():
    t = P.parse_lightcurve(response(P.LIGHTCURVE_COLUMNS, []), REF)
    assert t.num_rows == 0
    schemas.validate("measurements", t)


def test_output_columns_match_the_schema_order_and_types():
    t = lc([{}])
    assert t.schema.equals(schemas.SCHEMAS["measurements"].schema)


@given(
    mags=st.lists(st.floats(min_value=5, max_value=20, allow_nan=False), min_size=1, max_size=15),
    times=st.lists(st.floats(min_value=2400000, max_value=2447000), min_size=15, max_size=15),
)
@settings(max_examples=40, deadline=None)
def test_property_detection_values_round_trip_through_float32(mags, times):
    rows = [{"magcal_magdep": repr(m), "date_jd": repr(t)} for m, t in zip(mags, times, strict=False)]
    t = lc(rows)
    assert t.num_rows == len(rows)
    got = sorted(t.column("magcal_magdep").to_pylist())
    assert got == sorted(float(np.float32(m)) for m in mags)
    assert t.column("time").to_pylist() == sorted(t.column("time").to_pylist())


# ----------------------------------------------------------------- rejections


BITS = {
    "HIGH_BACKGROUND": 1 << 6,
    "LARGE_ISO_RMS": 1 << 11,
    "LARGE_LOCAL_SMOOTH_RMS": 1 << 12,
    "CLOSE_TO_LIMITING": 1 << 13,
    "BIN_DRAD_UNKNOWN": 1 << 15,
}
OTHER_BITS = {
    "BAD_PLATE_QUALITY": 1 << 7,
    "UNCERTAIN_CATALOG_MAG": 1 << 19,
    "SUSPECTED_DEFECT": 1 << 25,
    "TOO_BRIGHT": 1 << 29,
    "LOW_ALTITUDE": 1 << 30,
}


def test_standard_bits_are_exactly_the_five():
    assert P.STANDARD_BAD_AFLAGS == sum(BITS.values())


@pytest.mark.parametrize("bit", sorted(BITS.values()))
def test_each_standard_bit_rejects(bit):
    out = P.apply_standard_rejections(lc([{"aflags": bit}]))
    assert row(out, 0)["reject"] == P.BIT_STANDARD_AFLAGS


@pytest.mark.parametrize("bit", sorted(OTHER_BITS.values()))
def test_other_bits_alone_do_not_reject(bit):
    assert row(P.apply_standard_rejections(lc([{"aflags": bit}])), 0)["reject"] == 0


def test_a_standard_bit_among_other_bits_rejects():
    flags = OTHER_BITS["SUSPECTED_DEFECT"] | BITS["LARGE_ISO_RMS"]
    assert row(P.apply_standard_rejections(lc([{"aflags": flags}])), 0)["reject"] == 1


def test_non_detections_are_never_rejected():
    out = P.apply_standard_rejections(lc([NONDET]))
    assert row(out, 0)["reject"] == 0


def test_rejection_preserves_other_reject_bits_and_does_not_mutate_the_input():
    t = lc([{"aflags": BITS["HIGH_BACKGROUND"]}, {"aflags": 0}])
    marked = t.set_column(
        t.schema.get_field_index("reject"), "reject", pa.array([2, 4], type=pa.uint64())
    )
    out = P.apply_standard_rejections(marked)
    assert out.column("reject").to_pylist() == [3, 4]
    assert marked.column("reject").to_pylist() == [2, 4]  # unchanged


def test_applying_twice_changes_nothing():
    t = lc([{"aflags": bit} for bit in BITS.values()] + [{"aflags": 0}])
    once = P.apply_standard_rejections(t)
    assert P.apply_standard_rejections(once).equals(once)


@given(flags=st.lists(st.integers(0, 2**31 - 1), min_size=1, max_size=20))
@settings(max_examples=60, deadline=None)
def test_property_a_rejection_never_increases_the_good_points(flags):
    t = lc([{"aflags": f} for f in flags])
    before = int((t.column("reject").to_numpy() == 0).sum())
    out = P.apply_standard_rejections(t)
    after = int((out.column("reject").to_numpy() == 0).sum())
    assert after <= before
    expected = sum(1 for f in flags if f & P.STANDARD_BAD_AFLAGS == 0)
    assert after == expected


def test_constants_match_daschlab_when_it_is_installed():
    photometry = pytest.importorskip("daschlab.photometry")
    a = photometry.AFlags
    want = (
        a.HIGH_BACKGROUND | a.LARGE_ISO_RMS | a.LARGE_LOCAL_SMOOTH_RMS | a.CLOSE_TO_LIMITING
        | a.BIN_DRAD_UNKNOWN
    )  # fmt: skip
    assert int(want) == P.STANDARD_BAD_AFLAGS
    for name, bit in {**BITS, **OTHER_BITS}.items():
        assert int(getattr(a, name)) == bit, name


# ------------------------------------------------------- the real Sprint 0 data


@pytest.mark.skipif(not HAVE_RAW, reason="needs data/spike/raw from the Sprint 0 run")
class TestRealResponses:
    def test_catalog(self):
        cat = P.parse_catalog(load_raw("tcrb_querycat"))
        assert cat.num_rows == 333
        assert P.stars_from_catalog(cat, TILES["tcrb"]).num_rows == 10

    def test_exposures(self):
        t = P.parse_exposures(load_raw("tcrb_queryexps"))
        schemas.validate("exposures", t)
        ids = t.column("exposure_id").to_pylist()
        assert len(ids) == 16326 and len(set(ids)) == 16326
        assert sum(t.column("has_imaging").to_pylist()) == 11908
        assert t.column("obs_date").null_count == 6
        assert t.column("plate_class").null_count == 16326  # every exposure's class is empty

    def test_lightcurve_matches_the_sprint0_run(self):
        t = P.parse_lightcurve(load_raw("tcrb_lightcurve"), REF)
        schemas.validate("measurements", t)
        assert t.num_rows == 2882 and t.column("magcal_magdep").null_count == 277
        rejected = P.apply_standard_rejections(t)
        assert int((rejected.column("reject").to_numpy() != 0).sum()) == 1035  # daschlab's figure

    def test_every_lightcurve_row_joins_to_an_exposure(self):
        ex = set(P.parse_exposures(load_raw("tcrb_queryexps")).column("exposure_id").to_pylist())
        t = P.parse_lightcurve(load_raw("tcrb_lightcurve"), REF)
        assert all(i in ex for i in t.column("exposure_id").to_pylist())
