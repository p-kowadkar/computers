"""The one place the crew talks to a model (plan 8.6, tech.md rules 3 and 7, spec S8 R2).

`Gateway.complete` takes messages and returns the text with its token counts and cost. It pins
the provider with fallbacks off, uses temperature 0, caches every successful response on disk
keyed by model, provider and the request, logs one row per call, and refuses to spend past the
run's cap. A cache hit needs no key and costs nothing.

The `Shim` is a tiny OpenAI-compatible endpoint on localhost that forwards to `Gateway`, so a
framework that insists on its own HTTP client (NOOA, through litellm) is routed through the same
cache, price log and cap. The key is read from the environment only, inside the transport, and
never appears in a cache file, a log row, an error message or a hashed request.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Self

from computers.thresholds import THRESHOLDS

__all__ = [
    "DEFAULT_MODEL",
    "CapReached",
    "Completion",
    "Gateway",
    "HttpxTransport",
    "LLMError",
    "MissingKey",
    "ModelConfig",
    "RunBudget",
    "Shim",
    "cache_key",
]

KEY_VAR = "OPENROUTER_API_KEY"
URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(RuntimeError):
    """A call failed. The message carries no key and no prompt text."""


class MissingKey(LLMError):
    pass


class CapReached(LLMError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    model: str
    provider: str  # an OpenRouter provider tag; a model without one is refused, because an unpinned model can be served by anyone
    price_in_per_m: float  # US dollars per million input tokens, used only when a response reports no cost
    price_out_per_m: float
    structured_outputs: bool = True
    max_tokens: int | None = 4000  # the first live run used 1500, and 3 of 12 fallback replies hit it (0010)
    reasoning: Mapping[str, Any] | None = None  # sent as OpenRouter's `reasoning` object when set, for example {"enabled": False}

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError(f"{self.model}: a provider must be pinned, or the same model name can be served by different providers")


DEFAULT_MODEL = ModelConfig("anthropic/claude-haiku-5.5", "anthropic", 0.10, 0.50)  # chosen in the S8 spike (design.md)


@dataclass(frozen=True)
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float  # 0 for a cache hit
    cache_key: str
    hit: bool
    model: str
    provider: str


@dataclass
class RunBudget:
    """What one run may spend. The cap comes from the registry unless a test narrows it."""

    cap_usd: float = field(default_factory=lambda: float(THRESHOLDS["llm_cap_run_usd"].value))
    spent: float = 0.0
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        with self._lock:
            if self.spent >= self.cap_usd:
                raise CapReached(f"the run has spent ${self.spent:.4f} and its cap is ${self.cap_usd:.2f}; no further paid call")

    def add(self, cost: float) -> None:
        with self._lock:
            self.spent += cost
            self.calls += 1


class Transport(Protocol):
    def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> tuple[int, Any]: ...


class HttpxTransport:
    """The real transport: one POST, no retries (a retry would hide a cost)."""

    def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float = 120.0) -> tuple[int, Any]:
        import httpx

        r = httpx.post(url, headers=dict(headers), json=payload, timeout=timeout)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_for(cfg: ModelConfig, messages: list[dict], response_format: dict | None) -> dict[str, Any]:
    """The request that is sent, and the one that is hashed. It never holds the key or a framework's per-instance ids."""
    req: dict[str, Any] = {
        "model": cfg.model, "messages": messages, "temperature": 0,
        "provider": {"order": [cfg.provider], "allow_fallbacks": False},
    }  # fmt: skip
    if response_format is not None:
        req["response_format"] = response_format
    if cfg.max_tokens:
        req["max_tokens"] = cfg.max_tokens
    if cfg.reasoning is not None:
        req["reasoning"] = dict(cfg.reasoning)
    return req


def cache_key(cfg: ModelConfig, messages: list[dict], response_format: dict | None) -> str:
    return hashlib.sha256(_canonical(request_for(cfg, messages, response_format)).encode()).hexdigest()


class Gateway:
    def __init__(
        self,
        cfg: ModelConfig = DEFAULT_MODEL,
        *,
        cache_dir: str | os.PathLike[str] = "data/llm_cache",
        log_path: str | os.PathLike[str] | None = None,
        budget: RunBudget | None = None,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
        run_id: str = "run",
    ) -> None:
        self.cfg = cfg
        self.cache_dir = Path(cache_dir)
        self.log_path = Path(log_path) if log_path else Path("data/llm_log") / f"{run_id}.jsonl"
        self.budget = budget or RunBudget()
        self.transport = transport or HttpxTransport()
        self.env = os.environ if env is None else env
        self.run_id = run_id
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ files
    def _path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _log(self, **row: Any) -> None:
        row = {"ts": round(time.time(), 3), "run_id": self.run_id, "model": self.cfg.model, "provider": self.cfg.provider, **row}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.log_path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def _write_cache(self, key: str, entry: dict) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(entry, f, sort_keys=True)
        os.replace(tmp, path)  # atomic: a reader never sees half a file

    def _redact(self, text: str) -> str:
        key = self.env.get(KEY_VAR, "")
        if key:
            text = text.replace(key, "[key]")
        return text[:200]

    # ------------------------------------------------------------------- call
    def complete(self, messages: list[dict], *, response_format: dict | None = None) -> Completion:
        if response_format is not None and not self.cfg.structured_outputs:
            raise LLMError(f"{self.cfg.model} is configured without structured outputs, but a schema was requested")
        key = cache_key(self.cfg, messages, response_format)
        path = self._path(key)
        if path.is_file():
            entry = json.loads(path.read_text())
            c = Completion(entry["text"], entry["tokens_in"], entry["tokens_out"], 0.0, key, True, self.cfg.model, self.cfg.provider)
            self._log(cache_key=key, hit=True, tokens_in=c.tokens_in, tokens_out=c.tokens_out, cost_usd=0.0, status="ok")
            return c
        try:
            self.budget.check()
        except CapReached:
            self._log(cache_key=key, hit=False, tokens_in=0, tokens_out=0, cost_usd=0.0, status="refused", error="cap reached")
            raise
        api_key = self.env.get(KEY_VAR)
        if not api_key:
            self._log(cache_key=key, hit=False, tokens_in=0, tokens_out=0, cost_usd=0.0, status="refused", error=f"{KEY_VAR} not set")
            raise MissingKey(f"{KEY_VAR} is not set in the environment")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {**request_for(self.cfg, messages, response_format), "usage": {"include": True}}
        try:
            status, body = self.transport.post(URL, headers, payload, 120.0)
        except Exception as exc:  # noqa: BLE001  any transport failure is recorded in the log and re-raised as LLMError
            reason = self._redact(f"{type(exc).__name__}: {exc}")
            self._log(cache_key=key, hit=False, tokens_in=0, tokens_out=0, cost_usd=0.0, status="error", error=reason)
            raise LLMError(f"the request failed: {reason}") from None
        if status != 200 or not isinstance(body, dict):
            detail = body.get("error", {}).get("message", "") if isinstance(body, dict) and isinstance(body.get("error"), dict) else str(body)
            reason = self._redact(f"HTTP {status}: {detail}")
            self._log(cache_key=key, hit=False, tokens_in=0, tokens_out=0, cost_usd=0.0, status="error", error=reason)
            raise LLMError(reason)
        try:
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage") or {}
            t_in, t_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError):
            self._log(cache_key=key, hit=False, tokens_in=0, tokens_out=0, cost_usd=0.0, status="error", error="unreadable reply")
            raise LLMError("the reply could not be read (no choices[0].message.content)") from None
        reported = usage.get("cost")
        estimate = (t_in * self.cfg.price_in_per_m + t_out * self.cfg.price_out_per_m) / 1e6
        # A reported cost of 0 with tokens used is not a free call (credits and BYOK can report 0), so the price table decides.
        if isinstance(reported, int | float) and reported > 0:
            cost, source = float(reported), "reported"
        else:
            cost, source = estimate, "estimated"
        if not isinstance(text, str) or not text:
            self.budget.add(cost)  # the tokens were used, so the call was paid for even though nothing usable came back
            self._log(cache_key=key, hit=False, tokens_in=t_in, tokens_out=t_out, cost_usd=cost, cost_source=source, status="error", error="empty reply")
            raise LLMError("the reply was empty")
        self.budget.add(cost)
        self._write_cache(key, {"text": text, "tokens_in": t_in, "tokens_out": t_out, "cost_usd": cost, "cost_source": source,
                                "model": self.cfg.model, "provider": self.cfg.provider, "created": round(time.time(), 3)})  # fmt: skip
        self._log(cache_key=key, hit=False, tokens_in=t_in, tokens_out=t_out, cost_usd=cost, cost_source=source, status="ok")
        return Completion(text, t_in, t_out, cost, key, False, self.cfg.model, self.cfg.provider)


# --------------------------------------------------------------------------- shim


class Shim:
    """An OpenAI-compatible endpoint on 127.0.0.1 that answers from a `Gateway`. Use as a context manager."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        shim = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # the default writes request lines to stderr
                pass

            def _send(self, status: int, obj: dict) -> None:
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._send(404, {"error": {"message": "only /chat/completions is served"}})
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
                    messages, fmt = body["messages"], body.get("response_format")
                except (ValueError, KeyError):
                    return self._send(400, {"error": {"message": "bad request"}})
                try:
                    c = shim.gateway.complete(messages, response_format=fmt)
                except CapReached as exc:
                    return self._send(402, {"error": {"message": str(exc), "type": "cap_reached"}})
                except LLMError as exc:
                    return self._send(502, {"error": {"message": str(exc), "type": "upstream"}})
                self._send(200, {
                    "id": f"shim-{c.cache_key[:12]}", "object": "chat.completion", "created": int(time.time()), "model": c.model,
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": c.text}}],
                    "usage": {"prompt_tokens": c.tokens_in, "completion_tokens": c.tokens_out, "total_tokens": c.tokens_in + c.tokens_out},
                })  # fmt: skip

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def __enter__(self) -> Self:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
