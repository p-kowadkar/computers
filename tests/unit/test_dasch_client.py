import json
import random
import threading
import time

import pytest
import requests

from computers.data import dasch_client as C
from computers.data import dasch_parse as P
from computers.data.dasch_client import (
    DaschClient,
    FormatError,
    HttpError,
    Response,
    RetriesExhausted,
    RetryPolicy,
)
from computers.data.snapshot import SnapshotStore

KEY = "K" * 40


# ------------------------------------------------------------------- test doubles


def body_for(columns, n_rows=1) -> bytes:
    lines = [",".join(columns)] + [",".join("1" for _ in columns)] * n_rows
    return json.dumps(lines).encode()


CATALOG_OK = body_for(P.CATALOG_COLUMNS)
EXPOSURES_OK = body_for(P.EXPOSURE_COLUMNS)
LIGHTCURVE_OK = body_for(P.LIGHTCURVE_COLUMNS)


def ok(content=CATALOG_OK, status=200, headers=None):
    return Response(status, content, headers or {})


class FakeTransport:
    """Plays back a script. Items are Responses, exceptions to raise, or callables."""

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        self.default = default
        self.calls = []
        self._lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0
        self.hold = 0.0

    def request(self, method, url, *, json_body, headers, timeout):
        with self._lock:
            self.calls.append({"method": method, "url": url, "body": json_body,
                               "headers": dict(headers), "timeout": timeout})  # fmt: skip
            item = self.script.pop(0) if self.script else self.default
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.hold:
                time.sleep(self.hold)
            if callable(item):
                item = item()
            if isinstance(item, Exception):
                raise item
            if item is None:
                raise AssertionError("fake transport ran out of responses")
            return item
        finally:
            with self._lock:
                self.inflight -= 1


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path / "data")


def make(store, script=None, *, env=None, default=None, **kw):
    transport = FakeTransport(script, default)
    ft = FakeTime()
    kw.setdefault("min_interval", 0.0)
    client = DaschClient(
        store, transport=transport, env=env or {}, clock=ft.clock, sleep=ft.sleep,
        rng=random.Random(1), **kw,
    )  # fmt: skip
    return client, transport, ft


# --------------------------------------------------------------------- endpoints


def test_querycat_request_body_and_url(store):
    client, t, _ = make(store, [ok()])
    lines = client.querycat(239.87568123456789, 25.920171234567, 1850)
    assert lines[0].split(",") == list(P.CATALOG_COLUMNS)
    (call,) = t.calls
    assert call["url"] == "https://api.starglass.cfa.harvard.edu/public/dasch/dr7/querycat"
    assert call["method"] == "POST"
    assert call["body"] == {"refcat": "apass", "ra_deg": 239.875681, "dec_deg": 25.920171,
                            "radius_arcsec": 1850.0}  # fmt: skip
    assert call["timeout"] == (10.0, 60.0)


def test_queryexps_body_with_and_without_a_date_range(store):
    client, t, _ = make(store, [ok(EXPOSURES_OK), ok(EXPOSURES_OK)])
    client.queryexps(130.1, 19.7)
    client.queryexps(130.1, 19.7, "1900-01-01T00:00:00Z", "1950-01-01T00:00:00Z")
    assert t.calls[0]["body"] == {"ra_deg": 130.1, "dec_deg": 19.7}
    assert t.calls[1]["body"]["midpoint_min"] == "1900-01-01T00:00:00Z"
    assert t.calls[1]["body"]["midpoint_max"] == "1950-01-01T00:00:00Z"
    assert t.calls[1]["url"].endswith("/dasch/dr7/queryexps")


def test_lightcurve_body_uses_plain_ints(store):
    client, t, _ = make(store, [ok(LIGHTCURVE_OK)])
    client.lightcurve("apass", 415593021255513, 121096954)
    assert t.calls[0]["body"] == {"refcat": "apass", "ref_number": 415593021255513,
                                  "gsc_bin_index": 121096954}  # fmt: skip
    assert all(type(v) in (str, int) for v in t.calls[0]["body"].values())


# ------------------------------------------------------------------ store and resume


def test_a_successful_response_is_stored_exactly_as_received_before_returning(store):
    client, _, _ = make(store, [ok()])
    client.querycat(1.0, 2.0, 1850)
    (row,) = store.rows("dasch-dr7")
    assert store.read(row) == CATALOG_OK
    assert row.endpoint == "/dasch/dr7/querycat" and row.api_base == "public"
    assert client.stats.made == 1 and client.stats.bytes_stored == len(CATALOG_OK)


def test_a_stored_request_is_not_sent_again(store):
    client, t, _ = make(store, [ok()])
    first = client.querycat(1.0, 2.0, 1850)
    second = client.querycat(1.0, 2.0, 1850)
    assert first == second and len(t.calls) == 1
    assert client.stats.from_store == 1 and client.stats.made == 1


def test_resume_works_across_client_instances(store):
    make(store, [ok()])[0].querycat(1.0, 2.0, 1850)
    client2, t2, _ = make(store, [])  # a new client with an empty script
    client2.querycat(1.0, 2.0, 1850)
    assert t2.calls == []


def test_different_requests_are_both_sent(store):
    client, t, _ = make(store, [ok(), ok()])
    client.querycat(1.0, 2.0, 1850)
    client.querycat(1.0, 2.5, 1850)
    assert len(t.calls) == 2 and len(store.rows("dasch-dr7")) == 2


def test_a_stored_response_that_no_longer_matches_its_hash_is_refused(store):
    client, _, _ = make(store, [ok()])
    client.querycat(1.0, 2.0, 1850)
    (row,) = store.rows("dasch-dr7")
    (store.raw_dir / row.path).chmod(0o644)
    # A stored response that no longer matches its hash is an integrity error, not data.
    import gzip

    (store.raw_dir / row.path).write_bytes(gzip.compress(b"[]", mtime=0))
    with pytest.raises(Exception, match="does not match"):
        client.querycat(1.0, 2.0, 1850)


# ----------------------------------------------------------------------- retries


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_statuses_are_retried_then_succeed(store, status):
    client, t, ft = make(store, [Response(status, b"busy"), Response(status, b"busy"), ok()])
    client.querycat(1.0, 2.0, 1850)
    assert len(t.calls) == 3 and len(ft.sleeps) == 2
    assert client.stats.retries_by_status[status] == 2 and client.stats.made == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 422])
def test_other_client_errors_are_not_retried(store, status):
    client, t, ft = make(store, [Response(status, b"illegal radius_arcsec parameter")])
    with pytest.raises(HttpError, match=f"HTTP {status}.*illegal radius") as exc:
        client.querycat(1.0, 2.0, 3600)
    assert exc.value.status == status and len(t.calls) == 1 and ft.sleeps == []
    assert store.rows("dasch-dr7") == [] and client.stats.failed == 1


def test_gives_up_after_five_attempts_with_a_clear_message(store):
    client, t, ft = make(store, default=Response(504, b'{"message": "Endpoint request timed out"}'))
    with pytest.raises(RetriesExhausted, match=r"querycat failed after 5 attempts.*HTTP 504") as exc:
        client.querycat(1.0, 2.0, 3000)
    assert exc.value.attempts == 5 and len(t.calls) == 5
    assert len(ft.sleeps) == 4  # no sleep after the last attempt
    assert store.rows("dasch-dr7") == [] and client.stats.failed == 1


@pytest.mark.parametrize(
    "error",
    [requests.ConnectionError("no route"), requests.Timeout("slow"), TimeoutError("t"), OSError("x")],
)
def test_connection_errors_and_timeouts_are_retried(store, error):
    client, t, _ = make(store, [error, ok()])
    client.querycat(1.0, 2.0, 1850)
    assert len(t.calls) == 2 and client.stats.retries_by_status["network"] == 1


def test_backoff_doubles_within_the_jitter_bounds_and_is_capped(store):
    policy = RetryPolicy(max_attempts=7, base_delay=2.0, max_delay=10.0, jitter=0.25)
    client, _, ft = make(store, default=Response(503, b""), policy=policy)
    with pytest.raises(RetriesExhausted):
        client.querycat(1.0, 2.0, 1850)
    base = [2.0, 4.0, 8.0, 10.0, 10.0, 10.0]
    assert len(ft.sleeps) == 6
    for slept, b in zip(ft.sleeps, base, strict=True):
        assert b <= slept <= b * 1.25


def test_retry_after_is_honoured_when_longer_than_the_backoff(store):
    client, _, ft = make(store, [Response(429, b"", {"Retry-After": "30"}), ok()])
    client.querycat(1.0, 2.0, 1850)
    assert ft.sleeps[0] >= 30.0


def test_retry_after_does_not_shorten_the_backoff_and_is_capped(store):
    client, _, ft = make(store, [Response(503, b"", {"retry-after": "0"}), ok()])
    client.querycat(1.0, 2.0, 1850)
    assert ft.sleeps[0] >= 2.0
    client2, _, ft2 = make(store, [Response(503, b"", {"Retry-After": "99999"}), ok()])
    client2.querycat(1.0, 2.5, 1850)
    assert ft2.sleeps[0] == C.MAX_RETRY_AFTER


@pytest.mark.parametrize("value", [None, "", "soon", "-5", "Wed, 21 Oct 2026 07:28:00 GMT"])
def test_unreadable_retry_after_is_ignored(value):
    assert C.parse_retry_after(value) is None


def test_retry_after_seconds_are_read():
    assert C.parse_retry_after("12") == 12.0 and C.parse_retry_after(" 1.5 ") == 1.5


def test_policy_delay_is_deterministic_for_a_seed():
    p = RetryPolicy()
    assert p.delay(1, None, random.Random(7)) == p.delay(1, None, random.Random(7))


# ------------------------------------------------------------------ format checks


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not json", "not JSON"),
        (b'{"error": "nope"}', "not a JSON list of strings"),
        (b"[1, 2, 3]", "not a JSON list of strings"),
        (b"[]", "empty response"),
        (json.dumps(["a,b", "1,2"]).encode(), "missing column"),
    ],
)
def test_format_drift_raises_and_stores_nothing(store, content, message):
    client, t, _ = make(store, [ok(content)])
    with pytest.raises(FormatError, match=message):
        client.querycat(1.0, 2.0, 1850)
    assert store.rows("dasch-dr7") == [] and len(t.calls) == 1  # not retried, not stored


def test_the_missing_columns_are_named(store):
    header = [c for c in P.CATALOG_COLUMNS if c not in ("stdmag", "num_matches")]
    client, _, _ = make(store, [ok(body_for(header))])
    with pytest.raises(FormatError, match="stdmag, num_matches"):
        client.querycat(1.0, 2.0, 1850)


def test_extra_columns_are_fine(store):
    client, _, _ = make(store, [ok(body_for([*P.CATALOG_COLUMNS, "brand_new_column"]))])
    assert client.querycat(1.0, 2.0, 1850)


# --------------------------------------------------------------------------- key


def test_no_key_means_the_public_api_and_no_key_header(store):
    client, t, _ = make(store, [ok()])
    client.querycat(1.0, 2.0, 1850)
    assert client.api_mode == "public" and "/public/" in t.calls[0]["url"]
    assert "x-api-key" not in t.calls[0]["headers"]


def test_a_key_selects_the_full_api_and_is_sent_as_a_header(store):
    client, t, _ = make(store, [ok()], env={"STARGLASS_API_KEY": KEY})
    client.querycat(1.0, 2.0, 1850)
    assert client.api_mode == "full" and "/full/" in t.calls[0]["url"]
    assert t.calls[0]["headers"]["x-api-key"] == KEY
    assert t.calls[0]["body"].get("api_key") is None  # never in the body


def test_the_key_never_appears_in_errors(store):
    echo = Response(401, f'{{"error": "bad key {KEY}"}}'.encode())
    client, _, _ = make(store, [echo], env={"STARGLASS_API_KEY": KEY})
    with pytest.raises(HttpError) as exc:
        client.querycat(1.0, 2.0, 1850)
    assert KEY not in str(exc.value) and "[redacted]" in str(exc.value)


def test_the_key_never_appears_in_retry_errors_or_on_disk(store, tmp_path):
    client, _, _ = make(
        store, default=Response(500, f"oops {KEY}".encode()), env={"STARGLASS_API_KEY": KEY}
    )
    with pytest.raises(RetriesExhausted) as exc:
        client.querycat(1.0, 2.0, 1850)
    assert KEY not in str(exc.value)
    client2, _, _ = make(store, [ok()], env={"STARGLASS_API_KEY": KEY})
    client2.querycat(1.0, 2.0, 1850)
    for p in (tmp_path / "data").rglob("*"):
        if p.is_file():
            assert KEY.encode() not in p.read_bytes()
    assert KEY not in repr(vars(client2.stats))
    (row,) = store.rows("dasch-dr7")
    assert row.api_base == "full"


def test_a_connection_error_message_with_the_key_is_redacted(store):
    client, _, _ = make(
        store, default=requests.ConnectionError(f"failed for header {KEY}"),
        env={"STARGLASS_API_KEY": KEY},
    )  # fmt: skip
    with pytest.raises(RetriesExhausted) as exc:
        client.querycat(1.0, 2.0, 1850)
    assert KEY not in str(exc.value)


# ----------------------------------------------------------------- concurrency


def test_a_429_lowers_the_concurrency_by_one_down_to_a_floor_of_one(store):
    client, _, _ = make(store, [Response(429, b""), ok()], concurrency=3)
    client.querycat(1.0, 2.0, 1850)
    assert client.gate.limit == 2 and client.stats.concurrency_lowered == 1
    assert client.stats.rate_limited == 1
    client.transport.script = [Response(429, b""), ok(), Response(429, b""), ok()]
    client.querycat(1.0, 3.0, 1850)
    client.querycat(1.0, 4.0, 1850)
    assert client.gate.limit == 1
    client.transport.script = [Response(429, b""), ok()]
    client.querycat(1.0, 5.0, 1850)
    assert client.gate.limit == 1 and client.stats.concurrency_lowered == 2
    assert client.stats.rate_limited == 4


@pytest.mark.parametrize("bad", [0, 5, -1])
def test_concurrency_must_be_between_one_and_four(store, bad):
    with pytest.raises(ValueError):
        make(store, concurrency=bad)


def test_the_gate_limits_requests_in_flight(store):
    client, t, _ = make(store, default=ok(), concurrency=2)
    t.hold = 0.03
    threads = [
        threading.Thread(target=client.querycat, args=(1.0, float(i), 1850)) for i in range(10)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(t.calls) == 10
    assert t.max_inflight <= 2 and client.gate.max_active <= 2
    assert t.max_inflight == 2  # and it does use the allowance


def test_the_gate_lowering_takes_effect_for_waiting_threads():
    gate = C.Gate(3)
    assert gate.lower() and gate.lower() and not gate.lower()
    assert gate.limit == 1


def test_minimum_gap_between_request_starts(store):
    ft = FakeTime()
    t = FakeTransport(default=ok())
    client = DaschClient(
        store, transport=t, env={}, clock=ft.clock, sleep=ft.sleep, min_interval=0.2,
        rng=random.Random(0),
    )  # fmt: skip
    for i in range(4):
        client.querycat(1.0, float(i), 1850)
    assert len(ft.sleeps) == 3 and all(s == pytest.approx(0.2) for s in ft.sleeps)


def test_other_requests_continue_when_one_fails(store):
    script = [Response(404, b"missing"), ok(), ok()]
    client, _, _ = make(store, script)
    results = []
    for i in range(3):
        try:
            results.append(client.querycat(1.0, float(i), 1850))
        except HttpError as exc:
            results.append(exc)
    assert isinstance(results[0], HttpError) and isinstance(results[1], list)
    assert client.stats.made == 2 and client.stats.failed == 1


# -------------------------------------------------------------------------- stats


def test_timings_are_recorded_per_endpoint_for_made_requests_only(store):
    client, _, ft = make(store, [ok(), ok(LIGHTCURVE_OK)])
    client.querycat(1.0, 2.0, 1850)
    client.querycat(1.0, 2.0, 1850)  # from the store: no timing
    client.lightcurve("apass", 1, 2)
    assert len(client.stats.seconds["/dasch/dr7/querycat"]) == 1
    assert len(client.stats.seconds["/dasch/dr7/lightcurve"]) == 1
    assert ft.sleeps == []
