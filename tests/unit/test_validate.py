"""The claim validator (spec S8 R4)."""
import pytest

pytest.importorskip("pydantic")

from test_packet import CAND, STAR, make

from computers.crew import packet as P
from computers.crew.schemas import Argument, Claim
from computers.crew.validate import validate


def pk():
    trig, inp = make()
    return P.build_packet(CAND, STAR, trig, inp)


def arg(*claims):
    return Argument(side="artifact", claims=list(claims))


def test_a_claim_citing_real_ids_is_kept_unchanged_and_in_order():
    p = pk()
    ids = sorted(p.ids())
    a = arg(Claim(text="first", evidence_ids=[ids[0]]), Claim(text="second  with  spaces", evidence_ids=[ids[1], ids[2]]))
    r = validate(p, a)
    assert r.argument == a and r.removed == () and r.clean and not r.empty


def test_a_claim_with_an_unknown_id_is_removed_with_the_reason_and_the_rest_stay():
    p = pk()
    good, bad = Claim(text="ok", evidence_ids=["E-001"]), Claim(text="made up", evidence_ids=["E-001", "E-999"])
    r = validate(p, arg(good, bad))
    assert r.argument.claims == [good] and len(r.removed) == 1
    assert r.removed[0].claim == bad and r.removed[0].reason == "unknown_evidence_id: E-999" and not r.clean


def test_a_claim_with_no_ids_is_removed_when_built_past_the_schema():
    p = pk()
    hollow = Claim.model_construct(text="no support", evidence_ids=[])  # the schema refuses this, so build it around the check
    r = validate(p, Argument.model_construct(side="artifact", claims=[hollow]))
    assert r.removed[0].reason == "no_evidence_ids" and r.empty


def test_when_every_claim_goes_the_result_is_an_empty_argument_and_not_an_error():
    r = validate(pk(), arg(Claim(text="x", evidence_ids=["E-900"])))
    assert r.empty and not r.clean and r.argument.side == "artifact" and len(r.removed) == 1


def test_an_argument_with_no_claims_is_empty_with_nothing_removed():
    r = validate(pk(), arg())
    assert r.empty and r.removed == () and not r.clean


def test_the_validator_does_not_change_the_packet_or_the_argument_it_was_given():
    p = pk()
    a = arg(Claim(text="x", evidence_ids=["E-001"]), Claim(text="y", evidence_ids=["E-404"]))
    before = (p.model_dump_json(), a.model_dump_json())
    validate(p, a)
    assert (p.model_dump_json(), a.model_dump_json()) == before
