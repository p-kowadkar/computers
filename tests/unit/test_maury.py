"""Maury on both bases, with a scripted model (spec S8 R3). No network."""
import asyncio
import json

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("httpx")

from test_llm import Fake, gw, ok
from test_packet import CAND, STAR, make

from computers.crew import llm as L
from computers.crew import packet as P
from computers.crew.maury import FallbackMaury, MauryCore
from computers.crew.schemas import Argument

GOOD = json.dumps({"side": "artifact", "claims": [{"text": "plate-edge points", "evidence_ids": ["E-001"]}]})


def packet(tags=None):
    trig, inp = make(tags=tags)
    return P.build_packet(CAND, STAR, trig, inp)


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------- deterministic part


def test_known_issue_hits_are_the_flagged_checks_of_block_9_and_need_no_model():
    import numpy as np

    from computers.l1.tags import Tag

    tags = np.zeros(300, dtype=np.uint64)
    tags[42] |= int(Tag.SHAPE_OUTLIER)
    tags[44] |= int(Tag.PLATE_EDGE)
    hits = MauryCore().known_issue_hits(packet(tags))
    assert {h.check for h in hits} >= {"shape_outlier", "plate_edge"} and all(h.evidence_id.startswith("E-") for h in hits)
    clean = {h.check for h in MauryCore().known_issue_hits(packet())}
    assert "shape_outlier" not in clean and "rows_rejected_by_cleaning" not in clean


def test_no_flagged_check_gives_an_explicit_line_not_an_empty_string():
    from computers.crew.maury import format_hits

    assert "No L1 check is flagged" in format_hits([])


# ------------------------------------------------------------------------- fallback base


def test_the_fallback_returns_a_validated_argument_through_the_gateway(tmp_path):
    t = Fake(ok(GOOD, 800, 60, cost=0.001))
    m = FallbackMaury(gw(tmp_path, t))
    a = run(m.run(packet()))
    assert isinstance(a, Argument) and a.claims[0].evidence_ids == ["E-001"] and m.retries_used == 0
    p = t.calls[0]["payload"]
    assert p["provider"]["allow_fallbacks"] is False and p["response_format"]["json_schema"]["name"] == "Argument"
    sys_msg, user_msg = p["messages"][0]["content"], p["messages"][1]["content"]
    assert sys_msg == MauryCore.SYSTEM and MauryCore.TASK in user_msg and "E-0" in user_msg and "candidate" in sys_msg


def test_the_fallback_retries_once_on_an_unreadable_reply_and_counts_it(tmp_path):
    t = Fake(ok("not json"), ok(GOOD))
    m = FallbackMaury(gw(tmp_path, t))
    assert run(m.run(packet())).side == "artifact" and m.retries_used == 1 and len(t.calls) == 2
    assert t.calls[1]["payload"]["messages"][-1]["content"].startswith("That output could not be used")


def test_the_fallback_gives_up_after_one_retry_and_a_schema_violation_counts_as_unreadable(tmp_path):
    bad_schema = json.dumps({"side": "artifact", "claims": [{"text": "x", "evidence_ids": []}]})
    t = Fake(ok(bad_schema))
    m = FallbackMaury(gw(tmp_path, t))
    with pytest.raises(ValueError, match="after one retry"):
        run(m.run(packet()))
    assert len(t.calls) == 2 and m.retries_used == 1  # one try and one retry, never more


def test_the_cap_reaches_the_fallback_caller_as_a_cap_error(tmp_path):
    t = Fake(ok(GOOD, cost=3.0))
    g = gw(tmp_path, t, cap=1.0)
    FallbackMaury(g)  # constructing does not spend
    run(FallbackMaury(g).run(packet()))
    trig, inp = make(seed=5)
    with pytest.raises(L.CapReached):
        run(FallbackMaury(g).run(P.build_packet(CAND, STAR, trig, inp)))


# ---------------------------------------------------------------------------- NOOA base


def nooa_parts():
    pytest.importorskip("nooa")
    from computers.crew.base import nooa_client
    from computers.crew.maury_nooa import NooaMaury

    return nooa_client, NooaMaury


def run_nooa(g, pk, repeats=1):
    nooa_client, NooaMaury = nooa_parts()

    async def go(base_url):
        client = nooa_client(base_url)
        try:
            return [await NooaMaury(llm=client).run(pk) for _ in range(repeats)]
        finally:
            await client.aclose()

    with L.Shim(g) as shim:
        return asyncio.run(go(shim.base_url))


def test_the_nooa_path_returns_the_same_kind_of_argument_through_the_shim(tmp_path):
    t = Fake(ok(GOOD, 900, 70, cost=0.001))
    [a] = run_nooa(gw(tmp_path, t), packet())
    assert isinstance(a, Argument) and a.claims[0].evidence_ids == ["E-001"] and len(t.calls) == 1
    p = t.calls[0]["payload"]
    assert p["provider"] == {"order": ["prov"], "allow_fallbacks": False} and p["temperature"] == 0
    blob = json.dumps(p["messages"])
    assert MauryCore.SYSTEM[:60] in blob and MauryCore.TASK[:60] in blob and "E-001" in blob
    assert t.calls[0]["headers"]["Authorization"] == f"Bearer {L_SECRET}"  # the real key stays in the gateway, NOOA only had a placeholder


from test_llm import SECRET as L_SECRET


def test_a_second_nooa_agent_instance_with_the_same_packet_is_a_cache_hit(tmp_path):
    t = Fake(ok(GOOD, cost=0.002))
    g = gw(tmp_path, t)
    a = run_nooa(g, packet(), repeats=2)
    assert len(t.calls) == 1 and a[0] == a[1] and g.budget.spent == pytest.approx(0.002)  # a fresh prompt_cache_key per instance did not matter


def test_nooa_stops_after_its_configured_retries_on_an_unreadable_reply_and_the_calls_are_counted(tmp_path):
    from nooa.errors import GenerationError

    t = Fake(ok("not json at all", cost=0.001))
    g = gw(tmp_path, t)
    with pytest.raises(GenerationError):
        run_nooa(g, packet())
    assert len(t.calls) <= 3  # max_retries=2 means at most three attempts, not NOOA's default of ten
    # each retry carries the previous reply, so it is a different request and its own cache entry (a reply that parses as text is cached, valid or not)
    assert len(list((tmp_path / "cache").rglob("*.json"))) == len(t.calls)


def test_the_two_bases_share_the_same_prompts_and_the_same_deterministic_behaviour(tmp_path):
    _, NooaMaury = nooa_parts()
    assert NooaMaury.__doc__ == MauryCore.SYSTEM and NooaMaury.argue_artifact.__doc__ == MauryCore.TASK
    pk = packet()
    assert FallbackMaury(gw(tmp_path, Fake(ok()))).known_issue_hits(pk) == NooaMaury.known_issue_hits(NooaMaury.__new__(NooaMaury), pk)
    assert FallbackMaury.SYSTEM == NooaMaury.SYSTEM and FallbackMaury.TASK == NooaMaury.TASK
    pub = lambda c: {n for n in dir(c) if not n.startswith("_") and callable(getattr(c, n))}
    assert {"run", "argue_artifact", "known_issue_hits", "prompt_text"} <= pub(FallbackMaury) & pub(NooaMaury)
