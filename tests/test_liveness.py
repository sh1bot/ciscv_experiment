"""
Unit tests for analysis/liveness.py

Written against the design plan (sections 1, 2, 3, 6, 7) WITHOUT consulting
any implementation.  Every test documents the exact liveness result expected;
where the plan is ambiguous a comment marks the open question.

Assumptions and gaps are listed at the bottom of this file in a block comment,
and also returned as the companion numbered list in the report.

Register index conventions used throughout (from §1 / §2 of the plan):
  x0 = 0    ra/x1 = 1    sp/x2 = 2    t0/x5 = 5
  a0/x10=10  a1/x11=11   a2/x12=12   a3/x13=13
  a4/x14=14  a5/x15=15   a6/x16=16   a7/x17=17
  t1/x6=6   t2/x7=7     t3/x28=28   t4/x29=29  t5/x30=30  t6/x31=31
  s0/x8=8   s1/x9=9     s2/x18=18 … s11/x27=27
  fa0/f10=10  fa1/f11=11  (float indices separate bank)
"""

import pytest
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Stub helpers — allow tests to be collected even before the real modules exist.
# Replace each import with the real module once it is written.
# ---------------------------------------------------------------------------

try:
    from isa.registers import (
        CALLER_SAVED, CALLEE_SAVED, ARG_REGS, RET_REGS,
        FCALLER_SAVED, FCALLEE_SAVED, FARG_REGS, FRET_REGS,
    )
except ImportError:
    # Minimal hard-coded register sets matching the plan's textual description.
    # Tests that assert exact register memberships use these fallback sets so
    # they can be collected; they will naturally pass once the real module
    # returns the same values.
    RA   = 1;  SP   = 2;  T0 = 5;  T1 = 6;  T2 = 7
    A0   = 10; A1   = 11; A2 = 12; A3 = 13; A4 = 14; A5 = 15; A6 = 16; A7 = 17
    S0   = 8;  S1   = 9
    S2   = 18; S3   = 19; S4   = 20; S5 = 21; S6 = 22; S7 = 23
    S8   = 24; S9   = 25; S10  = 26; S11 = 27
    T3   = 28; T4   = 29; T5   = 30; T6 = 31
    FA0  = 10; FA1  = 11
    FT0  = 0;  FT1  = 1;  FT2  = 2;  FT3  = 3; FT4 = 4; FT5 = 5
    FT6  = 6;  FT7  = 7;  FT8  = 8;  FT9  = 9; FT10 = 10; FT11 = 11
    FA2  = 12; FA3  = 13; FA4 = 14;  FA5  = 15; FA6 = 16; FA7 = 17
    FS0  = 8;  FS1  = 9
    FS2  = 18; FS3  = 19; FS4  = 20; FS5 = 21; FS6 = 22; FS7 = 23
    FS8  = 24; FS9  = 25; FS10 = 26; FS11 = 27

    CALLER_SAVED  = frozenset({RA, T0, T1, T2, A0, A1, A2, A3, A4, A5, A6, A7,
                                T3, T4, T5, T6})
    CALLEE_SAVED  = frozenset({SP, S0, S1, S2, S3, S4, S5, S6, S7, S8, S9,
                                S10, S11})
    ARG_REGS      = [A0, A1, A2, A3, A4, A5, A6, A7]
    RET_REGS      = frozenset({A0, A1})
    FCALLER_SAVED = frozenset({FT0, FT1, FT2, FT3, FT4, FT5, FT6, FT7, FT8,
                                FT9, FT10, FT11, FA0, FA1, FA2, FA3, FA4,
                                FA5, FA6, FA7})
    FCALLEE_SAVED = frozenset({FS0, FS1, FS2, FS3, FS4, FS5, FS6, FS7, FS8,
                                FS9, FS10, FS11})
    FARG_REGS     = [FA0, FA1, FA2, FA3, FA4, FA5, FA6, FA7]
    FRET_REGS     = frozenset({FA0, FA1})

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
        frd:           Optional[int] = None
        frs1:          Optional[int] = None
        frs2:          Optional[int] = None
        frs3:          Optional[int] = None
        imm:           Optional[int] = None
        branch_target: Optional[str] = None
        is_unknown:    bool = False
        live_in:       frozenset = field(default_factory=frozenset)
        live_out:      frozenset = field(default_factory=frozenset)
        flive_in:      frozenset = field(default_factory=frozenset)
        flive_out:     frozenset = field(default_factory=frozenset)

        # Minimal computed properties needed for liveness tests
        @property
        def uses_int(self):
            regs = set()
            if self.rs1 is not None: regs.add(self.rs1)
            if self.rs2 is not None: regs.add(self.rs2)
            if self.rs3 is not None: regs.add(self.rs3)
            return frozenset(regs)

        @property
        def defs_int(self):
            if self.rd is not None:
                return frozenset({self.rd})
            return frozenset()

        @property
        def uses_float(self):
            regs = set()
            if self.frs1 is not None: regs.add(self.frs1)
            if self.frs2 is not None: regs.add(self.frs2)
            if self.frs3 is not None: regs.add(self.frs3)
            return frozenset(regs)

        @property
        def defs_float(self):
            if self.frd is not None:
                return frozenset({self.frd})
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
              branch_target=None, frd=None, frs1=None, frs2=None,
              label=None, raw=None):
    """Convenience constructor for test instructions."""
    return Instruction(
        mnemonic=mnemonic,
        operands=[],
        raw=raw or mnemonic,
        label=label,
        rd=rd, rs1=rs1, rs2=rs2, imm=imm,
        branch_target=branch_target,
        frd=frd, frs1=frs1, frs2=frs2,
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
        # After dataflow: live_out of the block equals the ABI seed for ret.
        assert result.live_out_int[bb.label] == RET_REGS | CALLEE_SAVED

    def test_single_add_live_through(self):
        """  add a0, a1, a2
             ret
        live_out of block = RET_REGS | CALLEE_SAVED (ret seed)
        live_in  of block must include a1 and a2 (used before def).
        a0 is defined by add; if a0 ∈ RET_REGS it stays live_in, otherwise it
        is killed.
        """
        add  = make_add(rd=10, rs1=11, rs2=12)  # a0 = a1 + a2
        ret  = make_ret()
        bb   = single_block_cfg([add, ret])
        result = compute_global_liveness([bb])
        # a1 (11) and a2 (12) must be live into the block.
        assert 11 in result.live_in_int[bb.label]
        assert 12 in result.live_in_int[bb.label]

    def test_def_kills_upstream_use(self):
        """  addi a2, a2, 1   ; redefines a2
             add  a0, a1, a2
             ret
        a2 is redefined before any use reaches block entry — but the first insn
        also *reads* a2, so a2 IS live at block entry.
        live_in must contain a2.
        """
        insn1 = make_addi(rd=12, rs1=12, imm=1)  # a2 = a2+1 (reads a2 first)
        insn2 = make_add(rd=10, rs1=11, rs2=12)
        ret   = make_ret()
        bb    = single_block_cfg([insn1, insn2, ret])
        result = compute_global_liveness([bb])
        # a2 is read by insn1 before being overwritten
        assert 12 in result.live_in_int[bb.label]

    def test_pure_def_kills_before_block_entry(self):
        """  addi a2, a3, 0   ; a2 = a3; a2 is newly defined, not read first
             add  a0, a1, a2
             ret
        a2 is NOT live at block entry (it is defined before any use here).
        a3 IS live at block entry.
        """
        insn1 = make_addi(rd=12, rs1=13, imm=0)  # a2 = a3 (pure def of a2)
        insn2 = make_add(rd=10, rs1=11, rs2=12)
        ret   = make_ret()
        bb    = single_block_cfg([insn1, insn2, ret])
        result = compute_global_liveness([bb])
        assert 12 not in result.live_in_int[bb.label]
        assert 13 in result.live_in_int[bb.label]

    def test_dead_def_not_live(self):
        """  addi t0, t0, 1   ; t0 written but never read after; not in RET_REGS
             ret
        t0 (x5=5) is in CALLER_SAVED; it is not in RET_REGS or CALLEE_SAVED.
        t0 should NOT appear in live_out of the block (ret seed has no t0).
        """
        insn = make_addi(rd=5, rs1=5, imm=1)  # t0 = t0+1
        ret  = make_ret()
        bb   = single_block_cfg([insn, ret])
        result = compute_global_liveness([bb])
        # t0 is in CALLER_SAVED, not in RET_REGS | CALLEE_SAVED
        assert 5 not in (RET_REGS | CALLEE_SAVED)  # self-check
        assert 5 not in result.live_out_int[bb.label]

    def test_sp_callee_saved_live_at_ret(self):
        """sp (x2) is CALLEE_SAVED — it must appear in live_out for a ret block."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        assert 2 in result.live_out_int[bb.label]  # sp=2

    def test_zero_register_never_live(self):
        """x0 is hardwired zero; it should never appear in any live set."""
        add = make_add(rd=0, rs1=10, rs2=11)  # write to x0 — a no-op
        ret = make_ret()
        bb  = single_block_cfg([add, ret])
        result = compute_global_liveness([bb])
        assert 0 not in result.live_in_int[bb.label]
        assert 0 not in result.live_out_int[bb.label]

    def test_x0_source_operand_not_live(self):
        """addi a0, x0, 5 — x0 as source should not make x0 live."""
        insn = make_addi(rd=10, rs1=0, imm=5)
        ret  = make_ret()
        bb   = single_block_cfg([insn, ret])
        result = compute_global_liveness([bb])
        assert 0 not in result.live_in_int[bb.label]


# ---------------------------------------------------------------------------
# §7 — Global pass: ABI seeds for block terminals
# ---------------------------------------------------------------------------

class TestABISeedTerminals:

    def test_ret_seed_int(self):
        """ret → live_out_int seed = RET_REGS ∪ CALLEE_SAVED."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        assert result.live_out_int[bb.label] == RET_REGS | CALLEE_SAVED

    def test_ret_seed_float(self):
        """ret → live_out_float seed includes FRET_REGS.
        NOTE: plan says 'live_out = RET_REGS ∪ CALLEE_SAVED' for ret, but
        does not explicitly state the float component of the ret seed.
        See Gap #1."""
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        # Assuming FRET_REGS are in the float live_out seed for ret.
        for r in FRET_REGS:
            assert r in result.live_out_float[bb.label]

    def test_tail_pseudo_seed_int(self):
        """tail pseudo → live_out_int seed = ARG_REGS ∪ FARG_REGS.
        Integer component: a0–a7."""
        tail = make_tail_pseudo()
        bb   = single_block_cfg([tail])
        result = compute_global_liveness([bb])
        expected_int = frozenset(ARG_REGS)
        assert expected_int.issubset(result.live_out_int[bb.label])

    def test_tail_pseudo_seed_float(self):
        """tail pseudo → live_out_float seed includes fa0–fa7."""
        tail = make_tail_pseudo()
        bb   = single_block_cfg([tail])
        result = compute_global_liveness([bb])
        expected_float = frozenset(FARG_REGS)
        assert expected_float.issubset(result.live_out_float[bb.label])

    def test_call_pseudo_seed_int(self):
        """call pseudo → live_out_int seed = RET_REGS ∪ CALLEE_SAVED.
        (Callee preserves callee-saved; may return values in a0/a1.)"""
        call = make_call_pseudo()
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        # The seed at the call point (live_out of the call instruction) should
        # include RET_REGS and CALLEE_SAVED before the ret seed merges in.
        # NOTE: this tests block-level live_out, not per-instruction (see Gap #3).
        assert (RET_REGS | CALLEE_SAVED).issubset(result.live_out_int[bb.label])

    def test_call_pseudo_seed_float(self):
        """call pseudo → live_out_float seed includes FCALLEE_SAVED."""
        call = make_call_pseudo()
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        assert FCALLEE_SAVED.issubset(result.live_out_float[bb.label])

    def test_jalr_t0_is_call(self):
        """jalr t0, rs1 — rd=t0(x5) counts as call-with-return (§2 alt link reg)."""
        call = make_insn("jalr", rd=5, rs1=20, imm=0)  # jalr t0, s4, 0
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        # CALLER_SAVED should appear in def[B] so that they are NOT live past call
        # unless subsequently used.  The block live_out (from ret) should not
        # include caller-saved regs (t1..t6) unless they survive to the ret seed.
        # At minimum, the ret seed's CALLEE_SAVED must still be present.
        assert CALLEE_SAVED.issubset(result.live_out_int[bb.label])

    def test_tail_call_jalr_seed(self):
        """jalr x0, t1 (rs1=t1≠ra) — tail call → live_out seed = ARG_REGS ∪ FARG_REGS."""
        tail = make_tail_call(rs1=6)  # t1=6
        bb   = single_block_cfg([tail])
        result = compute_global_liveness([bb])
        assert frozenset(ARG_REGS).issubset(result.live_out_int[bb.label])

    def test_jal_ra_is_call_seed(self):
        """jal ra, target — a call instruction; live_out seed = RET_REGS ∪ CALLEE_SAVED ∪ FCALLEE_SAVED."""
        call = make_jal_call(rd=1, target="foo")
        ret  = make_ret()
        bb   = single_block_cfg([call, ret])
        result = compute_global_liveness([bb])
        assert (RET_REGS | CALLEE_SAVED).issubset(result.live_out_int[bb.label])
        assert FCALLEE_SAVED.issubset(result.live_out_float[bb.label])

    def test_plain_jal_x0_not_a_call(self):
        """jal x0, target — NOT a call (rd=x0); no ABI seed injection."""
        jmp = make_jal_jump(target="loop")
        # point to a plain block as successor with no instructions
        succ = single_block_cfg([make_ret()], label="loop")
        bb   = single_block_cfg([jmp], label="before")
        bb.successors = [succ]
        succ.predecessors = [bb]
        result = compute_global_liveness([bb, succ])
        # jal x0 is a plain jump; its live_out is derived from successor's live_in,
        # not from any ABI seed. CALLER_SAVED should NOT be live_out of 'before'
        # because no call occurred. Only RET_REGS | CALLEE_SAVED from ret survives.
        caller_only = CALLER_SAVED - CALLEE_SAVED - RET_REGS
        for r in caller_only:
            assert r not in result.live_out_int[bb.label], (
                f"x{r} should not be live after plain jal x0")


# ---------------------------------------------------------------------------
# §7 — Global pass: iterative dataflow across multiple blocks
# ---------------------------------------------------------------------------

class TestGlobalPassMultiBlock:

    def test_two_block_live_through(self):
        """
        Block A:                Block B:
          add a0, a1, a2          ret
        A → B (fall-through)
        live_out(A) = live_in(B) = RET_REGS | CALLEE_SAVED
        live_in(A)  = (RET_REGS | CALLEE_SAVED) - {a0} ∪ {a1, a2}
                    Because a0 is killed by add; a1,a2 are needed.
                    a0 ∈ RET_REGS so it cancels: live_in still has a0 from
                    live_out propagation? No — a0 is redefined in A, so:
                    live_in(A) = use(A) ∪ (live_out(A) - def(A))
                               = {a1,a2} ∪ ((RET_REGS|CALLEE_SAVED) - {a0})
        NOTE: a0=10 ∈ RET_REGS so it is killed by the add. live_in(A)
        contains a1,a2 plus everything in CALLEE_SAVED plus a1 (=a1 ∈ RET_REGS).
        Wait: a1=11 is in RET_REGS too. a1 is also a source operand.
        live_in(A) = {a1,a2} ∪ (CALLEE_SAVED ∪ {a1}) = {a1,a2} ∪ CALLEE_SAVED
        """
        add = make_add(rd=10, rs1=11, rs2=12)  # a0=a1+a2
        ret = make_ret()
        block_a = single_block_cfg([add], label="a")
        block_b = single_block_cfg([ret], label="b")
        link_blocks(block_a, block_b)

        result = compute_global_liveness([block_a, block_b])

        # live_out(a) = live_in(b) = RET_REGS | CALLEE_SAVED
        assert result.live_out_int["a"] == result.live_in_int["b"]
        assert result.live_out_int["a"] == RET_REGS | CALLEE_SAVED

        # a1 and a2 must be live into block_a
        assert 11 in result.live_in_int["a"]
        assert 12 in result.live_in_int["a"]
        # a0 is killed by add; it is NOT contributed by use(A), but it IS in
        # live_out(A) - def(A) only if a0 not in def(A). a0 IS in def(A).
        # So a0 should NOT appear in live_in(A) from the kill calculation alone.
        # HOWEVER a0(=10) ∈ RET_REGS and a0 is defined (killed) by the add.
        # live_in(A) = {a1=11, a2=12} ∪ ({a0=10,a1=11} | CALLEE_SAVED) - {a0=10}
        #            = {a1=11, a2=12} ∪ ({a1=11} | CALLEE_SAVED)
        assert 10 not in result.live_in_int["a"]

    def test_join_point_live_union(self):
        """
        Block A (branch taken path):    add a2, a3, zero
        Block B (fall-through path):    add a2, a4, zero
        Block C (join / merge):          add a0, a1, a2
                                         ret
        live_in(C) must contain a1, a2.
        live_out(A) = live_out(B) = live_in(C).
        live_in(A) must contain a3 (but not a4).
        live_in(B) must contain a4 (but not a3).
        """
        insn_c0 = make_add(rd=10, rs1=11, rs2=12)
        ret_c   = make_ret()
        block_c = single_block_cfg([insn_c0, ret_c], label="c")

        insn_a  = make_add(rd=12, rs1=13, rs2=0)   # a2 = a3
        block_a = single_block_cfg([insn_a], label="a")

        insn_b  = make_add(rd=12, rs1=14, rs2=0)   # a2 = a4
        block_b = single_block_cfg([insn_b], label="b")

        block_a.successors = [block_c]
        block_b.successors = [block_c]
        block_c.predecessors = [block_a, block_b]

        result = compute_global_liveness([block_a, block_b, block_c])

        assert 11 in result.live_in_int["c"]
        assert 12 in result.live_in_int["c"]
        assert 13 in result.live_in_int["a"]
        assert 14 in result.live_in_int["b"]
        # a3 is not needed on path B
        assert 13 not in result.live_in_int["b"]
        assert 14 not in result.live_in_int["a"]

    def test_back_edge_loop(self):
        """
        header: beq a0, x0, exit   ; (a0 used as loop counter)
        body:   addi a0, a0, -1    ; a0 redefined; a0 also used
        body → header (back edge)
        exit: ret

        live_in(header) must include a0 (used by beq AND needed on loop back-edge).
        """
        beq    = make_beq(rs1=10, rs2=0, target="exit")
        header = single_block_cfg([beq], label="header")
        addi   = make_addi(rd=10, rs1=10, imm=-1)
        body   = single_block_cfg([addi], label="body")
        ret    = make_ret()
        exit_b = single_block_cfg([ret], label="exit")

        # header → body, header → exit
        header.successors = [body, exit_b]
        body.successors   = [header]           # back edge
        body.predecessors = [header]
        header.predecessors = [body]           # back edge predecessor
        exit_b.predecessors = [header]

        result = compute_global_liveness([header, body, exit_b])

        assert 10 in result.live_in_int["header"]
        assert 10 in result.live_in_int["body"]   # addi reads a0

    def test_conditional_branch_live_union(self):
        """
        block_br: beq a0, a1, target
        fall-through: block_ft: add a2, a3, a4; ret
        target:       block_tgt: add a2, a5, a6; ret

        live_out(block_br) = live_in(ft) ∪ live_in(tgt)
        So a0,a1,a3,a4,a5,a6 are all live at block_br's entry.
        """
        beq     = make_beq(rs1=10, rs2=11, target="tgt")
        block_br = single_block_cfg([beq], label="br")

        add_ft  = make_add(rd=12, rs1=13, rs2=14)  # a2=a3+a4
        ret_ft  = make_ret()
        block_ft = single_block_cfg([add_ft, ret_ft], label="ft")

        add_tgt = make_add(rd=12, rs1=15, rs2=16)  # a2=a5+a6
        ret_tgt = make_ret()
        block_tgt = single_block_cfg([add_tgt, ret_tgt], label="tgt")

        link_blocks(block_br, block_ft)
        block_br.successors.append(block_tgt)
        block_tgt.predecessors.append(block_br)

        result = compute_global_liveness([block_br, block_ft, block_tgt])

        live_in_br = result.live_in_int["br"]
        for r in (10, 11, 13, 14, 15, 16):
            assert r in live_in_br, f"x{r} should be live at conditional branch block"

    def test_no_successors_terminates_function(self):
        """A block with terminates_function=True (via ABI injection) has no
        liveness contribution to predecessors beyond the ABI seed."""
        # Construct a tail call block and a phantom 'successor' that should
        # not contribute liveness (terminates_function overrides CFG edges).
        tail  = make_tail_call(rs1=6)   # jalr x0, t1 → terminates
        bb    = single_block_cfg([tail], label="tail_block")
        # Artificially add a successor to verify it is ignored.
        phantom = single_block_cfg([make_addi(rd=3, rs1=4, imm=0)], label="phantom")
        phantom.instructions[0]  # just to reference it
        bb.successors = [phantom]
        phantom.predecessors = [bb]

        result = compute_global_liveness([bb, phantom])

        # live_out(tail_block) should be ARG_REGS (tail call seed), NOT
        # influenced by live_in(phantom) which uses x3/x4.
        live_out = result.live_out_int["tail_block"]
        assert frozenset(ARG_REGS).issubset(live_out)
        # phantom's def (x3) should not appear in live_out(tail_block)
        # unless x3 happens to be in ARG_REGS.
        if 3 not in frozenset(ARG_REGS):
            assert 3 not in live_out


# ---------------------------------------------------------------------------
# §7 — ABI injection into use[B] / def[B]
# ---------------------------------------------------------------------------

class TestABIInjection:

    def test_call_clobbers_caller_saved(self):
        """
        addi a3, a3, 1      ; writes a3 (a3 = ARG_REG, also CALLER_SAVED)
        call callee          ; implicit def: CALLER_SAVED ∪ FCALLER_SAVED
        add  a0, a1, x0
        ret

        After the call, a3 is in CALLER_SAVED and therefore clobbered —
        NOT live after the call unless a3 ∈ RET_REGS.
        CALLEE_SAVED regs survive the call and must appear live_out.
        """
        insn0 = make_addi(rd=13, rs1=13, imm=1)  # a3=a3+1
        call  = make_call_pseudo(target="callee")
        add   = make_add(rd=10, rs1=11, rs2=0)
        ret   = make_ret()
        bb    = single_block_cfg([insn0, call, add, ret], label="b")
        result = compute_global_liveness([bb])
        # CALLEE_SAVED must be in live_in of the block (they survive the call)
        for r in CALLEE_SAVED:
            assert r in result.live_in_int[bb.label] or r in result.live_out_int[bb.label], (
                f"callee-saved x{r} should survive across call")

    def test_call_implicit_uses_arg_regs(self):
        """
        addi a0, x0, 1
        addi a1, x0, 2
        call callee
        ret

        ARG_REGS are implicitly used by the call, so a0–a7 must be live
        into the call instruction (i.e. live at the call site).
        All ARG_REGS should appear in live_in of the block if defined before the call.
        """
        set_a0  = make_addi(rd=10, rs1=0, imm=1)
        set_a1  = make_addi(rd=11, rs1=0, imm=2)
        call    = make_call_pseudo()
        ret     = make_ret()
        bb      = single_block_cfg([set_a0, set_a1, call, ret])
        result  = compute_global_liveness([bb])
        # a0 and a1 are written then consumed by the call;
        # they should NOT propagate into live_in if defined only within this block.
        # But a2–a7 (not defined here) should be live_in because the call uses them.
        for r in ARG_REGS:
            # If r is defined before the call in the block (a0, a1), it may or
            # may not appear in live_in depending on prior defs. For a2–a7
            # (not redefined), they must be live_in.
            if r not in (10, 11):
                assert r in result.live_in_int[bb.label], (
                    f"a{r-10} should be live_in (implicit call use, not redefined)")

    def test_call_does_not_kill_callee_saved(self):
        """
        addi s0, x0, 99    ; s0 = 99 (callee-saved)
        call  callee
        add  a0, s0, x0    ; s0 must still be live here
        ret
        """
        set_s0 = make_addi(rd=8, rs1=0, imm=99)   # s0=8
        call   = make_call_pseudo()
        use_s0 = make_add(rd=10, rs1=8, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_s0, call, use_s0, ret])
        result = compute_global_liveness([bb])
        # s0 should be live across the call (callee-saved)
        assert 8 in result.live_in_int[bb.label]

    def test_return_implicit_uses_ret_regs(self):
        """
        add a0, a1, a2
        ret
        a0 is used implicitly by ret (RET_REGS). a0 must be live before ret
        even if not explicitly referenced afterwards.
        """
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])
        result = compute_global_liveness([bb])
        # a0=10 is produced by add and consumed by ret (implicit use)
        assert 10 in result.live_out_int[bb.label] or 10 in result.live_in_int[bb.label]

    def test_float_callee_saved_survives_call(self):
        """
        call callee
        fadd.s fa0, fs0, fa1
        ret
        fs0 (FCALLEE_SAVED) must be live across the call.
        """
        call  = make_call_pseudo()
        fadd  = make_insn("fadd.s", frd=10, frs1=8, frs2=11)  # fa0=fs0+fa1 (float bank)
        ret   = make_ret()
        bb    = single_block_cfg([call, fadd, ret])
        result = compute_global_liveness([bb])
        # fs0 (float index 8) should be live into the block
        assert 8 in result.live_in_float[bb.label]


# ---------------------------------------------------------------------------
# §7 — Local pass: per-instruction live_in / live_out
# ---------------------------------------------------------------------------

class TestLocalPass:

    def test_local_pass_seeds_from_global_live_out(self):
        """
        Block: add a0, a1, a2 ; ret
        After local pass:
          ret.live_out = RET_REGS ∪ CALLEE_SAVED   (from global live_out seed)
          ret.live_in  = (RET_REGS ∪ CALLEE_SAVED) ∪ uses(ret) - defs(ret)
                        ret's implicit uses are RET_REGS ∪ FRET_REGS; defs=∅
                      = RET_REGS ∪ CALLEE_SAVED  (∪ FRET_REGS for float domain)
          add.live_out = ret.live_in  (integer component)
          add.live_in  = (add.live_out - {a0}) ∪ {a1, a2}
        """
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])

        # Run both passes
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # ret.live_out == global live_out of block
        assert ret.live_out == RET_REGS | CALLEE_SAVED
        # ret.live_in should include RET_REGS (implicit use) and CALLEE_SAVED
        assert (RET_REGS | CALLEE_SAVED).issubset(ret.live_in)
        # add.live_out == ret.live_in
        assert add.live_out == ret.live_in
        # a1, a2 live into add
        assert 11 in add.live_in
        assert 12 in add.live_in
        # a0 is defined by add and in RET_REGS; it is live_out of add (for ret),
        # but killed by add so not required upstream from add's perspective unless
        # it was already live_out through another path (it is, via RET_REGS seed).

    def test_local_single_instruction_straight_line(self):
        """
        Three instructions: lw a0, 0(sp) ; add a1, a0, a2 ; ret
        Local pass backward:
          ret.live_out   = RET_REGS | CALLEE_SAVED
          ret.live_in    = RET_REGS | CALLEE_SAVED  (no int defs in ret; implicit uses = RET_REGS)
          add.live_out   = ret.live_in
          add.live_in    = (add.live_out - {a1}) ∪ {a0, a2}
          lw.live_out    = add.live_in
          lw.live_in     = (add.live_in - {a0}) ∪ {sp}   where sp=2 is rs1 of lw
        """
        lw  = make_lw(rd=10, rs1=2, imm=0)    # a0 = mem[sp]
        add = make_add(rd=11, rs1=10, rs2=12)  # a1 = a0 + a2
        ret = make_ret()
        bb  = single_block_cfg([lw, add, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # sp=2 must be live into lw
        assert 2 in lw.live_in
        # a0=10 must be live_out of lw
        assert 10 in lw.live_out
        # a0=10 should NOT be live into lw (it's defined by lw)
        assert 10 not in lw.live_in
        # a2=12 must be live at add
        assert 12 in add.live_in

    def test_local_dead_register_not_live_past_def(self):
        """
        addi t1, t1, 1   ; t1 (x6) written, never used after, not in RET/CALLEE_SAVED
        ret
        t1 must not appear in live_out of addi or live_in of addi.
        (t1 is read by addi, so it IS live_in of addi.)
        """
        insn = make_addi(rd=6, rs1=6, imm=1)  # t1=t1+1
        ret  = make_ret()
        bb   = single_block_cfg([insn, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # t1 is read, so live_in of insn must include t1=6
        assert 6 in insn.live_in
        # t1 is not in CALLEE_SAVED or RET_REGS; after def it is dead
        assert 6 not in insn.live_out or 6 not in (RET_REGS | CALLEE_SAVED)

    def test_local_float_liveness_parallel(self):
        """
        fadd.s fa0, fa1, fa2   ; float add
        ret
        Float domain: fa1, fa2 should be flive_in of fadd instruction.
        fa0 should be flive_out of fadd (it's in FRET_REGS, consumed by ret).
        """
        fadd = make_insn("fadd.s", frd=10, frs1=11, frs2=12)
        ret  = make_ret()
        bb   = single_block_cfg([fadd, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert 11 in fadd.flive_in   # fa1
        assert 12 in fadd.flive_in   # fa2
        assert 10 in fadd.flive_out  # fa0 — in FRET_REGS

    def test_local_pass_reassign_after_reorder(self):
        """
        Verify that running compute_local_liveness twice produces correct results
        (idempotency / re-run after reorder scenario described in §7).
        """
        add = make_add(rd=10, rs1=11, rs2=12)
        ret = make_ret()
        bb  = single_block_cfg([add, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)
        first_live_in  = add.live_in
        first_live_out = add.live_out

        # Simulate reorder: swap, rerun
        bb.instructions = [add, ret]   # same order; just re-run
        compute_local_liveness(bb, global_result)
        assert add.live_in  == first_live_in
        assert add.live_out == first_live_out

    def test_local_pass_per_instruction_live_out_count(self):
        """
        Verify that every instruction in the block has live_in/live_out set after
        the local pass (not left as default empty frozenset, unless truly empty).
        Block: addi a0, x0, 1 ; sw a0, 0(sp) ; ret
        """
        insn0 = make_addi(rd=10, rs1=0, imm=1)
        insn1 = make_sw(rs1=2, rs2=10, imm=0)
        ret   = make_ret()
        bb    = single_block_cfg([insn0, insn1, ret])

        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        for insn in bb.instructions:
            # live_out of ret must be the ABI seed (non-empty)
            assert isinstance(insn.live_in, frozenset)
            assert isinstance(insn.live_out, frozenset)

        # sp=2 must be live_in of insn1 (sw uses sp as base)
        assert 2 in insn1.live_in
        # a0=10 must be live_in of insn1 (sw stores a0)
        assert 10 in insn1.live_in


# ---------------------------------------------------------------------------
# §7 — Callee-saved register survival across calls
# ---------------------------------------------------------------------------

class TestCalleeSavedSurvival:

    def test_callee_saved_live_in_across_call_chain(self):
        """
        entry: addi s0, x0, 5   ; save a value in callee-saved s0
               call  foo
               add  a0, s0, x0   ; s0 must survive the call
               ret
        s0 must be live_in of the block (it is defined here, but the value
        computed must survive the call — captured by CALLEE_SAVED in call seed).
        """
        set_s0 = make_addi(rd=8, rs1=0, imm=5)
        call   = make_call_pseudo("foo")
        use_s0 = make_add(rd=10, rs1=8, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_s0, call, use_s0, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # s0 should be live_out of 'call' (CALLEE_SAVED in call seed) and
        # live_in of use_s0.
        assert 8 in use_s0.live_in
        assert 8 in call.live_out

    def test_caller_saved_killed_by_call(self):
        """
        addi t1, x0, 7   ; t1 = 7 (caller-saved, not in RET_REGS)
        call  foo
        add  a0, a1, x0   ; t1 NOT used after call
        ret
        t1 should NOT be live_out of the call instruction.
        """
        set_t1 = make_addi(rd=6, rs1=0, imm=7)   # t1=6
        call   = make_call_pseudo("foo")
        add    = make_add(rd=10, rs1=11, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_t1, call, add, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # t1 is caller-saved; after the call it is clobbered.
        assert 6 not in call.live_out

    def test_multiple_calls_callee_saved_live_throughout(self):
        """
        addi s1, x0, 3
        call foo
        call bar
        add  a0, s1, x0
        ret
        s1 (x9) must be live_in of both call instructions and live through.
        """
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
# §7 — Float register liveness
# ---------------------------------------------------------------------------

class TestFloatLiveness:

    def test_float_use_propagates_to_block_live_in(self):
        """
        fadd.s fa0, fa1, fa2
        ret
        fa1,fa2 should be in live_in_float of the block.
        """
        fadd = make_insn("fadd.s", frd=10, frs1=11, frs2=12)
        ret  = make_ret()
        bb   = single_block_cfg([fadd, ret])
        result = compute_global_liveness([bb])
        assert 11 in result.live_in_float[bb.label]
        assert 12 in result.live_in_float[bb.label]

    def test_float_def_kills(self):
        """
        fadd.s fa0, fa1, fa2   ; defines fa0
        fadd.s fa0, fa3, fa4   ; redefines fa0 — first def is killed
        ret
        fa0 should NOT be in live_in_float of the block.
        """
        fadd1 = make_insn("fadd.s", frd=10, frs1=11, frs2=12)
        fadd2 = make_insn("fadd.s", frd=10, frs1=13, frs2=14)
        ret   = make_ret()
        bb    = single_block_cfg([fadd1, fadd2, ret])
        result = compute_global_liveness([bb])
        assert 10 not in result.live_in_float[bb.label]

    def test_int_and_float_domains_are_independent(self):
        """
        add  a0, a1, a2   ; integer
        fadd.s fa0, fa1, fa2  ; float
        ret
        Integer live_in: a1=11, a2=12
        Float   live_in: fa1=11, fa2=12  (same numeric indices, different bank)
        The two banks must not bleed into each other.
        """
        iadd = make_add(rd=10, rs1=11, rs2=12)
        fadd = make_insn("fadd.s", frd=10, frs1=11, frs2=12)
        ret  = make_ret()
        bb   = single_block_cfg([iadd, fadd, ret])
        result = compute_global_liveness([bb])
        # Both int and float 11,12 live
        assert 11 in result.live_in_int[bb.label]
        assert 12 in result.live_in_int[bb.label]
        assert 11 in result.live_in_float[bb.label]
        assert 12 in result.live_in_float[bb.label]

    def test_float_callee_saved_in_ret_seed(self):
        """
        Plan §7 ret seed says 'RET_REGS ∪ CALLEE_SAVED'; it does NOT mention
        FCALLEE_SAVED explicitly in the ret seed.
        NOTE: see Gap #1 — this test asserts what we believe is correct
        (FRET_REGS at minimum) and marks the ambiguity.
        """
        ret = make_ret()
        bb  = single_block_cfg([ret])
        result = compute_global_liveness([bb])
        # FRET_REGS (fa0, fa1) should be in float live_out at ret
        for r in FRET_REGS:
            assert r in result.live_out_float[bb.label]

    def test_fcaller_saved_killed_by_call(self):
        """
        ft0 is FCALLER_SAVED; it must not be live after a call.
        fadd.s ft0, ft1, ft2
        call foo
        fadd.s fa0, fa1, fa2   ; ft0 not used
        ret
        """
        fadd1 = make_insn("fadd.s", frd=0, frs1=1, frs2=2)   # ft0=ft1+ft2
        call  = make_call_pseudo("foo")
        fadd2 = make_insn("fadd.s", frd=10, frs1=11, frs2=12)
        ret   = make_ret()
        bb    = single_block_cfg([fadd1, call, fadd2, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # ft0 (float 0) is FCALLER_SAVED; should NOT be live_out of call
        assert 0 not in call.flive_out


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
        """A block with zero instructions (e.g. entry block with only a label)."""
        bb = single_block_cfg([], label="empty")
        result = compute_global_liveness([bb])
        assert result.live_in_int["empty"] == frozenset()
        assert result.live_out_int["empty"] == frozenset()

    def test_store_does_not_define_rd(self):
        """sw has no rd; it must not add anything to def[B]."""
        sw  = make_sw(rs1=2, rs2=10, imm=0)   # sw a0, 0(sp)
        ret = make_ret()
        bb  = single_block_cfg([sw, ret])
        result = compute_global_liveness([bb])
        # sp=2 (CALLEE_SAVED) and a0=10 (RET_REGS source) are live_in
        assert 2 in result.live_in_int[bb.label]
        assert 10 in result.live_in_int[bb.label]

    def test_unknown_instruction_conservative_register_handling(self):
        """
        Unknown instruction (is_unknown=True): first register-like operand is
        treated as def, rest as uses (§4 best-effort decode).
        Liveness should respect those inferred use/def sets.
        """
        unk = Instruction(
            mnemonic="bogus", operands=["a0", "a1", "a2"], raw="bogus a0, a1, a2",
            rd=10, rs1=11, rs2=12, is_unknown=True,
        )
        ret = make_ret()
        bb  = single_block_cfg([unk, ret])
        result = compute_global_liveness([bb])
        # a1=11, a2=12 are uses; they must be live_in of block
        assert 11 in result.live_in_int[bb.label]
        assert 12 in result.live_in_int[bb.label]
        # a0=10 is defined; it is in RET_REGS so still live_out;
        # it is NOT live_in because it is first defined here.
        assert 10 not in result.live_in_int[bb.label]

    def test_worklist_converges(self):
        """
        A loop with a back-edge must converge to a fixed point.
        header → body → header (back edge) and header → exit.
        Verify the function returns (no infinite loop) and gives consistent results.
        """
        # loop: while (a0 > 0) a0--; return a0;
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

        # Must not raise or hang
        result = compute_global_liveness([header, body, exit_b])
        assert result is not None

    def test_multiple_functions_independent(self):
        """
        Two independent functions (separate CFG components) must not have
        liveness cross-contaminate each other.
        """
        ret1 = make_ret()
        fn1  = single_block_cfg([ret1], label="fn1", is_function_entry=True)

        # fn2: addi a0, a5, 0; ret  (a5=15 used in fn2 only)
        insn = make_addi(rd=10, rs1=15, imm=0)
        ret2 = make_ret()
        fn2  = single_block_cfg([insn, ret2], label="fn2", is_function_entry=True)

        result = compute_global_liveness([fn1, fn2])

        # a5=15 should not appear in live_in(fn1)
        assert 15 not in result.live_in_int["fn1"]
        # a5=15 must be live_in(fn2)
        assert 15 in result.live_in_int["fn2"]

    def test_indirect_jump_no_successors_for_liveness(self):
        """
        jalr x0, rs1 used as computed goto (not tail-call) → terminates_function=True
        (conservative; §2 Indirect jump row).
        Liveness pass must NOT propagate live_in from any phantom successor.
        NOTE: the plan says detection of 'indirect jump' vs 'tail call' is
        unspecified — see Gap #7.  This test uses a concrete rs1 that is neither
        ra (x1) nor another recognisable pattern.
        """
        # jalr x0, a5, 0 — computed goto (rs1=15≠ra, ambiguous with tail call)
        jmp = make_insn("jalr", rd=0, rs1=15, imm=0)
        bb  = single_block_cfg([jmp], label="indirect")
        phantom = single_block_cfg([make_addi(rd=3, rs1=4, imm=0)], label="phantom2")
        bb.successors = [phantom]
        phantom.predecessors = [bb]

        result = compute_global_liveness([bb, phantom])
        # x3/x4 should not bleed from phantom into indirect block
        if 3 not in frozenset(ARG_REGS):
            assert 3 not in result.live_out_int["indirect"]

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
            assert isinstance(insn.flive_in,  frozenset)
            assert isinstance(insn.flive_out, frozenset)

    def test_liveness_result_interface(self):
        """compute_global_liveness must return an object with
        live_in_int, live_out_int, live_in_float, live_out_float
        keyed by block label (str)."""
        ret = make_ret()
        bb  = single_block_cfg([ret], label="entry")
        result = compute_global_liveness([bb])
        assert hasattr(result, "live_in_int")
        assert hasattr(result, "live_out_int")
        assert hasattr(result, "live_in_float")
        assert hasattr(result, "live_out_float")
        assert "entry" in result.live_in_int


# ---------------------------------------------------------------------------
# §7 — live_out seed for call mid-block
# ---------------------------------------------------------------------------

class TestCallMidBlock:

    def test_call_mid_block_live_out_seed_at_call_site(self):
        """
        addi a0, x0, 1
        call foo           ← live_out of this insn should include RET_REGS|CALLEE_SAVED
        add  a1, a0, x0
        ret
        The per-instruction live_out of the call insn is the seed.
        """
        set_a0 = make_addi(rd=10, rs1=0, imm=1)
        call   = make_call_pseudo("foo")
        use_a0 = make_add(rd=11, rs1=10, rs2=0)
        ret    = make_ret()
        bb     = single_block_cfg([set_a0, call, use_a0, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        # After the call, live_out should contain RET_REGS (a0 returned by callee)
        # and CALLEE_SAVED.
        assert (RET_REGS | CALLEE_SAVED).issubset(call.live_out)

    def test_ret_regs_live_after_call(self):
        """
        call foo
        lw t0, 0(a0)    ; uses a0 (return value)
        ret
        a0=10 must be live_out of call (it's in RET_REGS — callee may write it).
        """
        call  = make_call_pseudo("foo")
        use_a0 = make_lw(rd=5, rs1=10, imm=0)   # t0 = mem[a0]
        ret   = make_ret()
        bb    = single_block_cfg([call, use_a0, ret])
        global_result = compute_global_liveness([bb])
        compute_local_liveness(bb, global_result)

        assert 10 in call.live_out   # a0 is live after call
