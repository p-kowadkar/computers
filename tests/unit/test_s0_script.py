"""The spike script must never print or save the API key (requirement R1.3)."""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("daschlab")  # only available with `uv sync --group spike`

ROOT = Path(__file__).resolve().parents[2]
FAKE_KEY = "FAKEKEY0123456789FAKEKEY0123456789FAKE"


def _load_script():
    spec = importlib.util.spec_from_file_location("s0_spike", ROOT / "scripts" / "s0_spike.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_key_is_never_printed_or_saved(tmp_path, monkeypatch, capsys):
    s0 = _load_script()
    monkeypatch.setenv("STARGLASS_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DASCHLAB_API_KEY", "")  # so the script's own assignment is undone afterwards
    monkeypatch.setattr(s0, "SPIKE_DIR", tmp_path)

    def failing_open(*_args, **_kwargs):
        # A realistic failure: an HTTP error message that echoes the key back.
        raise RuntimeError(f"401 for header x-api-key={FAKE_KEY}")

    monkeypatch.setattr(s0, "open_target_session", failing_open)

    code = s0.main(["--targets", "rycnc", "--no-timing"])

    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err
    saved = (tmp_path / "results.json").read_text(encoding="utf-8")
    assert FAKE_KEY not in saved
    assert "[redacted]" in saved
    assert "FULL API" in captured.out  # it still says which API it used
    assert code == 0  # only a T CrB failure makes the script exit non-zero
    assert json.loads(saved)["targets"]["rycnc"]["error"]


def test_public_mode_prints_notice(tmp_path, monkeypatch, capsys):
    s0 = _load_script()
    monkeypatch.delenv("STARGLASS_API_KEY", raising=False)
    monkeypatch.setenv("DASCHLAB_API_KEY", "")
    monkeypatch.setattr(s0, "SPIKE_DIR", tmp_path)
    monkeypatch.setattr(s0, "open_target_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    s0.main(["--targets", "rycnc", "--no-timing"])
    assert "PUBLIC API" in capsys.readouterr().out
    assert json.loads((tmp_path / "results.json").read_text())["last_run_api_mode"] == "public"
