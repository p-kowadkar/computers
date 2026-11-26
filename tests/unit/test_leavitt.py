import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from synth_leavitt import J2000, table, write_all

from computers.bench import leavitt as L
from computers.bench import variables as V


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    write_all(d)
    return d


@pytest.fixture(autouse=True)
def lock_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "LOCK_PATH", tmp_path / "docs" / "lock.json")


# --------------------------------------------------------------------- variables


def test_base_types_handles_uncertainty_and_compound_types():
    assert V.base_types("EA/SD") == ["EA", "SD"]
    assert V.base_types("SRB:") == ["SRB"]
    assert V.base_types("DSCT|GDOR|SXPHE") == ["DSCT", "GDOR", "SXPHE"]
    assert V.base_types("NR+ELL") == ["NR", "ELL"]
    assert V.base_types(None) == [] and V.base_types("") == []


@pytest.mark.parametrize(
    ("vsx_type", "amp", "klass"),
    [
        ("SRB", 1.3, "long_period"), ("M", None, "long_period"), ("RVA", 2.0, "rv_tauri"),
        ("NR+ELL", 8.8, "eruptive"), ("UGSS", None, "eruptive"),
        ("EA/SD", 2.0, "eclipsing"), ("EW", 0.5, "eclipsing"),
        ("EA", 0.49, None),  # under the 0.5 mag floor
        ("EA", None, None),  # an eclipsing binary with an unknown depth is not kept
        ("SRB", 0.49, None), ("SRB", 0.5, "long_period"),  # the floor applies to every type, both sides
        ("ROT", 3.0, None), ("DSCT|GDOR", 1.0, None), (None, 2.0, None), ("ELL", 1.0, None),
    ],
)
def test_classify(vsx_type, amp, klass):
    assert V.classify(vsx_type, amp) == klass


def test_sexagesimal_and_the_tang_giants():
    ra, dec = V.sexagesimal("08:30:38.5", "+14:07:13")
    assert ra == pytest.approx(127.66042, abs=1e-5) and dec == pytest.approx(14.12028, abs=1e-5)
    assert V.sexagesimal("00:00:00", "-30:30:00")[1] == pytest.approx(-30.5)
    names = [t.name for t in V.REFERENCE_TARGETS]
    assert {"T CrB", "KIC 8462852", "RY Cnc"} <= set(names) and sum("DASCH J" in n for n in names) == 3


# ------------------------------------------------------------------- host curves


def _meas(rows):
    n = len(rows)
    return table("measurements", n, ref_number=[r[0] for r in rows], time=[r[1] for r in rows],
                 magcal_magdep=[r[2] for r in rows], reject=[r[3] for r in rows],
                 series=[r[4] for r in rows], platenum=[r[5] for r in rows],
                 limiting_mag_local=[15.0] * n)


def test_host_curve_keeps_good_detections_and_non_detections_in_time_order():
    t = J2000
    rows = [(1, t + 3, 12.0, 0, "ac", 1), (1, t + 1, None, 0, "ac", 2), (1, t + 2, 12.5, 4, "ac", 3),
            (2, t, 11.0, 0, "ac", 4)]
    h = L.host_curves(_meas(rows))
    assert set(h) == {1, 2}
    assert h[1].time.tolist() == [t + 1, t + 3]  # the rejected detection is gone
    assert np.isnan(h[1].mag[0]) and h[1].mag[1] == 12.0
    assert set(L.host_curves(_meas(rows), refs={2})) == {2}


def test_host_curve_drops_rows_on_excluded_plates():
    t = J2000
    rows = [(1, t, 12.0, 0, "ac", 1), (1, t + 1, 12.0, 0, "ai", 7), (1, t + 2, None, 0, "ai", 7)]
    h = L.host_curves(_meas(rows), exclude_plates={"ai00007"})
    assert h[1].time.tolist() == [t]


# ------------------------------------------------------------------------ split


def test_held_out_split_lists_tabby_and_the_plates_it_shares_with_dev_tiles(data):
    s = L.held_out_split(data)
    assert s["tiles"] == ["tabby"] and len(s["ref_numbers"]) == 50
    assert s["excluded_plates"] == [f"ai{9000 + i:05d}" for i in range(5)]  # the shared wide-field plates
    assert len(s["sha256"]) == 64 and s == L.held_out_split(data)


def test_the_split_does_not_depend_on_thresholds_or_measurements(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_all(a, seed=1)
    write_all(b, seed=2)  # different noise, same stars and plates
    assert L.held_out_split(a)["sha256"] == L.held_out_split(b)["sha256"]


def test_without_shared_plates_nothing_is_excluded(tmp_path):
    write_all(tmp_path / "d", shared=False)
    assert L.held_out_split(tmp_path / "d")["excluded_plates"] == []


# ------------------------------------------------------------------------ build


def test_build_writes_the_files_and_a_complete_manifest(data):
    m = L.build_set(data, say=lambda _: None)
    out = L.set_dir(data)
    for f in ("constants.parquet", "variables.parquet", "injections.parquet", "test_split.json", "manifest.json"):
        assert (out / f).exists(), f
    assert m["version"] == "v0" and m["dev_tiles"] == ["m44", "tcrb"] and m["test_tiles"] == ["tabby"]
    assert set(m["tiles"]) == {"m44", "tcrb", "tabby"}
    assert len(m["inputs_sha256"]) == 15 and m["snapshot_digests"] == ["sha256:test"]
    assert m["thresholds"]["constant_scatter_factor"] == 1.5
    assert json.loads((out / "manifest.json").read_text()) == json.loads(json.dumps(m))
    found = {r["name"] for r in m["reference_targets"] if r["patrol_ref_number"]}
    assert found == {"T CrB", "KIC 8462852"}  # the synthetic tiles have a star at each tile centre


def test_known_variables_use_the_type_list_and_leave_the_constants(data):
    L.build_set(data, say=lambda _: None)
    out = L.set_dir(data)
    v = pq.read_table(out / "variables.parquet").to_pylist()
    assert sorted((r["tile"], r["klass"]) for r in v) == [("m44", "long_period"), ("tcrb", "eruptive")]
    cons = pq.read_table(out / "constants.parquet").to_pylist()
    byref = {r["ref_number"]: r for r in cons}
    assert all(not byref[r["ref_number"]]["is_constant"] for r in v)
    assert all(byref[r["ref_number"]]["reason"] == "vsx_match" for r in v)  # also the EA of 0.2 mag
    inj = pq.read_table(out / "injections.parquet")
    hosts = set(inj.column("host_id").to_pylist())
    assert not hosts & {r["ref_number"] for r in cons if not r["is_constant"]}


def test_injections_cover_every_constant_family_and_split(data):
    L.build_set(data, say=lambda _: None)
    inj = pq.read_table(L.set_dir(data) / "injections.parquet").to_pylist()
    cons = {r["ref_number"]: r for r in pq.read_table(L.set_dir(data) / "constants.parquet").to_pylist()}
    n_const = sum(r["is_constant"] for r in cons.values())
    assert len(inj) == n_const * 5 * 6 * 2
    assert {r["split"] for r in inj} == {"dev", "test"}
    assert all(r["split"] == cons[r["host_id"]]["split"] for r in inj)
    assert any(r["expected_missed"] for r in inj)


def test_the_build_is_reproducible_byte_for_byte(data):
    L.build_set(data, say=lambda _: None)
    out = L.set_dir(data)
    first = {f: (out / f).read_bytes() for f in ("manifest.json", "test_split.json")}
    first_inj = pq.read_table(out / "injections.parquet")
    L.build_set(data, say=lambda _: None)
    assert {f: (out / f).read_bytes() for f in first} == first
    assert pq.read_table(out / "injections.parquet").equals(first_inj)
    L.build_set(data, seed=1, say=lambda _: None)
    assert not pq.read_table(out / "injections.parquet").equals(first_inj)


def test_build_names_a_missing_input(tmp_path):
    with pytest.raises(L.BuildError, match="missing"):
        L.build_set(tmp_path / "empty", say=lambda _: None)


# --------------------------------------------------------------- freeze and check


def test_check_reports_an_unfrozen_split_then_passes_after_freezing(data, tmp_path):
    L.build_set(data, say=lambda _: None)
    assert any("not frozen" in p for p in L.check_set(data))
    decision = tmp_path / "docs" / "decisions" / "0006.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("# approved\n")
    L.freeze_test_split(data, decision="docs/decisions/0006.md", root=tmp_path)
    assert L.check_set(data) == []


def test_freeze_needs_a_decision_file(data, tmp_path):
    with pytest.raises(L.BuildError, match="does not exist"):
        L.freeze_test_split(data, decision="docs/decisions/none.md", root=tmp_path)


def _freeze(data, tmp_path):
    L.build_set(data, say=lambda _: None)
    (tmp_path / "d.md").write_text("x")
    L.freeze_test_split(data, decision="d.md", root=tmp_path)


def test_a_changed_test_split_fails_check_and_blocks_a_rebuild(data, tmp_path):
    _freeze(data, tmp_path)
    from computers.data import silver

    path = silver.silver_path(data, "stars", "tabby")
    stars = silver.read_table(path)
    refs = stars.column("ref_number").to_pylist()
    refs[0] += 999
    changed = stars.set_column(0, "ref_number", pa.array(refs, pa.uint64()))
    path.chmod(0o644)
    silver.write_table("stars", changed, path, snapshot_digest="sha256:test")
    problems = L.check_set(data)
    assert any("changed since it was frozen" in p for p in problems)
    with pytest.raises(L.BuildError, match="changed since it was frozen"):
        L.build_set(data, say=lambda _: None)


def test_a_changed_input_file_fails_check_but_not_the_split(data, tmp_path):
    _freeze(data, tmp_path)
    from computers.data import silver

    path = silver.silver_path(data, "vsx", "m44")
    silver.write_table("vsx", silver.read_table(path), path, snapshot_digest="sha256:other")
    problems = L.check_set(data)
    assert len(problems) == 1 and "vsx" in problems[0] and "changed since the set was built" in problems[0]


def test_check_without_a_build(tmp_path):
    assert "missing" in L.check_set(tmp_path)[0]
