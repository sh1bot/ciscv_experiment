"""
Tests for scheduler/pairing.py — pairing rules and can_pair().

Covers the full rule set defined in scheduler/rules.py (rsd-alu-pair,
chain/load/store-chain, the *-branch rules, mem-base-pair, dual-*-pair,
pre-inc-pair, epilogue-pair, ...).  encoding.yaml is authoritative for
op-sets, widths and layout; each rule's scheduler-side semantics are
documented at its definition in rules.py.
"""

import pytest
from isa.instruction import Instruction
from scheduler.pairing import (
    can_pair, greedy_pair, stamp_slot_eligibility, RULES,
    find_b_partners,
)
from scheduler.rules import NotPair, _post_inc_addi


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


class TestJumpSlotPairs:
    """arith-jump-pair / setup-jump-pair: pack a productive instruction with a
    trailing unconditional control transfer (ret / jr / j / indirect jalr)."""

    def _ret(self):   return make_insn("jalr", rd=0, rs1=1, imm=0)   # ret
    def _jr(self):    return make_insn("jalr", rd=0, rs1=15, imm=0)  # jr a5
    def _j(self):     return make_insn("j", branch_target="L")       # direct jump
    def _call(self):  return make_insn("jalr", rd=1, rs1=15, imm=0)  # jalr ra,a5,0

    def _rules(self, a, b):
        return [r.name for _, r in find_b_partners(a, [b])]

    def test_rsd_alu_then_ret(self):
        a = make_insn("addi", rd=10, rs1=10, imm=8)     # addi a0,a0,8 (RSD)
        assert "arith-jump-pair" in self._rules(a, self._ret())

    def test_rsd_alu_then_jump(self):
        a = make_add(10, 10, 11)                        # add a0,a0,a1 (RSD)
        assert "arith-jump-pair" in self._rules(a, self._j())

    def test_mv_then_ret(self):
        a = make_insn("addi", rd=10, rs1=11, imm=0)     # mv a0,a1
        assert "setup-jump-pair" in self._rules(a, self._ret())

    def test_small_load_then_jr(self):
        a = make_insn("lw", rd=10, rs1=11, imm=8)       # lw a0,8(a1): 8==2*width
        assert "setup-jump-pair" in self._rules(a, self._jr())

    def test_large_offset_load_not_paired(self):
        a = make_insn("lw", rd=10, rs1=11, imm=128)     # 128 > 31*4, the field max
        assert "setup-jump-pair" not in self._rules(a, self._ret())

    def test_direct_call_not_a_jump_slot(self):
        """A direct call's target is a displacement with nowhere to live:
        only 1.7% of cpp-rv32's calls fit a 10-bit packet field."""
        a = make_add(10, 10, 11)
        assert self._rules(a, make_call()) == []

    def test_indirect_call_is_a_jump_slot(self):
        """`jalr ra, rs` reads its target from a REGISTER and encodes no
        target at all, so the exclusion's reason does not reach it; the link
        value needs no field either (ra = packet + 4)."""
        a = make_add(10, 10, 11)
        assert "arith-jump-pair" in self._rules(a, self._call())

    def test_high_reg_pairs(self):
        """Registers are a full 5-bit field here: the frame's row spends the
        four 5-bit columns on operands, so x16..x31 encode fine."""
        a = make_add(16, 16, 17)
        assert "arith-jump-pair" in self._rules(a, self._ret())
        a = make_add(31, 31, 30)
        assert "arith-jump-pair" in self._rules(a, self._ret())

    def test_control_transfer_disqualified_from_a_slot(self):
        # A jump/return can never be the A (first) slot.
        stamp_slot_eligibility  # imported at module top
        j = make_insn("j", branch_target="L")
        ret = self._ret()
        assert j.a_slot_ok is False and ret.a_slot_ok is False
        assert j.b_slot_ok is True and ret.b_slot_ok is True



def make_tail():
    return make_insn("tail", branch_target="foo")


# ---------------------------------------------------------------------------
# rsd-alu-pair: the one defined rule
# ---------------------------------------------------------------------------

class TestRsdAluPair:

    def test_two_rsd_alu_ops_pair(self):
        """Two rsd-form ALU ops with supported mnemonics should pair."""
        a = make_insn("add", rd=10, rs1=10, rs2=11)   # is_rsd: rd==rs1
        b = make_insn("and", rd=12, rs1=12, rs2=13)   # is_rsd: rd==rs1
        assert can_pair(a, b) is None

    def test_all_supported_mnemonics_pair(self):
        """Every mnemonic in the supported set can appear in either slot."""
        supported = ["add", "and", "or", "xor"]
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
        """A mnemonic in no rule's supported set must not pair, even in rsd-form.

        sll/srl/sra (register-register shifts) are unsupported; only the
        shift-immediate forms slli/srli/srai are in rsd-alu-pair's set."""
        a = make_insn("sll", rd=10, rs1=10, rs2=11)   # is_rsd: rd==rs1
        b = make_insn("sll", rd=12, rs1=12, rs2=13)
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
# dual-*-pair family: two ops from a canonical tuple sharing inputs, distinct outputs
# ---------------------------------------------------------------------------

class TestDualOpPair:

    # --- arith2 ---

    def test_div_rem_same_sources_pairs(self):
        a = make_insn("div", rd=10, rs1=12, rs2=13)
        b = make_insn("rem", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_div_rem_different_sources_no_pair(self):
        a = make_insn("div", rd=10, rs1=12, rs2=13)
        b = make_insn("rem", rd=11, rs1=14, rs2=15)
        assert can_pair(a, b) is not None

    def test_div_rem_swapped_operand_order_no_pair(self):
        """div is non-commutative: rs1/rs2 must match positionally."""
        a = make_insn("div", rd=10, rs1=12, rs2=13)
        b = make_insn("rem", rd=11, rs1=13, rs2=12)
        assert can_pair(a, b) is not None

    def test_div_rem_same_dest_no_pair(self):
        a = make_insn("div", rd=10, rs1=12, rs2=13)
        b = make_insn("rem", rd=10, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_a_clobbers_shared_source_no_pair(self):
        """A-slot op writing a shared source corrupts B's read."""
        a = make_insn("divu", rd=12, rs1=12, rs2=13)   # rd == shared rs1
        b = make_insn("remu", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_b_writes_shared_source_canonical_ok(self):
        """Canonical order: B writing a shared source is a legal WAR."""
        a = make_insn("div", rd=10, rs1=12, rs2=13)
        b = make_insn("rem", rd=12, rs1=12, rs2=13)   # rd == shared rs1
        assert can_pair(a, b) is None

    def test_reversed_independent_pairs(self):
        """Reverse order accepted when fully independent."""
        a = make_insn("rem", rd=11, rs1=12, rs2=13)
        b = make_insn("div", rd=10, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_reversed_with_conflict_no_pair(self):
        """Reverse order rejected when a dest is a shared source."""
        a = make_insn("rem", rd=11, rs1=12, rs2=13)
        b = make_insn("div", rd=12, rs1=12, rs2=13)   # b.rd in a.uses
        assert can_pair(a, b) is not None

    def test_add_sub_no_longer_macro_op(self):
        """Cut deliberately: add/sub share arguments but are two separate
        computations, not two halves of one unit pass.  They belong to the
        prospective same-source frame (FRAMES.md sec 3), not here."""
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sub", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_min_max_no_longer_macro_op(self):
        a = make_insn("min", rd=10, rs1=12, rs2=13)
        b = make_insn("max", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_mul_mulh_pairs(self):
        a = make_insn("mul", rd=10, rs1=12, rs2=13)
        b = make_insn("mulh", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_mul_mulhu_pairs(self):
        a = make_insn("mul", rd=10, rs1=12, rs2=13)
        b = make_insn("mulhu", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_div_rem_pairs(self):
        a = make_insn("div", rd=10, rs1=12, rs2=13)
        b = make_insn("rem", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_divuw_remuw_pairs(self):
        a = make_insn("divuw", rd=10, rs1=12, rs2=13)
        b = make_insn("remuw", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_unrelated_mnemonics_no_pair(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("xor", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_same_mnemonic_no_tuple(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("add", rd=11, rs1=12, rs2=13)
        # (add, add) is not a tuple; falls to other rules (not rsd here) — no pair
        assert can_pair(a, b) is not None

    # --- post-increment: mem + addi ---
    #
    # encoding.yaml `post-inc-pair`:
    #     A: load rda, k*imma(rsda) / store rs2a, k*imma(rsda)
    #     B: addi rsda, rsda, k*immb
    # The base is one encoded field, so B must both read and write it:
    # b.rd == b.rs1 == a.rs1.  Order is fixed (reversing it is pre-inc-pair).

    def test_load_addi_post_increment_pairs(self):
        """Load through a base, then bump that base in place."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=16)   # stride = 2*8, uimm5*8
        assert can_pair(a, b) is None

    def test_load_addi_nonzero_offset_pairs(self):
        """A's own offset rides the width-scaled imma[4:0] field, so it may be
        nonzero — the frame draws k*imma, not 0(rsda)."""
        a = make_insn("ld", rd=10, rs1=12, imm=16)
        b = make_insn("addi", rd=12, rs1=12, imm=16)
        assert can_pair(a, b) is None

    def test_load_addi_offset_not_width_multiple_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=12)     # 12 not a multiple of 8
        b = make_insn("addi", rd=12, rs1=12, imm=8)
        assert can_pair(a, b) is not None

    def test_load_addi_offset_too_wide_no_pair(self):
        """imma is 5 bits scaled by the width: 32*8 overflows the field."""
        a = make_insn("ld", rd=10, rs1=12, imm=32 * 8)
        b = make_insn("addi", rd=12, rs1=12, imm=8)
        assert can_pair(a, b) is not None

    def test_load_addi_base_not_updated_in_place_no_pair(self):
        """addi writing some other register is not a base update."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=12, imm=16)
        assert can_pair(a, b) is not None

    def test_load_addi_stride_not_width_multiple_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=12)   # 12 not a multiple of 8
        assert can_pair(a, b) is not None

    def test_load_addi_zero_stride_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=0)
        assert can_pair(a, b) is not None

    def test_load_addi_base_mismatch_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=14, rs1=14, imm=8)
        assert can_pair(a, b) is not None

    def test_load_addi_into_base_not_a_post_inc(self):
        """Loading into the base register would leave B incrementing the loaded
        value instead of the pointer, so this is not a post-increment.

        It is still a legitimate load-chain pair — B consumes the loaded value —
        and load-alu-chain claims it now that non-sp bases are allowed."""
        a = make_insn("ld", rd=12, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=8)
        assert _rule_reason("post-inc-pair", a, b) is not None

    def test_lw_addi_width4(self):
        a = make_insn("lw", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=4)    # stride = 1*4
        assert can_pair(a, b) is None

    def test_lb_addi_no_longer_supported(self):
        """lb+addi is not in the tuple table (only 32/64-bit variants)."""
        a = make_insn("lb", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=1)
        assert can_pair(a, b) is not None

    def test_lwu_addi_not_supported(self):
        """encoding.yaml's post-inc-pair lists ld/lw/sd/sw — lwu is not there."""
        a = make_insn("lwu", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=4)
        assert can_pair(a, b) is not None

    def test_addi_load_reversed_is_pre_inc_not_post_inc(self):
        """Reversed order is a PRE-increment.  It still pairs — `pre-inc-pair`
        is a real frame — but post-inc-pair itself must not claim it."""
        a = make_insn("addi", rd=12, rs1=12, imm=16)
        b = make_insn("ld", rd=10, rs1=12, imm=0)
        assert can_pair(a, b) is None
        with pytest.raises(NotPair):
            _post_inc_addi(a, b)

    # --- post-increment: store + addi ---

    def test_store_addi_sd_pairs(self):
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=8)    # stride = 1*8
        assert can_pair(a, b) is None

    def test_store_addi_sw_pairs(self):
        a = make_insn("sw", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=4)    # stride = 1*4
        assert can_pair(a, b) is None

    def test_store_addi_nonzero_offset_pairs(self):
        """The stored value (rs2a) and the offset (imma) are separate fields."""
        a = make_insn("sd", rs1=12, rs2=13, imm=8)
        b = make_insn("addi", rd=12, rs1=12, imm=8)
        assert can_pair(a, b) is None

    def test_store_addi_base_mismatch_no_pair(self):
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=14, rs1=14, imm=8)
        assert can_pair(a, b) is not None

    def test_store_addi_stride_not_width_multiple_no_pair(self):
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=12)   # 12 not a multiple of 8
        assert can_pair(a, b) is not None

    # --- pre-increment reached through a shadd ---
    #
    # The post-increment shXadd frame was cut (zero pairs on every
    # corpus, both compilers).  Reversed, the same two instructions
    # are still a pre-increment, which is a live frame.

    def test_shadd_store_reversed_is_pre_inc(self):
        """Reversed (shadd, store) is a pre-increment, not this frame."""
        a = make_insn("sh3add", rd=12, rs1=12, rs2=13)
        b = make_insn("sd", rs1=12, rs2=13, imm=0)
        assert can_pair(a, b) is None
        assert _rule_reason("pre-inc-pair", a, b) is None

    # --- mem_base_pair ---

    def test_mem_pair_ld_ld_pairs(self):
        """Two sp-relative ld, offsets differ by 8 (data width)."""
        a = make_insn("ld", rd=10, rs1=2, imm=0)
        b = make_insn("ld", rd=11, rs1=2, imm=8)
        assert can_pair(a, b) is None

    def test_mem_pair_ld_ld_reversed_pairs(self):
        a = make_insn("ld", rd=10, rs1=2, imm=8)
        b = make_insn("ld", rd=11, rs1=2, imm=0)
        assert can_pair(a, b) is None

    def test_mem_pair_lw_lw_pairs(self):
        a = make_insn("lw", rd=10, rs1=2, imm=0)
        b = make_insn("lw", rd=11, rs1=2, imm=4)
        assert can_pair(a, b) is None

    def test_mem_pair_sd_sd_pairs(self):
        a = make_insn("sd", rs1=2, rs2=10, imm=0)
        b = make_insn("sd", rs1=2, rs2=11, imm=8)
        assert can_pair(a, b) is None

    def test_mem_pair_sw_sw_pairs(self):
        a = make_insn("sw", rs1=2, rs2=10, imm=0)
        b = make_insn("sw", rs1=2, rs2=11, imm=4)
        assert can_pair(a, b) is None

    def test_mem_pair_general_base_pairs(self):
        """mem_base_pair works with any shared base register, not just sp."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("ld", rd=11, rs1=12, imm=8)
        assert can_pair(a, b) is None

    def test_mem_pair_different_base_no_pair(self):
        """mem_base_pair requires the same base register on both ops."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("ld", rd=11, rs1=13, imm=8)
        assert can_pair(a, b) is not None

    def test_mem_pair_offset_gap_wrong_no_pair(self):
        """Offsets differ by 24, not the 8-byte ld width.
        A's offset is nonzero so load0-load10-chain (zero A offset) is out."""
        a = make_insn("ld", rd=10, rs1=2, imm=8)
        b = make_insn("ld", rd=11, rs1=2, imm=32)
        assert can_pair(a, b) is not None

    def test_mem_pair_same_dest_no_pair(self):
        """A's offset is nonzero so load0-load10-chain (zero A offset) is out."""
        a = make_insn("ld", rd=10, rs1=2, imm=8)
        b = make_insn("ld", rd=10, rs1=2, imm=16)
        assert can_pair(a, b) is not None

    def test_mem_pair_mixed_widths_no_pair(self):
        """ld and lw are different mnemonics — not a recognised mem_base_pair tuple.
        A's offset is nonzero so load0-load10-chain (zero A offset) is out."""
        a = make_insn("ld", rd=10, rs1=2, imm=8)
        b = make_insn("lw", rd=11, rs1=2, imm=16)
        assert can_pair(a, b) is not None

    def test_mem_pair_sp_8bit_offset_pairs(self):
        """sp-relative natural-word pair with an 8-bit scaled offset pairs --
        this is mem-sp-pair, whose implicit base pays for a 10-bit field.
        `ld` is the natural word only on RV64, so the base must be set."""
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            # scaled offset 183 = 0xB7, raw = 183*8 = 1464, needs 8 bits
            a = make_insn("ld", rd=10, rs1=2, imm=1464)
            b = make_insn("ld", rd=11, rs1=2, imm=1472)
            assert can_pair(a, b) is None
        finally:
            _r.set_xlen(old)

    def test_mem_pair_non_sp_load_6bit_limit(self):
        """The base rows draw imm[4:0] plus a shared sixth bit bought on the
        opcode list — six bits, so a scaled offset of 63 fits and 64 does
        not."""
        a = make_insn("ld", rd=10, rs1=12, imm=496)      # 496/8 = 62, fits 6b
        b = make_insn("ld", rd=11, rs1=12, imm=504)      # 504/8 = 63, fits 6b
        assert can_pair(a, b) is None
        a = make_insn("ld", rd=10, rs1=12, imm=512)      # 512/8 = 64, over 6b
        b = make_insn("ld", rd=11, rs1=12, imm=520)
        assert can_pair(a, b) is not None

    def test_mem_pair_non_sp_store_6bit_limit(self):
        """The base-register STORE row has the same shared six-bit field."""
        a = make_insn("sd", rs1=12, rs2=10, imm=512)     # 64, over 6b
        b = make_insn("sd", rs1=12, rs2=11, imm=520)
        assert can_pair(a, b) is not None

    def test_mem_pair_non_sp_5bit_in_range_pairs(self):
        """Non-sp base within 5-bit range still pairs."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("ld", rd=11, rs1=12, imm=8)
        assert can_pair(a, b) is None


# ---------------------------------------------------------------------------
# dual_setup_pair (li+li, mv+mv, mv+li)
# ---------------------------------------------------------------------------

class TestIndepPair:

    def test_li_li_pairs(self):
        """Two li (addi rd, x0, imm) — both rs1==0."""
        a = make_insn("addi", rd=10, rs1=0, imm=5)
        b = make_insn("addi", rd=11, rs1=0, imm=7)
        assert can_pair(a, b) is None

    def test_mv_mv_pairs(self):
        """Two mv (addi rd, rs1, 0) — both imm==0."""
        a = make_insn("addi", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=13, imm=0)
        assert can_pair(a, b) is None

    # --- epilogue_pair ---

    def test_epilogue_addi_ret_pairs(self):
        a = make_insn("addi", rd=2, rs1=2, imm=48)   # addi sp, sp, 48
        b = make_insn("ret",  rd=0, rs1=1, imm=0)
        assert can_pair(a, b) is None

    def test_epilogue_addi_jalr_zero_pairs(self):
        a = make_insn("addi", rd=2, rs1=2, imm=112)  # addi sp, sp, 112
        b = make_insn("jalr", rd=0, rs1=15, imm=0)
        assert can_pair(a, b) is None

    def test_epilogue_addi_jalr_nonzero_imm_pairs(self):
        """jalr with nonzero 12-bit offset (PIC call pattern) also pairs."""
        a = make_insn("addi", rd=2, rs1=2, imm=96)
        b = make_insn("jalr", rd=0, rs1=1, imm=-92)
        assert can_pair(a, b) is None

    def test_epilogue_jalr_rd1_pairs(self):
        """jalr with rd=1 (link register) provisionally allowed."""
        a = make_insn("addi", rd=2, rs1=2, imm=16)
        b = make_insn("jalr", rd=1, rs1=15, imm=0)
        assert can_pair(a, b) is None

    def test_epilogue_reversed_order_no_pair(self):
        """ret before addi sp,sp must NOT pair: the packet runs A then B, so a
        control transfer in the A slot would execute first and the addi (B slot)
        would never run."""
        a = make_insn("ret",  rd=0, rs1=1, imm=0)
        b = make_insn("addi", rd=2, rs1=2, imm=48)
        assert can_pair(a, b) is not None

    def test_epilogue_non_sp_addi_no_pair(self):
        """addi to a non-sp register doesn't qualify as an *epilogue*.

        (It legitimately pairs via arith-jump-pair — compute then return — so
        assert against the epilogue rule directly, not can_pair overall.)"""
        a = make_insn("addi", rd=10, rs1=10, imm=48)
        b = make_insn("ret",  rd=0, rs1=1, imm=0)
        epi = next(r for r in RULES if r.name == "epilogue-pair")
        with pytest.raises(NotPair):
            epi.check(a, b)

    def test_epilogue_negative_sp_no_pair(self):
        """Negative sp adjustment (prologue) doesn't qualify as an *epilogue*."""
        a = make_insn("addi", rd=2, rs1=2, imm=-48)
        b = make_insn("ret",  rd=0, rs1=1, imm=0)
        epi = next(r for r in RULES if r.name == "epilogue-pair")
        with pytest.raises(NotPair):
            epi.check(a, b)

    def test_epilogue_sp_adjust_out_of_range_no_pair(self):
        """sp adjustment beyond the yaml's 10-bit uimm×16 field (>16368)
        doesn't pair.  (The rule used to hand-narrow this to 7 bits; deriving
        from the yaml lifted it — A8.1.)"""
        a = make_insn("addi", rd=2, rs1=2, imm=16384)  # 1024×16, needs 11 bits
        b = make_insn("ret",  rd=0, rs1=1, imm=0)
        assert can_pair(a, b) is not None

    def test_mv_li_pairs(self):
        """mv + li."""
        a = make_insn("addi", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=0,  imm=5)
        assert can_pair(a, b) is None

    def test_addi4spn_addi4spn_pairs(self):
        """Two addi4spn (addi rd, sp, 4k) — rs1==sp, nonzero multiple of 4."""
        a = make_insn("addi", rd=10, rs1=2, imm=8)    # sp = x2
        b = make_insn("addi", rd=11, rs1=2, imm=16)
        assert can_pair(a, b) is None

    def test_addi4spn_li_pairs(self):
        a = make_insn("addi", rd=10, rs1=2, imm=8)
        b = make_insn("addi", rd=11, rs1=0, imm=5)
        assert can_pair(a, b) is None

    def test_addi4spn_mv_pairs(self):
        a = make_insn("addi", rd=10, rs1=2, imm=8)
        b = make_insn("addi", rd=11, rs1=12, imm=0)
        assert can_pair(a, b) is None

    def test_addi4spn_non_multiple_of_4_no_pair(self):
        """addi rd, sp, 6 is not a valid addi4spn (6 not a multiple of 4)."""
        a = make_insn("addi", rd=10, rs1=2, imm=6)
        b = make_insn("addi", rd=11, rs1=0, imm=5)
        assert can_pair(a, b) is not None

    def test_general_addi_addi_no_pair(self):
        """General addi (rs1!=0/sp and imm!=0) is not li, mv, or addi4spn."""
        a = make_insn("addi", rd=10, rs1=12, imm=4)
        b = make_insn("addi", rd=11, rs1=13, imm=8)
        assert can_pair(a, b) is not None

    def test_mixed_general_and_li_no_pair(self):
        """One li, one general addi — general addi is not a li/mv/addi4spn."""
        a = make_insn("addi", rd=10, rs1=0,  imm=5)
        b = make_insn("addi", rd=11, rs1=13, imm=4)
        assert can_pair(a, b) is not None

    def test_same_dest_no_pair(self):
        a = make_insn("addi", rd=10, rs1=0, imm=5)
        b = make_insn("addi", rd=10, rs1=0, imm=7)
        assert can_pair(a, b) is not None

    def test_b_feeds_a_no_pair(self):
        """B's result is a source of A."""
        a = make_insn("addi", rd=10, rs1=11, imm=0)   # mv: reads x11
        b = make_insn("addi", rd=11, rs1=0,  imm=5)   # li: writes x11
        assert can_pair(a, b) is not None



# ---------------------------------------------------------------------------
# pre-inc-pair
# ---------------------------------------------------------------------------

class TestPreIncPair:

    def test_addi_ld_rsd_pairs(self):
        """addi in RSD form pre-increments pointer; ld loads from zero offset."""
        a = make_insn("addi", rd=12, rs1=12, imm=8)   # a1 += 8
        b = make_insn("ld", rd=10, rs1=12, imm=0)     # load from new a1
        assert can_pair(a, b) is None

    def test_sh2add_lw_rsd_pairs(self):
        """sh2add in RSD form updates pointer; lw loads from zero offset."""
        a = make_insn("sh2add", rd=12, rs1=12, rs2=13)  # a1 = a1*4 + a2
        b = make_insn("lw", rd=10, rs1=12, imm=0)
        assert can_pair(a, b) is None

    def test_shadd_load_pairs(self):
        """sh3add in RSD form scales an index; the qword load reads it."""
        a = make_insn("sh3add", rd=12, rs1=12, rs2=13)
        b = make_insn("ld", rd=10, rs1=12, imm=0)
        assert _rule_reason("pre-inc-pair", a, b) is None

    def test_addi_word_forms_pair(self):
        """addi pairs with all four widths, not just the qword ones."""
        for mem in ("lw", "sw", "ld", "sd"):
            a = make_insn("addi", rd=12, rs1=12, imm=8)
            b = (make_insn(mem, rd=10, rs1=12, imm=0) if mem[0] == "l"
                 else make_insn(mem, rs1=12, rs2=13, imm=0))
            assert _rule_reason("pre-inc-pair", a, b) is None, mem

    def test_add_slt_not_a_pre_inc(self):
        """(add, slt) is not in the frame: encoding.yaml pairs each A with the
        load/store whose width its scale matches, and slt is not a store."""
        a = make_insn("add", rd=12, rs1=12, rs2=13)
        b = make_insn("slt", rd=10, rs1=12, rs2=14)
        assert _rule_reason("pre-inc-pair", a, b) is not None

    def test_shadd_width_must_match_scale(self):
        """sh2add scales by 4, so it pairs with word ops, not qword ones."""
        a = make_insn("sh2add", rd=12, rs1=12, rs2=13)
        assert _rule_reason("pre-inc-pair", a, make_insn("lw", rd=10, rs1=12, imm=0)) is None
        assert _rule_reason("pre-inc-pair", a, make_insn("ld", rd=10, rs1=12, imm=0)) is not None

    def test_addi_ld_not_rsd_no_pair(self):
        """A does not update its own source: not RSD form."""
        a = make_insn("addi", rd=11, rs1=12, imm=8)   # rd != rs1
        b = make_insn("ld", rd=10, rs1=11, imm=0)
        assert can_pair(a, b) is not None

    def test_addi_ld_b_reads_wrong_reg_no_pair(self):
        """B's rs1 does not match A's rd, so they cannot form a *pre-inc* pair.

        (They may still pair as an independent arith-mem pair — that is correct
        and not what this test is about, so assert against the rule directly.)"""
        a = make_insn("addi", rd=12, rs1=12, imm=8)
        b = make_insn("ld", rd=10, rs1=14, imm=0)     # loads from a4, not a2
        pre_inc = next(r for r in RULES if r.name == "pre-inc-pair")
        with pytest.raises(NotPair):
            pre_inc.check(a, b)

    def test_addi_ld_nonzero_offset_no_pair(self):
        """The addi rows access AT the bumped pointer — the offset field was
        spent on the 10-bit bump, so a nonzero B offset cannot encode."""
        a = make_insn("addi", rd=12, rs1=12, imm=8)
        b = make_insn("ld", rd=10, rs1=12, imm=8)
        assert _rule_reason("pre-inc-pair", a, b) is not None

    def test_addi_wide_scaled_bump_pairs(self):
        """The bump rides a 10-bit width-scaled field: ±512 units."""
        a = make_insn("addi", rd=12, rs1=12, imm=4088)   # 511*8, fits
        b = make_insn("ld", rd=10, rs1=12, imm=0)
        assert _rule_reason("pre-inc-pair", a, b) is None
        a = make_insn("addi", rd=12, rs1=12, imm=4096)   # 512*8, over
        assert _rule_reason("pre-inc-pair", a, b) is not None

    def test_shxadd_keeps_offset_field(self):
        """shXadd rows still draw the 5-bit scaled immb offset."""
        a = make_insn("sh3add", rd=12, rs1=12, rs2=14)
        b = make_insn("ld", rd=10, rs1=12, imm=248)      # 31*8, fits 5b
        assert _rule_reason("pre-inc-pair", a, b) is None
        b = make_insn("ld", rd=10, rs1=12, imm=256)      # 32*8, over 5b
        assert _rule_reason("pre-inc-pair", a, b) is not None

    def test_addi_ld_same_rd_no_pair(self):
        """A and B must not write the same register."""
        a = make_insn("addi", rd=12, rs1=12, imm=8)
        b = make_insn("ld", rd=12, rs1=12, imm=0)     # B clobbers the pointer
        assert can_pair(a, b) is not None

    def test_post_inc_pairs_as_dual_not_pre_inc(self):
        """(ld, addi) is the canonical post-increment order and matches dual-load-addi-pair.
        pre-inc-pair only matches (addi, ld), not the reverse."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=8)
        assert can_pair(a, b) is None  # accepted — but by dual-load-addi-pair, not pre-inc-pair

    def test_unrecognised_tuple_no_pair(self):
        """addi+slt is not in the pre-inc tuple table."""
        a = make_insn("addi", rd=12, rs1=12, imm=8)
        b = make_insn("slt", rd=10, rs1=12, rs2=14)
        assert can_pair(a, b) is not None


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
        assert unk.solo_reasons["is_unknown"] == {"A-slot disqualified"}

    def test_unknown_in_b_slot_not_paired(self):
        """An unknown B-slot candidate must not be paired; its solo reason names the disqualifier."""
        add = make_add_rsd(10, 11)
        unk = make_insn("unknown_op", rd=13, rs1=14, rs2=15)
        unk.is_unknown = True
        stamp_slot_eligibility([unk])
        packets = greedy_pair([add, unk])
        assert all(p[0] == 'solo' for p in packets)
        assert unk.solo_reasons["is_unknown"] == {"B-slot disqualified"}

    def test_unknown_reason_not_on_eligible_partner(self):
        """Disqualifier reason belongs only to the disqualified instruction, not its attempted partner."""
        unk = make_insn("unknown_op", rd=10, rs1=11, rs2=12)
        unk.is_unknown = True
        stamp_slot_eligibility([unk])
        add = make_add_rsd(13, 14)
        greedy_pair([unk, add])
        assert all(
            "disqualified" not in source
            for sources in add.solo_reasons.values()
            for source in sources
        )

    def test_two_unknown_each_gets_own_reason(self):
        """Each unknown instruction gets its own disqualifier reason in its own slot."""
        unk1 = make_insn("unknown_op", rd=10, rs1=11, rs2=12)
        unk1.is_unknown = True
        stamp_slot_eligibility([unk1])
        add = make_add_rsd(13, 14)
        unk2 = make_insn("unknown_op", rd=15, rs1=1, rs2=2)
        unk2.is_unknown = True
        stamp_slot_eligibility([unk2])
        packets = greedy_pair([unk1, add, unk2])
        assert all(p[0] == 'solo' for p in packets)
        assert unk1.solo_reasons["is_unknown"] == {"A-slot disqualified"}
        assert unk2.solo_reasons["is_unknown"] == {"B-slot disqualified"}


class TestNoApplicableRule:

    def test_load_alu_does_not_pair(self):
        lw = make_lw(10, 11)
        add = make_add(12, 13, 14)
        assert can_pair(lw, add) is not None

    def test_alu_store_does_not_pair(self):
        # sw stores x13, not the add's result x10, so alu-store-chain
        # (which requires the store value to be A's destination) does not apply.
        add = make_add(10, 11, 12)
        sw = make_sw(rs1=2, rs2=13)
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

    def test_greedy_annotates_b_with_pair_specific_reason(self):
        """When A is eligible but pair fails, B gets the pair-specific reason."""
        a = make_add_rsd(10, 11)   # valid A-slot candidate
        b = make_add(13, 14, 15)   # not rsd-form — B fails
        packets = greedy_pair([a, b])
        assert all(p[0] == 'solo' for p in packets)
        assert len(b.solo_reasons) > 0

    def test_greedy_no_annotation_when_a_ineligible(self):
        """When A is ineligible for all rules, B gets no pair-attempt reasons."""
        a = make_insn("auipc", rd=1, imm=0)   # unsupported mnemonic
        b = make_add_rsd(10, 11)
        b.solo_reasons.clear()
        greedy_pair([a, b])
        assert len(b.solo_reasons) == 0


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
        d = make_add_rsd(8, 9)
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


def make_ld(rd, rs1, imm=0):
    return make_insn("ld", rd=rd, rs1=rs1, imm=imm)


def make_sd(rs1, rs2, imm=0):
    return make_insn("sd", rs1=rs1, rs2=rs2, imm=imm)


class TestChainAluPair:
    """A computes a value B consumes; the chain register dies within the pair."""

    def test_basic_chain_pairs(self):
        # add x10, x8, x9; add x11, x10, x12 — B consumes x10, which then dies
        a = make_insn("add", rd=10, rs1=8, rs2=9)
        b = make_insn("add", rd=11, rs1=10, rs2=12)
        assert can_pair(a, b) is None

    def test_high_chain_register_pairs(self):
        """The chain register dies within the pair and is not encoded, so it is
        exempt from the x0..x15 range limit even when it is a high register."""
        a = make_insn("add", rd=16, rs1=8, rs2=9)     # chain reg x16 (out of window)
        b = make_insn("add", rd=10, rs1=16, rs2=11)   # consumes x16; x16 dead after
        assert can_pair(a, b) is None

    def test_high_encoded_register_accepted(self):
        """Encoded operands are a full 5-bit field: the chain row spends the
        four 5-bit columns on rs2b/rs2a/rs1a/rdb, so x16..x31 encode fine."""
        a = make_insn("add", rd=8, rs1=16, rs2=9)     # a.rs1 = x16 is encoded
        b = make_insn("add", rd=10, rs1=8, rs2=11)
        assert can_pair(a, b) is None


class TestLoadChainAluPair:
    """A = sp-relative load (8-bit scaled offset); B = ALU consuming the value."""

    def test_basic_pairs(self):
        # ld x10, 64(sp); add x10, x10, x11  — B consumes loaded x10
        a = make_ld(10, 2, imm=64)
        b = make_add(10, 10, 11)
        assert can_pair(a, b) is None

    def test_commutative_rs2_chain(self):
        # add is commutative; B may consume A's result as rs2
        a = make_ld(10, 2, imm=64)
        b = make_add(12, 11, 10)
        assert can_pair(a, b) is None

    def test_non_sp_base_pairs(self):
        """encoding.yaml gives this frame two templates — `load tmp, k*imma(rs1a)`
        as well as the sp form — and rows 1-2 draw the base register."""
        a = make_ld(10, 12, imm=64)   # base x12, not sp
        b = make_add(10, 10, 11)
        assert _rule_reason("load-alu-chain", a, b) is None

    def test_base_reg_offset_over_6bit_no_pair(self):
        # base-register rows: ld@6s scaled by 8 reaches 63*8 = 504
        a = make_ld(10, 12, imm=512)
        b = make_add(10, 10, 11)
        assert _rule_reason("load-alu-chain", a, b) is not None

    def test_offset_over_10bit_no_pair(self):
        # SP rows draw imma[4:0|9:5]: 10 bits scaled by 8 reaches 1023*8 = 8184
        a = make_ld(10, 2, imm=8192)
        b = make_add(10, 10, 11)
        assert can_pair(a, b) is not None

    def test_b_does_not_consume_no_pair(self):
        a = make_ld(10, 2, imm=64)
        b = make_add(12, 13, 14)      # does not read x10
        assert can_pair(a, b) is not None


class TestStoreChainAluPair:
    """A = ALU op; B = sp-relative store (8-bit scaled offset) of A's result."""

    def test_basic_pairs(self):
        # add x10, x11, x12; sd x10, 64(sp)  — B stores the computed x10
        a = make_add(10, 11, 12)
        b = make_sd(rs1=2, rs2=10, imm=64)
        assert can_pair(a, b) is None

    def test_store_value_mismatch_no_pair(self):
        a = make_add(10, 11, 12)
        b = make_sd(rs1=2, rs2=13, imm=64)   # stores x13, not x10
        assert can_pair(a, b) is not None

    def test_non_sp_base_pairs(self):
        """encoding.yaml gives this frame two templates — `store tmp, k*immb(rs1b)`
        as well as the sp form — and rows 1-2 draw the base register."""
        a = make_add(10, 11, 12)
        b = make_sd(rs1=14, rs2=10, imm=64)  # base x14: 64/8 = 8, fits immb[4:0]
        assert _rule_reason("alu-store-chain", a, b) is None

    def test_offset_over_10bit_no_pair(self):
        # SP rows draw immb[9:5]/immb[4:0]: 10 bits scaled by 8 reaches 8184
        a = make_add(10, 11, 12)
        b = make_sd(rs1=2, rs2=10, imm=8192)
        assert can_pair(a, b) is not None


class TestBaseChainLoadPair:
    """A = lx rtmp, 0(rb); B = load rd, imm10(rtmp); rtmp dead after B.

    `ld` is the natural word only on RV64, and the A slot now spends ONE
    XLEN-switchable opcode on it (the loaded value is B's base ADDRESS, so a
    byte or halfword there is meaningless), which is why these set the base.
    """

    def test_basic_pairs(self):
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_ld(10, 12, imm=0)     # ld x10, 0(x12)
            b = make_ld(11, 10, imm=512)   # ld x11, 512(x10)
            assert can_pair(a, b) is None
        finally:
            _r.set_xlen(old)

    def test_a_not_natural_word_no_pair(self):
        """A loads a byte, so it is not producing an address."""
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_insn("lbu", rd=10, rs1=12, imm=0)
            b = make_ld(11, 10, imm=512)
            assert can_pair(a, b) is not None
        finally:
            _r.set_xlen(old)

    def test_wrong_base_natural_word_no_pair(self):
        """`ld` is not the natural word on RV32, so the A slot cannot hold it."""
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(32)
        try:
            a = make_ld(10, 12, imm=0)
            b = make_ld(11, 10, imm=512)
            assert can_pair(a, b) is not None
        finally:
            _r.set_xlen(old)

    def test_b_base_mismatch_no_pair(self):
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_ld(10, 12, imm=0)
            b = make_ld(11, 13, imm=512)   # B base is not A's result
            assert can_pair(a, b) is not None
        finally:
            _r.set_xlen(old)

    def test_b_offset_over_10bit_no_pair(self):
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_ld(10, 12, imm=0)
            b = make_ld(11, 10, imm=8192)
            assert can_pair(a, b) is not None
        finally:
            _r.set_xlen(old)

    def test_a_auipc_got_load_no_pair(self):
        # A is the load half of an auipc+load GOT access — excluded.
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_ld(10, 12, imm=0)
            a.base_from_auipc = True
            b = make_ld(11, 10, imm=512)
            assert can_pair(a, b) is not None
        finally:
            _r.set_xlen(old)


class TestBaseChainLoadOffPair:
    """A = lx rtmp, imm5(rb); B = load rd, imm5(rtmp); rtmp dead after B.

    The offset-bearing sibling: the pointer itself sits at an offset, typically
    a stack slot.  It replaces the old deref-load-chain, whose population is
    this frame's immb == 0 column.
    """

    def test_both_offsets_pair(self):
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            # ld scale 8: scaled 5-bit max is 31, raw 248
            a = make_ld(10, 12, imm=248)   # ld x10, 248(x12)
            b = make_ld(11, 10, imm=248)   # ld x11, 248(x10)
            assert can_pair(a, b) is None
        finally:
            _r.set_xlen(old)

    def test_deref_shape_pairs_here(self):
        """The old deref-load-chain shape -- offset on A, B at zero."""
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_ld(10, 12, imm=64)
            b = make_ld(11, 10, imm=0)
            assert can_pair(a, b) is None
        finally:
            _r.set_xlen(old)

    def test_a_offset_over_5bit_no_pair(self):
        """512/8 = 64 needs seven bits; the split field holds five."""
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_ld(10, 12, imm=512)
            b = make_ld(11, 10, imm=0)
            assert can_pair(a, b) is not None
        finally:
            _r.set_xlen(old)

    def test_b_offset_over_5bit_with_a_offset_no_pair(self):
        """With A offset nonzero this is load5-load5-chain's, and B gets only
        five bits there -- load0-load10-chain's ten are unavailable."""
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            a = make_ld(10, 12, imm=8)
            b = make_ld(11, 10, imm=512)
            assert can_pair(a, b) is not None
        finally:
            _r.set_xlen(old)

    def test_frames_are_disjoint(self):
        """A's offset zero is load0-load10-chain's and nonzero is the sibling's, so
        no chase is ever encodable both ways."""
        import scheduler.rules as _r
        old = _r.XLEN
        _r.set_xlen(64)
        try:
            for a_imm in (0, 8, 248):
                a = make_ld(10, 12, imm=a_imm)
                b = make_ld(11, 10, imm=8)
                accepting = [n for n in ("load0-load10-chain", "load5-load5-chain")
                             if _rule_reason(n, a, b) is None]
                assert len(accepting) <= 1, (a_imm, accepting)
        finally:
            _r.set_xlen(old)


def _rule_reason(name, a, b):
    """Run a single named rule's check() directly; return None if it accepts or
    the NotPair reason string if it rejects."""
    rule = next(r for r in RULES if r.name == name)
    try:
        rule.check(a, b)
        return None
    except NotPair as exc:
        return exc.reason


class TestLoadSpBranch:
    """A = sp-relative load (width-scaled uimm10 offset); B = beqz/bnez on the
    value.  The loaded value is kept alive (null-check idiom)."""

    def test_basic_pairs(self):
        a = make_insn("lw", rd=10, rs1=2, imm=8)          # lw a0, 8(sp)
        b = make_insn("beqz", rs1=10, branch_target="L")  # beqz a0, L
        assert can_pair(a, b) is None

    def test_scaled_offset_extends_reach(self):
        # 2048 exceeds a 10-bit *byte* range but is 512×4 — in range once scaled.
        a = make_insn("lw", rd=10, rs1=2, imm=2048)
        b = make_insn("beqz", rs1=10, branch_target="L")
        assert can_pair(a, b) is None

    def test_offset_over_10bit_no_pair(self):
        a = make_insn("lw", rd=10, rs1=2, imm=4096)       # 4096>>2 = 1024 > uimm10 max 1023
        b = make_insn("beqz", rs1=10, branch_target="L")
        assert can_pair(a, b) is not None

    def test_unaligned_offset_no_pair(self):
        a = make_insn("lw", rd=10, rs1=2, imm=6)          # 6 not a multiple of 4
        b = make_insn("beqz", rs1=10, branch_target="L")
        assert can_pair(a, b) is not None

    def test_branch_does_not_consume_no_pair(self):
        a = make_insn("lw", rd=10, rs1=2, imm=8)
        b = make_insn("bnez", rs1=11, branch_target="L")  # tests a1, not a0
        assert can_pair(a, b) is not None

    def test_value_may_stay_alive(self):
        # rd kept live after the branch still pairs (not a dead-value rule).
        a = make_insn("lw", rd=10, rs1=2, imm=8)
        b = make_insn("beqz", rs1=10, branch_target="L")
        b.live_out = frozenset({10})
        assert can_pair(a, b) is None


class TestLoadBaseBranch:
    """A = any-base load (width-scaled uimm5 offset); B = beqz/bnez on the value."""

    def test_basic_pairs(self):
        a = make_insn("lw", rd=10, rs1=11, imm=8)         # lw a0, 8(a1): 8 = 2×4
        b = make_insn("bnez", rs1=10, branch_target="L")
        assert can_pair(a, b) is None

    def test_byte_load_unscaled_offset_pairs(self):
        # lb has width 1 (shift 0), so any byte offset up to 31 is in range.
        a = make_insn("lb", rd=10, rs1=11, imm=31)
        b = make_insn("bnez", rs1=10, branch_target="L")
        assert can_pair(a, b) is None

    def test_offset_over_5bit_no_pair(self):
        a = make_insn("lw", rd=10, rs1=11, imm=128)       # 128>>2 = 32 > uimm5 max 31
        b = make_insn("bnez", rs1=10, branch_target="L")
        assert can_pair(a, b) is not None

    def test_unaligned_offset_no_pair(self):
        a = make_insn("lw", rd=10, rs1=11, imm=6)         # 6 not a multiple of 4
        b = make_insn("bnez", rs1=10, branch_target="L")
        assert can_pair(a, b) is not None

    def test_auipc_base_no_pair(self):
        a = make_insn("lw", rd=10, rs1=11, imm=8)
        a.base_from_auipc = True                          # GOT-relative → excluded
        b = make_insn("bnez", rs1=10, branch_target="L")
        assert _rule_reason("load-base-branch-pair", a, b) is not None


class TestChainLiBranch:
    """A = li rtmp, imm8; B = comparison branch consuming rtmp; rtmp dies."""

    def test_basic_pairs(self):
        a = make_insn("addi", rd=10, rs1=0, imm=5)        # li a0, 5
        b = make_insn("beq", rs1=10, rs2=11, branch_target="L")
        assert can_pair(a, b) is None

    def test_commutative_rs2_chain_pairs(self):
        # branches are commutative for chaining: rtmp may be the second operand
        a = make_insn("addi", rd=10, rs1=0, imm=5)
        b = make_insn("blt", rs1=11, rs2=10, branch_target="L")
        assert can_pair(a, b) is None

    def test_immediate_over_8bit_no_pair(self):
        a = make_insn("addi", rd=10, rs1=0, imm=200)      # 200 > int8 max 127
        b = make_insn("beq", rs1=10, rs2=11, branch_target="L")
        assert _rule_reason("li-branch-chain", a, b) is not None

    def test_value_escapes_no_pair(self):
        a = make_insn("addi", rd=10, rs1=0, imm=5)
        b = make_insn("beq", rs1=10, rs2=11, branch_target="L")
        b.live_out = frozenset({10})                      # rtmp still live after B
        assert _rule_reason("li-branch-chain", a, b) is not None


class TestIncBranchPair:
    """A = inc/dec (addi rsd, rsd, +/-1); B = comparison branch reading the
    counter; rd may stay alive (loop-counter idiom).  The allowed branch
    modes depend on step direction — the frame enumerates joint cells."""

    def test_basic_pairs(self):
        a = make_insn("addi", rd=10, rs1=10, imm=1)       # addi a0, a0, 1
        b = make_insn("blt", rs1=10, rs2=11, branch_target="L")
        assert can_pair(a, b) is None

    def test_counter_may_stay_alive(self):
        a = make_insn("addi", rd=10, rs1=10, imm=1)
        b = make_insn("bne", rs1=10, rs2=11, branch_target="L")
        b.live_out = frozenset({10})                      # counter survives the branch
        assert can_pair(a, b) is None

    def test_high_register_pairs(self):
        """rsda is a full 5-bit column in this frame's row."""
        a = make_insn("addi", rd=16, rs1=16, imm=1)
        b = make_insn("blt", rs1=16, rs2=11, branch_target="L")
        assert _rule_reason("inc-branch-pair", a, b) is None

    def test_non_unit_step_no_pair(self):
        a = make_insn("addi", rd=10, rs1=10, imm=4)
        b = make_insn("blt", rs1=10, rs2=11, branch_target="L")
        assert _rule_reason("inc-branch-pair", a, b) is not None

    def test_addiw_folds_into_inc(self):
        """addiw sites are matched and billed as the full-width op."""
        a = make_insn("addiw", rd=10, rs1=10, imm=-1)
        b = make_insn("bne", rs1=10, rs2=11, branch_target="L")
        assert _rule_reason("inc-branch-pair", a, b) is None

    def test_zero_compare_alias_pairs(self):
        """beqz is beq with rs2b = x0 — free in the row."""
        a = make_insn("addi", rd=10, rs1=10, imm=-1)
        b = make_insn("bnez", rs1=10, rs2=0, branch_target="L")
        assert _rule_reason("inc-branch-pair", a, b) is None

    def test_direction_gates_mode(self):
        """bge sum-first is an up-loop cell; the down cluster carries the
        reversed spelling instead."""
        up = make_insn("addi", rd=10, rs1=10, imm=1)
        down = make_insn("addi", rd=10, rs1=10, imm=-1)
        bge_sf = make_insn("bge", rs1=10, rs2=11, branch_target="L")
        bge_ss = make_insn("bge", rs1=11, rs2=10, branch_target="L")
        assert _rule_reason("inc-branch-pair", up, bge_sf) is None
        assert _rule_reason("inc-branch-pair", up, bge_ss) is not None
        assert _rule_reason("inc-branch-pair", down, bge_sf) is not None
        assert _rule_reason("inc-branch-pair", down, bge_ss) is None


class TestChainBitTestBranch:
    """A isolates/masks bits (andi pow2, or slli/srli); B branches on zero."""

    def test_andi_pow2_pairs(self):
        a = make_insn("andi", rd=10, rs1=10, imm=8)       # single-bit mask
        b = make_insn("bnez", rs1=10, branch_target="L")
        assert can_pair(a, b) is None

    def test_slli_any_shift_pairs(self):
        a = make_insn("slli", rd=10, rs1=10, imm=3)
        b = make_insn("beqz", rs1=10, branch_target="L")
        assert can_pair(a, b) is None

    def test_andi_non_pow2_no_pair(self):
        a = make_insn("andi", rd=10, rs1=10, imm=6)       # 6 not pow2/shift-expressible
        b = make_insn("bnez", rs1=10, branch_target="L")
        assert _rule_reason("bit-test-branch-chain", a, b) is not None

    def test_beq_requires_zero_rs2_no_pair(self):
        a = make_insn("andi", rd=10, rs1=10, imm=8)
        b = make_insn("beq", rs1=10, rs2=11, branch_target="L")  # not a zero-test
        assert _rule_reason("bit-test-branch-chain", a, b) is not None


class TestProloguePair:
    """A = addi sp,sp,-N (reserve frame); B = sw/sd ra at top of frame."""

    def test_basic_pairs(self):
        a = make_insn("addi", rd=2, rs1=2, imm=-16)       # addi sp, sp, -16
        b = make_insn("sd", rs1=2, rs2=1, imm=8)          # sd ra, 8(sp): 8+8-16=0
        assert can_pair(a, b) is None

    def test_sw_width_delta_pairs(self):
        a = make_insn("addi", rd=2, rs1=2, imm=-16)
        b = make_insn("sw", rs1=2, rs2=1, imm=12)         # sw ra, 12(sp): 12+4-16=0
        assert can_pair(a, b) is None

    def test_wrong_delta_no_pair(self):
        # sd ra, 0(sp) is a valid *pre-increment* store (pairs via pre-inc-pair),
        # so assert against the prologue rule directly.
        a = make_insn("addi", rd=2, rs1=2, imm=-16)
        b = make_insn("sd", rs1=2, rs2=1, imm=0)          # 0+8-16 != 0
        assert _rule_reason("prologue-pair", a, b) == "B-bad-delta"

    def test_not_ra_source_no_prologue(self):
        """prologue-pair is for saving ra; storing a0 is not one.

        pre-inc-pair does claim it — adjusting sp then storing at an offset
        from the new sp is a genuine pre-increment — so assert on this frame."""
        a = make_insn("addi", rd=2, rs1=2, imm=-16)
        b = make_insn("sd", rs1=2, rs2=10, imm=8)         # stores a0, not ra
        assert _rule_reason("prologue-pair", a, b) is not None

    def test_positive_adjust_no_pair(self):
        a = make_insn("addi", rd=2, rs1=2, imm=16)        # positive → not a prologue
        b = make_insn("sd", rs1=2, rs2=1, imm=8)
        assert _rule_reason("prologue-pair", a, b) is not None


class TestRvcEligiblePseudoOps:
    """RVC eligibility must recognise pseudo-op spellings, and reject RV32-only
    c.jal on an RV64 target."""

    def test_mv_as_addi_imm0_compresses(self):
        # mv folded into `addi rd, rs1, 0` → c.mv
        assert make_insn("addi", rd=10, rs1=11, imm=0).rvc_eligible

    def test_mv_mnemonic_compresses(self):
        assert make_insn("mv", rd=10, rs1=11).rvc_eligible

    def test_beqz_compresses(self):
        assert make_insn("beqz", rs1=10).rvc_eligible          # x10 in x8..x15
        assert not make_insn("beqz", rs1=5).rvc_eligible       # x5 out of range

    def test_bnez_compresses(self):
        assert make_insn("bnez", rs1=12).rvc_eligible
        assert not make_insn("bnez", rs1=2).rvc_eligible

    def test_j_compresses(self):
        assert make_insn("j", branch_target="L").rvc_eligible
        assert make_insn("jal", rd=0).rvc_eligible             # jal x0 == j

    def test_ret_compresses(self):
        assert make_insn("ret").rvc_eligible                   # c.jr ra

    def test_jal_ra_does_not_compress_on_rv64(self):
        # c.jal is RV32C-only; jal ra, target must NOT compress on RV64.
        assert not make_insn("jal", rd=1).rvc_eligible


class TestLoadStoreChain:
    """A loads a value, B stores it straight back out through a dead temp —
    a memory copy.  Widths must match; both offsets are width-scaled uimm6."""

    def test_word_copy_pairs(self):
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("sw", rs1=13, rs2=31, imm=0)
        assert _rule_reason("load-store-chain", a, b) is None

    def test_width_mismatch_no_pair(self):
        """`ld` into `sw` truncates — a real idiom, but out of this frame's
        width-matched diagonal (it would cost 4x the block)."""
        a = make_insn("ld", rd=31, rs1=12, imm=0)
        b = make_insn("sw", rs1=13, rs2=31, imm=0)
        assert _rule_reason("load-store-chain", a, b) is not None

    def test_signed_load_pairs(self):
        """`lb` feeding `sb` writes the same byte as `lbu` would, so it is
        matched and encoded as the unsigned form."""
        a = make_insn("lb", rd=31, rs1=12, imm=0)
        b = make_insn("sb", rs1=13, rs2=31, imm=0)
        assert _rule_reason("load-store-chain", a, b) is None

    def test_offset_over_6bit_no_pair(self):
        a = make_insn("lw", rd=31, rs1=12, imm=252)      # 63*4, fits 6b
        b = make_insn("sw", rs1=13, rs2=31, imm=0)
        assert _rule_reason("load-store-chain", a, b) is None
        a = make_insn("lw", rd=31, rs1=12, imm=256)      # 64*4, over 6b
        assert _rule_reason("load-store-chain", a, b) is not None

    def test_value_must_die(self):
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("sw", rs1=13, rs2=31, imm=0)
        b.live_out = frozenset({31})                     # copy survives
        assert _rule_reason("load-store-chain", a, b) is not None

    def test_store_must_consume_the_load(self):
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("sw", rs1=13, rs2=14, imm=0)       # stores something else
        assert _rule_reason("load-store-chain", a, b) is not None


class TestLoadCallChain:
    """A loads a function pointer, B transfers through it, the pointer dies.

    The frame draws no rd field, so the link register is an op-select choice
    and the permitted set is whatever encoding.yaml's B op list spells.  Only
    `ra` is a call; `t1` is a linking jump (the PLT stub's spelling, chosen
    precisely because x6 is not a link register and so neither clobbers the
    caller's `ra` nor unbalances the return-address stack)."""

    def test_ra_link_pairs(self):
        """Virtual dispatch: the corpus writes the one-operand `jalr rs`."""
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("jalr", rd=1, rs1=31, imm=0)
        assert _rule_reason("load-call-chain", a, b) is None

    def test_t1_link_pairs(self):
        """The PLT stub's `jalr t1, rs` — a jump that saves a link."""
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("jalr", rd=6, rs1=31, imm=0)
        assert _rule_reason("load-call-chain", a, b) is None

    def test_t0_link_no_pair(self):
        """x5 is an ISA link register but the frame spells no codepoint for
        it, and it occurs zero times in every corpus.  Pairing it would encode
        a link into `ra` — a silent mis-encode, not a missed pair."""
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("jalr", rd=5, rs1=31, imm=0)
        assert _rule_reason("load-call-chain", a, b) is not None

    def test_jr_no_pair(self):
        """rd=x0 saves no link at all — a tail call, not this frame."""
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("jalr", rd=0, rs1=31, imm=0)
        assert _rule_reason("load-call-chain", a, b) is not None

    def test_link_set_comes_from_yaml(self):
        """The rule must not carry its own copy of the permitted registers."""
        from scheduler.imm_contracts import link_regs_for
        from scheduler.rules import _LOAD_CALL_LINK_REGS
        assert set(_LOAD_CALL_LINK_REGS) == set(
            link_regs_for("load-call-chain", "b"))

    def test_nonzero_b_offset_no_pair(self):
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("jalr", rd=1, rs1=31, imm=8)
        assert _rule_reason("load-call-chain", a, b) is not None

    def test_pointer_must_die(self):
        a = make_insn("lw", rd=31, rs1=12, imm=0)
        b = make_insn("jalr", rd=1, rs1=31, imm=0)
        b.live_out = frozenset({31})
        assert _rule_reason("load-call-chain", a, b) is not None

    def test_relocatable_offset_pairs_by_declaration(self):
        """An auipc-fed load pairs here, unlike everywhere else: the frame
        declares `accepts_pcrel_lo`, because its field spans the whole
        pcrel-lo range and the offset belongs to the packed layout, not to
        the one the corpus was linked for.  This is the PLT stub shape."""
        a = make_insn("lw", rd=31, rs1=31, imm=-0x528)
        b = make_insn("jalr", rd=6, rs1=31, imm=0)
        a.base_from_auipc = True
        assert _rule_reason("load-call-chain", a, b) is None

    def test_relocatable_offset_is_not_range_checked(self):
        """The corpus value is an artifact, so it is not checked at all —
        an offset far outside the field still pairs on this path."""
        a = make_insn("lw", rd=31, rs1=31, imm=0x7ffff)
        b = make_insn("jalr", rd=1, rs1=31, imm=0)
        a.base_from_auipc = True
        assert _rule_reason("load-call-chain", a, b) is None

    def test_ordinary_load_still_range_checked(self):
        """The declaration is scoped to auipc-fed loads: a real displacement
        is still measured against the 10-bit width-scaled field."""
        a = make_insn("lw", rd=31, rs1=12, imm=0x7ffff)
        b = make_insn("jalr", rd=1, rs1=31, imm=0)
        assert _rule_reason("load-call-chain", a, b) is not None

    def test_declaration_comes_from_yaml(self):
        from scheduler.imm_contracts import accepts_pcrel_lo
        assert accepts_pcrel_lo("load-call-chain")
        assert not accepts_pcrel_lo("load-base-branch-pair")


class TestMacroOpCarry:
    """`add rda, rs1, rs2 ; sltu rdb, rda, rs1-or-rs2` — the sum and its
    carry-out, riding macro-op-pair's row."""

    def test_carry_against_second_addend(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sltu", rd=11, rs1=10, rs2=13)
        assert can_pair(a, b) is None

    def test_carry_against_first_addend(self):
        """Either addend gives the same carry, so both spellings pair."""
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sltu", rd=11, rs1=10, rs2=12)
        assert can_pair(a, b) is None

    def test_addw_carry(self):
        a = make_insn("addw", rd=10, rs1=12, rs2=13)
        b = make_insn("sltu", rd=11, rs1=10, rs2=13)
        assert can_pair(a, b) is None

    def test_comparand_not_an_addend_no_pair(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sltu", rd=11, rs1=10, rs2=14)
        assert can_pair(a, b) is not None

    def test_compare_not_of_this_sum_no_pair(self):
        """sltu reading the addends, not the sum, is a different idiom."""
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sltu", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_carry_overwriting_the_sum_no_pair(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sltu", rd=10, rs1=10, rs2=13)
        assert can_pair(a, b) is not None

    def test_reversed_order_no_pair(self):
        """The carry cannot precede the sum it tests."""
        a = make_insn("sltu", rd=11, rs1=10, rs2=13)
        b = make_insn("add", rd=10, rs1=12, rs2=13)
        assert can_pair(a, b) is not None
