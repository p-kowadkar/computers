"""Triggers on the real local tiles: T CrB's 1946 eruption survives; KIC 8462852 gives no fade or step.
KIC runs as a regression check at the chosen thresholds, not as tuning. Run with `-m slow`.
"""
from pathlib import Path

import pytest

from computers.l1.analyse import analyse_star
from computers.l1.context import TileContext

ROOT = Path(__file__).resolve().parents[2]
T_CRB, KIC = 415593021255513, 420061551442725
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (ROOT / "data" / "silver" / "measurements" / "tile=tcrb.parquet").exists(), reason="needs the local pull"),
]


def test_t_crb_full_curve_fires_a_surviving_outburst():
    r = analyse_star(TileContext.from_silver(ROOT / "data", "tcrb"), T_CRB)
    # T CrB has other surviving outbursts (1939 to 1951); pick the 1946 eruption by epoch.
    out = [t for t in r.survivors if t.kind == "outburst" and 1946.0 <= t.epoch_start <= 1946.3]
    assert len(out) == 1
    assert len(out[0].evidence) >= 20 and out[0].amplitude_mag >= 3.0
    assert not out[0].single_plate and not out[0].detrended


@pytest.mark.skipif(not (ROOT / "data" / "silver" / "measurements" / "tile=tabby.parquet").exists(), reason="needs the Tabby pull")
def test_kic_8462852_has_no_surviving_long_term_or_step_trigger():
    # Read-only regression on the test tile's star. A guard in `l1 run` still refuses a Tabby patrol before the split is frozen.
    r = analyse_star(TileContext.from_silver(ROOT / "data", "tabby"), KIC)
    assert not [t for t in r.survivors if t.kind in ("long_term", "step")]
