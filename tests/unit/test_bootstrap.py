import importlib.util
from pathlib import Path

import pytest

from computers import __version__
from computers.cli import main

ROOT = Path(__file__).resolve().parents[2]


def _load_check_secrets():
    spec = importlib.util.spec_from_file_location(
        "check_secrets", ROOT / "scripts" / "check_secrets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_secrets = _load_check_secrets()


def test_version_is_set():
    assert __version__


def test_cli_runs(capsys):
    assert main([]) == 0
    assert "computers" in capsys.readouterr().out


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize(
    "text",
    [
        "OPENROUTER_API_KEY = 'sk-or-v1-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4" + "'",
        "aws = AKIA" + "ABCDEFGHIJKLMNOP",
        "x-api-key: '" + "A" * 40 + "'",
        "-----BEGIN " + "PRIVATE KEY-----",
        "token = hf_" + "a" * 34,
    ],
)
def test_secret_patterns_are_caught(text):
    assert check_secrets.scan_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "api_key = os.environ['STARGLASS_API_KEY']",
        "curl -H 'x-api-key: [API_KEY]'",
        "ordinary prose about sky-survey cutouts",
        "sha256: " + "0123456789abcdef" * 4,
    ],
)
def test_clean_text_passes(text):
    assert check_secrets.scan_text(text) == []


def test_scan_file_flags_and_cleans(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("key = 'AKIA" + "ABCDEFGHIJKLMNOP'")
    good = tmp_path / "good.py"
    good.write_text("key = os.environ['X']")
    assert check_secrets.main([str(bad)]) == 1
    assert check_secrets.main([str(good)]) == 0


def test_plan_has_no_secrets():
    assert check_secrets.scan_file(ROOT / "plan.md") == []
