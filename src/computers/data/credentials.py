"""Where API keys come from, and how to keep them out of text.

Keys live only in environment variables. Nothing here reads a file or writes anything.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["DASCHLAB_ENV", "STARGLASS_ENV", "api_mode_from_env", "redact"]

STARGLASS_ENV = "STARGLASS_API_KEY"
DASCHLAB_ENV = "DASCHLAB_API_KEY"  # what daschlab itself reads; scripts set it for their own process


def api_mode_from_env(env: Mapping[str, str]) -> tuple[str, str | None]:
    """Choose the DASCH API base from the environment.

    Returns `("full", key)` when `STARGLASS_API_KEY` is set and non-blank, otherwise
    `("public", None)`. The caller must never print or store the key.
    """
    key = env.get(STARGLASS_ENV, "").strip()
    if key:
        return "full", key
    return "public", None


def redact(text: str, key: str | None) -> str:
    """Replace every occurrence of the key in `text`. Safe to call with no key."""
    if not key:
        return text
    return text.replace(key, "[redacted]")
