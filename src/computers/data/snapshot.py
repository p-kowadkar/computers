"""Immutable snapshot store for raw API responses (plan 4.4, principle 5).

Every successful response is stored exactly as received, named by the hash of its
bytes, and recorded as one line in an append-only manifest. Raw files are never
replaced or edited, and the manifests are small enough to commit, so anyone can
see which requests produced a dataset and re-run them.

Layout under the data directory::

    raw/<source>/<hh>/<sha256>.gz        gzip of the response body, read-only
    manifests/<source>.jsonl             one JSON object per line, append-only

This module stores bytes. It does not retry, page, rate-limit or parse; the client
that makes the requests does that (Task 4).
"""

from __future__ import annotations

import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
import re
import threading
import uuid
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from computers import __version__

__all__ = [
    "ImmutableError",
    "IntegrityError",
    "ManifestError",
    "ManifestRow",
    "NotSuccessError",
    "Request",
    "RequestError",
    "SnapshotError",
    "SnapshotStore",
    "VerifyReport",
    "canonical_json",
]

SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
# Names that suggest a credential. A request body may never contain one (R8).
KEY_LIKE = re.compile(r"key|token|secret|authorization|password|credential", re.IGNORECASE)
API_BASES = ("public", "full")
METHODS = ("GET", "POST")

ROW_FIELDS = (
    "sha256",
    "source",
    "method",
    "endpoint",
    "request",
    "request_key",
    "path",
    "bytes",
    "fetched_at",
    "client_version",
    "api_base",
)


class SnapshotError(Exception):
    """Base class for store errors."""


class RequestError(SnapshotError, ValueError):
    """A request object is malformed or carries something it must not."""


class NotSuccessError(SnapshotError):
    """Only successful responses are stored."""


class ImmutableError(SnapshotError):
    """A raw file already exists with content that does not match its name."""


class IntegrityError(SnapshotError):
    """A raw file no longer matches the hash in its manifest row."""


class ManifestError(SnapshotError, ValueError):
    """A manifest line is not a valid row."""


# ----------------------------------------------------------------------- helpers


def canonical_json(value: Any) -> str:
    """Compact JSON with sorted keys, so equal values always give equal text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _find_key_like(value: Any, path: str = "body") -> str | None:
    if isinstance(value, Mapping):
        for k, v in value.items():
            if KEY_LIKE.search(str(k)):
                return f"{path}.{k}"
            hit = _find_key_like(v, f"{path}.{k}")
            if hit:
                return hit
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            hit = _find_key_like(v, f"{path}[{i}]")
            if hit:
                return hit
    return None


# ----------------------------------------------------------------------- request


@dataclass(frozen=True)
class Request:
    """What was asked, and of whom. There is deliberately no headers field."""

    source: str
    endpoint: str
    body: Mapping[str, Any] | None = None
    method: str = "POST"
    api_base: str = "public"

    def __post_init__(self) -> None:
        if not SOURCE_RE.match(self.source):
            raise RequestError(f"bad source name {self.source!r} (lowercase letters, digits, . _ -)")
        if not self.endpoint.startswith("/"):
            raise RequestError(f"endpoint must start with '/': {self.endpoint!r}")
        if self.method not in METHODS:
            raise RequestError(f"method must be one of {METHODS}, got {self.method!r}")
        if self.api_base not in API_BASES:
            raise RequestError(f"api_base must be one of {API_BASES}, got {self.api_base!r}")
        hit = _find_key_like(self.body)
        if hit:
            raise RequestError(
                f"{hit} looks like a credential; keys never go into a request record"
            )
        try:
            canonical_json(self.body)
        except (TypeError, ValueError) as exc:
            raise RequestError(f"body is not plain JSON: {exc}") from exc

    @property
    def canonical_body(self) -> str:
        return canonical_json(self.body)

    @property
    def key(self) -> str:
        """Identifies the logical request. Excludes `api_base`: both bases return the same data."""
        return _sha256(f"{self.method} {self.endpoint} {self.canonical_body}".encode())


# ------------------------------------------------------------------ manifest row


@dataclass(frozen=True)
class ManifestRow:
    sha256: str
    source: str
    method: str
    endpoint: str
    request: str  # canonical JSON of the body
    request_key: str
    path: str  # relative to the raw directory
    bytes: int
    fetched_at: str
    client_version: str
    api_base: str

    def to_line(self) -> str:
        data = {name: getattr(self, name) for name in ROW_FIELDS}
        return json.dumps(data, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_line(cls, line: str) -> ManifestRow:
        try:
            data = json.loads(line)
        except ValueError as exc:
            raise ManifestError(f"not JSON: {exc}") from exc
        if not isinstance(data, dict) or set(data) != set(ROW_FIELDS):
            raise ManifestError("wrong set of fields")
        for name in ROW_FIELDS:
            want = int if name == "bytes" else str
            value = data[name]
            if not isinstance(value, want) or isinstance(value, bool):
                raise ManifestError(f"{name} must be {want.__name__}")
        row = cls(**{name: data[name] for name in ROW_FIELDS})
        row._check()
        return row

    def _check(self) -> None:
        if not SHA_RE.match(self.sha256) or not SHA_RE.match(self.request_key):
            raise ManifestError("sha256 and request_key must be 64 hex digits")
        if not SOURCE_RE.match(self.source):
            raise ManifestError("bad source name")
        if self.method not in METHODS or self.api_base not in API_BASES:
            raise ManifestError("bad method or api_base")
        if not TIME_RE.match(self.fetched_at):
            raise ManifestError("bad fetched_at")
        if self.bytes < 0:
            raise ManifestError("negative size")
        if self.path != f"{self.source}/{self.sha256[:2]}/{self.sha256}.gz":
            raise ManifestError("path does not match source and hash")
        try:
            json.loads(self.request)
        except ValueError as exc:
            raise ManifestError("request is not JSON") from exc


# ----------------------------------------------------------------------- report


@dataclass
class VerifyReport:
    rows_checked: int = 0
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unreferenced: list[str] = field(default_factory=list)
    stray_temp: list[str] = field(default_factory=list)
    bad_lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.missing or self.mismatched or self.unreferenced or self.stray_temp or self.bad_lines
        )

    def lines(self) -> list[str]:
        out = [f"checked {self.rows_checked} manifest rows"]
        for label, items in (
            ("missing raw file", self.missing),
            ("content does not match hash", self.mismatched),
            ("raw file with no manifest row", self.unreferenced),
            ("stray temporary file", self.stray_temp),
            ("bad manifest line", self.bad_lines),
        ):
            out += [f"{label}: {item}" for item in items]
        return out


@dataclass
class _Index:
    size: int
    mtime_ns: int
    rows: list[ManifestRow]
    by_key: dict[str, list[ManifestRow]]
    bad: list[int]  # 1-based line numbers


# ------------------------------------------------------------------------- store


class SnapshotStore:
    def __init__(
        self,
        data_dir: str | os.PathLike[str],
        *,
        client_version: str | None = None,
        clock: Callable[[], str] = _now_utc,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.manifest_dir = self.data_dir / "manifests"
        self.client_version = client_version or f"computers/{__version__}"
        self._clock = clock
        self._lock = threading.RLock()
        self._index: dict[str, _Index] = {}

    # -- paths ------------------------------------------------------------------

    def _manifest_path(self, source: str) -> Path:
        return self.manifest_dir / f"{source}.jsonl"

    def _raw_path(self, rel: str) -> Path:
        path = (self.raw_dir / rel).resolve()
        if self.raw_dir.resolve() not in path.parents:
            raise ManifestError(f"path escapes the raw directory: {rel!r}")
        return path

    # -- reading the manifest ---------------------------------------------------

    def _load(self, source: str) -> _Index:
        path = self._manifest_path(source)
        try:
            st = path.stat()
        except FileNotFoundError:
            return _Index(0, 0, [], {}, [])
        cached = self._index.get(source)
        if cached and cached.size == st.st_size and cached.mtime_ns == st.st_mtime_ns:
            return cached
        rows: list[ManifestRow] = []
        by_key: dict[str, list[ManifestRow]] = {}
        bad: list[int] = []
        with open(path, "rb") as f:
            for n, raw in enumerate(f, start=1):
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    row = ManifestRow.from_line(text)
                    if row.source != source:
                        raise ManifestError("row source does not match the manifest file")
                except ManifestError:
                    bad.append(n)
                    continue
                rows.append(row)
                by_key.setdefault(row.request_key, []).append(row)
        index = _Index(st.st_size, st.st_mtime_ns, rows, by_key, bad)
        self._index[source] = index
        return index

    def rows(self, source: str) -> list[ManifestRow]:
        with self._lock:
            return list(self._load(source).rows)

    def sources(self) -> list[str]:
        if not self.manifest_dir.exists():
            return []
        return sorted(p.stem for p in self.manifest_dir.glob("*.jsonl"))

    # -- writing ----------------------------------------------------------------

    def put(self, request: Request, body: bytes, *, status: int = 200) -> ManifestRow:
        """Store a successful response and return its manifest row.

        When the newest row for this request already holds the same response, that
        row is returned and nothing is appended. A different response appends a new row.
        """
        if not 200 <= status < 300:
            raise NotSuccessError(f"HTTP {status} is not stored; only 2xx responses are")
        if not isinstance(body, bytes | bytearray):
            raise TypeError("body must be bytes, exactly as received")
        body = bytes(body)
        sha = _sha256(body)
        with self._lock:
            index = self._load(request.source)
            history = index.by_key.get(request.key, [])
            self._write_raw(request.source, sha, body)  # idempotent, heals a missing file
            if history and history[-1].sha256 == sha:
                return history[-1]
            row = ManifestRow(
                sha256=sha,
                source=request.source,
                method=request.method,
                endpoint=request.endpoint,
                request=request.canonical_body,
                request_key=request.key,
                path=f"{request.source}/{sha[:2]}/{sha}.gz",
                bytes=len(body),
                fetched_at=self._clock(),
                client_version=self.client_version,
                api_base=request.api_base,
            )
            self._append(row)
            return row

    def _write_raw(self, source: str, sha: str, body: bytes) -> Path:
        final = self.raw_dir / source / sha[:2] / f"{sha}.gz"
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            self._require_matches(final, sha)
            return final
        data = gzip.compress(body, mtime=0)
        tmp = final.parent / f".tmp-{uuid.uuid4().hex}"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, final)  # fails if `final` exists, so it never replaces anything
            except FileExistsError:
                self._require_matches(final, sha)
                return final
        finally:
            tmp.unlink(missing_ok=True)
        os.chmod(final, 0o444)
        return final

    @staticmethod
    def _decompress_hash(path: Path) -> str | None:
        try:
            with gzip.open(path, "rb") as f:
                return _sha256(f.read())
        except (OSError, EOFError, zlib.error):
            return None

    def _require_matches(self, path: Path, sha: str) -> None:
        if self._decompress_hash(path) != sha:
            raise ImmutableError(
                f"{path} exists but its content does not match its name; refusing to touch it"
            )

    def _append(self, row: ManifestRow) -> None:
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        path = self._manifest_path(row.source)
        line = (row.to_line() + "\n").encode("utf-8")
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            size = os.fstat(fd).st_size
            prefix = b""
            if size and os.pread(fd, 1, size - 1) != b"\n":
                prefix = b"\n"  # a torn last line must not swallow this row
            os.write(fd, prefix + line)
            os.fsync(fd)
            st = os.fstat(fd)
        finally:
            os.close(fd)  # also releases the lock
        cached = self._index.get(row.source)
        if cached and cached.size == size and not prefix:
            cached.rows.append(row)
            cached.by_key.setdefault(row.request_key, []).append(row)
            cached.size, cached.mtime_ns = st.st_size, st.st_mtime_ns
        else:
            self._index.pop(row.source, None)

    # -- reading ----------------------------------------------------------------

    def lookup(self, request: Request) -> ManifestRow | None:
        """Newest row for this request whose raw file exists. No network involved."""
        with self._lock:
            for row in reversed(self._load(request.source).by_key.get(request.key, [])):
                if self._raw_path(row.path).exists():
                    return row
        return None

    def history(self, request: Request) -> list[ManifestRow]:
        with self._lock:
            return list(self._load(request.source).by_key.get(request.key, []))

    def read(self, row: ManifestRow) -> bytes:
        """The original bytes. Raises `IntegrityError` if the file no longer matches."""
        path = self._raw_path(row.path)
        try:
            with gzip.open(path, "rb") as f:
                body = f.read()
        except (OSError, EOFError, zlib.error) as exc:
            raise IntegrityError(f"{path} cannot be read: {exc}") from exc
        if _sha256(body) != row.sha256:
            raise IntegrityError(f"{path} content does not match hash {row.sha256}")
        return body

    # -- whole-store operations --------------------------------------------------

    def verify(self) -> VerifyReport:
        """Re-hash every raw file named in a manifest and look for strays."""
        report = VerifyReport()
        referenced: set[Path] = set()
        checked: dict[Path, str | None] = {}
        with self._lock:
            for source in self.sources():
                index = self._load(source)
                report.bad_lines += [f"{source}.jsonl line {n}" for n in index.bad]
                for row in index.rows:
                    report.rows_checked += 1
                    path = self._raw_path(row.path)
                    referenced.add(path)
                    if not path.exists():
                        report.missing.append(row.path)
                        continue
                    if path not in checked:
                        checked[path] = self._decompress_hash(path)
                    if checked[path] != row.sha256:
                        report.mismatched.append(row.path)
        if self.raw_dir.exists():
            for p in sorted(self.raw_dir.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(self.raw_dir).as_posix()
                if p.name.startswith(".tmp-"):
                    report.stray_temp.append(rel)
                elif p.resolve() not in referenced:
                    report.unreferenced.append(rel)
        report.missing = sorted(set(report.missing))
        report.mismatched = sorted(set(report.mismatched))
        return report

    def digest(self) -> str:
        """One id for the whole set of stored responses (R6.5).

        Covers the newest row of every request in every manifest. It does not depend
        on the order the responses were fetched in.
        """
        lines = []
        with self._lock:
            for source in self.sources():
                for key, rows in self._load(source).by_key.items():
                    lines.append(f"{source}\t{key}\t{rows[-1].sha256}")
        lines.sort()
        return "sha256:" + _sha256("\n".join(lines).encode("utf-8"))

    def status(self) -> dict[str, dict[str, int]]:
        """Per source: manifest rows, distinct requests, and total stored bytes."""
        out: dict[str, dict[str, int]] = {}
        with self._lock:
            for source in self.sources():
                index = self._load(source)
                newest = [rows[-1] for rows in index.by_key.values()]
                out[source] = {
                    "rows": len(index.rows),
                    "requests": len(index.by_key),
                    "bytes": sum(r.bytes for r in newest),
                    "bad_lines": len(index.bad),
                }
        return out
