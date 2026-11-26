import dataclasses
import gzip
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from computers.data import snapshot
from computers.data.snapshot import (
    ImmutableError,
    IntegrityError,
    ManifestRow,
    NotSuccessError,
    Request,
    RequestError,
    SnapshotStore,
)


class Clock:
    """Strictly increasing, valid timestamps."""

    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"2026-11-24T10:00:{self.n % 60:02d}.{self.n:06d}Z"


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path / "data", client_version="test/1", clock=Clock())


def req(endpoint="/dasch/dr7/querycat", body=None, **kw):
    body = {"refcat": "apass", "ra_deg": 1.0} if body is None else body
    return Request(source=kw.pop("source", "dasch-dr7"), endpoint=endpoint, body=body, **kw)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def manifest_lines(store, source="dasch-dr7"):
    p = store.manifest_dir / f"{source}.jsonl"
    return p.read_text().splitlines() if p.exists() else []


# ------------------------------------------------------------------- writing


def test_first_put_writes_a_read_only_file_and_one_row(store):
    body = b'["a,b","1,2"]'
    row = store.put(req(), body)
    path = store.raw_dir / row.path
    assert row.path == f"dasch-dr7/{sha(body)[:2]}/{sha(body)}.gz"
    assert path.is_file()
    assert gzip.decompress(path.read_bytes()) == body
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert len(manifest_lines(store)) == 1
    assert row.bytes == len(body) and row.sha256 == sha(body)
    assert row.client_version == "test/1" and row.api_base == "public"
    assert row.fetched_at.endswith("Z")


def test_manifest_line_has_exactly_the_documented_fields_in_order(store):
    store.put(req(), b"x")
    data = json.loads(manifest_lines(store)[0])
    assert list(data) == list(snapshot.ROW_FIELDS)


def test_gzip_output_is_deterministic_across_stores_and_times(tmp_path):
    a = SnapshotStore(tmp_path / "a", clock=Clock())
    b = SnapshotStore(tmp_path / "b", clock=Clock())
    ra, rb = a.put(req(), b"same bytes" * 100), b.put(req(), b"same bytes" * 100)
    assert (a.raw_dir / ra.path).read_bytes() == (b.raw_dir / rb.path).read_bytes()


def test_repeat_put_returns_the_same_row_without_a_duplicate(store):
    r1 = store.put(req(), b"payload")
    r2 = store.put(req(), b"payload")
    assert r1 == r2
    assert len(manifest_lines(store)) == 1


def test_changed_response_appends_and_lookup_returns_the_newest(store):
    r1 = store.put(req(), b"v1")
    r2 = store.put(req(), b"v2")
    assert r1 != r2 and len(manifest_lines(store)) == 2
    assert store.lookup(req()) == r2
    assert [r.sha256 for r in store.history(req())] == [sha(b"v1"), sha(b"v2")]


def test_flip_back_to_an_older_response_becomes_the_newest(store):
    store.put(req(), b"A")
    store.put(req(), b"B")
    r3 = store.put(req(), b"A")
    assert len(manifest_lines(store)) == 3
    assert store.lookup(req()) == r3 and r3.sha256 == sha(b"A")


def test_same_body_for_two_requests_shares_one_raw_file(store):
    r1 = store.put(req(body={"ra_deg": 1.0}), b"identical")
    r2 = store.put(req(body={"ra_deg": 2.0}), b"identical")
    assert r1.path == r2.path and r1.request_key != r2.request_key
    assert len(manifest_lines(store)) == 2
    assert len(list(store.raw_dir.rglob("*.gz"))) == 1


def test_str_body_is_refused_and_bytearray_accepted(store):
    with pytest.raises(TypeError):
        store.put(req(), "text")
    assert store.put(req(), bytearray(b"ok")).sha256 == sha(b"ok")


def test_empty_body_is_storable(store):
    row = store.put(req(), b"")
    assert store.read(row) == b""


# ----------------------------------------------------------------- immutability


def test_existing_file_with_different_content_is_never_touched(store):
    body = b"the real response"
    target = store.raw_dir / "dasch-dr7" / sha(body)[:2] / f"{sha(body)}.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(gzip.compress(b"someone else's bytes", mtime=0))
    before = target.read_bytes()
    with pytest.raises(ImmutableError):
        store.put(req(), body)
    assert target.read_bytes() == before
    assert manifest_lines(store) == []


def test_corrupt_gzip_at_the_target_is_also_refused(store):
    body = b"x"
    target = store.raw_dir / "dasch-dr7" / sha(body)[:2] / f"{sha(body)}.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not gzip at all")
    with pytest.raises(ImmutableError):
        store.put(req(), body)
    assert target.read_bytes() == b"not gzip at all"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_raw_files_cannot_be_opened_for_writing(store):
    row = store.put(req(), b"payload")
    with pytest.raises(PermissionError), open(store.raw_dir / row.path, "wb"):
        pass


def test_manifest_is_append_only_existing_bytes_never_change(store):
    store.put(req(body={"a": 1}), b"one")
    path = store.manifest_dir / "dasch-dr7.jsonl"
    first = path.read_bytes()
    store.put(req(body={"a": 2}), b"two")
    store.put(req(body={"a": 1}), b"one-changed")
    assert path.read_bytes().startswith(first)


def test_failed_link_leaves_no_file_no_temp_and_no_row(store, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "link", boom)
    with pytest.raises(OSError):
        store.put(req(), b"data")
    assert list(store.raw_dir.rglob("*.gz")) == []
    assert [p for p in store.raw_dir.rglob("*") if p.is_file()] == []
    assert manifest_lines(store) == []


# ------------------------------------------------------------------ success only


@pytest.mark.parametrize("status", [100, 199, 300, 301, 400, 404, 429, 500, 504])
def test_non_2xx_is_refused_with_no_file_and_no_row(store, status):
    with pytest.raises(NotSuccessError):
        store.put(req(), b"error page", status=status)
    assert not store.raw_dir.exists() or list(store.raw_dir.rglob("*.gz")) == []
    assert manifest_lines(store) == []


@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_2xx_is_stored(store, status):
    assert store.put(req(), b"ok", status=status)


# ---------------------------------------------------------------------- secrets


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": "abc"},
        {"x-api-key": "abc"},
        {"headers": {"X-API-KEY": "abc"}},
        {"nested": [{"Token": 1}]},
        {"password": "p"},
        {"Authorization": "Bearer x"},
        {"client_secret": "s"},
    ],
)
def test_credential_like_bodies_are_rejected(body):
    with pytest.raises(RequestError, match="credential"):
        Request(source="dasch-dr7", endpoint="/x", body=body)


def test_request_has_no_headers_field():
    names = {f.name for f in dataclasses.fields(Request)}
    assert not any("header" in n for n in names)
    assert names == {"source", "endpoint", "body", "method", "api_base"}


def test_manifest_row_has_no_field_that_could_hold_a_key():
    assert not any(snapshot.KEY_LIKE.search(n) for n in snapshot.ROW_FIELDS if n != "request_key")


def test_api_key_value_never_reaches_disk(store):
    secret = "A" * 40
    # The only way a caller could leak it is through the body, which is rejected.
    with pytest.raises(RequestError):
        store.put(req(body={"api_key": secret}), b"x")
    for p in Path(store.data_dir).rglob("*"):
        if p.is_file():
            assert secret.encode() not in p.read_bytes()


# --------------------------------------------------------------------- requests


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source": "Bad Name"},
        {"source": ""},
        {"source": "-lead"},
        {"endpoint": "no-slash"},
        {"method": "DELETE"},
        {"api_base": "private"},
        {"body": {"x": {1, 2}}},
        {"body": {"x": float("nan")}},
    ],
)
def test_invalid_requests_are_rejected(kwargs):
    base = {"source": "dasch-dr7", "endpoint": "/x", "body": {"a": 1}}
    base.update(kwargs)
    with pytest.raises(RequestError):
        Request(**base)


def test_request_key_ignores_dict_order_and_api_base_but_not_content():
    a = Request("s", "/e", {"x": 1, "y": [1, 2]})
    b = Request("s", "/e", {"y": [1, 2], "x": 1}, api_base="full")
    assert a.key == b.key
    assert a.key != Request("s", "/e", {"x": 1, "y": [2, 1]}).key
    assert a.key != Request("s", "/other", {"x": 1, "y": [1, 2]}).key
    assert a.key != Request("s", "/e", {"x": 1, "y": [1, 2]}, method="GET").key
    assert Request("s", "/e", None).key == Request("s", "/e").key


def test_api_base_is_recorded_but_both_bases_share_a_request(store):
    r1 = store.put(req(api_base="public"), b"data")
    r2 = store.put(req(api_base="full"), b"data")
    assert r1 == r2 and r1.api_base == "public"


# ------------------------------------------------------------- reading, resuming


def test_lookup_is_none_for_an_unknown_request(store):
    assert store.lookup(req()) is None


def test_lookup_resumes_from_disk_in_a_new_store_instance(store):
    row = store.put(req(), b"data")
    fresh = SnapshotStore(store.data_dir, clock=Clock())
    assert fresh.lookup(req()) == row
    assert fresh.read(row) == b"data"


def test_lookup_ignores_a_row_whose_raw_file_is_gone(store):
    row = store.put(req(), b"data")
    path = store.raw_dir / row.path
    path.chmod(0o644)
    path.unlink()
    assert store.lookup(req()) is None


def test_missing_file_is_healed_by_a_repeat_put_without_a_second_row(store):
    row = store.put(req(), b"data")
    path = store.raw_dir / row.path
    path.chmod(0o644)
    path.unlink()
    again = store.put(req(), b"data")
    assert again == row and path.exists() and len(manifest_lines(store)) == 1


def test_read_returns_original_bytes(store):
    body = bytes(range(256)) * 50
    assert store.read(store.put(req(), body)) == body


def test_read_detects_a_changed_file(store):
    row = store.put(req(), b"original")
    path = store.raw_dir / row.path
    path.chmod(0o644)
    path.write_bytes(gzip.compress(b"tampered", mtime=0))
    with pytest.raises(IntegrityError):
        store.read(row)


def test_read_detects_garbage(store):
    row = store.put(req(), b"original")
    path = store.raw_dir / row.path
    path.chmod(0o644)
    path.write_bytes(b"\x00\x01garbage")
    with pytest.raises(IntegrityError):
        store.read(row)


def test_another_process_appending_is_seen(tmp_path):
    a = SnapshotStore(tmp_path, clock=Clock())
    b = SnapshotStore(tmp_path, clock=Clock())
    assert a.lookup(req()) is None  # loads (empty) index
    b.put(req(), b"from b")
    assert a.lookup(req()).sha256 == sha(b"from b")
    a.put(req(body={"other": 1}), b"from a")
    assert b.lookup(req(body={"other": 1})).sha256 == sha(b"from a")


def test_cache_stays_correct_over_many_interleaved_puts(tmp_path):
    a = SnapshotStore(tmp_path, clock=Clock())
    b = SnapshotStore(tmp_path, clock=Clock())
    for i in range(20):
        (a if i % 2 else b).put(req(body={"i": i}), f"body {i}".encode())
    for i in range(20):
        assert a.lookup(req(body={"i": i})) is not None
        assert b.lookup(req(body={"i": i})) is not None
    assert len(manifest_lines(a)) == 20


# ---------------------------------------------------------------------- torn tail


def test_torn_last_line_is_skipped_reported_and_not_merged_with_the_next_row(store):
    store.put(req(body={"a": 1}), b"one")
    path = store.manifest_dir / "dasch-dr7.jsonl"
    with open(path, "ab") as f:
        f.write(b'{"sha256":"abc","sour')  # a write cut short, no newline
    before = path.read_bytes()

    row = store.put(req(body={"a": 2}), b"two")

    assert path.read_bytes().startswith(before)  # nothing already there was changed
    lines = manifest_lines(store)
    assert len(lines) == 3 and json.loads(lines[2])["sha256"] == row.sha256
    fresh = SnapshotStore(store.data_dir)
    assert len(fresh.rows("dasch-dr7")) == 2
    report = fresh.verify()
    assert not report.ok and report.bad_lines == ["dasch-dr7.jsonl line 2"]


@pytest.mark.parametrize(
    "junk",
    [
        "not json",
        "[]",
        '{"sha256": 1}',
        json.dumps({k: "x" for k in snapshot.ROW_FIELDS}),
    ],
)
def test_junk_lines_are_skipped_not_fatal(store, junk):
    store.put(req(), b"data")
    with open(store.manifest_dir / "dasch-dr7.jsonl", "a") as f:
        f.write(junk + "\n")
    fresh = SnapshotStore(store.data_dir)
    assert len(fresh.rows("dasch-dr7")) == 1
    assert fresh.lookup(req()) is not None


def test_row_with_a_path_outside_raw_is_rejected(store):
    row = store.put(req(), b"data")
    evil = dataclasses.replace(row, path="../../etc/passwd")
    with open(store.manifest_dir / "dasch-dr7.jsonl", "a") as f:
        f.write(evil.to_line() + "\n")
    fresh = SnapshotStore(store.data_dir)
    assert len(fresh.rows("dasch-dr7")) == 1
    assert len(fresh.verify().bad_lines) == 1


def test_row_filed_under_the_wrong_source_is_rejected(store):
    row = store.put(req(), b"data")
    other = store.manifest_dir / "vizier-vsx.jsonl"
    other.write_text(row.to_line() + "\n")
    assert SnapshotStore(store.data_dir).rows("vizier-vsx") == []


def test_manifest_row_round_trip_and_validation():
    row = ManifestRow(
        sha256="a" * 64, source="s", method="POST", endpoint="/e", request="null",
        request_key="b" * 64, path=f"s/aa/{'a' * 64}.gz", bytes=3,
        fetched_at="2026-11-24T10:00:00.000001Z", client_version="c", api_base="public",
    )  # fmt: skip
    assert ManifestRow.from_line(row.to_line()) == row
    for bad in (
        dataclasses.replace(row, sha256="xyz"),
        dataclasses.replace(row, bytes=-1),
        dataclasses.replace(row, fetched_at="yesterday"),
        dataclasses.replace(row, api_base="private"),
        dataclasses.replace(row, request="{not json"),
        dataclasses.replace(row, path="s/zz/other.gz"),
    ):
        with pytest.raises(snapshot.ManifestError):
            ManifestRow.from_line(bad.to_line())


# ------------------------------------------------------------------------ verify


def test_verify_clean_store(store):
    store.put(req(body={"a": 1}), b"one")
    store.put(req(body={"a": 2}), b"two")
    report = store.verify()
    assert report.ok and report.rows_checked == 2


def test_verify_flags_a_corrupted_file(store):
    row = store.put(req(), b"original")
    path = store.raw_dir / row.path
    path.chmod(0o644)
    path.write_bytes(gzip.compress(b"corrupted", mtime=0))
    report = store.verify()
    assert not report.ok and report.mismatched == [row.path]


def test_verify_flags_a_missing_file(store):
    row = store.put(req(), b"original")
    path = store.raw_dir / row.path
    path.chmod(0o644)
    path.unlink()
    assert store.verify().missing == [row.path]


def test_verify_flags_unreferenced_files_and_stray_temps(store):
    store.put(req(), b"original")
    folder = store.raw_dir / "dasch-dr7" / "ff"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("f" * 64 + ".gz")).write_bytes(gzip.compress(b"orphan"))
    (folder / ".tmp-abc").write_bytes(b"half")
    report = store.verify()
    assert report.unreferenced == [f"dasch-dr7/ff/{'f' * 64}.gz"]
    assert report.stray_temp == ["dasch-dr7/ff/.tmp-abc"]
    assert any("stray temporary file" in line for line in report.lines())


def test_verify_on_an_empty_directory(tmp_path):
    assert SnapshotStore(tmp_path / "nothing").verify().ok


# ----------------------------------------------------------------- digest, status


def test_digest_is_independent_of_fetch_order(tmp_path):
    items = [(req(body={"i": i}), f"body {i}".encode()) for i in range(6)]
    a = SnapshotStore(tmp_path / "a", clock=Clock())
    b = SnapshotStore(tmp_path / "b", clock=Clock())
    for r, body in items:
        a.put(r, body)
    for r, body in reversed(items):
        b.put(r, body)
    assert a.digest() == b.digest() and a.digest().startswith("sha256:")


def test_digest_matches_an_independent_computation(store):
    r1, r2 = req(body={"i": 1}), req(body={"i": 2})
    store.put(r1, b"one")
    store.put(r2, b"two")
    lines = sorted(f"dasch-dr7\t{r.key}\t{sha(b)}" for r, b in ((r1, b"one"), (r2, b"two")))
    assert store.digest() == "sha256:" + sha("\n".join(lines).encode())


def test_digest_changes_when_a_response_changes_and_ignores_history(store):
    store.put(req(), b"v1")
    d1 = store.digest()
    store.put(req(), b"v2")
    d2 = store.digest()
    assert d1 != d2
    store.put(req(), b"v1")  # newest is v1 again, so the set equals the first one
    assert store.digest() == d1


def test_digest_spans_sources_and_empty_store_is_stable(store, tmp_path):
    empty = SnapshotStore(tmp_path / "empty").digest()
    assert empty == "sha256:" + sha(b"")
    store.put(req(source="dasch-dr7"), b"a")
    d = store.digest()
    store.put(req(source="vizier-vsx"), b"a")
    assert store.digest() != d


def test_status_counts_per_source(store):
    store.put(req(body={"a": 1}), b"123")
    store.put(req(body={"a": 1}), b"1234")  # changed response, same request
    store.put(req(body={"a": 2}), b"12")
    st = store.status()["dasch-dr7"]
    assert st == {"rows": 3, "requests": 2, "bytes": 4 + 2, "bad_lines": 0}
