import json

import pytest
from synth_leavitt import write_all

from computers.bench import leavitt as L
from computers.bench import recovery as R
from computers.bench import trigger_bench as TB
from computers.cli import main
from computers.l1.triggers import KINDS, Trigger, TriggerParams


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("trig") / "data"
    write_all(d)
    L.build_set(d, say=lambda _: None)
    return d


def test_load_dev_gives_hosts_contexts_and_specs_for_the_dev_tiles_only(built):
    dev = TB.load_dev(built)
    assert dev.policy == "reject" and set(dev.contexts) == set(dev.hosts) and dev.hosts
    assert all(r["split"] == "dev" for r in dev.rows)
    assert {s.family for s in dev.specs()} == {"linear", "step", "dip", "outburst", "wave"}
    one = dev.specs(("step",), replicate=0)
    assert one and {s.family for s in one} == {"step"} and len(one) == len(dev.specs(("step",))) // 2


def test_the_false_alarm_table_counts_hosts_not_triggers_and_names_the_kill_reasons():
    def t(kind, *reasons, single=False):
        return Trigger(kind, 1900.0, 1901.0, 1.0, single, (1,), True, tuple(reasons))

    base = {
        1: (t("outburst", "single_series_only"), t("outburst", "single_series_only", "non_blue_only", single=True)),
        2: (t("dip"),),
        3: (),
        4: (t("step", "known_variable"),),  # known_variable alone does not kill
    }
    fa = TB.false_alarm_table(base)
    assert fa["hosts"] == 4 and set(KINDS) <= set(fa)
    assert fa["outburst"]["found"] == 1 and fa["outburst"]["survive"] == 0
    assert fa["outburst"]["killed_by"] == {"non_blue_only": 1, "single_series_only": 1}  # hosts, not triggers
    assert fa["dip"]["survive"] == 1 and fa["step"]["survive"] == 1 and fa["step"]["killed_by"] == {}
    assert fa["any"]["found"] == 3 and fa["any"]["survive"] == 2
    assert fa["any"]["per_1000"] == pytest.approx(500.0) and fa["any"]["low"] < 500.0 < fa["any"]["high"]
    assert fa["single_plate_outbursts"] == {"found": 1, "survive": 0}


@pytest.mark.slow
def test_the_report_has_every_section_on_synthetic_data(built):
    r = TB.run_trigger_report(built, sweeps=False, ablations=True, say=lambda _: None)
    assert set(r["default"]) == {"false_alarms", "completeness", "confusion"}
    assert set(r["ablations"]) == {"no_detrending", "no_seam_rule", "meteor_keep"}
    for a in r["ablations"].values():
        assert a["false_alarms"]["hosts"] == r["hosts"] and "bins" in a["completeness"]["any"]
    assert {c["family"] for c in r["default"]["completeness"]["any"]["crossings"]} == {"dip", "linear", "outburst", "step", "wave"}
    assert sum(sum(row.values()) for row in r["default"]["confusion"].values()) == r["injections"]
    text = TB.format_trigger_report(r)
    for title in ("1. False alarms", "2. Completeness", "3. Confusion", "4. Ablations", "6. Known variables"):
        assert title in text
    assert "vanish" not in text.lower()


def test_a_sweep_changes_one_parameter_and_reports_false_alarms_and_recovery(built, monkeypatch):
    monkeypatch.setattr(TB, "SWEEPS", {"step": {"families": ("step",), "grid": [("step_shift_mag", [0.5, 5.0])]}})
    dev = TB.load_dev(built)
    rows = TB.sweep(dev)["step"]
    assert [r["value"] for r in rows] == [0.5, 5.0] and all(r["parameter"] == "step_shift_mag" for r in rows)
    assert rows[1]["recovered"] == 0 and rows[0]["recovered"] >= rows[1]["recovered"]  # a 5 mag shift is never reached
    assert rows[0]["n"] == rows[1]["n"] and rows[0]["false_alarms"] >= rows[1]["false_alarms"]


def test_the_series_sweep_sets_the_per_kind_override():
    base = TriggerParams.from_registry()
    p = TB._params(base, "outburst", "series", 1)
    assert p.min_series("outburst") == 1 and p.min_series("dip") == 2
    assert TB._params(base, "outburst", "return_points", 3).return_points == 3
    assert TB._params(base, "dip", "return_recovery", 0.5).return_recovery == 0.5


@pytest.mark.slow
def test_the_command_writes_its_json(built, capsys, monkeypatch):
    monkeypatch.setattr(TB, "SWEEPS", {})
    assert main(["bench", "triggers", "--quick", "--data-dir", str(built)]) == 0
    out = capsys.readouterr().out
    assert "L1 triggers on the dev split" in out
    data = json.loads((built / "reports" / "bench-triggers.json").read_text())
    assert data["default"]["false_alarms"]["hosts"] == data["hosts"]


def test_recovery_helpers_are_consistent_with_the_report(built):
    dev = TB.load_dev(built)
    base = R.host_triggers(dev.contexts, dev.hosts)
    assert set(base) == set(dev.hosts)
