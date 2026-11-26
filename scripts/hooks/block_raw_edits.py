#!/usr/bin/env python3
"""Kiro PreToolUse hook: stop the agent's file tools from touching data/raw/.

Raw snapshots are immutable (plan 4.4, requirement R5). The snapshot store enforces
that for its own writes. This hook covers the agent's file tools: write, edit,
append, delete, move and rename.

Contract (Kiro IDE, measured by others on 1.1.14, see the spec):
* The payload is JSON on stdin. Newer builds use snake_case (`tool_name`,
  `tool_input`, `cwd`), older ones camelCase (`toolName`, `toolArgs`).
* To block, write the reason to **stderr** and exit **2**. Stdout never reaches the
  model, and exit 1 does not block.

Only the *paths* a tool targets are examined. A file whose text merely mentions
"data/raw/" is never blocked. When the input cannot be read, the hook lets the call
through (exit 0) and logs why, because a hook bug must not stop unrelated work. The
store's own protections and `computers snapshot verify` are the backstop.

Standard library only, so it starts fast.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
from pathlib import Path

PATH_KEYS = frozenset(
    {
        "path",
        "file_path",
        "filePath",
        "file",
        "targetFile",
        "target_file",
        "sourcePath",
        "source_path",
        "destinationPath",
        "destination_path",
    }
)
INPUT_KEYS = ("tool_input", "toolInput", "toolArgs", "tool_args", "input")
TOOL_KEYS = ("tool_name", "toolName")
DEFAULT_STDIN_TIMEOUT = 2.0


def repo_root() -> Path:
    override = os.environ.get("COMPUTERS_ROOT")
    return Path(override) if override else Path(__file__).resolve().parents[2]


def read_payload(fd: int, timeout: float) -> object | None:
    """Read JSON from `fd`, returning as soon as a whole value has arrived.

    Some Kiro builds never close stdin, so waiting for end-of-file could hang.
    """
    deadline = time.monotonic() + timeout
    buf = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            break
        chunk = os.read(fd, 65536)
        if not chunk:  # end of file
            break
        buf += chunk
        try:
            return json.loads(buf)
        except ValueError:
            continue  # not complete yet
    try:
        return json.loads(buf) if buf.strip() else None
    except ValueError:
        return None


def candidate_paths(tool_input: object) -> list[str]:
    """String values stored under a path-like key, at any depth."""
    found: list[str] = []
    if isinstance(tool_input, dict):
        for key, value in tool_input.items():
            if key in PATH_KEYS and isinstance(value, str) and value:
                found.append(value)
            else:
                found.extend(candidate_paths(value))
    elif isinstance(tool_input, list):
        for item in tool_input:
            found.extend(candidate_paths(item))
    return found


def is_under_raw(path: str, cwd: str, root: Path) -> bool:
    raw = Path(os.path.realpath(root / "data" / "raw"))
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p
    resolved = Path(os.path.realpath(p))
    return resolved == raw or raw in resolved.parents


def log_event(root: Path, text: str) -> None:
    """One short line for debugging. Never raises, never records file contents."""
    try:
        folder = root / "data" / "hooks"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        with open(folder / "block_raw_edits.log", "a", encoding="utf-8") as f:
            f.write(f"{stamp} {text}\n")
    except OSError:
        pass


def decide(payload: object, root: Path) -> tuple[int, str]:
    """Return (exit code, stderr message). Pure apart from path resolution."""
    if not isinstance(payload, dict):
        return 0, "unreadable"
    tool = next((payload[k] for k in TOOL_KEYS if k in payload), "?")
    tool_input = next((payload[k] for k in INPUT_KEYS if k in payload), None)
    if not isinstance(tool_input, dict) or not tool_input:
        return 0, f"no tool input for {tool}"
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    for path in candidate_paths(tool_input):
        if is_under_raw(path, cwd, root):
            return 2, (
                f"Blocked: {path} is under data/raw/. Raw snapshots are immutable "
                "(plan 4.4). Add data with SnapshotStore.put(), and never edit, replace "
                "or delete a raw file. To clean up on purpose, do it yourself outside the agent."
            )
    return 0, ""


def main() -> int:
    root = repo_root()
    timeout = float(os.environ.get("COMPUTERS_HOOK_STDIN_TIMEOUT", DEFAULT_STDIN_TIMEOUT))
    payload = read_payload(sys.stdin.fileno(), timeout)
    code, message = decide(payload, root)
    if code == 2:
        tool = payload.get("tool_name", payload.get("toolName", "?")) if isinstance(payload, dict) else "?"
        log_event(root, f"blocked tool={tool}")
        print(message, file=sys.stderr)
        return 2
    if message:  # allowed, but we could not read enough to be sure
        keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        log_event(root, f"allowed unchecked: {message}; payload keys={keys}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
