"""The budget rule (spec S5 R5)."""
from hypothesis import given, settings
from hypothesis import strategies as st

from computers.l2.budget import select


def cand(i, tile="m44", ref=None, score=0.5, flags=()):
    return {"candidate_id": f"c{i:04d}", "ref_number": i if ref is None else ref, "tile": tile, "score": score, "flags": list(flags)}


def test_the_best_scores_are_taken_up_to_the_tile_cap():
    cs = [cand(i, score=i / 100) for i in range(30)]
    s = select(cs, per_tile=20, total=60)
    assert len(s.selected) == 20 and s.selected[0] == "c0029"
    assert {x[0] for x in s.left_out} == {f"c{i:04d}" for i in range(10)}
    assert all(why == "tile_cap" for _, _, why in s.left_out)


def test_the_total_cap_trims_the_weakest_across_tiles():
    cs = [cand(i, tile=t, score=(i + (0.5 if t == "tcrb" else 0)) / 100) for t in ("m44", "tcrb") for i in range(20)]
    s = select(cs, per_tile=20, total=30)
    assert len(s.selected) == 30
    assert all(why == "total_cap" for _, _, why in s.left_out) and len(s.left_out) == 10


def test_known_variables_take_no_place_and_are_listed():
    cs = [cand(0, score=0.99, flags=["known_variable"]), cand(1, score=0.1)]
    s = select(cs, per_tile=1, total=1)
    assert s.selected == ("c0001",) and s.known_variable == ("c0000",) and s.left_out == ()


def test_one_trigger_per_star_the_best_one():
    cs = [cand(0, ref=7, score=0.4), cand(1, ref=7, score=0.9), cand(2, ref=8, score=0.5)]
    s = select(cs, per_tile=5, total=5)
    assert s.selected == ("c0001", "c0002") and s.same_star == ("c0000",)


def test_the_same_ref_on_two_tiles_is_two_stars():
    s = select([cand(0, tile="m44", ref=7), cand(1, tile="tcrb", ref=7)], per_tile=5, total=5)
    assert len(s.selected) == 2


def test_a_tile_with_fewer_than_the_cap_sends_all_of_them_and_an_empty_input_is_fine():
    assert len(select([cand(i) for i in range(3)], per_tile=20, total=60).selected) == 3
    s = select([], per_tile=20, total=60)
    assert s.selected == () and s.left_out == ()


def test_a_tie_is_broken_by_id_so_the_result_is_reproducible():
    cs = [cand(i, score=0.5) for i in (3, 1, 2)]
    assert select(cs, per_tile=2, total=2).selected == ("c0001", "c0002")
    assert select(list(reversed(cs)), per_tile=2, total=2).selected == ("c0001", "c0002")


@settings(max_examples=60, deadline=None)
@given(
    scores=st.lists(st.floats(0, 1), min_size=0, max_size=60),
    per_tile=st.integers(1, 10),
    total=st.integers(1, 15),
)
def test_never_over_either_cap_and_a_worse_candidate_never_displaces_a_better_one(scores, per_tile, total):
    tiles = ["m44", "tcrb", "tabby"]
    cs = [cand(i, tile=tiles[i % 3], score=s) for i, s in enumerate(scores)]
    s = select(cs, per_tile=per_tile, total=total)
    chosen = [c for c in cs if c["candidate_id"] in set(s.selected)]
    assert len(chosen) <= total
    assert all(sum(c["tile"] == t for c in chosen) <= per_tile for t in tiles)
    # a candidate worse than everything chosen never removes one that was chosen
    floor = min((c["score"] for c in chosen), default=1.0)
    s2 = select([*cs, cand(999, tile="m44", score=floor - 1.0)], per_tile=per_tile, total=total)
    assert set(s.selected) <= set(s2.selected)
