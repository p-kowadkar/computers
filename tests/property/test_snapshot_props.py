"""Property tests for the snapshot store (requirements R4, R6, R7)."""

import datetime as dt
import hashlib
import json
import tempfile
import threading
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from computers.data.snapshot import ManifestRow, Request, SnapshotStore, canonical_json

SAFE_KEYS = ["a", "b", "ra_deg", "dec_deg", "refcat", "ref_number", "radius_arcsec", "n"]
json_scalars = (
    st.none()
    | st.booleans()
    | st.integers(-(10**9), 10**9)
    | st.floats(allow_nan=False, allow_infinity=False, width=64)
    | st.text(max_size=12)
)
json_values = st.recursive(
    json_scalars,
    lambda children: st.lists(children, max_size=3)
    | st.dictionaries(st.sampled_from(SAFE_KEYS), children, max_size=3),
    max_leaves=10,
)
bodies = st.dictionaries(st.sampled_from(SAFE_KEYS), json_values, max_size=5)
payloads = st.binary(max_size=2000)
no_fixture_check = settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)


def reorder(value, rnd):
    """The same JSON value with every dict's key order shuffled."""
    if isinstance(value, dict):
        items = [(k, reorder(v, rnd)) for k, v in value.items()]
        rnd.shuffle(items)
        return dict(items)
    if isinstance(value, list):
        return [reorder(v, rnd) for v in value]
    return value


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Clock:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"2026-11-24T10:00:00.{self.n % 1_000_000:06d}Z"


# ------------------------------------------------------------------ round trips


@given(
    payload=payloads,
    body_hash=payloads,
    source=st.from_regex(r"[a-z0-9][a-z0-9._-]{0,20}", fullmatch=True),
    method=st.sampled_from(["GET", "POST"]),
    api_base=st.sampled_from(["public", "full"]),
    when=st.datetimes(min_value=dt.datetime(1990, 1, 1), max_value=dt.datetime(2200, 1, 1)),
    client=st.text(min_size=1, max_size=30),
    body=bodies,
)
def test_manifest_row_round_trips_exactly(
    payload, body_hash, source, method, api_base, when, client, body
):
    digest = sha(payload)
    row = ManifestRow(
        sha256=digest,
        source=source,
        method=method,
        endpoint="/dasch/dr7/x",
        request=canonical_json(body),
        request_key=sha(body_hash),
        path=f"{source}/{digest[:2]}/{digest}.gz",
        bytes=len(payload),
        fetched_at=when.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        client_version=client,
        api_base=api_base,
    )
    line = row.to_line()
    assert "\n" not in line
    assert ManifestRow.from_line(line) == row
    assert ManifestRow.from_line(line).to_line() == line


@given(payload=payloads, body=bodies)
@no_fixture_check
def test_put_then_read_returns_the_exact_bytes(payload, body):
    with tempfile.TemporaryDirectory() as d:
        store = SnapshotStore(d, clock=Clock())
        row = store.put(Request("dasch-dr7", "/e", body), payload)
        assert store.read(row) == payload
        assert row.sha256 == sha(payload) and row.bytes == len(payload)
        assert store.verify().ok


@given(payload=payloads, body=bodies)
@no_fixture_check
def test_putting_twice_never_adds_a_second_row(payload, body):
    with tempfile.TemporaryDirectory() as d:
        store = SnapshotStore(d, clock=Clock())
        request = Request("dasch-dr7", "/e", body)
        first = store.put(request, payload)
        assert store.put(request, payload) == first
        lines = (Path(d) / "manifests" / "dasch-dr7.jsonl").read_text().splitlines()
        assert len(lines) == 1


# ------------------------------------------------------------------ stability


@given(body=bodies, seed=st.randoms(use_true_random=False))
def test_request_key_ignores_dict_key_order(body, seed):
    a = Request("s", "/e", body)
    b = Request("s", "/e", reorder(body, seed))
    assert a.key == b.key
    assert a.canonical_body == b.canonical_body


@given(body=bodies)
def test_request_key_changes_with_the_endpoint_and_method(body):
    base = Request("s", "/e", body)
    assert base.key != Request("s", "/f", body).key
    assert base.key != Request("s", "/e", body, method="GET").key


@given(payload=payloads)
@no_fixture_check
def test_hash_and_file_bytes_are_identical_across_independent_stores(payload):
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        s1, s2 = SnapshotStore(d1, clock=Clock()), SnapshotStore(d2, clock=Clock())
        r1 = s1.put(Request("dasch-dr7", "/e", {"a": 1}), payload)
        r2 = s2.put(Request("dasch-dr7", "/e", {"a": 1}), payload)
        assert r1.sha256 == r2.sha256 == sha(payload)
        assert (s1.raw_dir / r1.path).read_bytes() == (s2.raw_dir / r2.path).read_bytes()


@given(
    items=st.lists(st.tuples(bodies, payloads), min_size=0, max_size=8, unique_by=lambda t: canonical_json(t[0])),
    seed=st.randoms(use_true_random=False),
)
@no_fixture_check
def test_digest_does_not_depend_on_the_order_of_fetching(items, seed):
    shuffled = list(items)
    seed.shuffle(shuffled)
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        a, b = SnapshotStore(d1, clock=Clock()), SnapshotStore(d2, clock=Clock())
        for body, payload in items:
            a.put(Request("dasch-dr7", "/e", body), payload)
        for body, payload in shuffled:
            b.put(Request("dasch-dr7", "/e", body), payload)
        assert a.digest() == b.digest()


@given(first=payloads, second=payloads)
@no_fixture_check
def test_digest_follows_the_newest_response_only(first, second):
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        a, b = SnapshotStore(d1, clock=Clock()), SnapshotStore(d2, clock=Clock())
        request = Request("dasch-dr7", "/e", {"a": 1})
        a.put(request, first)
        a.put(request, second)
        b.put(request, second)
        assert a.digest() == b.digest()


# ------------------------------------------------------------------ concurrency


def test_threads_never_lose_or_interleave_rows(tmp_path):
    store = SnapshotStore(tmp_path, clock=Clock())
    n_threads, per_thread = 8, 50
    errors = []

    def worker(t: int):
        try:
            for i in range(per_thread):
                body = {"t": t, "i": i}
                store.put(Request("dasch-dr7", "/e", body), f"thread {t} item {i}".encode() * 20)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []
    lines = (tmp_path / "manifests" / "dasch-dr7.jsonl").read_text().splitlines()
    assert len(lines) == n_threads * per_thread
    for line in lines:
        ManifestRow.from_line(line)  # every line is a whole, valid row
    assert len({json.loads(line)["request_key"] for line in lines}) == len(lines)
    report = store.verify()
    assert report.ok and report.rows_checked == len(lines)


def test_threads_putting_the_same_request_leave_one_row(tmp_path):
    store = SnapshotStore(tmp_path, clock=Clock())
    request = Request("dasch-dr7", "/e", {"same": True})
    threads = [
        threading.Thread(target=lambda: store.put(request, b"identical" * 100)) for _ in range(16)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len((tmp_path / "manifests" / "dasch-dr7.jsonl").read_text().splitlines()) == 1
    assert store.verify().ok


def test_two_stores_on_one_directory_do_not_corrupt_each_other(tmp_path):
    """Separate store objects share no lock but share the manifest, as separate processes would."""
    a, b = SnapshotStore(tmp_path, clock=Clock()), SnapshotStore(tmp_path, clock=Clock())

    def work(store, tag):
        for i in range(40):
            store.put(Request("dasch-dr7", "/e", {"tag": tag, "i": i}), f"{tag}{i}".encode() * 10)

    ta, tb = threading.Thread(target=work, args=(a, "a")), threading.Thread(target=work, args=(b, "b"))
    ta.start(), tb.start()
    ta.join(), tb.join()
    lines = (tmp_path / "manifests" / "dasch-dr7.jsonl").read_text().splitlines()
    assert len(lines) == 80
    assert all(ManifestRow.from_line(line) for line in lines)
    assert SnapshotStore(tmp_path).verify().ok
