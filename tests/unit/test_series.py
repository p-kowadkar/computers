import pytest

from computers.data import series as S
from computers.data import silver


@pytest.mark.parametrize(
    ("scale", "kind"),
    [(0.0, "narrow"), (399.9, "narrow"), (400.0, "narrow"), (400.1, "patrol"),
     (899.9, "patrol"), (900.0, "meteor"), (1360.0, "meteor")],
)
def test_kind_from_scale_at_both_sides_of_each_boundary(scale, kind):
    assert S.kind_from_scale(scale) == kind


def test_the_table_has_every_series_and_known_kinds():
    assert len(S.SERIES) == 73
    assert {k for k, _, _ in S.SERIES.values()} == set(S.KINDS)
    assert set(S.KIND_CODES) == {"unknown", *S.KINDS}


def test_kinds_follow_the_scale_rule_except_for_the_listed_exceptions():
    assert S.KIND_EXCEPTIONS == {"ad", "ayroe", "bc", "darnor", "darsou", "m", "y"}
    for name, (kind, scale, _) in S.SERIES.items():
        if name not in S.KIND_EXCEPTIONS:
            assert kind == S.kind_from_scale(scale), name


def test_the_commonest_series_of_the_tiles():
    assert S.kind_of("ai") == "meteor" and S.kind_of("fa") == "meteor"
    assert S.kind_of("ac") == "patrol" and S.kind_of("i") == "narrow"


def test_an_unknown_series_is_unknown_not_an_error():
    assert S.kind_of("zzz") == "unknown" and S.kind_of("") == "unknown"


def test_the_table_matches_daschlab_when_it_is_installed():
    ds = pytest.importorskip("daschlab.series")
    assert set(ds.SERIES) == set(S.SERIES)
    for name, v in ds.SERIES.items():
        kind, scale, aperture = S.SERIES[name]
        assert kind == str(v.kind).split(".")[-1].lower(), name
        assert (scale is None and v.plate_scale != v.plate_scale) or scale == float(v.plate_scale), name
        assert (aperture is None and v.aperture != v.aperture) or aperture == float(v.aperture), name


def test_every_series_in_the_local_tiles_is_known():
    from pathlib import Path

    seen = set()
    for tile in ("m44", "tcrb", "tabby"):
        path = silver.silver_path(Path(__file__).resolve().parents[2] / "data", "measurements", tile)
        if not path.exists():
            pytest.skip("no local measurements")
        import pyarrow.compute as pc

        seen |= set(pc.unique(silver.read_table(path).column("series")).to_pylist())
    assert seen and seen <= set(S.SERIES), seen - set(S.SERIES)
