"""
Tests for scheduler/pairing.py — pairing rules and can_pair().

Only rsd-alu-pair exists. All other combinations should be rejected.
"""

import pytest
from isa.instruction import Instruction
from scheduler.pairing import (
    can_pair, greedy_pair, stamp_slot_eligibility, RULES,
)


def make_insn(mnemonic, rd=None, rs1=None, rs2=None, imm=None, branch_target=None):
    insn = Instruction(
        mnemonic=mnemonic, operands=[], raw=mnemonic,
        rd=rd, rs1=rs1, rs2=rs2, imm=imm,
        branch_target=branch_target,
    )
    stamp_slot_eligibility([insn])
    return insn


def make_add(rd, rs1, rs2):
    return make_insn("add", rd=rd, rs1=rs1, rs2=rs2)


def make_add_rsd(rd, rs2):
    """make_add with rd==rs1 so is_rsd==True."""
    return make_insn("add", rd=rd, rs1=rd, rs2=rs2)


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
# rsd-alu-pair: the one defined rule
# ---------------------------------------------------------------------------

class TestRsdAluPair:

    def test_two_rsd_alu_ops_pair(self):
        """Two rsd-form ALU ops with supported mnemonics should pair."""
        a = make_insn("add", rd=10, rs1=10, rs2=11)   # is_rsd: rd==rs1
        b = make_insn("sub", rd=12, rs1=12, rs2=13)   # is_rsd: rd==rs1
        assert can_pair(a, b) is None

    def test_all_supported_mnemonics_pair(self):
        """Every mnemonic in the supported set can appear in either slot."""
        supported = ["add", "sub", "and", "or", "xor", "addw", "subw"]
        for m in supported:
            a = make_insn(m, rd=10, rs1=10, rs2=11)
            b = make_insn(m, rd=12, rs1=12, rs2=13)
            assert can_pair(a, b) is None, f"{m}+{m} should pair"

    def test_non_rsd_form_does_not_pair(self):
        """add with rd != rs1 and rd != rs2 is not rsd-form, should not pair."""
        a = make_add(10, 11, 12)   # rd=10, rs1=11, rs2=12 — is_rsd=False
        b = make_add(13, 14, 15)
        assert can_pair(a, b) is not None

    def test_commutative_rd_eq_rs2_pairs(self):
        """add with rd==rs2 (not rs1) is valid because add is commutative."""
        a = make_insn("add", rd=11, rs1=10, rs2=11)   # rd==rs2, commutative
        b = make_insn("add", rd=12, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_non_commutative_rd_eq_rs2_does_not_pair(self):
        """sub with rd==rs2 (not rs1) must not pair — operands can't be swapped."""
        a = make_insn("sub", rd=11, rs1=10, rs2=11)   # rd==rs2, non-commutative
        b = make_insn("sub", rd=12, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_unsupported_mnemonic_does_not_pair(self):
        """addi is not in the rsd-alu supported set — even rsd-form should not pair."""
        a = make_insn("addi", rd=10, rs1=10, imm=1)   # is_rsd: rd==rs1
        b = make_insn("addi", rd=12, rs1=12, imm=2)
        assert can_pair(a, b) is not None

    def test_pair_returns_none_for_valid(self):
        """can_pair returns None (not empty string) for a valid pair."""
        a = make_insn("add", rd=10, rs1=10, rs2=11)
        b = make_insn("add", rd=12, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_pair_returns_string_for_invalid(self):
        """can_pair returns a non-empty string for an invalid pair."""
        a = make_add(10, 11, 12)   # not rsd-form
        b = make_add(13, 14, 15)
        result = can_pair(a, b)
        assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# Combinations that should not pair (no applicable rule)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Slot disqualifiers
# ---------------------------------------------------------------------------

class TestSlotDisqualifiers:

    def test_unknown_in_a_slot_not_paired(self):
        """An unknown instruction must not be paired; its solo reason names the disqualifier."""
        unk = make_insn("unknown_op", rd=10, rs1=11, rs2=12)
        unk.is_unknown = True
        stamp_slot_eligibility([unk])
        add = make_add_rsd(13, 14)
        packets = greedy_pair([unk, add])
        assert all(p[0] == 'solo' for p in packets)
        assert any("A-slot disqualified: is_unknown" in r for r in unk.solo_reasons)

    def test_unknown_in_b_slot_not_paired(self):
        """An unknown B-slot candidate must not be paired; its solo reason names the disqualifier."""
        add = make_add_rsd(10, 11)
        unk = make_insn("unknown_op", rd=13, rs1=14, rs2=15)
        unk.is_unknown = True
        stamp_slot_eligibility([unk])
        packets = greedy_pair([add, unk])
        assert all(p[0] == 'solo' for p in packets)
        assert any("B-slot disqualified: is_unknown" in r for r in unk.solo_reasons)

    def test_unknown_reason_not_on_eligible_partner(self):
        """Disqualifier reason belongs only to the disqualified instruction, not its attempted partner."""
        unk = make_insn("unknown_op", rd=10, rs1=11, rs2=12)
        unk.is_unknown = True
        stamp_slot_eligibility([unk])
        add = make_add_rsd(13, 14)
        greedy_pair([unk, add])
        assert not any("disqualified" in r for r in add.solo_reasons)

    def test_two_unknown_each_gets_own_reason(self):
        """Two unknown instructions both go solo; each gets its own disqualifier reason."""
        unk1 = make_insn("unknown_op", rd=10, rs1=11, rs2=12)
        unk1.is_unknown = True
        unk2 = make_insn("unknown_op", rd=13, rs1=14, rs2=15)
        unk2.is_unknown = True
        stamp_slot_eligibility([unk1, unk2])
        packets = greedy_pair([unk1, unk2])
        assert all(p[0] == 'solo' for p in packets)
        assert any("A-slot disqualified: is_unknown" in r for r in unk1.solo_reasons)
        assert any("B-slot disqualified: is_unknown" in r for r in unk2.solo_reasons)


class TestNoApplicableRule:

    def test_load_alu_does_not_pair(self):
        lw = make_lw(10, 11)
        add = make_add(12, 13, 14)
        assert can_pair(lw, add) is not None

    def test_alu_store_does_not_pair(self):
        add = make_add(10, 11, 12)
        sw = make_sw(rs1=2, rs2=10)
        assert can_pair(add, sw) is not None

    def test_alu_branch_does_not_pair(self):
        add = make_add(10, 11, 12)
        beq = make_beq(10, 11)
        assert can_pair(add, beq) is not None

    def test_branch_alu_does_not_pair(self):
        beq = make_beq(10, 11)
        add = make_add(12, 13, 14)
        assert can_pair(beq, add) is not None

    def test_ret_alu_does_not_pair(self):
        ret = make_ret()
        add = make_add(10, 11, 12)
        assert can_pair(ret, add) is not None

    def test_call_alu_does_not_pair(self):
        call = make_call()
        add = make_add(10, 11, 12)
        assert can_pair(call, add) is not None

    def test_tail_alu_does_not_pair(self):
        tail = make_tail()
        add = make_add(10, 11, 12)
        assert can_pair(tail, add) is not None


# ---------------------------------------------------------------------------
# Solo reasons
# ---------------------------------------------------------------------------

class TestSoloReasons:

    def test_rule_rejection_goes_to_both(self):
        """Rule-check rejection reasons should go to both instructions."""
        a = make_add(10, 11, 12)   # not rsd-form — rule rejects, not disqualifier
        b = make_add(13, 14, 15)
        greedy_pair([a, b])
        assert len(a.solo_reasons) > 0
        assert len(b.solo_reasons) > 0
        # Both should see the same rule-rejection reason
        assert a.solo_reasons == b.solo_reasons

    def test_greedy_records_solo_reason_on_non_pairable(self):
        """After greedy rejects a pair, both instructions have solo_reasons."""
        a = make_add(10, 11, 12)   # not rsd-form, can't pair
        b = make_add(13, 14, 15)
        a.solo_reasons = set()
        b.solo_reasons = set()
        packets = greedy_pair([a, b])
        # Both should be solo and have reasons
        assert all(p[0] == 'solo' for p in packets)
        assert len(a.solo_reasons) > 0
        assert len(b.solo_reasons) > 0


# ---------------------------------------------------------------------------
# Greedy pairing model
# ---------------------------------------------------------------------------

class TestGreedyPairing:

    def test_simple_pair(self):
        """Two rsd-form ALU instructions should be paired."""
        a = make_add_rsd(10, 11)
        b = make_add_rsd(12, 13)
        packets = greedy_pair([a, b])
        assert len(packets) == 1
        assert packets[0][0] == 'pair'

    def test_three_instructions_one_pair_one_solo(self):
        """Three instructions: first two pair, third is solo."""
        a = make_add_rsd(10, 11)
        b = make_add_rsd(12, 13)
        c = make_add_rsd(14, 15)
        packets = greedy_pair([a, b, c])
        assert len(packets) == 2
        assert packets[0][0] == 'pair'
        assert packets[1][0] == 'solo'
        assert packets[1][1] is c

    def test_non_pairable_stays_solo(self):
        """Non-rsd-form adds stay solo."""
        a = make_add(10, 11, 12)
        b = make_add(13, 14, 15)
        packets = greedy_pair([a, b])
        assert all(p[0] == 'solo' for p in packets)

    def test_free_candidate_advances(self):
        """After a pair, the next free candidate advances correctly."""
        a = make_add_rsd(10, 11)
        b = make_add_rsd(12, 13)
        c = make_add_rsd(14, 15)
        d = make_add_rsd(16, 17)
        packets = greedy_pair([a, b, c, d])
        assert len(packets) == 2
        assert all(p[0] == 'pair' for p in packets)

    def test_solo_flush_at_end(self):
        """If last instruction has no partner, it's emitted as solo."""
        a = make_add_rsd(10, 11)
        b = make_add_rsd(12, 13)
        c = make_add_rsd(14, 15)
        packets = greedy_pair([a, b, c])
        assert len(packets) == 2
        assert packets[0][0] == 'pair'
        assert packets[1][0] == 'solo'
