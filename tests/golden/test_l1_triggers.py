"""Golden tests for L1 triggers (spec R11.2, R11.3).
Frozen counts on the five committed fixtures live in `expected/l1_triggers.json`. Changing them
needs a decision record. The excerpts have no neighbours, so they are read undetrended.
The full-curve T CrB and KIC 8462852 checks are in `test_l1_triggers_real.py` (slow).
"""
import json

import pytest
from _l1 import fixtures
from _l1_triggers import EXPECTED_TRIGGERS, analyse_fixture, current

T_CRB = 415593021255513


def test_frozen_trigger_counts_match():
    assert current() == json.loads(EXPECTED_TRIGGERS.read_text())


def test_t_crb_excerpt_eruption_is_a_single_series_outburst_under_keep():
    # The excerpt's eruption points are one series, so the series rule must kill them: found, then killed.
    path = next(p for p in fixtures() if str(T_CRB) in p.name)
    _, r = analyse_fixture(path, "keep")
    out = [t for t in r.triggers if t.kind == "outburst"]
    assert len(out) == 1 and 1946.0 <= out[0].epoch_start <= 1946.3
    assert out[0].amplitude_mag >= 3.0 and out[0].kill_reasons == ("single_series_only",)
    assert not r.survivors


@pytest.mark.parametrize("path", fixtures(), ids=lambda p: p.stem)
@pytest.mark.parametrize("policy", ["reject", "keep"])
def test_no_fixture_excerpt_yields_a_surviving_trigger(path, policy):
    # No fixture can pass the two-series rule alone, so a survivor here would mean the rule broke.
    _, r = analyse_fixture(path, policy)
    assert not r.survivors


def test_a_check_that_can_fail_the_frozen_file_detects_a_change():
    cur = current()
    cur[str(T_CRB)]["keep"]["n_triggers"] += 1
    assert cur != json.loads(EXPECTED_TRIGGERS.read_text())
