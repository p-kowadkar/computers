"""Golden test: our lightcurve parser must agree with daschlab 1.1.0's own, exactly.

Requirement R5 of .kiro/specs/s1b-thin-client/. It runs offline on:

* committed excerpts of real responses (tests/golden/fixtures), so it runs everywhere;
* every lightcurve response in the local snapshot store, when there are any.

When the thin client cannot be made to match, the project stays on daschlab for pulls.
"""

import numpy as np
import pyarrow as pa
import pytest
from _parity import compare, daschlab_table, fixture_inputs, local_inputs, parse_ours

pytest.importorskip("daschlab", reason="needs `uv sync --group spike`")

FIXTURES = list(fixture_inputs())
LOCAL = list(local_inputs())
ALL = FIXTURES + LOCAL


def _id(item):
    return item[0]


def test_there_is_something_to_compare():
    assert FIXTURES, "no committed fixtures in tests/golden/fixtures"


@pytest.mark.parametrize("item", FIXTURES, ids=_id)
def test_fixture_excerpt_agrees_with_daschlab(item):
    name, ref, lines = item
    diffs = compare(parse_ours(lines, ref), daschlab_table(lines))
    assert diffs == [], f"{name}: " + "; ".join(diffs)


@pytest.mark.parametrize("item", LOCAL, ids=_id)
def test_local_snapshot_response_agrees_with_daschlab(item):
    name, ref, lines = item
    diffs = compare(parse_ours(lines, ref), daschlab_table(lines))
    assert diffs == [], f"{name}: " + "; ".join(diffs)


def test_twenty_stars_were_compared():
    """R5.2: at least 20 stars. Skipped, with the reason, when the local snapshot is small."""
    if len(ALL) < 20:
        pytest.skip(
            f"only {len(ALL)} stars available; pull more with `computers pull --stage lightcurves`"
        )
    assert len({ref for _, ref, _ in ALL}) >= 20


# ---------------------------------------------------------- the comparison itself


def test_the_comparison_detects_a_wrong_value():
    """A parity test that cannot fail proves nothing."""
    _, ref, lines = (FIXTURES or ALL)[0]
    ours = parse_ours(lines, ref)
    theirs = daschlab_table(lines)
    assert compare(ours, theirs) == []

    # change one magnitude by one float32 step
    i = int(np.flatnonzero(~np.asarray(ours.column("magcal_magdep").is_null()))[0])
    col = ours.column("magcal_magdep").to_numpy(zero_copy_only=False).copy()
    col[i] = np.nextafter(np.float32(col[i]), np.float32(100))
    j = ours.schema.get_field_index("magcal_magdep")
    broken = ours.set_column(j, ours.schema.field(j), pa.array(col, type=pa.float32(), mask=np.isnan(col)))
    assert any("magcal_magdep" in d for d in compare(broken, theirs))


def test_the_comparison_detects_a_wrong_rejection_and_a_wrong_null():
    _, ref, lines = (FIXTURES or ALL)[0]
    ours = parse_ours(lines, ref)
    theirs = daschlab_table(lines)
    j = ours.schema.get_field_index("reject")
    flipped = ours.column("reject").to_numpy().copy()
    flipped[0] ^= 1
    bad_reject = ours.set_column(j, ours.schema.field(j), pa.array(flipped, type=pa.uint64()))
    assert any(d.startswith("reject") for d in compare(bad_reject, theirs))

    k = ours.schema.get_field_index("ellipticity")
    vals = ours.column("ellipticity").to_pylist()
    vals[1] = None if vals[1] is not None else 0.5
    bad_null = ours.set_column(k, ours.schema.field(k), pa.array(vals, type=pa.float32()))
    assert any("ellipticity" in d and "missing-ness" in d for d in compare(bad_null, theirs))


def test_row_count_mismatch_is_reported():
    _, ref, lines = (FIXTURES or ALL)[0]
    ours = parse_ours(lines, ref)
    assert compare(ours.slice(0, ours.num_rows - 1), daschlab_table(lines))[0].startswith("row count")


def test_input_sizes_are_reported(capsys):
    print(f"golden inputs: {len(FIXTURES)} fixtures, {len(LOCAL)} local responses")
    assert len(ALL) >= len(FIXTURES) >= 1
