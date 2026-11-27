"""Schemas and packet v0 (spec S8 R1). Needs the `crew` group (pydantic)."""
import numpy as np
import pytest
from trig_helpers import flat, make_curve

pytest.importorskip("pydantic")

from computers.crew import packet as P
from computers.crew.schemas import Argument, Claim, ContextPacket, Evidence, Verdict
from computers.l1 import triggers as T
from computers.l1.tags import Tag

CAND = {"candidate_id": "abc123", "l2_score": 0.9876, "l1_flags": []}
STAR = {"ref_number": 7, "tile": "m44", "ra_deg": 130.3, "dec_deg": 19.9, "apass_b": 11.2, "color": 0.6}


def make(n=300, *, evid=None, tags=None, series="ac", kind="outburst", seed=0, limit=15.0):
    years, mag = flat(n, noise=0.05, seed=seed)
    curve = make_curve(years, mag, series=series, tags=tags, limit=limit)
    inp = T.prepare(curve)
    ev = tuple(range(40, 52)) if evid is None else tuple(evid)
    trig = T.Trigger(kind, float(inp.year[ev[0]]), float(inp.year[ev[-1]]), 1.5, False, ev, False)
    return trig, inp


def test_the_same_input_gives_the_same_packet_and_the_same_text():
    trig, inp = make()
    a, b = P.build_packet(CAND, STAR, trig, inp), P.build_packet(CAND, STAR, trig, inp)
    assert a == b and P.render(a) == P.render(b)


def test_ids_are_unique_ordered_by_block_and_every_block_is_present():
    trig, inp = make()
    pk = P.build_packet(CAND, STAR, trig, inp)
    ids = [e.id for e in pk.evidence]
    assert len(ids) == len(set(ids)) and ids == [f"E-{i + 1:03d}" for i in range(len(ids))]
    blocks = [e.block for e in pk.evidence]
    assert blocks == sorted(blocks) and set(blocks) == {1, 2, 3, 9} and pk.mode == "lightcurve_only"


def test_the_event_is_called_a_candidate_and_no_claim_of_reality_appears():
    trig, inp = make()
    text = P.render(P.build_packet(CAND, STAR, trig, inp)).lower()
    assert '"candidate":true' in text
    for word in ("vanish", "discover", "confirmed", "real event"):
        assert word not in text


def test_an_outburst_amplitude_is_marked_as_a_lower_limit_and_a_dip_is_not():
    trig, inp = make(kind="outburst")
    out = P.build_packet(CAND, STAR, trig, inp)
    ev = next(e for e in out.evidence if e.kind == "event")
    assert ev.payload["amplitude_is_lower_limit"] is True and out.event.lower_limit is True
    trig2, inp2 = make(kind="dip")
    assert next(e for e in P.build_packet(CAND, STAR, trig2, inp2).evidence if e.kind == "event").payload["amplitude_is_lower_limit"] is False


def test_block_three_is_capped_and_says_what_it_left_out():
    trig, inp = make(n=400, evid=range(200))
    pk = P.build_packet(CAND, STAR, trig, inp)
    pts = [e for e in pk.evidence if e.kind == "measurement"]
    assert len(pts) <= P.MAX_EVENT_POINTS
    omitted = next(e for e in pk.evidence if e.kind == "points_omitted")
    assert omitted.payload["total"] == 200 and omitted.payload["listed"] == len(pts)
    assert any("of the 200 points" in c for c in pk.cuts)


def test_the_packet_stays_inside_the_token_budget_and_blocks_1_and_9_are_never_cut():
    trig, inp = make(n=2000, evid=range(300), series=np.array([f"s{i % 40}" for i in range(2000)]))
    full = P.build_packet(CAND, STAR, trig, inp, budget=10_000)
    tight = P.build_packet(CAND, STAR, trig, inp, budget=900)
    assert full.token_estimate > tight.token_estimate and tight.cuts
    for blk in (1, 9):
        assert [e.payload for e in full.evidence if e.block == blk] == [e.payload for e in tight.evidence if e.block == blk]
    assert P.build_packet(CAND, STAR, trig, inp).token_estimate <= P.TOKEN_BUDGET


def test_an_impossible_budget_is_reported_and_not_hidden():
    trig, inp = make()
    pk = P.build_packet(CAND, STAR, trig, inp, budget=50)
    assert any("still over the budget" in c for c in pk.cuts) and pk.token_estimate > 50


def test_the_checklist_flags_a_tagged_evidence_row_and_passes_a_clean_one():
    tags = np.zeros(300, dtype=np.uint64)
    trig, inp = make()
    clean = {e.payload["check"]: e.payload for e in P.build_packet(CAND, STAR, trig, inp).evidence if e.kind == "check"}
    assert clean["blend_suspect"]["status"] == "pass" and clean["emulsion_non_blue"]["status"] == "pass"
    ev = list(range(40, 52))
    tags[ev[3]] |= int(Tag.BLEND_SUSPECT)
    tags[ev[5]] |= int(Tag.NON_BLUE)
    trig2, inp2 = make(tags=tags)
    flagged = {e.payload["check"]: e.payload for e in P.build_packet(CAND, STAR, trig2, inp2).evidence if e.kind == "check"}
    assert flagged["blend_suspect"] == {"check": "blend_suspect", "status": "flag", "flagged": 1, "of": 12}
    assert flagged["emulsion_non_blue"]["status"] == "flag" and flagged["shape_outlier"]["status"] == "pass"


def test_known_variable_and_a_single_series_show_in_the_checklist():
    trig, inp = make(series="ac")
    cand = {**CAND, "l1_flags": ["known_variable"]}
    checks = {e.payload["check"]: e.payload for e in P.build_packet(cand, STAR, trig, inp).evidence if e.kind == "check"}
    assert checks["known_variable"]["status"] == "flag" and checks["series_count"]["status"] == "flag"  # one series is below the rule


def test_the_maury_view_reads_the_checklist_first_and_another_role_reads_identity_first():
    trig, inp = make()
    pk = P.build_packet(CAND, STAR, trig, inp)
    heads = [ln for ln in P.render(pk, "maury").splitlines() if ln.startswith("## Block")]
    assert [h.split(":")[0] for h in heads] == ["## Block 9", "## Block 3", "## Block 2", "## Block 1"]
    heads2 = [ln.split(":")[0] for ln in P.render(pk, "someone").splitlines() if ln.startswith("## Block")]
    assert heads2[0] == "## Block 1"


def test_a_decade_summary_and_a_series_zero_point_describe_the_century():
    trig, inp = make(series=np.array(["ac", "ee"] * 150))
    pk = P.build_packet(CAND, STAR, trig, inp)
    dec = [e.payload for e in pk.evidence if e.kind == "decade_summary"]
    zp = {e.payload["series"]: e.payload for e in pk.evidence if e.kind == "series_zero_point"}
    assert sum(d["n"] for d in dec) == int(inp.good.sum()) and set(zp) == {"ac", "ee"}


# --------------------------------------------------------------------------- schemas


def test_a_claim_without_evidence_is_rejected_and_the_schema_has_no_min_items_keyword():
    with pytest.raises(ValueError, match="at least one"):
        Claim(text="x", evidence_ids=[])
    assert "'minItems':" not in str(Argument.model_json_schema())  # strict structured output on some providers refuses it


def test_extra_fields_and_bad_sides_are_rejected():
    with pytest.raises(ValueError):
        Argument.model_validate({"side": "maybe", "claims": []})
    with pytest.raises(ValueError):
        Claim.model_validate({"text": "x", "evidence_ids": ["E-001"], "extra": 1})


def test_evidence_ids_must_look_like_ids_and_be_unique_in_a_packet():
    with pytest.raises(ValueError):
        Evidence(id="17", block=1, kind="k", payload={})
    with pytest.raises(ValueError):
        Evidence(id="E-001", block=10, kind="k", payload={})
    trig, inp = make()
    pk = P.build_packet(CAND, STAR, trig, inp)
    with pytest.raises(ValueError, match="unique"):
        ContextPacket(**{**pk.model_dump(), "evidence": [pk.evidence[0], pk.evidence[0]]})


def test_a_verdict_checks_confidence_and_the_rationale_length():
    base = {"verdict": "artifact", "cause": "dust", "confidence": 0.5, "evidence_ids": ["E-001"], "rationale": "short", "model_id": "m", "prompt_hash": "h"}
    assert Verdict.model_validate(base)
    with pytest.raises(ValueError):
        Verdict.model_validate({**base, "confidence": 1.5})
    with pytest.raises(ValueError, match="80 words"):
        Verdict.model_validate({**base, "rationale": "word " * 81})
