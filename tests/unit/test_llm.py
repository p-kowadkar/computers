"""The LLM client and the shim (spec S8 R2, R8.1). No test here touches the network."""
import json

import pytest

httpx = pytest.importorskip("httpx")

from computers.crew import llm as L

SECRET = "sk-or-test-SECRET-do-not-leak-12345"
MSGS = [{"role": "system", "content": "You are the Skeptic."}, {"role": "user", "content": "PACKET E-001 secret-looking prompt text"}]
FMT = {"type": "json_schema", "json_schema": {"name": "Argument", "strict": True, "schema": {"type": "object"}}}
CFG = L.ModelConfig("vendor/model-x", "prov", 1.0, 2.0)


class Fake:
    """A scripted transport: each call pops the next (status, body) or raises it."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def post(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "payload": json.loads(json.dumps(payload))})
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r


def ok(text="{}", tin=100, tout=20, cost=None):
    usage = {"prompt_tokens": tin, "completion_tokens": tout}
    if cost is not None:
        usage["cost"] = cost
    return 200, {"choices": [{"message": {"content": text}}], "usage": usage}


def gw(tmp_path, transport, *, cap=2.0, env=None, cfg=CFG, run_id="t"):
    return L.Gateway(cfg, cache_dir=tmp_path / "cache", log_path=tmp_path / "log.jsonl", budget=L.RunBudget(cap_usd=cap),
                     transport=transport, env={L.KEY_VAR: SECRET} if env is None else env, run_id=run_id)  # fmt: skip


def log_rows(tmp_path):
    return [json.loads(x) for x in (tmp_path / "log.jsonl").read_text().splitlines()]


def test_the_request_pins_the_provider_turns_fallbacks_off_and_uses_temperature_zero(tmp_path):
    t = Fake(ok())
    gw(tmp_path, t).complete(MSGS, response_format=FMT)
    p = t.calls[0]["payload"]
    assert p["provider"] == {"order": ["prov"], "allow_fallbacks": False} and p["temperature"] == 0
    assert p["model"] == "vendor/model-x" and p["response_format"] == FMT and p["usage"] == {"include": True}
    assert t.calls[0]["headers"]["Authorization"] == f"Bearer {SECRET}"


def test_a_model_without_a_pinned_provider_is_refused():
    with pytest.raises(ValueError, match="provider"):
        L.ModelConfig("vendor/model-x", "", 1.0, 2.0)


def test_a_repeat_is_a_cache_hit_with_no_transport_call_no_cost_and_no_key_needed(tmp_path):
    t = Fake(ok('{"a":1}', cost=0.01))
    first = gw(tmp_path, t).complete(MSGS, response_format=FMT)
    keyless = gw(tmp_path, t, env={})  # a new gateway with no key at all
    again = keyless.complete(MSGS, response_format=FMT)
    assert len(t.calls) == 1 and not first.hit and again.hit and again.text == first.text == '{"a":1}'
    assert first.cost_usd == 0.01 and again.cost_usd == 0.0 and keyless.budget.spent == 0.0
    assert again.cache_key == first.cache_key and [r["hit"] for r in log_rows(tmp_path)] == [False, True]


def test_a_different_request_is_a_different_key_and_a_framework_instance_id_does_not_matter(tmp_path):
    k1 = L.cache_key(CFG, MSGS, FMT)
    assert k1 == L.cache_key(CFG, [dict(m) for m in MSGS], dict(FMT))
    assert k1 != L.cache_key(CFG, MSGS[:1], FMT) and k1 != L.cache_key(CFG, MSGS, None)
    assert k1 != L.cache_key(L.ModelConfig("vendor/model-x", "other", 1.0, 2.0), MSGS, FMT)  # another provider is another entry
    assert k1 != L.cache_key(L.ModelConfig("vendor/model-y", "prov", 1.0, 2.0), MSGS, FMT)


def test_the_cost_is_the_reported_one_or_the_price_table_and_the_log_says_which(tmp_path):
    g = gw(tmp_path, Fake(ok(cost=0.05), ok(tin=1_000_000, tout=500_000, cost=None)))
    a = g.complete([{"role": "user", "content": "a"}])
    b = g.complete([{"role": "user", "content": "b"}])
    assert a.cost_usd == 0.05 and b.cost_usd == pytest.approx(1.0 + 1.0)  # 1M in at $1 plus 0.5M out at $2
    rows = log_rows(tmp_path)
    assert rows[0]["cost_source"] == "reported" and rows[1]["cost_source"] == "estimated"
    assert g.budget.spent == pytest.approx(2.05) and g.budget.calls == 2


@pytest.mark.parametrize(
    "reply",
    [
        (500, {"error": {"message": "upstream melted"}}),
        (429, "slow down"),
        (200, {"unexpected": True}),
        (200, {"choices": [{"message": {"content": ""}}], "usage": {}}),
        ConnectionError("boom"),
    ],
)
def test_a_failed_call_is_logged_not_cached_and_costs_nothing(tmp_path, reply):
    t = Fake(reply)
    g = gw(tmp_path, t)
    with pytest.raises(L.LLMError):
        g.complete(MSGS)
    with pytest.raises(L.LLMError):  # not cached: the second call goes to the transport again
        g.complete(MSGS)
    assert len(t.calls) == 2 and g.budget.spent == 0.0
    assert [r["status"] for r in log_rows(tmp_path)] == ["error", "error"] and not list((tmp_path / "cache").rglob("*.json"))


def test_the_cap_stops_the_next_paid_call_on_both_sides_of_the_boundary(tmp_path):
    t = Fake(ok(cost=0.6))
    g = gw(tmp_path, t, cap=1.0)
    g.complete([{"role": "user", "content": "1"}])  # spent 0.6 of 1.0: allowed
    g.complete([{"role": "user", "content": "2"}])  # spent 1.2: the check before this call saw 0.6 < 1.0, so it ran
    with pytest.raises(L.CapReached, match=r"cap is \$1.00"):
        g.complete([{"role": "user", "content": "3"}])  # 1.2 >= 1.0: refused
    assert len(t.calls) == 2
    g.complete([{"role": "user", "content": "1"}])  # a cache hit is still free at the cap
    last = log_rows(tmp_path)[-2:]
    assert last[0]["status"] == "refused" and last[1]["hit"] is True
    exactly = gw(tmp_path / "e", Fake(ok(cost=1.0)), cap=1.0)
    exactly.complete(MSGS)
    with pytest.raises(L.CapReached):
        exactly.complete([{"role": "user", "content": "other"}])  # spent == cap is refused


def test_the_default_cap_is_the_registry_value():
    assert L.RunBudget().cap_usd == 2.0


def test_a_missing_key_names_the_variable_and_no_value_and_makes_no_call(tmp_path):
    t = Fake(ok())
    g = gw(tmp_path, t, env={})
    with pytest.raises(L.MissingKey) as e:
        g.complete(MSGS)
    assert L.KEY_VAR in str(e.value) and not t.calls and log_rows(tmp_path)[0]["status"] == "refused"


def test_a_schema_request_on_a_model_without_structured_outputs_is_refused(tmp_path):
    cfg = L.ModelConfig("vendor/plain", "prov", 1.0, 2.0, structured_outputs=False)
    with pytest.raises(L.LLMError, match="structured"):
        gw(tmp_path, Fake(ok()), cfg=cfg).complete(MSGS, response_format=FMT)


def test_no_key_and_no_prompt_text_reaches_any_file_even_when_the_error_echoes_the_key(tmp_path):
    t = Fake(ok(), (401, {"error": {"message": f"bad key {SECRET}"}}))
    g = gw(tmp_path, t)
    g.complete(MSGS)
    with pytest.raises(L.LLMError) as e:
        g.complete([{"role": "user", "content": "another secret-looking prompt text"}])
    assert SECRET not in str(e.value)
    for f in tmp_path.rglob("*"):
        if f.is_file() and f.suffix != ".json":
            blob = f.read_text()
            assert SECRET not in blob and "secret-looking" not in blob
    cache_blob = "".join(f.read_text() for f in (tmp_path / "cache").rglob("*.json"))
    assert SECRET not in cache_blob and "secret-looking" not in cache_blob  # the cache holds the reply, not the prompt


def test_the_cache_write_is_atomic_and_leaves_no_temporary_file(tmp_path):
    gw(tmp_path, Fake(ok())).complete(MSGS)
    assert not list((tmp_path / "cache").rglob("*.tmp")) and len(list((tmp_path / "cache").rglob("*.json"))) == 1


# ------------------------------------------------------------------------------- shim


def post(shim, body, path="/chat/completions"):
    return httpx.post(shim.base_url + path, json=body, timeout=10)


def test_the_shim_answers_in_chat_completions_format_and_caches(tmp_path):
    t = Fake(ok('{"side":"artifact","claims":[]}', 50, 7, cost=0.002))
    g = gw(tmp_path, t)
    with L.Shim(g) as shim:
        body = {"model": "whatever", "messages": MSGS, "response_format": FMT, "prompt_cache_key": "uuid-1"}
        r = post(shim, body)
        r2 = post(shim, {**body, "prompt_cache_key": "uuid-2"})  # a per-instance id must not defeat the cache
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == '{"side":"artifact","claims":[]}'
    assert r.json()["usage"] == {"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57}
    assert r2.status_code == 200 and len(t.calls) == 1 and "prompt_cache_key" not in t.calls[0]["payload"]
    assert t.calls[0]["payload"]["provider"]["allow_fallbacks"] is False and t.calls[0]["payload"]["temperature"] == 0


def test_the_shim_turns_a_cap_into_402_and_an_upstream_failure_into_502_and_rejects_other_paths(tmp_path):
    g = gw(tmp_path, Fake(ok(cost=5.0)), cap=1.0)
    with L.Shim(g) as shim:
        assert post(shim, {"messages": MSGS}).status_code == 200
        r = post(shim, {"messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 402 and "cap" in r.json()["error"]["message"]
        assert post(shim, {"nope": 1}).status_code == 400
        assert post(shim, {"messages": MSGS}, path="/embeddings").status_code == 404
    bad = gw(tmp_path / "b", Fake((500, {"error": {"message": "down"}})))
    with L.Shim(bad) as shim:
        r = post(shim, {"messages": MSGS})
    assert r.status_code == 502 and "down" in r.json()["error"]["message"]


def test_the_shim_listens_on_localhost_only(tmp_path):
    with L.Shim(gw(tmp_path, Fake(ok()))) as shim:
        assert shim.base_url.startswith("http://127.0.0.1:")


def test_a_reported_cost_of_zero_with_tokens_used_is_replaced_by_the_price_table(tmp_path):
    # OpenRouter reported 0 for the first live run although 120,000 tokens were used (credits or BYOK), so 0 cannot be trusted for the cap
    g = gw(tmp_path, Fake(ok(tin=1_000_000, tout=1_000_000, cost=0.0)))
    c = g.complete(MSGS)
    assert c.cost_usd == pytest.approx(3.0) and g.budget.spent == pytest.approx(3.0)  # 1M in at $1 plus 1M out at $2
    assert log_rows(tmp_path)[0]["cost_source"] == "estimated"


def test_an_empty_reply_that_used_tokens_is_paid_for_logged_and_not_cached(tmp_path):
    g = gw(tmp_path, Fake((200, {"choices": [{"message": {"content": ""}}], "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000, "cost": 0}})))
    with pytest.raises(L.LLMError, match="empty"):
        g.complete(MSGS)
    assert g.budget.spent == pytest.approx(2.0)  # 1M in at $1 plus 0.5M out at $2: the tokens went on thinking or truncation
    row = log_rows(tmp_path)[0]
    assert row["status"] == "error" and row["cost_usd"] == pytest.approx(2.0) and not list((tmp_path / "cache").rglob("*.json"))


def test_the_request_carries_max_tokens_and_the_reasoning_setting_only_when_configured():
    plain = L.request_for(CFG, MSGS, None)
    assert plain["max_tokens"] == 4000 and "reasoning" not in plain
    cfg = L.ModelConfig("vendor/model-x", "prov", 1.0, 2.0, max_tokens=900, reasoning={"enabled": False})
    q = L.request_for(cfg, MSGS, None)
    assert q["max_tokens"] == 900 and q["reasoning"] == {"enabled": False}
    assert L.cache_key(cfg, MSGS, None) != L.cache_key(CFG, MSGS, None)  # another setting is another cache entry
