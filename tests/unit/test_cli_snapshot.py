import gzip
import json

import pytest
import requests

from computers import cli
from computers.cli import main
from computers.data.snapshot import SnapshotStore


def run(tmp_path, *args):
    return main(["snapshot", *args, "--data-dir", str(tmp_path / "data")])


@pytest.fixture
def healthy(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return 200, b'{"status":"ready","message":"DB is available and fully loaded"}'

    monkeypatch.setattr(cli, "_http_get", fake_get)
    return calls


def test_probe_stores_the_response_and_prints_the_row(tmp_path, healthy, capsys):
    assert run(tmp_path, "probe") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["endpoint"] == "/health" and out["method"] == "GET"
    assert out["source"] == "dasch-dr7" and out["api_base"] == "public"
    assert out["client_version"].startswith("computers/")
    assert healthy == [("https://api.starglass.cfa.harvard.edu/public/health", 20)]

    store = SnapshotStore(tmp_path / "data")
    (row,) = store.rows("dasch-dr7")
    assert gzip.decompress((store.raw_dir / row.path).read_bytes()).startswith(b'{"status"')
    assert store.verify().ok


def test_probe_twice_does_not_duplicate(tmp_path, healthy):
    assert run(tmp_path, "probe") == 0
    assert run(tmp_path, "probe") == 0
    assert len(SnapshotStore(tmp_path / "data").rows("dasch-dr7")) == 1


@pytest.mark.parametrize("exc", [requests.ConnectionError("no route"), requests.Timeout("slow")])
def test_probe_unreachable_exits_nonzero_and_writes_nothing(tmp_path, monkeypatch, capsys, exc):
    def boom(url, timeout):
        raise exc

    monkeypatch.setattr(cli, "_http_get", boom)
    assert run(tmp_path, "probe") == 1
    assert "cannot reach the API" in capsys.readouterr().err
    assert not (tmp_path / "data").exists() or not list((tmp_path / "data").rglob("*.*"))


@pytest.mark.parametrize("status", [404, 500, 504])
def test_probe_http_error_exits_nonzero_and_writes_nothing(tmp_path, monkeypatch, capsys, status):
    monkeypatch.setattr(cli, "_http_get", lambda url, timeout: (status, b"error"))
    assert run(tmp_path, "probe") == 1
    assert f"HTTP {status}" in capsys.readouterr().err
    assert not (tmp_path / "data" / "raw").exists() or not list(
        (tmp_path / "data" / "raw").rglob("*.gz")
    )
    assert SnapshotStore(tmp_path / "data").rows("dasch-dr7") == []


def test_verify_ok_then_flags_a_corrupted_file(tmp_path, healthy, capsys):
    run(tmp_path, "probe")
    capsys.readouterr()
    assert run(tmp_path, "verify") == 0
    assert "ok" in capsys.readouterr().out

    store = SnapshotStore(tmp_path / "data")
    (row,) = store.rows("dasch-dr7")
    path = store.raw_dir / row.path
    path.chmod(0o644)
    path.write_bytes(gzip.compress(b"corrupted", mtime=0))

    assert run(tmp_path, "verify") == 1
    captured = capsys.readouterr()
    assert "content does not match hash" in captured.err
    assert "PROBLEMS FOUND" in captured.out


def test_digest_and_status(tmp_path, healthy, capsys):
    assert run(tmp_path, "status") == 0
    assert "no snapshots yet" in capsys.readouterr().out
    run(tmp_path, "probe")
    capsys.readouterr()
    assert run(tmp_path, "digest") == 0
    assert capsys.readouterr().out.startswith("sha256:")
    assert run(tmp_path, "status") == 0
    assert "dasch-dr7" in capsys.readouterr().out
