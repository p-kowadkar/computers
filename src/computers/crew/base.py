"""The agent machinery: the one module of the crew that imports an agent framework (spec S8 R3.4).

Two bases end in the same `Gateway`, so the agents differ only in machinery:

* `FallbackAgent`: a plain class that renders the prompt, asks the gateway for JSON that matches a
  pydantic model, parses it, and retries once if it does not parse. This is the base the plan names
  as the fallback if NOOA breaks or stalls.
* NOOA: re-exported here (`Agent`, `strategy`, `PredictStrategy`, `PREDICT`) together with
  `nooa_client`, which points NOOA's own HTTP client at the local `Shim`. NOOA never sees the real
  API key: it gets a placeholder, and the shim's gateway holds the real one.

Code as action stays off: every ellipsis method in the crew carries `@strategy(PredictStrategy(...))`,
and a test walks the source to check it.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from computers.crew.llm import Gateway

try:  # the `crew` dependency group; the fast suite runs without it
    from nooa import Agent, PredictStrategy, strategy
    from nooa.config import PredictConfig
    from nooa.unifiedllm import CompletionClient

    NOOA_AVAILABLE = True
    # Two validation retries and temperature 0. NOOA's default is 10 attempts, and each one is a paid call.
    PREDICT = PredictConfig(max_retries=2, temperature=0)
except ImportError:  # pragma: no cover - exercised only when the group is not installed
    NOOA_AVAILABLE = False
    Agent = PredictStrategy = strategy = PredictConfig = CompletionClient = PREDICT = None  # type: ignore[assignment,misc]

__all__ = [
    "NOOA_AVAILABLE",
    "PREDICT",
    "Agent",
    "FallbackAgent",
    "PredictStrategy",
    "nooa_client",
    "response_format_for",
    "strategy",
]

M = TypeVar("M", bound=BaseModel)
PLACEHOLDER_KEY = "local-shim-not-a-real-key"


def response_format_for(model: type[BaseModel]) -> dict[str, Any]:
    """A strict JSON-schema response format, the same shape NOOA sends for a pydantic return type."""
    return {"type": "json_schema", "json_schema": {"name": model.__name__, "strict": True, "schema": model.model_json_schema()}}


class FallbackAgent:
    """A plain base: `ask` returns a validated pydantic object, or raises after one retry."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self.retries_used = 0  # uncached second calls caused by an unreadable first reply

    def _ask_sync(self, system: str, user: str, model: type[M]) -> M:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        fmt = response_format_for(model)
        err = ""
        for attempt in range(2):
            c = self.gateway.complete(messages, response_format=fmt)
            try:
                return model.model_validate(json.loads(c.text))
            except (ValueError, ValidationError) as exc:
                err = str(exc)[:300]
                if attempt == 0:
                    self.retries_used += 1
                    messages = [*messages, {"role": "assistant", "content": c.text},
                                {"role": "user", "content": f"That output could not be used: {err}\nReply with only JSON that matches the schema."}]  # fmt: skip
        raise ValueError(f"the model's reply did not match {model.__name__} after one retry: {err}")

    async def ask(self, system: str, user: str, model: type[M]) -> M:
        return await asyncio.to_thread(self._ask_sync, system, user, model)


def nooa_client(base_url: str, model: str = "openai/shim") -> Any:
    """A NOOA chat client that talks to the local shim. The model name is ignored by the shim, which uses its gateway's model."""
    if not NOOA_AVAILABLE:
        raise ImportError("NOOA is not installed; install the group with `uv sync --group crew`")
    return CompletionClient(model, api_base=base_url, api_key=PLACEHOLDER_KEY)
