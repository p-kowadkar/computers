"""The Sprint 8 exit run, on saved packets and a scripted model (spec S8 R6). No network."""
import json

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("httpx")
pytest.importorskip("nooa")

from test_llm import Fake, ok
from test_packet import CAND, STAR, make

from computers.crew import llm as L
from computers.crew import packet as P
from computers.crew import spike as S

CFG = L.ModelConfig("vendor/model-x", "prov", 1.0, 2.0)
GOOD = json.dumps({"side": "artifact", "claims": [{"text": "points sit on plate edges", "evidence_ids": ["E-001"]}]})
MIXED = json.dumps({"side": "artifact", "claims": [{"text": "kept", "evidence_ids": ["E-001"]}, {"text": "invented", "evidence_ids": ["E-777"]}]})
ALL_BAD = json.dumps({"side": "artifact", "claims": [{"text": "invented", "evidence_ids": ["E-777"]}]})


def packets(n=3):
    out = []
    for i in range(n):
        trig, inp = make(seed=i)
        out.append(P.build_packet({**CAND, "candidate_id": f"cand{i}"}, STAR, trig, inp))
    return out


def factory(tmp_path, transport, *, cap=2.0, key=True):
    def gateway_for(path):
        return L.Gateway(CFG, cache_dir=tmp_path / "cache", log_path=tmp_path / f"{path}.jsonl", budget=L.RunBudget(cap_usd=cap),
                         transport=transport, env={L.KEY_VAR: "sk-test"} if key else {}, run_id=path)  # fmt: skip

    return gateway_for


def test_both_paths_count_valid_replies_and_report_tokens_and_cost(tmp_path):
    pk = packets(3)
    r = S.run_paths(pk, factory(tmp_path, Fake(ok(GOOD, 800, 60, cost=0.001))))
    for name in ("nooa", "fallback"):
        p = r["paths"][name]
        assert p["valid"] == 3 and p["of"] == 3 and p["needed_stripping"] == 0 and p["failures"] == []
        assert p["calls"] >= 3 and p["tokens_in"] >= 2400 and 0 < p["cost_usd"] <= p["cap_usd"]


def test_a_reply_that_needed_the_validator_is_counted_and_is_valid_only_if_something_remains(tmp_path):
    pk = packets(2)
    r = S.run_paths(pk, factory(tmp_path, Fake(ok(MIXED))), paths=("fallback",))
    p = r["paths"]["fallback"]
    assert p["valid"] == 2 and p["needed_stripping"] == 2 and p["claims_removed"] == 2
    assert p["rows"][0]["claims_removed"][0]["reason"] == "unknown_evidence_id: E-777" and p["rows"][0]["claims_kept"] == 1
    r2 = S.run_paths(pk, factory(tmp_path / "b", Fake(ok(ALL_BAD))), paths=("fallback",))
    q = r2["paths"]["fallback"]
    assert q["valid"] == 0 and q["needed_stripping"] == 2  # everything removed: counted, and not valid


def test_an_unreadable_reply_is_a_recorded_failure_and_does_not_stop_the_other_packets(tmp_path):
    t = Fake(ok("not json"), ok("still not json"), ok(GOOD))
    r = S.run_paths(packets(2), factory(tmp_path, t), paths=("fallback",))
    p = r["paths"]["fallback"]
    assert p["valid"] == 1 and p["of"] == 2 and p["fallback_retries"] >= 1
    assert len(p["failures"]) == 1 and "after one retry" in p["failures"][0]["failure"]


def test_the_cap_ends_the_spend_and_is_reported_as_failures_not_a_crash(tmp_path):
    r = S.run_paths(packets(4), factory(tmp_path, Fake(ok(GOOD, cost=1.5)), cap=1.0), paths=("fallback",))
    p = r["paths"]["fallback"]
    assert p["valid"] == 1 and len(p["failures"]) == 3 and all("CapReached" in f["failure"] for f in p["failures"])
    assert p["cost_usd"] == 1.5


def test_a_second_run_from_the_cache_gives_identical_arguments_at_zero_cost_with_no_key_and_no_network(tmp_path):
    pk = packets(3)
    first = S.run_paths(pk, factory(tmp_path / "one", Fake(ok(GOOD, 700, 50, cost=0.002))))
    # new logs, the same cache, a transport that fails if touched, and no key
    class Boom:
        def post(self, *a, **k):
            raise AssertionError("the network was touched on a cached run")

    def again(path):
        return L.Gateway(CFG, cache_dir=tmp_path / "one" / "cache", log_path=tmp_path / "two" / f"{path}.jsonl", transport=Boom(), env={}, run_id=path)

    second = S.run_paths(pk, again)
    for name in ("nooa", "fallback"):
        assert [r["argument"] for r in second["paths"][name]["rows"]] == [r["argument"] for r in first["paths"][name]["rows"]]
        assert second["paths"][name]["cost_usd"] == 0.0 and second["paths"][name]["cache_hits"] == second["paths"][name]["calls"] > 0


def test_packets_round_trip_through_files_so_the_fixtures_rebuild_the_same_request(tmp_path):
    pk = packets(2)
    S.save_packets(pk, tmp_path / "p")
    back = S.load_packets(tmp_path / "p")
    assert sorted(b.candidate_id for b in back) == sorted(p.candidate_id for p in pk)
    by_id = {b.candidate_id: b for b in back}
    for p in pk:
        assert P.render(by_id[p.candidate_id]) == P.render(p)


def test_fixtures_copy_exactly_the_cache_entries_a_run_used(tmp_path):
    r = factory(tmp_path / "run", Fake(ok(GOOD, cost=0.001)))
    S.run_paths(packets(2), r, paths=("fallback",))
    other = tmp_path / "run" / "cache" / "zz"
    other.mkdir(parents=True)
    (other / ("zz" + "0" * 62 + ".json")).write_text("{}")  # an entry from some other run
    n = S.write_fixtures(tmp_path / "run" / "cache", [tmp_path / "run" / "fallback.jsonl"], tmp_path / "fx")
    assert n == 2 and len(list((tmp_path / "fx").rglob("*.json"))) == 2


def test_the_report_text_names_each_path_and_its_cost(tmp_path):
    text = S.format_report(S.run_paths(packets(2), factory(tmp_path, Fake(ok(GOOD, cost=0.001)))))
    assert "nooa: 2 of 2 valid" in text and "fallback: 2 of 2 valid" in text and "cap" in text


def test_a_planted_key_appears_in_no_file_a_run_writes_on_either_path(tmp_path, monkeypatch):
    secret = "sk" + "-or-PLANTED-SECRET-98765"  # built at runtime so the secret scan does not flag the test
    monkeypatch.setenv(L.KEY_VAR, secret)
    seen = []

    class Spy(Fake):
        def post(self, url, headers, payload, timeout):
            seen.append(headers["Authorization"])
            return super().post(url, headers, payload, timeout)

    def gateway_for(path):
        return L.Gateway(CFG, cache_dir=tmp_path / "cache", log_path=tmp_path / f"{path}.jsonl", transport=Spy(ok(GOOD, cost=0.001)), run_id=path)

    r = S.run_paths(packets(2), gateway_for)
    assert seen and all(h == f"Bearer {secret}" for h in seen)  # the real key reaches the provider call, and only there
    files = [f for f in tmp_path.rglob("*") if f.is_file()]
    assert files and not [f for f in files if secret in f.read_text()]
    assert secret not in json.dumps(r)


def test_the_crew_command_refuses_without_a_key_and_says_which_variable(monkeypatch, capsys, tmp_path):
    from computers.cli import main

    monkeypatch.delenv(L.KEY_VAR, raising=False)
    assert main(["crew", "spike", "--data-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert L.KEY_VAR in out and "sk-" not in out
