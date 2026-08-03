"""The anchor-scan cache must answer the same question the scheduler does."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.anchors import (Rec, movable, shapes, candidates, greedy,
                              coverage, _fingerprint)


def rec(mnem, rd=None, rs1=None, rs2=None, imm=None, solo=True, **kw):
    return Rec(mnem, rd, rs1, rs2, imm, solo,
               kw.get("is_call", False), kw.get("is_branch", False),
               kw.get("is_jump", False), kw.get("is_load", False),
               kw.get("is_store", False))


def test_fingerprint_is_stable_and_short():
    assert _fingerprint() == _fingerprint()
    assert len(_fingerprint()) == 16


def test_movable_refuses_to_cross_a_control_transfer():
    b = [rec("mv", rd=10, rs1=8), rec("beq", rs1=1, rs2=0, is_branch=True),
         rec("jal", is_call=True)]
    assert not movable(b, 0, 2)


def test_movable_refuses_a_raw_hazard():
    b = [rec("mv", rd=10, rs1=8), rec("addi", rd=11, rs1=10, imm=1),
         rec("jal", is_call=True)]
    assert not movable(b, 0, 2)


def test_movable_refuses_a_war_hazard():
    b = [rec("mv", rd=10, rs1=8), rec("li", rd=8, rs1=0, imm=1),
         rec("jal", is_call=True)]
    assert not movable(b, 0, 2)


def test_movable_allows_an_independent_instruction():
    b = [rec("mv", rd=10, rs1=8), rec("li", rd=12, rs1=0, imm=1),
         rec("jal", is_call=True)]
    assert movable(b, 0, 2)


def test_memory_is_not_reordered():
    b = [rec("lw", rd=10, rs1=9, imm=0, is_load=True),
         rec("sw", rs1=9, rs2=11, imm=4, is_store=True),
         rec("jal", is_call=True)]
    assert not movable(b, 0, 2)


def test_candidates_skips_what_another_frame_took():
    b = [rec("mv", rd=10, rs1=8, solo=False), rec("jal", is_call=True)]
    assert candidates(b, 1) == []
    assert candidates(b, 1, solo_only=False) == [0]


def test_shapes_respect_the_bit_budget():
    r = rec("li", rd=10, rs1=0, imm=100)
    assert "li rd3,imm8" in shapes(r, budget=11)
    assert "li rd3,imm8" not in shapes(r, budget=10)


def test_shapes_know_the_argument_register_class():
    assert "mv rd3,rs5" in shapes(rec("mv", rd=10, rs1=8))
    assert "mv rd3,rs5" not in shapes(rec("mv", rd=18, rs1=8))
    assert "mv rd5,rs5" in shapes(rec("mv", rd=18, rs1=8))


def test_shapes_offer_the_scaled_form_only_when_aligned():
    aligned = rec("lw", rd=10, rs1=2, imm=64, is_load=True)
    odd = rec("lw", rd=10, rs1=2, imm=65, is_load=True)
    assert "load rd3,k*imm5(sp)" in shapes(aligned)
    assert "load rd3,k*imm5(sp)" not in shapes(odd)
    assert "load rd3,imm7(sp)" in shapes(odd)


def test_rsd_shape_is_offered_for_a_commutative_op_either_way():
    assert "add rsd5,rs5" in shapes(rec("add", rd=10, rs1=10, rs2=11))
    assert "add rsd5,rs5" in shapes(rec("add", rd=10, rs1=11, rs2=10))
    assert shapes(rec("sub", rd=10, rs1=11, rs2=10)) == set()


def test_greedy_is_monotone_and_covers_what_it_claims():
    rows = [(frozenset({"a", "b"}), 2), (frozenset({"b"}), 1),
            (frozenset({"c"}), 1), (frozenset(), 0)]
    steps = list(greedy(rows))
    assert steps[0][0] == "b" and steps[0][1] == 2
    gains = [g for _, g, _ in steps]
    assert gains == sorted(gains, reverse=True)
    assert steps[-1][2] == coverage(rows, [s for s, _, _ in steps])
