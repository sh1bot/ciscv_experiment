"""
Tests for scheduler/pairing.py — pairing rules and can_pair().

Only rsd-alu-pair exists. All other combinations should be rejected.
"""

import pytest
from isa.instruction import Instruction
from scheduler.pairing import (
    can_pair, greedy_pair, stamp_slot_eligibility, stamp_solo_reasons, RULES,
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
        """slli is not in the rsd-alu supported set — even rsd-form should not pair."""
        a = make_insn("slli", rd=10, rs1=10, imm=1)   # is_rsd: rd==rs1
        b = make_insn("slli", rd=12, rs1=12, imm=2)
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
# dual-op-pair: two ops from a canonical tuple sharing inputs, distinct outputs
# ---------------------------------------------------------------------------

class TestDualOpPair:

    # --- arith2 ---

    def test_add_sub_same_sources_pairs(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sub", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_add_sub_different_sources_no_pair(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sub", rd=11, rs1=14, rs2=15)
        assert can_pair(a, b) is not None

    def test_add_sub_swapped_operand_order_no_pair(self):
        """sub is non-commutative: rs1/rs2 must match positionally."""
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sub", rd=11, rs1=13, rs2=12)
        assert can_pair(a, b) is not None

    def test_add_sub_same_dest_no_pair(self):
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sub", rd=10, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_a_clobbers_shared_source_no_pair(self):
        """A-slot op writing a shared source corrupts B's read.

        min/max chosen so no chain/rsd rule applies — isolates dual-op-pair.
        """
        a = make_insn("min", rd=12, rs1=12, rs2=13)   # rd == shared rs1
        b = make_insn("max", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_b_writes_shared_source_canonical_ok(self):
        """Canonical order: B writing a shared source is a legal WAR."""
        a = make_insn("add", rd=10, rs1=12, rs2=13)
        b = make_insn("sub", rd=12, rs1=12, rs2=13)   # rd == shared rs1
        assert can_pair(a, b) is None

    def test_reversed_independent_pairs(self):
        """Reverse order accepted when fully independent."""
        a = make_insn("sub", rd=11, rs1=12, rs2=13)
        b = make_insn("add", rd=10, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_reversed_with_conflict_no_pair(self):
        """Reverse order rejected when a dest is a shared source."""
        a = make_insn("sub", rd=11, rs1=12, rs2=13)
        b = make_insn("add", rd=12, rs1=12, rs2=13)   # b.rd in a.uses
        assert can_pair(a, b) is not None

    def test_min_max_pairs(self):
        a = make_insn("min", rd=10, rs1=12, rs2=13)
        b = make_insn("max", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_minu_maxu_pairs(self):
        a = make_insn("minu", rd=10, rs1=12, rs2=13)
        b = make_insn("maxu", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

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

    # --- load_addi ---

    def test_load_addi_zero_offset_pairs(self):
        """Zero load offset + valid stride addi: post-increment load."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=12, imm=16)   # stride = 2*8, valid uimm5*8
        assert can_pair(a, b) is None

    def test_load_addi_nonzero_offset_no_pair(self):
        """Non-zero load offset is not allowed."""
        a = make_insn("ld", rd=10, rs1=12, imm=16)
        b = make_insn("addi", rd=11, rs1=12, imm=16)
        assert can_pair(a, b) is not None

    def test_load_addi_stride_not_width_multiple_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=12, imm=12)   # 12 not a multiple of 8
        assert can_pair(a, b) is not None

    def test_load_addi_zero_stride_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=12, imm=0)
        assert can_pair(a, b) is not None

    def test_load_addi_base_mismatch_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=14, imm=8)
        assert can_pair(a, b) is not None

    def test_lw_addi_width4(self):
        a = make_insn("lw", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=12, imm=4)    # stride = 1*4
        assert can_pair(a, b) is None

    def test_lb_addi_no_longer_supported(self):
        """lb+addi is not in the tuple table (only 32/64-bit variants)."""
        a = make_insn("lb", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=11, rs1=12, imm=1)
        assert can_pair(a, b) is not None

    def test_load_addi_reversed_pairs(self):
        """Reversed (addi, ld) is accepted when load offset is zero."""
        a = make_insn("addi", rd=11, rs1=12, imm=16)
        b = make_insn("ld", rd=10, rs1=12, imm=0)
        assert can_pair(a, b) is None

    # --- store_addi ---

    def test_store_addi_sd_zero_offset_pairs(self):
        """sd + addi: post-increment store, zero offset, stride = data width."""
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=8)    # stride = 1*8
        assert can_pair(a, b) is None

    def test_store_addi_sw_pairs(self):
        a = make_insn("sw", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=4)    # stride = 1*4
        assert can_pair(a, b) is None

    def test_store_addi_nonzero_offset_no_pair(self):
        a = make_insn("sd", rs1=12, rs2=13, imm=8)
        b = make_insn("addi", rd=12, rs1=12, imm=8)
        assert can_pair(a, b) is not None

    def test_store_addi_base_mismatch_no_pair(self):
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=14, rs1=14, imm=8)
        assert can_pair(a, b) is not None

    def test_store_addi_stride_not_width_multiple_no_pair(self):
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=12)   # 12 not a multiple of 8
        assert can_pair(a, b) is not None

    # --- load_shadd ---

    def test_load_shadd_base_match_zero_offset(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("sh3add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_load_shadd_nonzero_offset_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=8)
        b = make_insn("sh3add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_load_shadd_base_mismatch_no_pair(self):
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("sh3add", rd=11, rs1=14, rs2=13)
        assert can_pair(a, b) is not None

    def test_load_shadd_wrong_width_no_pair(self):
        """lw pairs with sh2add, not sh3add."""
        a = make_insn("lw", rd=10, rs1=12, imm=0)
        b = make_insn("sh3add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_lw_sh2add_pairs(self):
        a = make_insn("lw", rd=10, rs1=12, imm=0)
        b = make_insn("sh2add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_load_shadd_a_feeds_b_no_pair(self):
        """Load dest == shadd's second source: A feeds B (chain, not dual)."""
        a = make_insn("ld", rd=13, rs1=12, imm=0)
        b = make_insn("sh3add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    # --- store_shadd ---

    def test_store_shadd_regset_match(self):
        """sd + sh3add with zero offset: store and compute index from same regs."""
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("sh3add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_store_shadd_regset_match_swapped(self):
        """Store {base,value} vs shadd sources compared as a set."""
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("sh3add", rd=11, rs1=13, rs2=12)
        assert can_pair(a, b) is None

    def test_store_shadd_nonzero_offset_no_pair(self):
        """Non-zero store offset is not allowed."""
        a = make_insn("sd", rs1=12, rs2=13, imm=8)
        b = make_insn("sh3add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is not None

    def test_store_shadd_regs_mismatch_no_pair(self):
        a = make_insn("sd", rs1=12, rs2=13, imm=0)
        b = make_insn("sh3add", rd=11, rs1=12, rs2=14)
        assert can_pair(a, b) is not None

    def test_sw_sh2add_pairs(self):
        a = make_insn("sw", rs1=12, rs2=13, imm=0)
        b = make_insn("sh2add", rd=11, rs1=12, rs2=13)
        assert can_pair(a, b) is None

    def test_store_shadd_shadd_clobbers_store_reg_reversed_no_pair(self):
        """Reversed (shadd, store): shadd dest is a store source -> corrupts store."""
        a = make_insn("sh3add", rd=12, rs1=12, rs2=13)   # rd == store base
        b = make_insn("sd", rs1=12, rs2=13, imm=0)
        assert can_pair(a, b) is not None

    # --- mem_pair ---

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
        """mem_pair works with any shared base register, not just sp."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("ld", rd=11, rs1=12, imm=8)
        assert can_pair(a, b) is None

    def test_mem_pair_different_base_no_pair(self):
        """mem_pair requires the same base register on both ops."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("ld", rd=11, rs1=13, imm=8)
        assert can_pair(a, b) is not None

    def test_mem_pair_offset_gap_wrong_no_pair(self):
        """Offsets differ by 24, not the 8-byte ld width.
        A's offset is nonzero so base-chain-load-pair (zero A offset) is out."""
        a = make_insn("ld", rd=10, rs1=2, imm=8)
        b = make_insn("ld", rd=11, rs1=2, imm=32)
        assert can_pair(a, b) is not None

    def test_mem_pair_same_dest_no_pair(self):
        """A's offset is nonzero so base-chain-load-pair (zero A offset) is out."""
        a = make_insn("ld", rd=10, rs1=2, imm=8)
        b = make_insn("ld", rd=10, rs1=2, imm=16)
        assert can_pair(a, b) is not None

    def test_mem_pair_mixed_widths_no_pair(self):
        """ld and lw are different mnemonics — not a recognised mem_pair tuple.
        A's offset is nonzero so base-chain-load-pair (zero A offset) is out."""
        a = make_insn("ld", rd=10, rs1=2, imm=8)
        b = make_insn("lw", rd=11, rs1=2, imm=16)
        assert can_pair(a, b) is not None

    def test_mem_pair_sp_8bit_offset_pairs(self):
        """sp-relative ld pair with 8-bit scaled offset (>5-bit range) pairs."""
        # scaled offset 183 = 0xB7, raw = 183*8 = 1464, needs 8 bits
        a = make_insn("ld", rd=10, rs1=2, imm=1464)
        b = make_insn("ld", rd=11, rs1=2, imm=1472)
        assert can_pair(a, b) is None

    def test_mem_pair_non_sp_5bit_limit(self):
        """Non-sp base uses 5-bit limit; offset beyond that fails."""
        # scaled offset 32 > 31, so fails 5-bit; rs1=12 (not sp)
        a = make_insn("ld", rd=10, rs1=12, imm=256)
        b = make_insn("ld", rd=11, rs1=12, imm=264)
        assert can_pair(a, b) is not None

    def test_mem_pair_non_sp_5bit_in_range_pairs(self):
        """Non-sp base within 5-bit range still pairs."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("ld", rd=11, rs1=12, imm=8)
        assert can_pair(a, b) is None


# ---------------------------------------------------------------------------
# indep_pair (li+li, mv+mv, mv+li)
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

    def test_epilogue_reversed_order_pairs(self):
        """ret before addi sp,sp also pairs (order-insensitive)."""
        a = make_insn("ret",  rd=0, rs1=1, imm=0)
        b = make_insn("addi", rd=2, rs1=2, imm=48)
        assert can_pair(a, b) is None

    def test_epilogue_non_sp_addi_no_pair(self):
        """addi to a non-sp register doesn't qualify."""
        a = make_insn("addi", rd=10, rs1=10, imm=48)
        b = make_insn("ret",  rd=0, rs1=1, imm=0)
        assert can_pair(a, b) is not None

    def test_epilogue_negative_sp_no_pair(self):
        """Negative sp adjustment (prologue) doesn't qualify."""
        a = make_insn("addi", rd=2, rs1=2, imm=-48)
        b = make_insn("ret",  rd=0, rs1=1, imm=0)
        assert can_pair(a, b) is not None

    def test_epilogue_sp_adjust_out_of_range_no_pair(self):
        """sp adjustment beyond 7-bit uimm×16 (>2032) doesn't pair."""
        a = make_insn("addi", rd=2, rs1=2, imm=2048)  # 128×16, needs 8 bits
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

    def test_add_slt_rsd_pairs(self):
        """add in RSD form; slt uses the result as left operand."""
        a = make_insn("add", rd=12, rs1=12, rs2=13)
        b = make_insn("slt", rd=10, rs1=12, rs2=14)
        assert can_pair(a, b) is None

    def test_add_commutative_rsd_pairs(self):
        """add is commutative: rd==rs2 is also RSD form."""
        a = make_insn("add", rd=13, rs1=12, rs2=13)   # rd==rs2
        b = make_insn("slt", rd=10, rs1=13, rs2=14)
        assert can_pair(a, b) is None

    def test_addi_ld_not_rsd_no_pair(self):
        """A does not update its own source: not RSD form."""
        a = make_insn("addi", rd=11, rs1=12, imm=8)   # rd != rs1
        b = make_insn("ld", rd=10, rs1=11, imm=0)
        assert can_pair(a, b) is not None

    def test_addi_ld_b_reads_wrong_reg_no_pair(self):
        """B rs1 does not match A's rd."""
        a = make_insn("addi", rd=12, rs1=12, imm=8)
        b = make_insn("ld", rd=10, rs1=14, imm=0)     # loads from a3, not a1
        assert can_pair(a, b) is not None

    def test_addi_ld_nonzero_offset_no_pair(self):
        """B memory offset must be zero."""
        a = make_insn("addi", rd=12, rs1=12, imm=8)
        b = make_insn("ld", rd=10, rs1=12, imm=8)
        assert can_pair(a, b) is not None

    def test_addi_ld_same_rd_no_pair(self):
        """A and B must not write the same register."""
        a = make_insn("addi", rd=12, rs1=12, imm=8)
        b = make_insn("ld", rd=12, rs1=12, imm=0)     # B clobbers the pointer
        assert can_pair(a, b) is not None

    def test_post_inc_pairs_as_dual_not_pre_inc(self):
        """(ld, addi) is the canonical post-increment order and matches dual-op-pair/load_addi.
        pre-inc-pair only matches (addi, ld), not the reverse."""
        a = make_insn("ld", rd=10, rs1=12, imm=0)
        b = make_insn("addi", rd=12, rs1=12, imm=8)
        assert can_pair(a, b) is None  # accepted — but by dual-op-pair, not pre-inc-pair

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
        assert any("A-slot disqualified: is_unknown" in r for r in unk1.solo_reasons)
        assert any("B-slot disqualified: is_unknown" in r for r in unk2.solo_reasons)


class TestNoApplicableRule:

    def test_load_alu_does_not_pair(self):
        lw = make_lw(10, 11)
        add = make_add(12, 13, 14)
        assert can_pair(lw, add) is not None

    def test_alu_store_does_not_pair(self):
        # sw stores x13, not the add's result x10, so store-chain-alu-pair
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

    def test_intrinsic_reasons_from_stamp(self):
        """stamp_solo_reasons gives each instruction its own intrinsic reasons."""
        a = make_add(10, 11, 12)   # not rsd-form
        b = make_add(13, 14, 15)
        stamp_solo_reasons([a, b])
        assert any("not RSD form" in r for r in a.solo_reasons)
        assert any("not RSD form" in r for r in b.solo_reasons)

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
        b.solo_reasons = set()
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

    def test_non_sp_base_no_pair(self):
        a = make_ld(10, 12, imm=64)   # base x12, not sp
        b = make_add(10, 10, 11)
        assert can_pair(a, b) is not None

    def test_offset_over_8bit_no_pair(self):
        # ld scale 8: 8-bit max scaled offset = 255*8 = 2040; 2048 is too big
        a = make_ld(10, 2, imm=2048)
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

    def test_non_sp_base_no_pair(self):
        a = make_add(10, 11, 12)
        b = make_sd(rs1=14, rs2=10, imm=64)  # base x14, not sp
        assert can_pair(a, b) is not None

    def test_offset_over_8bit_no_pair(self):
        a = make_add(10, 11, 12)
        b = make_sd(rs1=2, rs2=10, imm=2048)
        assert can_pair(a, b) is not None


class TestDerefChainLoadPair:
    """A = load rtmp, imm10(rb); B = load rd, 0(rtmp); rtmp dead after B."""

    def test_basic_pairs(self):
        a = make_ld(10, 12, imm=512)   # ld x10, 512(x12)
        b = make_ld(11, 10, imm=0)     # ld x11, 0(x10)
        assert can_pair(a, b) is None

    def test_b_offset_nonzero_no_pair(self):
        a = make_ld(10, 12, imm=512)
        b = make_ld(11, 10, imm=8)     # B must dereference at offset zero
        assert can_pair(a, b) is not None

    def test_b_base_mismatch_no_pair(self):
        a = make_ld(10, 12, imm=512)
        b = make_ld(11, 13, imm=0)     # B base is not A's result
        assert can_pair(a, b) is not None

    def test_a_offset_over_10bit_no_pair(self):
        # ld scale 8: 10-bit max scaled = 1023*8 = 8184; 8192 too big
        a = make_ld(10, 12, imm=8192)
        b = make_ld(11, 10, imm=0)
        assert can_pair(a, b) is not None

    def test_a_auipc_got_load_no_pair(self):
        # A is the load half of an auipc+load GOT access; its offset is a
        # %pcrel_lo relocation, not a real displacement.
        a = make_ld(10, 12, imm=512)
        a.base_from_auipc = True
        b = make_ld(11, 10, imm=0)
        assert can_pair(a, b) is not None


class TestBaseChainLoadPair:
    """A = load rtmp, 0(rb); B = load rd, imm10(rtmp); rtmp dead after B."""

    def test_basic_pairs(self):
        a = make_ld(10, 12, imm=0)     # ld x10, 0(x12)
        b = make_ld(11, 10, imm=512)   # ld x11, 512(x10)
        assert can_pair(a, b) is None

    def test_a_offset_nonzero_no_pair(self):
        a = make_ld(10, 12, imm=8)     # A must dereference at offset zero
        b = make_ld(11, 10, imm=512)
        assert can_pair(a, b) is not None

    def test_b_base_mismatch_no_pair(self):
        a = make_ld(10, 12, imm=0)
        b = make_ld(11, 13, imm=512)   # B base is not A's result
        assert can_pair(a, b) is not None

    def test_b_offset_over_10bit_no_pair(self):
        a = make_ld(10, 12, imm=0)
        b = make_ld(11, 10, imm=8192)
        assert can_pair(a, b) is not None

    def test_a_auipc_got_load_no_pair(self):
        # A is the load half of an auipc+load GOT access — excluded.
        a = make_ld(10, 12, imm=0)
        a.base_from_auipc = True
        b = make_ld(11, 10, imm=512)
        assert can_pair(a, b) is not None


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
