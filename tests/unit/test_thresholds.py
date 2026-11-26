import json
from pathlib import Path

import pytest

from computers import thresholds as th
from computers.cli import main

ROOT = Path(__file__).resolve().parents[2]
DECISION = "docs/decisions/0002-initial-thresholds.md"


def committed_lock() -> dict:
    return json.loads((ROOT / th.LOCK_PATH).read_text(encoding="utf-8"))


# ----------------------------------------------------- the committed repo state


def test_registry_lock_and_decision_files_agree():
    assert th.problems(committed_lock(), ROOT) == []


def test_every_threshold_has_a_known_status_and_plan_section():
    for t in th.THRESHOLDS.values():
        assert t.status in th.STATUSES and t.plan and t.unit


def test_the_four_plan_values_are_present():
    got = {n: t.value for n, t in th.THRESHOLDS.items()}
    assert {n: got[n] for n in committed_lock() if committed_lock()[n]["decision"] == DECISION} == {
        "amplitude_floor_long_term_mag": 0.5,
        "amplitude_floor_single_epoch_mag": 1.0,
        "astrometry_max_sep_arcsec": 15.0,
        "source_split_radius_arcsec": 5.0,
    }


def test_the_decision_record_mentions_every_threshold():
    text = (ROOT / DECISION).read_text(encoding="utf-8")
    cited = [n for n, e in committed_lock().items() if e["decision"] == DECISION]
    assert len(cited) == 4
    for name in cited:
        assert f"`{name}`" in text, name


# ------------------------------------------------------------------ the checks


def fake_root(tmp_path, files=(DECISION,)):
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# decision\n")
    return tmp_path


def reg(value=1.0):
    return {"x": th.Threshold("x", value, "mag", "start", "1.0")}


def lock(value=1.0, decision=DECISION):
    return {"x": {"value": value, "decision": decision}}


def test_matching_state_has_no_problems(tmp_path):
    assert th.problems(lock(), fake_root(tmp_path), reg()) == []


def test_changed_value_without_updating_the_lock_fails(tmp_path):
    found = th.problems(lock(1.0), fake_root(tmp_path), reg(2.0))
    assert len(found) == 1 and "x: value is 2.0 but the lock says 1.0" in found[0]


def test_missing_decision_file_fails(tmp_path):
    found = th.problems(lock(), fake_root(tmp_path, files=()), reg())
    assert found == [f"x: decision file {DECISION} does not exist"]


@pytest.mark.parametrize(
    "decision", ["", "README.md", "docs/decisions/notes.txt", "../outside/0001.md", "docs/other/1.md"]
)
def test_decision_must_be_a_markdown_file_under_docs_decisions(tmp_path, decision):
    root = fake_root(tmp_path, files=(decision,) if decision else ())
    found = th.problems(lock(decision=decision), root, reg())
    assert any("must be a .md file under docs/decisions/" in f for f in found)


def test_threshold_missing_from_the_lock_fails(tmp_path):
    found = th.problems({}, fake_root(tmp_path), reg())
    assert found and "not in" in found[0]


def test_lock_entry_for_a_removed_threshold_fails(tmp_path):
    lk = {**lock(), "gone": {"value": 3.0, "decision": DECISION}}
    found = th.problems(lk, fake_root(tmp_path), reg())
    assert found == [f"gone: in {th.LOCK_PATH} but no longer in the registry; record why in a decision"]


def test_bad_status_is_rejected_at_construction():
    with pytest.raises(ValueError, match="status"):
        th.Threshold("x", 1.0, "mag", "maybe", "1")


# ----------------------------------------------------------- change violations


def test_changed_value_citing_the_same_decision_is_a_violation():
    found = th.change_violations(lock(1.0), lock(2.0))
    assert len(found) == 1 and "write a new one" in found[0]


def test_changed_value_citing_a_new_decision_is_fine():
    new = lock(2.0, "docs/decisions/0007-retune.md")
    assert th.change_violations(lock(1.0), new) == []


def test_unchanged_value_is_fine_and_new_thresholds_are_not_violations():
    assert th.change_violations(lock(1.0), lock(1.0)) == []
    assert th.change_violations({}, lock()) == []


# ----------------------------------------------------------------------- command


def test_cli_check_passes_on_the_real_repo(capsys):
    assert main(["thresholds", "check", "--root", str(ROOT)]) == 0
    assert "ok" in capsys.readouterr().out


def test_cli_check_fails_when_the_decision_file_is_missing(tmp_path, capsys):
    (tmp_path / "docs" / "decisions").mkdir(parents=True)
    (tmp_path / th.LOCK_PATH).write_text(json.dumps(committed_lock()))
    assert main(["thresholds", "check", "--root", str(tmp_path)]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_cli_check_fails_without_a_lock(tmp_path, capsys):
    assert main(["thresholds", "check", "--root", str(tmp_path)]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_a_value_edit_in_the_registry_fails_the_real_check(monkeypatch):
    """The scenario the lock exists for: someone edits a number and nothing else."""
    changed = dict(th.THRESHOLDS)
    old = changed["astrometry_max_sep_arcsec"]
    changed["astrometry_max_sep_arcsec"] = th.Threshold(
        old.name, 20.0, old.unit, old.status, old.plan
    )
    found = th.problems(committed_lock(), ROOT, changed)
    assert len(found) == 1 and "astrometry_max_sep_arcsec" in found[0]
