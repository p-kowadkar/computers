import json

import numpy as np
import pyarrow as pa
import pytest
from fake_dasch import FakeDasch, Stop

from computers.data import dasch_parse as P
from computers.data import pull, schemas, silver
from computers.data.dasch_client import DaschClient, Response, RetryPolicy
from computers.data.snapshot import SnapshotStore
from computers.data.tiles import Tile, in_tile

TILE = Tile("tcrb", 239.87568, 25.92017)


@pytest.fixture
def tiles_patch(monkeypatch):
    """Make the runner's tile table know our fake tile."""
    monkeypatch.setitem(pull.TILES, "tcrb", TILE)


def make_client(fake, data_dir, **kw):
    store = SnapshotStore(data_dir)
    return DaschClient(
        store, transport=fake, env={}, min_interval=0, sleep=lambda s: None,
        policy=RetryPolicy(max_attempts=3), **kw,
    )  # fmt: skip


def lines_of(fake, bright=True):
    return [s for s in fake.sources_in_tile() if s["stdmag"] <= 13.5] if bright else fake.sources_in_tile()


# --------------------------------------------------------------- magnitude bins


def test_magnitude_bins_use_inclusive_upper_edges_and_the_limit():
    bins = pull.magnitude_bins(np.array([12.0, 12.01, 12.5, 13.5, 13.51, 99.0, np.nan, 5.0]))
    assert bins == {"5.0": 1, "12.0": 1, "12.5": 2, "13.5": 1}  # 13.51 is over the cut


def test_magnitude_bins_empty():
    assert pull.magnitude_bins(np.array([14.0, np.nan])) == {}


# ---------------------------------------------------------------------- catalog


def test_catalog_stage_finds_every_source_in_the_tile_exactly_once(tmp_path):
    fake = FakeDasch(TILE, n_sources=400)
    result = pull.pull_catalog(make_client(fake, tmp_path), TILE)
    truth = fake.sources_in_tile()
    assert result.n_in_tile == len(truth) > 50
    assert result.n_outside_tile > 0  # the boxes reach past the tile edge, and those are dropped
    assert result.n_unique == result.n_in_tile + result.n_outside_tile
    assert fake.calls["querycat"] == 4


def test_a_source_on_every_seam_is_merged_and_counted(tmp_path):
    fake = FakeDasch(TILE, n_sources=0)
    seam_source = {
        "ref_text": "SEAM",
        "ref_number": 5,
        "gsc_bin_index": 1,
        "ra_deg": TILE.ra0,
        "dec_deg": TILE.dec0,
        "stdmag": 10.0,
        "num_matches": 9,
    }
    fake.sources.append(seam_source)
    result = pull.pull_catalog(make_client(fake, tmp_path), TILE)
    assert result.n_returned == 4 and result.n_unique == 1 and result.n_multi_box == 1
    assert result.stars.num_rows == 1


def test_stars_are_the_bright_in_tile_sources_only(tmp_path):
    fake = FakeDasch(TILE, n_sources=400)
    result = pull.pull_catalog(make_client(fake, tmp_path), TILE)
    schemas.validate("stars", result.stars)
    want = sorted(s["ref_number"] for s in lines_of(fake))
    assert result.stars.column("ref_number").to_pylist() == want
    assert max(result.stars.column("apass_b").to_pylist()) <= 13.5
    assert result.n_bright == len(want)
    assert sum(result.bright_bins.values()) == len(want)
    assert result.n_with_magnitude == sum(1 for s in fake.sources_in_tile() if s["stdmag"] < 99)


def test_a_failed_box_stops_the_stage_and_names_the_box(tmp_path):
    fake = FakeDasch(TILE, n_sources=100)
    fake.fail_boxes = {2}
    with pytest.raises(pull.StageError, match=r"incomplete, nothing written.*box \d of 4"):
        pull.pull_catalog(make_client(fake, tmp_path), TILE)


def test_catalog_stage_is_resumable(tmp_path):
    fake = FakeDasch(TILE, n_sources=100)
    pull.pull_catalog(make_client(fake, tmp_path), TILE)
    again = FakeDasch(TILE, n_sources=100)
    pull.pull_catalog(make_client(again, tmp_path), TILE)
    assert again.calls["querycat"] == 0


def test_one_box_is_retried_after_a_504(tmp_path):
    fake = FakeDasch(TILE, n_sources=100)
    fake.script["querycat"] = [Response(504, b'{"message": "Endpoint request timed out"}')]
    result = pull.pull_catalog(make_client(fake, tmp_path), TILE)
    assert result.n_in_tile > 0 and fake.calls["querycat"] == 5  # 4 boxes + 1 retry


# --------------------------------------------------------------------- exposures


def test_exposures_stage_calls_five_positions_and_removes_duplicates(tmp_path):
    fake = FakeDasch(TILE, n_exposures=80)
    result = pull.pull_exposures(make_client(fake, tmp_path), TILE)
    schemas.validate("exposures", result.table)
    assert result.n_calls == 5 and result.n_conflicts == 0
    ids = result.table.column("exposure_id").to_pylist()
    assert len(ids) == len(set(ids)) < result.n_returned  # overlapping positions repeat plates
    assert result.table.column("obs_date").to_pylist() == sorted(result.table.column("obs_date").to_pylist())


def test_query_relative_columns_are_null_in_the_tile_table_and_not_counted_as_conflicts(tmp_path):
    fake = FakeDasch(TILE, n_exposures=80)
    result = pull.pull_exposures(make_client(fake, tmp_path), TILE)
    for column in pull.QUERY_RELATIVE_COLUMNS:
        assert result.table.column(column).null_count == result.table.num_rows, column
    assert result.n_conflicts == 0
    assert result.n_position_dependent > 0  # copies of one exposure did differ, in those columns only
    # Everything else is kept: dates, colourterm, positions, ids.
    assert result.table.column("obs_date").null_count == 0
    assert result.table.column("median_colorterm_apass").null_count == 0


def test_a_real_conflict_between_copies_is_still_counted(tmp_path):
    fake = FakeDasch(TILE, n_exposures=80)
    original = fake._queryexps
    calls = {"n": 0}

    def tampered(body):
        response = original(body)
        calls["n"] += 1
        if calls["n"] == 2:  # change a stable column in one position's copy of every exposure
            lines = json.loads(response.content)
            header = lines[0].split(",")
            i = header.index("expdate")
            out = [lines[0]]
            for line in lines[1:]:
                parts = line.split(",")
                parts[i] = "1999-01-01T00:00:00Z"
                out.append(",".join(parts))
            return Response(200, json.dumps(out).encode())
        return response

    fake._queryexps = tampered
    result = pull.pull_exposures(make_client(fake, tmp_path), TILE)
    assert result.n_conflicts > 0


def test_an_oversize_response_is_split_by_date_range_and_gives_the_same_table(tmp_path):
    whole = FakeDasch(TILE, n_exposures=120)
    expected = pull.pull_exposures(make_client(whole, tmp_path / "a"), TILE)
    limited = FakeDasch(TILE, n_exposures=120, max_unsplit_exposures=20)
    result = pull.pull_exposures(make_client(limited, tmp_path / "b"), TILE)
    assert result.splits and all("502" in s for s in result.splits)
    assert result.table.column("exposure_id").to_pylist() == expected.table.column("exposure_id").to_pylist()
    assert result.n_calls > 5


def test_giving_up_after_the_maximum_number_of_splits(tmp_path):
    fake = FakeDasch(TILE, n_exposures=60)
    fake.max_unsplit_exposures = 0  # never satisfied while there is any data
    fake.script["queryexps"] = [Response(502, b"too big") for _ in range(500)]
    with pytest.raises(pull.StageError, match="incomplete, nothing written"):
        pull.pull_exposures(make_client(fake, tmp_path), TILE)


def test_a_4xx_on_exposures_is_not_split(tmp_path):
    fake = FakeDasch(TILE)
    fake.script["queryexps"] = [Response(400, b"bad request")] * 5
    with pytest.raises(pull.StageError):
        pull.pull_exposures(make_client(fake, tmp_path), TILE)
    assert fake.calls["queryexps"] == 5  # one try per position, no splitting


# ------------------------------------------------------------------ pilot subset


def stars_with_mags(mags):
    n = len(mags)
    s = schemas.SCHEMAS["stars"].schema
    cols = {
        "ref_number": list(range(1, n + 1)), "refcat": ["apass"] * n, "ref_text": ["x"] * n,
        "gsc_bin_index": [1] * n, "ra_deg": [1.0] * n, "dec_deg": [1.0] * n,
        "pm_ra_masyr": [None] * n, "pm_dec_masyr": [None] * n, "apass_b": list(mags),
        "color": [None] * n, "num_matches": [10] * n, "tile": ["t"] * n, "n_points": [10] * n,
    }  # fmt: skip
    return pa.table({f.name: pa.array(cols[f.name], type=f.type) for f in s}, schema=s)


def test_pilot_returns_everything_when_the_tile_is_small():
    stars = stars_with_mags([10.0, 11.0, 12.0])
    assert pull.pilot_subset(stars, 5).num_rows == 3


def test_pilot_subset_is_stratified_deterministic_and_covers_every_bin():
    rng = np.random.default_rng(0)
    mags = np.concatenate([rng.uniform(8, 9, 20), rng.uniform(12, 13, 500), rng.uniform(13, 13.5, 480)])
    mags = np.round(mags, 3).astype(np.float32)  # the table stores float32
    stars = stars_with_mags(mags)
    a, b = pull.pilot_subset(stars, 100), pull.pilot_subset(stars, 100)
    assert a.equals(b)
    assert 100 <= a.num_rows <= 100 + 12  # at most one extra per bin
    bins_all = set(np.ceil(np.asarray(mags) / 0.5))
    bins_sub = set(np.ceil(a.column("apass_b").to_numpy() / 0.5))
    assert bins_sub == bins_all  # no bin lost, including the 20 bright stars
    assert not pull.pilot_subset(stars, 100, seed=1).equals(a)
    # Stratified: the 20 bright stars are 2% of the tile, and keep roughly that share.
    bright = (a.column("apass_b").to_numpy() < 9.5).sum()
    assert 1 <= bright <= 5


def test_pilot_rejects_a_silly_size():
    with pytest.raises(ValueError):
        pull.pilot_subset(stars_with_mags([10.0]), 0)


# ------------------------------------------------------------------ lightcurves


def run(fake, tmp_path, stages=("catalog", "exposures", "lightcurves"), **kw):
    log = []
    client = kw.pop("client", None) or make_client(fake, tmp_path)
    code = pull.run_pull(["tcrb"], list(stages), tmp_path, client=client, say=log.append, **kw)
    return code, log, client


def reports(tmp_path):
    return sorted((tmp_path / "reports").glob("pull-tcrb-*.json"))


def test_full_run_writes_validated_silver_files_and_a_report(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=300)
    code, _, _ = run(fake, tmp_path)
    assert code == 0
    n = len(lines_of(fake))
    for table in ("stars", "exposures", "measurements"):
        path = silver.silver_path(tmp_path, table, "tcrb")
        assert path.exists(), table
        meta = silver.read_metadata(path)
        assert meta["computers.snapshot_digest"].startswith("sha256:")
        assert meta["computers.schema_version"] == "1"
    m = silver.read_table(silver.silver_path(tmp_path, "measurements", "tcrb"))
    schemas.validate("measurements", m)
    assert m.num_rows == n * 30
    assert set(m.column("ref_number").to_pylist()) == {s["ref_number"] for s in lines_of(fake)}
    assert (m.column("reject").to_numpy() != 0).any()  # the standard rejections were applied
    (rep,) = reports(tmp_path)
    data = json.loads(rep.read_text())
    assert data["tile"] == "tcrb" and data["failures"] == []
    assert data["lightcurves"]["stars_requested"] == n and data["lightcurves"]["rows"] == n * 30
    assert data["catalog"]["bright_b_le_13_5"] == n
    assert data["client"]["requests_made"] == 4 + 5 + n and data["client"]["api_mode"] == "public"
    assert data["client"]["seconds_by_endpoint"]["/dasch/dr7/lightcurve"]["n"] == n
    assert data["snapshot_digest"].startswith("sha256:")


def test_every_lightcurve_row_joins_to_an_exposure(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=200)
    run(fake, tmp_path)
    ex = set(silver.read_table(silver.silver_path(tmp_path, "exposures", "tcrb")).column("exposure_id").to_pylist())
    m = silver.read_table(silver.silver_path(tmp_path, "measurements", "tcrb"))
    assert set(m.column("exposure_id").to_pylist()) <= ex


def test_a_second_run_makes_no_network_calls(tmp_path, tiles_patch):
    run(FakeDasch(TILE, n_sources=200), tmp_path)
    again = FakeDasch(TILE, n_sources=200)
    code, _, client = run(again, tmp_path)
    assert code == 0 and sum(again.calls.values()) == 0
    assert client.stats.made == 0 and client.stats.from_store > 0


def test_resume_after_an_interrupt_makes_only_the_missing_requests(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=300)
    fake.stop_after_lightcurves = 40  # "Ctrl-C" partway through the lightcurves
    with pytest.raises(Stop):
        run(fake, tmp_path)
    store = SnapshotStore(tmp_path)
    done = sum(1 for r in store.rows("dasch-dr7") if r.endpoint.endswith("/lightcurve"))
    total = len(lines_of(fake))
    assert 0 < done < total  # it really did stop partway
    assert not silver.silver_path(tmp_path, "measurements", "tcrb").exists()
    assert store.verify().ok  # nothing half-written

    resumed = FakeDasch(TILE, n_sources=300)
    code, _, _ = run(resumed, tmp_path)
    assert code == 0
    assert resumed.calls["lightcurve"] == total - done  # exactly the missing ones
    assert resumed.calls["querycat"] == 0 and resumed.calls["queryexps"] == 0
    assert len(set(resumed.lightcurve_refs)) == len(resumed.lightcurve_refs)  # none twice
    assert silver.silver_path(tmp_path, "measurements", "tcrb").exists()


def test_failed_stars_do_not_stop_the_others_and_block_the_table(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=300)
    bad = [s["ref_number"] for s in lines_of(fake)[:2]]
    fake.fail_lightcurve = set(bad)
    code, log, _ = run(fake, tmp_path)
    assert code == 1
    assert not silver.silver_path(tmp_path, "measurements", "tcrb").exists()
    (rep,) = reports(tmp_path)
    data = json.loads(rep.read_text())
    assert sorted(f["ref_number"] for f in data["failures"]) == sorted(bad)
    assert data["lightcurves"]["stars_failed"] == 2
    assert any("2 stars failed" in line for line in log)
    # Everything else was fetched, so the retry needs only the two failures.
    fixed = FakeDasch(TILE, n_sources=300)
    code2, _, _ = run(fixed, tmp_path)
    assert code2 == 0 and fixed.calls["lightcurve"] == 2
    assert silver.silver_path(tmp_path, "measurements", "tcrb").exists()


def test_lightcurves_without_a_catalog_is_a_clear_error(tmp_path, tiles_patch):
    code, log, _ = run(FakeDasch(TILE), tmp_path, stages=("lightcurves",))
    assert code == 1 and any("run the catalog stage first" in line for line in log)


def test_catalog_failure_writes_no_silver_file_and_exits_nonzero(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=100)
    fake.fail_boxes = {0}
    code, log, _ = run(fake, tmp_path, stages=("catalog",))
    assert code == 1 and not silver.silver_path(tmp_path, "stars", "tcrb").exists()
    assert any("box 1 of 4" in line or "box" in line for line in log)
    (rep,) = reports(tmp_path)
    assert json.loads(rep.read_text())["failures"]


def test_pilot_flag_limits_the_lightcurve_requests(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=800)
    code, _, _ = run(fake, tmp_path, pilot=True, max_stars=10)
    n = len(lines_of(fake))
    assert n > 30  # enough stars that the limit matters
    assert code == 0 and 10 <= fake.calls["lightcurve"] < n
    (rep,) = reports(tmp_path)
    assert json.loads(rep.read_text())["pilot"] is True


def test_without_the_pilot_flag_every_star_is_pulled(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=400)
    run(fake, tmp_path, max_stars=5)
    assert fake.calls["lightcurve"] == len(lines_of(fake))


def test_standard_rejections_in_the_table_match_the_parser(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=150)
    run(fake, tmp_path)
    m = silver.read_table(silver.silver_path(tmp_path, "measurements", "tcrb"))
    aflags = np.nan_to_num(m.column("aflags").to_numpy(zero_copy_only=False)).astype(np.int64)
    expected = (aflags & P.STANDARD_BAD_AFLAGS) != 0
    assert np.array_equal(m.column("reject").to_numpy() != 0, expected)


def test_stars_in_the_silver_table_are_inside_the_tile(tmp_path, tiles_patch):
    fake = FakeDasch(TILE, n_sources=400)
    run(fake, tmp_path, stages=("catalog",))
    stars = silver.read_table(silver.silver_path(tmp_path, "stars", "tcrb"))
    assert all(in_tile(TILE, r, d) for r, d in zip(stars.column("ra_deg").to_pylist(), stars.column("dec_deg").to_pylist(), strict=True))
