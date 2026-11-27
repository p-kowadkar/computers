"""L1 on the real local tiles: the T CrB eruption and KIC 8462852 (spec R7.1, R9).

These need the pulled data under `data/` and take several seconds, so they are marked
slow and skipped when the data is absent. Run them with `uv run pytest -m slow`.
"""

from pathlib import Path

import numpy as np
import pytest

from computers.l1 import detrend as D
from computers.l1.context import TileContext
from computers.l1.tags import Tag

ROOT = Path(__file__).resolve().parents[2]
T_CRB, KIC = 415593021255513, 420061551442725
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (ROOT / "data" / "silver" / "measurements" / "tile=tcrb.parquet").exists(), reason="needs the local pull"),
]


def eruption(c, d_mag, base):
    yr = 2000.0 + (c.time - 2451545.0) / 365.25
    return c.detected & (yr >= 1946.0) & (yr <= 1946.3) & (d_mag <= base - 1.0)


@pytest.fixture(scope="module")
def tcrb():
    return {p: TileContext.from_silver(ROOT / "data", "tcrb", meteor_policy=p) for p in ("reject", "keep")}


def test_t_crb_keeps_its_eruption_through_cleaning_under_both_meteor_policies(tcrb):
    for policy, ctx in tcrb.items():
        c = ctx.curve(T_CRB)
        base = float(np.median(c.mag[c.good]))
        rows = eruption(c, c.mag, base)
        kept = rows & c.good_for_outburst
        assert rows.sum() >= 30 and kept.sum() >= 25, policy  # dozens of points survive, not two
        assert kept.sum() / rows.sum() > 0.6
        assert c.mag[kept].min() < 8.1  # the eruption reaches its saturated peak (7.7 to 8.0)


def test_t_crb_cannot_be_detrended_because_it_is_bright_and_red(tcrb):
    ctx = tcrb["reject"]
    i = ctx.index[T_CRB]
    assert ctx.colour[i] > 1.3 and ctx.mag[i] < 11.5
    d = D.detrend(ctx, ctx.curve(T_CRB))
    assert d.kill_reason == "few_comparisons" and not d.detrended and d.offsets.pool_size == 2
    assert np.array_equal(d.mag, ctx.curve(T_CRB).mag, equal_nan=True)  # the raw curve, eruption intact
    c = ctx.curve(T_CRB)
    assert eruption(c, d.mag, float(np.median(c.mag[c.good]))).sum() >= 30


def test_kic_8462852_has_a_full_pool_and_is_detrended():
    ctx = TileContext.from_silver(ROOT / "data", "tabby")
    c = ctx.curve(KIC)
    d = D.detrend(ctx, c)
    assert d.detrended and d.kill_reason is None and d.offsets.pool_size == 40
    assert d.offsets.colour_cut
    no_offset = d.curve.has(Tag.NO_OFFSET)
    assert (no_offset & c.good).sum() / c.n_good < 0.01  # 5 of 1,775 good detections have no offset


def test_the_meteor_policy_changes_what_survives_in_the_tile(tcrb):
    reject, keep = tcrb["reject"], tcrb["keep"]
    n_reject, n_keep = np.isfinite(reject.matrix).sum(), np.isfinite(keep.matrix).sum()
    assert n_keep > n_reject and 0.1 < 1 - n_reject / n_keep < 0.35  # meteor plates are about a fifth of good points
    assert reject.quiet.tolist() == keep.quiet.tolist()
