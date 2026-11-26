"""A thin client for the DASCH web API (the Starglass service).

What it adds over daschlab's own client, which has no retries and does not check
status codes:

* every request goes through the snapshot store: a stored response is reused without
  touching the network (resume), and every successful response is stored before it is
  parsed;
* retries with exponential backoff and jitter on 429, 5xx, timeouts and connection
  errors, honouring `Retry-After`;
* format checks, so an API change fails loudly instead of corrupting tables;
* a concurrency gate that shrinks after a 429, and a minimum gap between requests;
* the key is read from the environment only and never appears in errors or on disk.

Facts it is built on (docs/decisions/0001-sprint0-findings.md): `querycat` rejects a
3,600 arcsec radius and times out from 3,000; the gateway answers 504 after about 30 s;
responses are capped at 6 MB; the raw columns of `querycat` and `lightcurve` are
snake_case while `queryexps` is camelCase.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, Self

from computers import __version__
from computers.data import dasch_parse as P
from computers.data.credentials import api_mode_from_env, redact
from computers.data.snapshot import Request, SnapshotStore

__all__ = [
    "API_ROOT",
    "ClientStats",
    "DaschClient",
    "DaschError",
    "FormatError",
    "Gate",
    "HttpError",
    "RateLimiter",
    "RequestsTransport",
    "Response",
    "RetriesExhausted",
    "RetryPolicy",
    "Transport",
    "parse_retry_after",
]

API_ROOT = "https://api.starglass.cfa.harvard.edu"
SOURCE = "dasch-dr7"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 300.0  # never sleep longer than this because a server asked


class DaschError(Exception):
    """Base class for client errors. Messages never contain the API key."""


class HttpError(DaschError):
    """A non-retryable HTTP error (a 4xx other than 429)."""

    def __init__(self, status: int, endpoint: str, detail: str):
        super().__init__(f"HTTP {status} from {endpoint}: {detail}")
        self.status = status
        self.endpoint = endpoint


class FormatError(DaschError):
    """A 2xx response is not the shape the parsers need. Nothing is stored."""


class RetriesExhausted(DaschError):
    def __init__(self, endpoint: str, attempts: int, last: str):
        super().__init__(f"{endpoint} failed after {attempts} attempts; last error: {last}")
        self.endpoint = endpoint
        self.attempts = attempts


# --------------------------------------------------------------------- transport


@dataclass(frozen=True)
class Response:
    status: int
    content: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
    ) -> Response: ...


class RequestsTransport:
    """The only code here that imports `requests`. One session per thread."""

    def __init__(self) -> None:
        self._local = threading.local()

    def _session(self):
        import requests

        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def request(self, method, url, *, json_body, headers, timeout) -> Response:
        resp = self._session().request(
            method, url, json=json_body, headers=dict(headers), timeout=timeout,
            allow_redirects=False,
        )  # fmt: skip
        return Response(resp.status_code, resp.content, dict(resp.headers))


# ------------------------------------------------------------------------- policy


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 2.0
    max_delay: float = 60.0
    jitter: float = 0.25  # up to this fraction of the delay is added at random

    def delay(self, attempt: int, retry_after: float | None, rng: random.Random) -> float:
        """Seconds to wait before attempt number `attempt + 1` (attempt counts from 1)."""
        backoff = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        backoff += backoff * self.jitter * rng.random()
        if retry_after is not None:
            return max(backoff, min(retry_after, MAX_RETRY_AFTER))
        return backoff


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a `Retry-After` header. Only the number-of-seconds form is read."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class Gate:
    """A concurrency limit that can shrink while requests are in flight."""

    def __init__(self, limit: int):
        if not 1 <= limit <= 4:
            raise ValueError("concurrency must be between 1 and 4")
        self._cond = threading.Condition()
        self.limit = limit
        self.initial = limit
        self._active = 0
        self.max_active = 0

    def __enter__(self) -> Self:
        with self._cond:
            while self._active >= self.limit:
                self._cond.wait()
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        return self

    def __exit__(self, *exc) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    def lower(self) -> bool:
        """Drop the limit by one, never below 1. Returns True if it changed."""
        with self._cond:
            if self.limit > 1:
                self.limit -= 1
                return True
            return False


class RateLimiter:
    """Keeps request starts at least `min_interval` seconds apart, across threads."""

    def __init__(self, min_interval: float, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.min_interval = min_interval
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.min_interval
        if start > now:
            self._sleep(start - now)


@dataclass
class ClientStats:
    made: int = 0
    from_store: int = 0
    failed: int = 0
    retries_by_status: Counter = field(default_factory=Counter)
    rate_limited: int = 0
    concurrency_lowered: int = 0
    bytes_stored: int = 0
    seconds: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))


# ------------------------------------------------------------------------- client


class DaschClient:
    def __init__(
        self,
        store: SnapshotStore,
        *,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
        policy: RetryPolicy | None = None,
        concurrency: int = 3,
        min_interval: float = 0.2,
        timeouts: tuple[float, float] = (10.0, 60.0),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.transport = transport or RequestsTransport()
        self.policy = policy or RetryPolicy()
        self.api_mode, self._key = api_mode_from_env(os.environ if env is None else env)
        self.base_url = f"{API_ROOT}/{'full' if self._key else 'public'}"
        self.gate = Gate(concurrency)
        self.timeouts = timeouts
        self._limiter = RateLimiter(min_interval, clock, sleep)
        self._clock, self._sleep = clock, sleep
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self.stats = ClientStats()

    # -- the three endpoints --------------------------------------------------

    def querycat(self, ra: float, dec: float, radius_arcsec: float, refcat: str = "apass") -> list[str]:
        body = {
            "refcat": refcat,
            "ra_deg": round(float(ra), 6),
            "dec_deg": round(float(dec), 6),
            "radius_arcsec": float(radius_arcsec),
        }
        return self._call("/dasch/dr7/querycat", body, P.CATALOG_COLUMNS)

    def queryexps(
        self,
        ra: float,
        dec: float,
        midpoint_min: str | None = None,
        midpoint_max: str | None = None,
    ) -> list[str]:
        body: dict[str, Any] = {"ra_deg": round(float(ra), 6), "dec_deg": round(float(dec), 6)}
        if midpoint_min is not None:
            body["midpoint_min"] = midpoint_min
        if midpoint_max is not None:
            body["midpoint_max"] = midpoint_max
        return self._call("/dasch/dr7/queryexps", body, P.EXPOSURE_COLUMNS)

    def lightcurve(self, refcat: str, ref_number: int, gsc_bin_index: int) -> list[str]:
        body = {
            "refcat": refcat,
            "ref_number": int(ref_number),
            "gsc_bin_index": int(gsc_bin_index),
        }
        return self._call("/dasch/dr7/lightcurve", body, P.LIGHTCURVE_COLUMNS)

    # -- machinery ------------------------------------------------------------

    def _safe(self, text: str) -> str:
        return redact(text, self._key)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"computers/{__version__}",
        }
        if self._key:
            headers["x-api-key"] = self._key
        return headers

    @staticmethod
    def _check_format(content: bytes, required: Sequence[str], endpoint: str) -> list[str]:
        try:
            data = json.loads(content)
        except ValueError:
            raise FormatError(f"{endpoint}: response is not JSON") from None
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise FormatError(f"{endpoint}: response is not a JSON list of strings")
        try:
            P.parse_records(data[:1], required)  # checks the header only
        except P.ParseError as exc:
            raise FormatError(f"{endpoint}: {exc}") from None
        return data

    def _count(self, **inc: int) -> None:
        with self._lock:
            for name, n in inc.items():
                setattr(self.stats, name, getattr(self.stats, name) + n)

    def _call(self, endpoint: str, body: dict[str, Any], required: Sequence[str]) -> list[str]:
        request = Request(SOURCE, endpoint, body, method="POST", api_base=self.api_mode)

        stored = self.store.lookup(request)
        if stored is not None:
            data = self._check_format(self.store.read(stored), required, endpoint)
            self._count(from_store=1)
            return data

        url = self.base_url + endpoint
        last = "no attempt made"
        for attempt in range(1, self.policy.max_attempts + 1):
            retry_after: float | None = None
            try:
                with self.gate:
                    self._limiter.wait()
                    started = self._clock()
                    response = self.transport.request(
                        "POST", url, json_body=body, headers=self._headers(), timeout=self.timeouts
                    )
                    elapsed = self._clock() - started
            except OSError as exc:  # connection errors and timeouts
                last = self._safe(f"{type(exc).__name__}: {exc}")
                status_key = "network"
            else:
                status_key = response.status
                if 200 <= response.status < 300:
                    data = self._check_format(response.content, required, endpoint)
                    self.store.put(request, response.content, status=response.status)
                    with self._lock:
                        self.stats.made += 1
                        self.stats.bytes_stored += len(response.content)
                        self.stats.seconds[endpoint].append(elapsed)
                    return data
                detail = self._safe(response.content[:200].decode("utf-8", errors="replace"))
                last = f"HTTP {response.status}: {detail}"
                if response.status not in RETRY_STATUSES:
                    self._count(failed=1)
                    raise HttpError(response.status, endpoint, detail)
                retry_after = parse_retry_after(
                    {k.lower(): v for k, v in response.headers.items()}.get("retry-after")
                )
                if response.status == 429:
                    self._count(rate_limited=1)
                    if self.gate.lower():
                        self._count(concurrency_lowered=1)

            with self._lock:
                self.stats.retries_by_status[status_key] += 1
            if attempt < self.policy.max_attempts:
                self._sleep(self.policy.delay(attempt, retry_after, self._rng))

        self._count(failed=1)
        raise RetriesExhausted(endpoint, self.policy.max_attempts, last)

