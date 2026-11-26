"""Pre-commit check: refuse files that contain strings resembling API keys.

Keys must come from environment variables. This is a tripwire, not a full scanner.
"""

import re
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "OpenRouter/OpenAI-style key": re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9_-]{20,}"),
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "Private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    # Starglass keys are 40 characters of letters and digits. Only flag them when
    # they are assigned to something that looks like an API key.
    "Starglass-style key assignment": re.compile(
        r"""(?ix)(?:api[_-]?key|x-api-key)["']?\s*[:=]\s*["'][A-Za-z0-9]{40}["']"""
    ),
}

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".fits", ".gz", ".parquet", ".pdf", ".npy"}


def scan_text(text: str) -> list[str]:
    """Return the names of every pattern that matches `text`."""
    return [name for name, pattern in PATTERNS.items() if pattern.search(text)]


def scan_file(path: Path) -> list[str]:
    if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return scan_text(text)


def main(argv: list[str]) -> int:
    failed = False
    for name in argv:
        hits = scan_file(Path(name))
        if hits:
            failed = True
            print(f"{name}: possible secret ({', '.join(hits)})", file=sys.stderr)
    if failed:
        print("Remove the secret and read it from an environment variable instead.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
