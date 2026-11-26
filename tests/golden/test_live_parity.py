"""Live parity (R5.4): daschlab's own HTTP client and ours get the same bytes from DASCH.

The offline golden test shows the parsers agree on a given response. This one shows the
*requests* agree: for 20 stars it sends the same payload through daschlab's `ApiClient`
and compares the response, line by line, with what our client stored. It talks to the
real API, so it carries the `network` marker and is excluded from the default run:

    uv run pytest tests/golden/test_live_parity.py -m network
"""

import json
from collections import Counter

import pyarrow.compute as pc
import pytest
from _parity import ROOT, daschlab_table, parse_ours

from computers.data import silver
from computers.data.snapshot import SnapshotStore

pytestmark = pytest.mark.network
pytest.importorskip("daschlab", reason="needs `uv sync --group spike`")

N_STARS = 20


def dates(lines):
    return [float(line.split(",")[0]) for line in lines[1:]]


def stored_lightcurves():
    store = SnapshotStore(ROOT / "data")
    seen = {}
    for source in store.sources():
        for row in store.rows(source):
            if row.endpoint.endswith("/lightcurve"):
                body = json.loads(row.request)
                seen.setdefault(int(body["ref_number"]), (body, json.loads(store.read(row))))
    return seen


def test_twenty_stars_return_identical_responses_through_both_clients(monkeypatch):
    from daschlab.apiclient import ApiClient

    monkeypatch.delenv("DASCHLAB_API_KEY", raising=False)  # the public API is enough
    stored = stored_lightcurves()
    if len(stored) < N_STARS:
        pytest.skip(f"only {len(stored)} lightcurves stored; run `computers pull --stage lightcurves` first")
    client = ApiClient()
    compared = 0
    for ref, (body, ours) in sorted(stored.items())[:N_STARS]:
        theirs = client.invoke("/dasch/dr7/lightcurve", dict(body))
        assert isinstance(theirs, list), f"daschlab's client got an error for {ref}: {theirs!r}"
        # Same header, same rows. The API orders rows that share a timestamp differently on
        # every call, so compare as multisets and check that both are sorted by date.
        assert theirs[0] == ours[0], f"star {ref}: different headers"
        assert Counter(theirs) == Counter(ours), f"star {ref}: the two clients received different rows"
        assert dates(theirs) == sorted(dates(theirs)) and dates(ours) == sorted(dates(ours))
        compared += 1
    assert compared == N_STARS


def test_a_catalog_box_returns_identical_responses_through_both_clients(monkeypatch):
    from daschlab.apiclient import ApiClient

    monkeypatch.delenv("DASCHLAB_API_KEY", raising=False)
    store = SnapshotStore(ROOT / "data")
    rows = [r for s in store.sources() for r in store.rows(s) if r.endpoint.endswith("/querycat")]
    if not rows:
        pytest.skip("no catalog responses stored; run `computers pull --stage catalog` first")
    row = rows[0]
    body = json.loads(row.request)
    assert ApiClient().invoke("/dasch/dr7/querycat", dict(body)) == json.loads(store.read(row))


def test_the_stored_response_also_parses_the_same_way_as_daschlab():
    """One last end-to-end check on the silver table the pull wrote."""
    path = silver.silver_path(ROOT / "data", "measurements", "m44")
    if not path.exists():
        pytest.skip("no M44 measurements table")
    table = silver.read_table(path)
    ref = table.column("ref_number")[0].as_py()
    store = SnapshotStore(ROOT / "data")
    lines = next(
        json.loads(store.read(r))
        for s in store.sources()
        for r in store.rows(s)
        if r.endpoint.endswith("/lightcurve") and int(json.loads(r.request)["ref_number"]) == ref
    )
    mine = table.filter(pc.equal(table.column("ref_number"), ref))
    assert mine.num_rows == parse_ours(lines, ref).num_rows == len(daschlab_table(lines))
