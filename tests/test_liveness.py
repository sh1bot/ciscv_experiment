"""
Unit tests for analysis/liveness.py

Updated to match the unified flat register namespace (§1 of PLAN.md):
  Integer registers: indices 0–31
  Float registers:   indices 32–63

Register index conventions used throughout:
  x0 = 0    ra/x1 = 1    sp/x2 = 2    t0/x5 = 5
  a0/x10=10  a1/x11=11   a2/x12=12   a3/x13=13
  a4/x14=14  a5/x15=15   a6/x16=16   a7/x17=17
  t1/x6=6   t2/x7=7     t3/x28=28   t4/x29=29  t5/x30=30  t6/x31=31
  s0/x8=8   s1/x9=9     s2/x18=18 … s11/x27=27
  fa0=42  fa1=43  fa2=44  fa3=45  fa4=46  fa5=47  fa6=48  fa7=49
  ft0=32  ft1=33  ...  ft7=39   ft8=60 ft9=61 ft10=62 ft11=63
  fs0=40  fs1=41  fs2=50 ... fs11=59
"""

import pytest
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Stub helpers — allow tests to be collected even before the real modules exist.
# ---------------------------------------------------------------------------

try:
    from isa.registers import (
        CALLER_SAVED, CALLEE_SAVED, ARG_REGS, RET_REGS,
    )
except ImportError:
    # Minimal hard-coded register sets matching the plan's unified namespace.
    RA   = 1;  SP   = 2;  T0 = 5;  T1 = 6;  T2 = 7
    A0   = 10; A1   = 11; A2 = 12; A3 = 13; A4 = 14; A5 = 15; A6 = 16; A7 = 17
    S0   = 8;  S1   = 9
    S2   = 18; S3   = 19; S4   = 20; S5 = 21; S6 = 22; S7 = 23
    S8   = 24; S9   = 25; S10  = 26; S11 = 27
    T3   = 28; T4   = 29; T5   = 30; T6 = 31
    FA0  = 42; FA1  = 43; FA2  = 44; FA3  = 45; FA4 = 46; FA5 = 47; FA6 = 48; FA7 = 49
    FT0  = 32; FT1  = 33; FT2  = 34; FT3  = 35; FT4 = 36; FT5 = 37; FT6 = 38; FT7 = 39
    FT8  = 60; FT9  = 61; FT10 = 62; FT11 = 63
    FS0  = 40; FS1  = 41
    FS2  = 50; FS3  = 51; FS4  = 52; FS5 = 53; FS6 = 54; FS7 = 55
    FS8  = 56; FS9  = 57; FS10 = 58; FS11 = 59

    CALLER_SAVED  = frozenset({RA, T0, T1, T2, A0, A1, A2, A3, A4, A5, A6, A7,
                                T3, T4, T5, T6,
                                FT0, FT1, FT2, FT3, FT4, FT5, FT6, FT7,
                                FA0, FA1, FA2, FA3, FA4, FA5, FA6, FA7,
                                FT8, FT9, FT10, FT11})
    CALLEE_SAVED  = frozenset({SP, S0, S1, S2, S3, S4, S5, S6, S7, S8, S9,
                                S10, S11,
                                FS0, FS1, FS2, FS3, FS4, FS5, FS6, FS7,
                                FS8, FS9, FS10, FS11})
    ARG_REGS      = frozenset({A0, A1, A2, A3, A4, A5, A6, A7,
                                FA0, FA1, FA2, FA3, FA4, FA5, FA6, FA7})
    RET_REGS      = frozenset({A0, A1, FA0, FA1})

# Derived sets for convenience in tests
_INT_CALLER_SAVED = frozenset(r for r in CALLER_SAVED if r < 32)
_INT_CALLEE_SAVED = frozenset(r for r in CALLEE_SAVED if r < 32)
_FLOAT_CALLER_SAVED = frozenset(r for r in CALLER_SAVED if r >= 32)
_FLOAT_CALLEE_SAVED = frozenset(r for r in CALLEE_SAVED if r >= 32)
_INT_ARG_REGS = frozenset(r for r in ARG_REGS if r < 32)
_FLOAT_ARG_REGS = frozenset(r for r in ARG_REGS if r >= 32)
_INT_RET_REGS = frozenset(r for r in RET_REGS if r < 32)
_FLOAT_RET_REGS = frozenset(r for r in RET_REGS if r >= 32)

# fa0–fa7 indices in the unified namespace
FA0, FA1, FA2, FA3, FA4, FA5, FA6, FA7 = 42, 43, 44, 45, 46, 47, 48, 49
FT0, FT1, FT2, FT3 = 32, 33, 34, 35
FS0, FS1 = 40, 41

try:
    from isa.instruction import Instruction
except ImportError:
    @dataclass
    class Instruction:
        mnemonic:      str
        operands:      list = field(default_factory=list)
        raw:           str  = ""
        label:         Optional[str] = None
        prefix_lines:  list = field(default_factory=list)
        rd:            Optional[int] = None
        rs1:           Optional[int] = None
        rs2:           Optional[int] = None
        rs3:           Optional[int] = None
        imm:           Optional[int] = None
        branch_target: Optional[str] = None
        is_unknown:    bool = False
        live_in:       frozenset = field(default_factory=frozenset)
        live_out:      frozenset = field(default_factory=frozenset)

        @property
        def uses_regs(self):
            regs = set()
            for r in (self.rs1, self.rs2, self.rs3):
                if r is not None and r != 0:
                    regs.add(r)
            return frozenset(regs)

        @property
        def defs_regs(self):
            if self.rd is not None and self.rd != 0:
                return frozenset({self.rd})
            return frozenset()

        @property
        def is_call(self):
            return (self.mnemonic in ("jal", "jalr") and
                    self.rd in (1, 5))  # ra or t0

        @property
        def is_return(self):
            return (self.mnemonic == "jalr" and
                    self.rd == 0 and self.rs1 == 1 and self.imm == 0)


try:
    from analysis.cfg import BasicBlock
except ImportError:
    @dataclass
    class BasicBlock:
        label:             Optional[str]
        instructions:      list = field(default_factory=list)
        successors:        list = field(default_factory=list)
        predecessors:      list = field(default_factory=list)
        is_function_entry: bool = False

try:
    from analysis.liveness import (
        compute_global_liveness,
        compute_local_liveness,
        LivenessResult,
    )
    LIVENESS_AVAILABLE = True
except ImportError:
    LIVENESS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not LIVENESS_AVAILABLE,
    reason="analysis.liveness not yet implemented",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_insn(mnemonic, rd=None, rs1=None, rs2=None, imm=None,
              branch_target=None, label=None, raw=None):
    """Convenience constructor for test instructions.

    All operands (including float) use the unified rd/rs1/rs2 with indices 0–63.
    """
    return Instruction(
        mnemonic=mnemonic,
        operands=[],
        raw=raw or mnemonic,
        label=label,
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
    """sw rs2, imm(rs1) — no rd."""
    return make_insn("sw", rs1=rs1, rs2=rs2, imm=imm)


def make_ret():
    """jalr x0, ra, 0"""
    return make_insn("jalr", rd=0, rs1=1, imm=0)


def make_jal_call(rd=1, target="callee"):
    """jal ra, target — a call."""
    return make_insn("jal", rd=rd, branch_target=target)


def make_jal_jump(target):
    """jal x0, target — an unconditional jump (not a call)."""
    return make_insn("jal", rd=0, branch_target=target)


def make_beq(rs1, rs2, target):
    return make_insn("beq", rs1=rs1, rs2=rs2, branch_target=target)


def make_tail_call(rs1):
    """jalr x0, rs1 with rs1 != ra — tail call per §2."""
    return make_insn("jalr", rd=0, rs1=rs1, imm=0)


def make_call_pseudo(target="callee"):
    """'call' pseudo-instruction — retained as-is per §4."""
    return make_insn("call", branch_target=target)


def make_tail_pseudo(target="callee"):
    """'tail' pseudo-instruction — retained as-is per §4."""
    return make_insn("tail", branch_target=target)


def make_fadd(rd, rs1, rs2):
    """fadd.s with unified register indices (rd/rs1/rs2 in 32–63)."""
    return make_insn("fadd.s", rd=rd, rs1=rs1, rs2=rs2)


def single_block_cfg(instructions, label="entry", is_function_entry=True,
                     successors=None, predecessors=None):
    bb = BasicBlock(
        label=label,
        instructions=instructions,
        successors=successors or [],
        predecessors=predecessors or [],
        is_function_entry=is_function_entry,
    )
    return bb


def link_blocks(*blocks):
    """Set up successor/predecessor edges in order."""
    for i in range(len(blocks) - 1):
        blocks[i].successors.append(blocks[i + 1])
        blocks[i + 1].predecessors.append(blocks[i])


# Convenience accessors for the unified LivenessResult
def live_in(result, label):
    return result.live_in[label]

def live_out(result, label):
    return result.live_out[label]


# ---------------------------------------------------------------------------
# §7 — Global pass: straight-line / single-block basics
# ---------------------------------------------------------------------------

class TestGlobalPassSingleBlock:

    def test_empty_block_before_ret_has_ret_seed(self):
        """A block whose only instruction is `ret` should have
        live_out seeded with RET_REGS ∪ CALLEE_SAVED before iteration."""
        ret = make_ret()
        bb = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        assert live_out(result, bb.label) == RET_REGS | CALLEE_SAVED

    def test_single_add_live_through(self):
        """  add a0, a1, a2
             ret
        live_out of block = RET_REGS | CALLEE_SAVED (ret seed)
        live_in  of block must include a1 and a2.
        """
        add  = make_add(rd=10, rs1=11, rs2=12)  # a0 = a1 + a2
        ret  = make_ret()
        bb   = single_block_cfg([add, ret])
        result = compute_global_liveness([bb])
        assert 11 in live_in(result, bb.label)
        assert 12 in live_in(result, bb.label)

    def test_def_kills_upstream_use(self):
        """  addi a2, a2, 1   ; redefines a2 (also reads it)
             add  a0, a1, a2
             ret
        a2 is live at block entry because the first insn reads it.
        """
        insn1 = make_addi(rd=12, rs1=12, imm=1)
        insn2 = make_add(rd=10, rs1=11, rs2=12)
        ret   = make_ret()
        bb    = single_block_cfg([insn1, insn2, ret])
        result = compute_global_liveness([bb])
        assert 12 in live_in(result, bb.label)

    def test_pure_def_kills_before_block_entry(self):
        """  addi a2, a3, 0   ; a2 = a3; a2 is newly defined
             add  a0, a1, a2
             ret
        a2 is NOT live at block entry.
        a3 IS live at block entry.
        """
        insn1 = make_addi(rd=12, rs1=13, imm=0)
        insn2 = make_add(rd=10, rs1=11, rs2=12)
        ret   = make_ret()
        bb    = single_block_cfg([insn1, insn2, ret])
        result = compute_global_liveness([bb])
        assert 12 not in live_in(result, bb.label)
        assert 13 in live_in(result, bb.label)

    def test_dead_def_not_live(self):
        """  addi t0, t0, 1   ; t0 written but never read after
             ret
        t0 (x5=5) is not in RET_REGS or CALLEE_SAVED.
        """
        insn = make_addi(rd=5, rs1=5, imm=1)
        ret  = make_ret()
        bb   = single_block_cfg([insn, ret])
        result = compute_global_liveness([bb])
        assert 5 not in (RET_REGS | CALLEE_SAVED)
        assert 5 not in live_out(result, bb.label)

    def test_sp_callee_saved_live_at_ret(self):
        """sp (x2) is CALLEE_SAVED — it must appear in live_out for a ret block."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        assert 2 in live_out(result, bb.label)

    def test_zero_register_never_live(self):
        """x0 is hardwired zero; it should never appear in any live set."""
        add = make_add(rd=0, rs1=10, rs2=11)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])
        result = compute_global_liveness([bb])
        assert 0 not in live_in(result, bb.label)
        assert 0 not in live_out(result, bb.label)

    def test_x0_source_operand_not_live(self):
        """addi a0, x0, 5 — x0 as source should not make x0 live."""
        insn = make_addi(rd=10, rs1=0, imm=5)
        ret  = make_ret()
        bb   = single_block_cfg([insn, ret])
        result = compute_global_liveness([bb])
        assert 0 not in live_in(result, bb.label)


# ---------------------------------------------------------------------------
# §7 — Global pass: ABI seeds for block terminals
# ---------------------------------------------------------------------------

class TestABISeedTerminals:

    def test_ret_seed(self):
        """ret → live_out seed = RET_REGS ∪ CALLEE_SAVED (unified)."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        assert live_out(result, bb.label) == RET_REGS | CALLEE_SAVED

    def test_ret_seed_float(self):
        """ret → live_out includes float RET_REGS (fa0, fa1 at indices 42, 43)."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        for r in _FLOAT_RET_REGS:
            assert r in live_out(result, bb.label)

    def test_tail_pseudo_seed_int(self):
        """tail pseudo → live_out seed includes a0–a7."""
        tail = make_tail_pseudo()
        bb   = single_block_cfg([tail])
        result = compute_global_liveness([bb])
        assert _INT_ARG_REGS.issubset(live_out(result, bb.label))

    def test_tail_pseudo_seed_float(self):
        """tail pseudo → live_out seed includes fa0–fa7 (indices 42–49)."""
        tail = make_tail_pseudo()
        bb   = single_block_cfg([tail])
        result = compute_global_liveness([bb])
        assert _FLOAT_ARG_REGS.issubset(live_out(result, bb.label))

    def test_call_pseudo_seed_int(self):
        """call pseudo → live_out seed = RET_REGS ∪ CALLEE_SAVED."""
        call = make_call_pseudo()
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        assert (RET_REGS | CALLEE_SAVED).issubset(live_out(result, bb.label))

    def test_call_pseudo_seed_float(self):
        """call pseudo → live_out includes float CALLEE_SAVED (fs0–fs11)."""
        call = make_call_pseudo()
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        assert _FLOAT_CALLEE_SAVED.issubset(live_out(result, bb.label))

    def test_jalr_t0_is_call(self):
        """jalr t0, rs1 — rd=t0(x5) counts as call-with-return."""
        call = make_insn("jalr", rd=5, rs1=20, imm=0)
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        assert _INT_CALLEE_SAVED.issubset(live_out(result, bb.label))

    def test_tail_call_jalr_seed(self):
        """jalr x0, t1 (rs1=t1≠ra) — tail call → live_out seed includes a0–a7."""
        tail = make_tail_call(rs1=6)
        bb   = single_block_cfg([tail])
        result = compute_global_liveness([bb])
        assert _INT_ARG_REGS.issubset(live_out(result, bb.label))

    def test_jal_ra_is_call_seed(self):
        """jal ra, target — call; live_out seed = RET_REGS ∪ CALLEE_SAVED."""
        call = make_jal_call(rd=1, target="foo")
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        assert (RET_REGS | CALLEE_SAVED).issubset(live_out(result, bb.label))
        assert _FLOAT_CALLEE_SAVED.issubset(live_out(result, bb.label))

    def test_plain_jal_x0_not_a_call(self):
        """jal x0, target — NOT a call; no ABI seed injection."""
        jmp = make_jal_jump(target="loop")
        succ = single_block_cfg([make_ret()], label="loop")
        bb   = single_block_cfg([jmp], label="before")
        bb.successors = [succ]
        succ.predecessors = [bb]
        result = compute_global_liveness([bb, succ])
        # Only RET_REGS | CALLEE_SAVED from ret should be in live_out of 'before'
        caller_only = CALLER_SAVED - CALLEE_SAVED - RET_REGS
        for r in caller_only:
            assert r not in live_out(result, bb.label), (
                f"reg {r} should not be live after plain jal x0")


# ---------------------------------------------------------------------------
# §7 — Global pass: iterative dataflow across multiple blocks
# ---------------------------------------------------------------------------

class TestGlobalPassMultiBlock:

    def test_two_block_live_through(self):
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        block_a = single_block_cfg([add], label="a")
        block_b = single_block_cfg([ret], label="b")
        link_blocks(block_a, block_b)

        result = compute_global_liveness([block_a, block_b])

        assert live_out(result, "a") == live_in(result, "b")
        assert live_out(result, "a") == RET_REGS | CALLEE_SAVED

        assert 11 in live_in(result, "a")
        assert 12 in live_in(result, "a")
        assert 10 not in live_in(result, "a")

    def test_join_point_live_union(self):
        insn_c0 = make_add(rd=10, rs1=11, rs2=12)
        ret_c   = make_ret()
        block_c = single_block_cfg([insn_c0, ret_c], label="c")

        insn_a  = make_add(rd=12, rs1=13, rs2=0)
        block_a = single_block_cfg([insn_a], label="a")

        insn_b  = make_add(rd=12, rs1=14, rs2=0)
        block_b = single_block_cfg([insn_b], label="b")

        block_a.successors = [block_c]
        block_b.successors = [block_c]
        block_c.predecessors = [block_a, block_b]

        result = compute_global_liveness([block_a, block_b, block_c])

        assert 11 in live_in(result, "c")
        assert 12 in live_in(result, "c")
        assert 13 in live_in(result, "a")
        assert 14 in live_in(result, "b")
        assert 13 not in live_in(result, "b")
        assert 14 not in live_in(result, "a")

    def test_back_edge_loop(self):
        beq    = make_beq(rs1=10, rs2=0, target="exit")
        header = single_block_cfg([beq], label="header")
        addi   = make_addi(rd=10, rs1=10, imm=-1)
        body   = single_block_cfg([addi], label="body")
        ret    = make_ret()
        exit_b = single_block_cfg([ret], label="exit")

        header.successors = [body, exit_b]
        body.successors   = [header]
        body.predecessors = [header]
        header.predecessors = [body]
        exit_b.predecessors = [header]

        result = compute_global_liveness([header, body, exit_b])

        assert 10 in live_in(result, "header")
        assert 10 in live_in(result, "body")

    def test_conditional_branch_live_union(self):
        beq     = make_beq(rs1=10, rs2=11, target="tgt")
        block_br = single_block_cfg([beq], label="br")

        add_ft  = make_add(rd=12, rs1=13, rs2=14)
        ret_ft  = make_ret()
        block_ft = single_block_cfg([add_ft, ret_ft], label="ft")

        add_tgt = make_add(rd=12, rs1=15, rs2=16)
        ret_tgt = make_ret()
        block_tgt = single_block_cfg([add_tgt, ret_tgt], label="tgt")

        link_blocks(block_br, block_ft)
        block_br.successors.append(block_tgt)
        block_tgt.predecessors.append(block_br)

        result = compute_global_liveness([block_br, block_ft, block_tgt])

        li_br = live_in(result, "br")
        for r in (10, 11, 13, 14, 15, 16):
            assert r in li_br, f"x{r} should be live at conditional branch block"

    def test_no_successors_terminates_function(self):
        tail  = make_tail_call(rs1=6)
        bb    = single_block_cfg([tail], label="tail_block")
        phantom = single_block_cfg([make_addi(rd=3, rs1=4, imm=0)], label="phantom")
        bb.successors = [phantom]
        phantom.predecessors = [bb]

        result = compute_global_liveness([bb, phantom])

        lo = live_out(result, "tail_block")
        assert _INT_ARG_REGS.issubset(lo)
        if 3 not in ARG_REGS:
            assert 3 not in lo


# ---------------------------------------------------------------------------
# §7 — ABI injection into use[B] / def[B]
# ---------------------------------------------------------------------------

class TestABIInjection:

    def test_call_clobbers_caller_saved(self):
        insn0 = make_addi(rd=13, rs1=13, imm=1)
        call  = make_call_pseudo(target="callee")
        add   = make_add(rd=10, rs1=11, rs2=0)
        ret   = make_ret()
        bb    = single_block_cfg([insn0, call, add, ret], label="b")
        result = compute_global_liveness([bb])
        for r in _INT_CALLEE_SAVED:
            assert r in live_in(result, bb.label) or r in live_out(result, bb.label), (
                f"callee-saved x{r} should survive across call")

    def test_call_implicit_uses_arg_regs(self):
        set_a0  = make_addi(rd=10, rs1=0, imm=1)
        set_a1  = make_addi(rd=11, rs1=0, imm=2)
        call    = make_call_pseudo()
        ret     = make_ret()
        bb      = single_block_cfg([set_a0, set_a1, call, ret])
        result  = compute_global_liveness([bb])
        for r in _INT_ARG_REGS:
            if r not in (10, 11):
                assert r in live_in(result, bb.label), (
                    f"a{r-10} should be live_in (implicit call use, not redefined)")

    def test_call_does_not_kill_callee_saved(self):
        set_s0 = make_addi(rd=8, rs1=0, imm=99)
        call   = make_call_pseudo()
        use_s0 = make_add(rd=10, rs1=8, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_s0, call, use_s0, ret])
        result = compute_global_liveness([bb])
        assert 8 in live_in(result, bb.label)

    def test_return_implicit_uses_ret_regs(self):
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])
        result = compute_global_liveness([bb])
        assert 10 in live_out(result, bb.label) or 10 in live_in(result, bb.label)

    def test_float_callee_saved_survives_call(self):
        """fs0 (float index 40) must be live across a call (CALLEE_SAVED)."""
        call  = make_call_pseudo()
        # fadd.s fa0, fs0, fa1 — using unified indices: fa0=42, fs0=40, fa1=43
        fadd  = make_fadd(rd=FA0, rs1=FS0, rs2=FA1)
        ret   = make_ret()
        bb    = single_block_cfg([call, fadd, ret])
        result = compute_global_liveness([bb])
        # fs0 (float index 40) should be live into the block
        assert FS0 in live_in(result, bb.label)


# ---------------------------------------------------------------------------
# §7 — Local pass: per-instruction live_in / live_out
# ---------------------------------------------------------------------------

class TestLocalPass:

    def test_local_pass_seeds_from_global_live_out(self):
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert ret.live_out == RET_REGS | CALLEE_SAVED
        assert (RET_REGS | CALLEE_SAVED).issubset(ret.live_in)
        assert add.live_out == ret.live_in
        assert 11 in add.live_in
        assert 12 in add.live_in

    def test_local_single_instruction_straight_line(self):
        lw  = make_lw(rd=10, rs1=2, imm=0)    # a0 = mem[sp]
        add = make_add(rd=11, rs1=10, rs2=12)  # a1 = a0 + a2
        ret = make_ret()
        bb  = single_block_cfg([lw, add, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert 2 in lw.live_in
        assert 10 in lw.live_out
        assert 10 not in lw.live_in
        assert 12 in add.live_in

    def test_local_dead_register_not_live_past_def(self):
        insn = make_addi(rd=6, rs1=6, imm=1)  # t1=t1+1
        ret  = make_ret()
        bb   = single_block_cfg([insn, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert 6 in insn.live_in
        assert 6 not in insn.live_out or 6 not in (RET_REGS | CALLEE_SAVED)

    def test_local_float_liveness(self):
        """fadd.s fa0, fa1, fa2 — unified: rd=42, rs1=43, rs2=44.
        fa1, fa2 should be in live_in of fadd instruction.
        fa0 should be in live_out (it's in RET_REGS).
        """
        fadd = make_fadd(rd=FA0, rs1=FA1, rs2=FA2)
        ret  = make_ret()
        bb   = single_block_cfg([fadd, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert FA1 in fadd.live_in   # fa1=43
        assert FA2 in fadd.live_in   # fa2=44
        assert FA0 in fadd.live_out  # fa0=42 — in RET_REGS

    def test_local_pass_reassign_after_reorder(self):
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)
        first_live_in  = add.live_in
        first_live_out = add.live_out

        bb.instructions = [add, ret]
        compute_local_liveness(bb, global_result)
        assert add.live_in  == first_live_in
        assert add.live_out == first_live_out

    def test_local_pass_per_instruction_live_out_count(self):
        insn0 = make_addi(rd=10, rs1=0, imm=1)
        insn1 = make_sw(rs1=2, rs2=10, imm=0)
        ret   = make_ret()
        bb    = single_block_cfg([insn0, insn1, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        for insn in bb.instructions:
            assert isinstance(insn.live_in, frozenset)
            assert isinstance(insn.live_out, frozenset)

        assert 2 in insn1.live_in
        assert 10 in insn1.live_in


# ---------------------------------------------------------------------------
# §7 — Callee-saved register survival across calls
# ---------------------------------------------------------------------------

class TestCalleeSavedSurvival:

    def test_callee_saved_live_in_across_call_chain(self):
        set_s0 = make_addi(rd=8, rs1=0, imm=5)
        call   = make_call_pseudo("foo")
        use_s0 = make_add(rd=10, rs1=8, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_s0, call, use_s0, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert 8 in use_s0.live_in
        assert 8 in call.live_out

    def test_caller_saved_killed_by_call(self):
        set_t1 = make_addi(rd=6, rs1=0, imm=7)
        call   = make_call_pseudo("foo")
        add    = make_add(rd=10, rs1=11, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_t1, call, add, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # t1 is caller-saved; after the call it is clobbered.
        assert 6 not in call.live_out

    def test_multiple_calls_callee_saved_live_throughout(self):
        set_s1  = make_addi(rd=9, rs1=0, imm=3)
        call1   = make_call_pseudo("foo")
        call2   = make_call_pseudo("bar")
        use_s1  = make_add(rd=10, rs1=9, rs2=0)
        ret     = make_ret()
        bb      = single_block_cfg([set_s1, call1, call2, use_s1, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert 9 in call1.live_in
        assert 9 in call1.live_out
        assert 9 in call2.live_in
        assert 9 in call2.live_out
        assert 9 in use_s1.live_in


# ---------------------------------------------------------------------------
# §7 — Float register liveness (unified namespace)
# ---------------------------------------------------------------------------

class TestFloatLiveness:

    def test_float_use_propagates_to_block_live_in(self):
        """fadd.s fa0, fa1, fa2 — fa1=43, fa2=44 should be in live_in."""
        fadd = make_fadd(rd=FA0, rs1=FA1, rs2=FA2)
        ret  = make_ret()
        bb   = single_block_cfg([fadd, ret])
        result = compute_global_liveness([bb])
        assert FA1 in live_in(result, bb.label)
        assert FA2 in live_in(result, bb.label)

    def test_float_def_kills(self):
        """Two fadd.s instructions both writing fa0; first def is killed."""
        fadd1 = make_fadd(rd=FA0, rs1=FA1, rs2=FA2)
        fadd2 = make_fadd(rd=FA0, rs1=FA3, rs2=FA4)
        ret   = make_ret()
        bb    = single_block_cfg([fadd1, fadd2, ret])
        result = compute_global_liveness([bb])
        assert FA0 not in live_in(result, bb.label)

    def test_int_and_float_same_numeric_index_independent(self):
        """a0 (int=10) and fa0 (float=42) are different registers in unified namespace.
        add a0, a1, a2 and fadd.s fa0, fa1, fa2 both live_in a1/a2 and fa1/fa2.
        """
        iadd = make_add(rd=10, rs1=11, rs2=12)
        fadd = make_fadd(rd=FA0, rs1=FA1, rs2=FA2)
        ret  = make_ret()
        bb   = single_block_cfg([iadd, fadd, ret])
        result = compute_global_liveness([bb])
        # Integer a1=11, a2=12
        assert 11 in live_in(result, bb.label)
        assert 12 in live_in(result, bb.label)
        # Float fa1=43, fa2=44
        assert FA1 in live_in(result, bb.label)
        assert FA2 in live_in(result, bb.label)

    def test_float_ret_regs_in_ret_seed(self):
        """ret seed includes fa0 (42) and fa1 (43) — float RET_REGS."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        for r in _FLOAT_RET_REGS:
            assert r in live_out(result, bb.label)

    def test_float_caller_saved_killed_by_call(self):
        """ft0 (float index 32) is CALLER_SAVED; must not be live after call."""
        fadd1 = make_fadd(rd=FT0, rs1=FT1, rs2=FT2)   # ft0 = ft1+ft2
        call  = make_call_pseudo("foo")
        fadd2 = make_fadd(rd=FA0, rs1=FA1, rs2=FA2)
        ret   = make_ret()
        bb    = single_block_cfg([fadd1, call, fadd2, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # ft0 (float 32) is CALLER_SAVED; should NOT be live_out of call
        assert FT0 not in call.live_out


# ---------------------------------------------------------------------------
# §7 — Edge cases and regression tests
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_single_instruction_block_just_ret(self):
        """Minimal function: single ret instruction."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        global_result = result
        compute_local_liveness(bb, global_result)

        assert ret.live_out == RET_REGS | CALLEE_SAVED
        assert isinstance(ret.live_in, frozenset)

    def test_empty_block(self):
        """A block with zero instructions."""
        bb = single_block_cfg([], label="empty")
        result = compute_global_liveness([bb])
        assert live_in(result, "empty") == frozenset()
        assert live_out(result, "empty") == frozenset()

    def test_store_does_not_define_rd(self):
        """sw has no rd; it must not add anything to def[B]."""
        sw  = make_sw(rs1=2, rs2=10, imm=0)
        ret = make_ret()
        bb  = single_block_cfg([sw, ret])
        result = compute_global_liveness([bb])
        assert 2 in live_in(result, bb.label)
        assert 10 in live_in(result, bb.label)

    def test_unknown_instruction_conservative_register_handling(self):
        """Unknown instruction: first register-like operand is def, rest are uses."""
        unk = Instruction(
            mnemonic="bogus", operands=["a0", "a1", "a2"], raw="bogus a0, a1, a2",
            rd=10, rs1=11, rs2=12, is_unknown=True,
        )
        ret = make_ret()
        bb  = single_block_cfg([unk, ret])
        result = compute_global_liveness([bb])
        assert 11 in live_in(result, bb.label)
        assert 12 in live_in(result, bb.label)
        assert 10 not in live_in(result, bb.label)

    def test_worklist_converges(self):
        """A loop with a back-edge must converge to a fixed point."""
        beq    = make_beq(rs1=10, rs2=0, target="exit")
        header = single_block_cfg([beq], label="header")
        addi   = make_addi(rd=10, rs1=10, imm=-1)
        body   = single_block_cfg([addi], label="body")
        ret    = make_ret()
        exit_b = single_block_cfg([ret], label="exit")

        header.successors   = [body, exit_b]
        header.predecessors = [body]
        body.successors     = [header]
        body.predecessors   = [header]
        exit_b.predecessors = [header]

        result = compute_global_liveness([header, body, exit_b])
        assert result is not None

    def test_multiple_functions_independent(self):
        """Two independent functions must not cross-contaminate liveness."""
        ret1 = make_ret()
        fn1  = single_block_cfg([ret1], label="fn1", is_function_entry=True)

        insn = make_addi(rd=10, rs1=15, imm=0)
        ret2 = make_ret()
        fn2  = single_block_cfg([insn, ret2], label="fn2", is_function_entry=True)

        result = compute_global_liveness([fn1, fn2])

        assert 15 not in live_in(result, "fn1")
        assert 15 in live_in(result, "fn2")

    def test_indirect_jump_no_successors_for_liveness(self):
        """jalr x0, rs1 — computed goto; terminates_function=True."""
        jmp = make_insn("jalr", rd=0, rs1=15, imm=0)
        bb  = single_block_cfg([jmp], label="indirect")
        phantom = single_block_cfg([make_addi(rd=3, rs1=4, imm=0)], label="phantom2")
        bb.successors = [phantom]
        phantom.predecessors = [bb]

        result = compute_global_liveness([bb, phantom])
        if 3 not in ARG_REGS:
            assert 3 not in live_out(result, "indirect")

    def test_liveness_result_is_frozensets(self):
        """live_in / live_out stored on Instruction objects must be frozenset."""
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)
        for insn in [add, ret]:
            assert isinstance(insn.live_in,  frozenset)
            assert isinstance(insn.live_out, frozenset)

    def test_liveness_result_interface(self):
        """compute_global_liveness must return an object with live_in and live_out
        keyed by block label (str)."""
        ret = make_ret()
        bb  = single_block_cfg([ret], label="entry")
        result = compute_global_liveness([bb])
        assert hasattr(result, "live_in")
        assert hasattr(result, "live_out")
        assert "entry" in result.live_in


# ---------------------------------------------------------------------------
# §7 — live_out seed for call mid-block
# ---------------------------------------------------------------------------

class TestCallMidBlock:

    def test_call_mid_block_live_out_seed_at_call_site(self):
        set_a0 = make_addi(rd=10, rs1=0, imm=1)
        call   = make_call_pseudo("foo")
        use_a0 = make_add(rd=11, rs1=10, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_a0, call, use_a0, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert (RET_REGS | CALLEE_SAVED).issubset(call.live_out)

    def test_ret_regs_live_after_call(self):
        call  = make_call_pseudo("foo")
        use_a0 = make_lw(rd=5, rs1=10, imm=0)   # t0 = mem[a0]
        ret   = make_ret()
        bb    = single_block_cfg([call, use_a0, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert 10 in call.live_out   # a0 is live after call
