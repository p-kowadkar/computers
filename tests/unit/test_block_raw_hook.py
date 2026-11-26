import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hooks" / "block_raw_edits.py"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "data" / "raw" / "dasch-dr7" / "ab").mkdir(parents=True)
    (tmp_path / "data" / "spike" / "raw").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    return tmp_path


def run_hook(repo: Path, payload, *, raw: str | None = None, timeout=1.0):
    env = {
        **os.environ,
        "COMPUTERS_ROOT": str(repo),
        "COMPUTERS_HOOK_STDIN_TIMEOUT": str(timeout),
    }
    data = raw if raw is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=data, capture_output=True, text=True, env=env, timeout=30, check=False,
    )  # fmt: skip


def event(tool, tool_input, repo, style="snake"):
    if style == "snake":
        return {"hook_event_name": "PreToolUse", "cwd": str(repo), "tool_name": tool,
                "tool_input": tool_input}  # fmt: skip
    return {"hookEventName": "preToolUse", "cwd": str(repo), "toolName": tool,
            "toolArgs": tool_input}  # fmt: skip


# --------------------------------------------------------------------- blocked


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("fs_write", {"path": "data/raw/dasch-dr7/ab/x.gz", "text": "x"}),
        ("fs_append", {"path": "data/raw/dasch-dr7/ab/x.gz", "text": "x"}),
        ("str_replace", {"path": "data/raw/a.gz", "oldStr": "a", "newStr": "b"}),
        ("delete_file", {"targetFile": "data/raw/dasch-dr7/ab/x.gz", "explanation": "e"}),
        ("delete_file", {"targetFile": "data/raw"}),  # the directory itself
        ("smart_relocate", {"sourcePath": "data/raw/a.gz", "destinationPath": "docs/a.gz"}),
        ("smart_relocate", {"sourcePath": "docs/a.gz", "destinationPath": "data/raw/a.gz"}),
        ("semantic_rename", {"path": "data/raw/a.py", "line": 0, "newName": "b"}),
        ("fs_write", {"path": "src/../data/raw/a.gz", "text": "x"}),  # .. tricks
        ("fs_write", {"path": "./data/./raw/a.gz", "text": "x"}),
        ("fs_write", {"file_path": "data/raw/a.gz", "text": "x"}),  # other key names
        ("fs_write", {"nested": {"filePath": "data/raw/a.gz"}}),
    ],
)
def test_writes_under_data_raw_are_blocked_with_exit_2_and_a_stderr_reason(repo, tool, tool_input):
    result = run_hook(repo, event(tool, tool_input, repo))
    assert result.returncode == 2
    assert "data/raw/" in result.stderr and "immutable" in result.stderr
    assert result.stdout == ""  # stdout never reaches the model


def test_absolute_path_is_blocked(repo):
    target = str(repo / "data" / "raw" / "dasch-dr7" / "ab" / "x.gz")
    assert run_hook(repo, event("fs_write", {"path": target, "text": "x"}, repo)).returncode == 2


def test_camel_case_payload_from_older_builds_is_blocked(repo):
    result = run_hook(repo, event("fs_write", {"targetFile": "data/raw/a.gz"}, repo, style="camel"))
    assert result.returncode == 2


def test_relative_path_is_resolved_against_the_payload_cwd(repo):
    payload = event("fs_write", {"path": "raw/a.gz", "text": "x"}, repo)
    payload["cwd"] = str(repo / "data")
    assert run_hook(repo, payload).returncode == 2


def test_symlink_into_raw_is_followed(repo):
    link = repo / "src" / "shortcut"
    link.symlink_to(repo / "data" / "raw")
    assert run_hook(repo, event("fs_write", {"path": "src/shortcut/a.gz"}, repo)).returncode == 2


def test_blocked_call_is_logged_without_contents(repo):
    run_hook(repo, event("fs_write", {"path": "data/raw/a.gz", "text": "SECRET-BODY"}, repo))
    log = (repo / "data" / "hooks" / "block_raw_edits.log").read_text()
    assert "blocked tool=fs_write" in log and "SECRET-BODY" not in log


# ---------------------------------------------------------------------- allowed


@pytest.mark.parametrize(
    "path",
    [
        "src/computers/x.py",
        "data/spike/raw/x.gz",  # a different folder with "raw" in it
        "data/rawdata.txt",  # shares a prefix, is not inside
        "data/raw_notes/x.md",
        "docs/data/raw/x.md",  # not the repo's data/raw
        "data/manifests/dasch-dr7.jsonl",
        "plan.md",
    ],
)
def test_other_paths_are_allowed(repo, path):
    result = run_hook(repo, event("fs_write", {"path": path, "text": "x"}, repo))
    assert result.returncode == 0 and result.stderr == ""


def test_text_that_mentions_data_raw_is_not_blocked(repo):
    text = "Raw files live in data/raw/ and are immutable. See data/raw/dasch-dr7/."
    result = run_hook(repo, event("fs_write", {"path": "docs/notes.md", "text": text}, repo))
    assert result.returncode == 0


def test_str_replace_text_mentioning_data_raw_is_not_blocked(repo):
    payload = event(
        "str_replace",
        {"path": "plan.md", "oldStr": "data/raw/", "newStr": "data/raw/ (immutable)"},
        repo,
    )
    assert run_hook(repo, payload).returncode == 0


def test_an_unrelated_project_with_the_same_folder_name_is_not_blocked(repo, tmp_path_factory):
    other = tmp_path_factory.mktemp("other") / "data" / "raw"
    other.mkdir(parents=True)
    result = run_hook(repo, event("fs_write", {"path": str(other / "x.gz")}, repo))
    assert result.returncode == 0


# ----------------------------------------------- unreadable input fails open


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not json", "[]", "null", '{"tool_name": "fs_write"}', '{"tool_input": {}}'],
)
def test_unreadable_input_allows_the_call_and_says_why_in_the_log(repo, raw):
    result = run_hook(repo, None, raw=raw, timeout=0.3)
    assert result.returncode == 0
    log = (repo / "data" / "hooks" / "block_raw_edits.log").read_text()
    assert "allowed unchecked" in log


def test_empty_tool_input_is_allowed_and_logged(repo):
    result = run_hook(repo, event("fs_write", {}, repo))
    assert result.returncode == 0
    assert "no tool input for fs_write" in (repo / "data" / "hooks" / "block_raw_edits.log").read_text()


def test_a_tool_with_no_path_is_allowed_silently(repo):
    result = run_hook(repo, event("fs_write", {"text": "no path field"}, repo))
    assert result.returncode == 0 and result.stderr == ""
    assert not (repo / "data" / "hooks").exists()  # nothing worth logging


# --------------------------------------------------------------- stdin handling


def test_stdin_that_never_closes_does_not_hang(repo):
    """Some Kiro builds write the payload but never close the pipe."""
    env = {**os.environ, "COMPUTERS_ROOT": str(repo), "COMPUTERS_HOOK_STDIN_TIMEOUT": "10"}
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )  # fmt: skip
    payload = json.dumps(event("fs_write", {"path": "data/raw/a.gz"}, repo)).encode()
    started = time.monotonic()
    proc.stdin.write(payload)
    proc.stdin.flush()  # deliberately not closed
    try:
        code = proc.wait(timeout=8)
    finally:
        proc.kill()
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()
    assert code == 2
    assert time.monotonic() - started < 8  # returned on the whole JSON value, not on the timeout


def test_payload_arriving_in_pieces_is_assembled(repo):
    env = {**os.environ, "COMPUTERS_ROOT": str(repo), "COMPUTERS_HOOK_STDIN_TIMEOUT": "10"}
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )  # fmt: skip
    payload = json.dumps(event("fs_write", {"path": "data/raw/a.gz", "text": "x" * 5000}, repo)).encode()
    half = len(payload) // 2
    proc.stdin.write(payload[:half])
    proc.stdin.flush()
    time.sleep(0.3)
    proc.stdin.write(payload[half:])
    proc.stdin.close()
    code = proc.wait(timeout=8)
    proc.stdout.close()
    proc.stderr.close()
    assert code == 2


def test_large_payload_is_handled(repo):
    big = "x" * 400_000
    result = run_hook(repo, event("fs_write", {"path": "docs/big.md", "text": big}, repo))
    assert result.returncode == 0
    result = run_hook(repo, event("fs_write", {"path": "data/raw/big.gz", "text": big}, repo))
    assert result.returncode == 2
