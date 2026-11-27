"""Replay of the first live run of the crew spike from its recorded responses (spec S8 R6.3).

The packets and the model's replies are committed in `tests/golden/llm/`. This test builds the same
requests, finds every answer in the cache, and checks the arguments are the recorded ones, with
no key and no network. One fallback reply was empty in the live run (it hit `max_tokens`), and an
error is never cached, so that one packet fails on replay too.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("httpx")
pytest.importorskip("nooa")

from computers.crew import llm as L
from computers.crew import spike as S

FX = Path(__file__).parent / "llm"
RUN = json.loads((FX / "run.json").read_text())
EXPECTED = json.loads((FX / "expected.json").read_text())


class Boom:
    def post(self, *a, **k):
        raise AssertionError("the network was touched during a replay")


def replay(tmp_path):
    packets = S.load_packets(FX / "packets")
    cfg = L.ModelConfig(**RUN["model"])

    def gateway_for(path):
        return L.Gateway(cfg, cache_dir=FX, log_path=tmp_path / f"{path}.jsonl", transport=Boom(), env={}, run_id=path)

    return packets, S.run_paths(packets, gateway_for)


def test_the_recorded_run_replays_to_the_same_arguments_at_zero_cost(tmp_path):
    packets, report = replay(tmp_path)
    assert len(packets) == 11  # 10 counted packets and the T CrB reference
    for path, p in report["paths"].items():
        for row in p["rows"]:
            want = EXPECTED[path][row["candidate_id"]]
            if want["failure"] is None:
                assert row["valid"] and row["argument"] == want["argument"], (path, row["candidate_id"])
            else:
                assert not row["valid"]  # a recorded failure stays a failure: errors are never cached
        assert p["cost_usd"] == 0.0 and p["cache_hits"] > 0


def test_the_live_result_is_what_the_record_says_ten_of_ten_on_nooa_and_nine_on_the_fallback(tmp_path):
    _, report = replay(tmp_path)
    live = RUN["result"]
    ok = {p: sum(v["valid"] for v in EXPECTED[p].values()) for p in EXPECTED}
    assert (live["nooa"]["valid"], live["nooa"]["of"]) == (10, 10) and (live["fallback"]["valid"], live["fallback"]["of"]) == (9, 10)
    assert ok["nooa"] == 11 and ok["fallback"] == 10  # with the reference, which passed on both
    assert {p: sum(r["valid"] for r in report["paths"][p]["rows"]) for p in report["paths"]} == ok


def test_every_recorded_claim_cites_ids_that_exist_in_its_packet():
    packets = {p.candidate_id: p for p in S.load_packets(FX / "packets")}
    for path, rows in EXPECTED.items():
        for cid, v in rows.items():
            if v["failure"]:
                continue
            assert v["argument"]["claims"], (path, cid)
            for claim in v["argument"]["claims"]:
                assert claim["evidence_ids"] and set(claim["evidence_ids"]) <= packets[cid].ids(), (path, cid)


def test_no_key_appears_in_any_committed_fixture():
    blob = "".join(f.read_text() for f in FX.rglob("*.json"))
    assert "sk-or-" not in blob and "Bearer" not in blob and "OPENROUTER" not in blob
