"""
Tests for analysis/pairing.py — pairing rules and can_pair().
"""

import pytest
from isa.instruction import Instruction
from analysis.pairing import (
    can_pair, greedy_pair, RULES,
    A_SLOT_DISQUALIFIERS, B_SLOT_DISQUALIFIERS,
)


def make_insn(mnemonic, rd=None, rs1=None, rs2=None, imm=None, branch_target=None,
              frd=None, frs1=None, frs2=None):
    # frd/frs1/frs2 are ignored — float operands use rd/rs1/rs2 with indices 32–63
    return Instruction(
        mnemonic=mnemonic, operands=[], raw=mnemonic,
        rd=rd, rs1=rs1, rs2=rs2, imm=imm,
        branch_target=branch_target,
    )


def make_add(rd, rs1, rs2):
    return make_insn("add", rd=rd, rs1=rs1, rs2=rs2)


def make_addi(rd, rs1, imm):
    return make_insn("addi", rd=rd, rs1=rs1, imm=imm)


def make_lw(rd, rs1, imm=0):
    return make_insn("lw", rd=rd, rs1=rs1, imm=imm)


def make_sw(rs1, rs2, imm=0):
    return make_insn("sw", rs1=rs1, rs2=rs2, imm=imm)


def make_ret():
    return make_insn("jalr", rd=0, rs1=1, imm=0)


def make_beq(rs1, rs2, target="L1"):
    return make_insn("beq", rs1=rs1, rs2=rs2, branch_target=target)


def make_call():
    return make_insn("call", branch_target="foo")


def make_tail():
    return make_insn("tail", branch_target="foo")


# ---------------------------------------------------------------------------
# A-slot disqualifiers
# ---------------------------------------------------------------------------

class TestASlotDisqualifiers:

    def test_branch_not_in_a_slot(self):
        """A branch in A-slot should be disqualified."""
        beq = make_beq(10, 11)
        add = make_add(12, 13, 14)
        reason = can_pair(beq, add)
        assert reason is not None
        assert "A-slot disqualified: is_branch" in reason

    def test_ret_not_in_a_slot(self):
        """ret in A-slot should be disqualified."""
        ret = make_ret()
        add = make_add(10, 11, 12)
        reason = can_pair(ret, add)
        assert reason is not None
        assert "A-slot disqualified" in reason

    def test_call_not_in_a_slot(self):
        """call in A-slot should be disqualified."""
        call = make_call()
        add = make_add(10, 11, 12)
        reason = can_pair(call, add)
        assert reason is not None
        assert "A-slot disqualified: is_call" in reason

    def test_tail_not_in_a_slot(self):
        """tail pseudo in A-slot should be disqualified."""
        tail = make_tail()
        add = make_add(10, 11, 12)
        reason = can_pair(tail, add)
        assert reason is not None
        assert "A-slot disqualified: is_tail" in reason

    def test_jal_rd_ra_not_in_a_slot(self):
        """jal ra (call) in A-slot should be disqualified."""
        jal_call = make_insn("jal", rd=1, branch_target="foo")
        add = make_add(10, 11, 12)
        reason = can_pair(jal_call, add)
        assert reason is not None
        assert "A-slot disqualified" in reason

    def test_branch_in_b_slot_ok(self):
        """A branch in B-slot is explicitly allowed (executes after A)."""
        add = make_add(10, 11, 12)
        beq = make_beq(10, 11)
        reason = can_pair(add, beq)
        # Branch in B-slot should NOT be disqualified
        assert reason is None or "B-slot disqualified" not in reason


# ---------------------------------------------------------------------------
# Basic pairing acceptance
# ---------------------------------------------------------------------------

class TestCanPairBasic:

    def test_two_rsd_alu_ops_pair(self):
        """Two RSD ALU ops should pair."""
        a = make_insn("add", rd=10, rs1=10, rs2=11)  # is_rsd=True
        b = make_insn("sub", rd=12, rs1=12, rs2=13)  # is_rsd=True
        assert can_pair(a, b) is None

    def test_alu_alu_pair(self):
        """Two plain ALU instructions should pair."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        assert can_pair(a, b) is None

    def test_alu_branch_pair(self):
        """ALU + branch should pair."""
        a = make_add(10, 11, 12)
        b = make_beq(10, 11)
        assert can_pair(a, b) is None

    def test_alu_load_pair(self):
        """ALU + load should pair."""
        a = make_add(10, 11, 12)
        b = make_lw(13, 14)
        assert can_pair(a, b) is None

    def test_load_alu_pair(self):
        """Load + ALU should pair."""
        a = make_lw(10, 11)
        b = make_add(12, 13, 14)
        assert can_pair(a, b) is None

    def test_alu_store_pair(self):
        """ALU + store should pair."""
        a = make_add(10, 11, 12)
        b = make_sw(rs1=2, rs2=10)
        assert can_pair(a, b) is None

    def test_store_alu_pair(self):
        """Store + ALU should pair."""
        a = make_sw(rs1=2, rs2=10)
        b = make_add(12, 13, 14)
        assert can_pair(a, b) is None

    def test_ret_not_pairable_in_a_slot(self):
        """ret cannot be in A-slot."""
        ret = make_ret()
        add = make_add(10, 11, 12)
        assert can_pair(ret, add) is not None

    def test_pair_returns_none_for_valid(self):
        """can_pair returns None (not empty string) for a valid pair."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        result = can_pair(a, b)
        assert result is None

    def test_pair_returns_string_for_invalid(self):
        """can_pair returns a non-empty string for an invalid pair."""
        branch = make_beq(10, 11)
        add = make_add(12, 13, 14)
        result = can_pair(branch, add)
        assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# Solo reasons
# ---------------------------------------------------------------------------

class TestSoloReasons:

    def test_a_slot_reason_goes_to_free_only(self):
        """A-slot disqualifier reason should go to free.solo_reasons, not curr."""
        branch = make_beq(10, 11)
        add = make_add(12, 13, 14)
        # Simulate greedy pass
        branch.solo_reasons = set()
        add.solo_reasons = set()

        packets = greedy_pair([branch, add])
        # branch is A-slot disqualified; reason should be in branch.solo_reasons
        assert any("A-slot" in r for r in branch.solo_reasons)
        # Not in add.solo_reasons
        assert not any("A-slot" in r for r in add.solo_reasons)

    def test_rule_rejection_goes_to_both(self):
        """Rule-check rejection reasons should go to both instructions."""
        # Two branches — first is A-slot disqualified (so never reaches rule check)
        # Instead, test with atomic (has_side_effects) pair
        from analysis.pairing import _record_solo_reasons
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        a.solo_reasons = set()
        b.solo_reasons = set()
        # Force a rejection scenario: make a fence-like (side effects)
        a2 = make_insn("fence")
        b2 = make_add(10, 11, 12)
        a2.solo_reasons = set()
        b2.solo_reasons = set()
        # fence has side effects, so alu-alu-pair would reject
        # but A-slot disqualifier check happens first...
        # Let's just test _record_solo_reasons directly
        _record_solo_reasons(a, b, "some-rule: some reason")
        assert "some-rule: some reason" in a.solo_reasons
        assert "some-rule: some reason" in b.solo_reasons


# ---------------------------------------------------------------------------
# Greedy pairing model
# ---------------------------------------------------------------------------

class TestGreedyPairing:

    def test_simple_pair(self):
        """Two ALU instructions should be paired."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        packets = greedy_pair([a, b])
        assert len(packets) == 1
        assert packets[0][0] == 'pair'

    def test_three_instructions_one_pair_one_solo(self):
        """Three instructions: first two pair, third is solo."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        ret = make_ret()
        packets = greedy_pair([a, b, ret])
        assert len(packets) == 2
        assert packets[0][0] == 'pair'
        assert packets[1][0] == 'solo'
        assert packets[1][1] is ret

    def test_branch_solo(self):
        """Branch that can't pair stays solo."""
        beq = make_beq(10, 11)
        packets = greedy_pair([beq])
        assert packets[0][0] == 'solo'

    def test_free_candidate_advances(self):
        """After a pair, the next free candidate advances."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        c = make_add(16, 17, 18)
        d = make_add(19, 20, 21)
        packets = greedy_pair([a, b, c, d])
        # Should be two pairs
        assert len(packets) == 2
        assert all(p[0] == 'pair' for p in packets)

    def test_solo_flush_at_end(self):
        """If last instruction has no partner, it's emitted as solo."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        c = make_add(16, 17, 18)
        packets = greedy_pair([a, b, c])
        assert len(packets) == 2
        assert packets[0][0] == 'pair'
        assert packets[1][0] == 'solo'
