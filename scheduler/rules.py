"""
scheduler/rules.py — Pairing policy: rule definitions and slot disqualifiers.

This is the only file that needs to change when iterating on pairing policy.
The mechanism (can_pair, greedy_pair, stamp_slot_eligibility) lives in pairing.py.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional, Final
from functools import wraps

from isa.instruction import Instruction
from isa.xlen import DEFAULT as _XLEN_DEFAULT, is_xlen_width
from scheduler.imm_contracts import width_of as _yaml_width
from scheduler.imm_contracts import rd_column_slots as _rd_slots
from scheduler.imm_contracts import link_regs_for as _yaml_link_regs
from scheduler.imm_contracts import accepts_pcrel_lo as _yaml_pcrel_lo


def _w(rule: str, slot: str, op: str) -> int:
    """A declared immediate width from encoding.yaml, required to exist.
    Every numeric width below derives through here at import (TODO A8.1), so
    the number in the rule and the number in the frame are the same number."""
    v = _yaml_width(rule, slot, op)
    if v is None:
        raise RuntimeError(
            f"encoding.yaml declares no immediate width for {rule}/{slot}/{op}")
    return v


def _slot_widths(rule: str, slot: str) -> dict:
    """{yaml op name: declared bits} for one slot, from encoding.yaml."""
    from scheduler.imm_contracts import widths_for
    return dict(widths_for(rule, slot))


def _slot_mnemonics(rule: str, slot: str) -> frozenset:
    """The MNEMONICS one slot accepts, from its yaml op list.

    `li` is not a mnemonic -- it is `addi` with rs1 = x0 -- so a slot declaring
    li accepts `addi` and the width lookup separates them.
    """
    ops = set(_slot_widths(rule, slot))
    if "li" in ops:
        ops.discard("li")
        ops.add("addi")
    return frozenset(ops)


def _lr(rule: str, slot: str) -> frozenset:
    """The destination registers a slot's ops hard-code, from encoding.yaml.

    Same discipline as `_w`: a frame that means to constrain rd says so in its
    op list, and the rule reads it rather than repeating it.  Required to be
    non-empty, so a mistyped op name fails at import instead of silently
    widening the rule to accept every register."""
    regs = _yaml_link_regs(rule, slot)
    if not regs:
        raise RuntimeError(
            f"encoding.yaml declares no hard-coded rd for {rule}/{slot}")
    return frozenset(regs)

# Which base this run is scheduling for.  Set once per input by __main__ from
# the corpus's own ELF-class header (see isa/xlen.detect_xlen); frames that
# spend a single opcode on "the natural word" cannot be checked without it.
# Module-level rather than threaded through every check because RULES is built
# at import, long before any file is read.
XLEN = _XLEN_DEFAULT


def set_xlen(bits):
    """Point the XLEN-width predicates at RV32 or RV64 for this input."""
    global XLEN
    XLEN = bits


def a_is_xlen_mem(insn) -> bool:
    """`lw`/`sw` on RV32, `ld`/`sd` on RV64 -- the op an sp-relative frame
    spends its one load/store opcode on."""
    return is_xlen_width(insn, XLEN)


# ---------------------------------------------------------------------------
# PairingRule dataclass
# ---------------------------------------------------------------------------

@dataclass
class PairingRule:
    name: str

    # check(a, b) -> None means encoding accepts; str -> encoding rejects (reason).
    check: Callable

    # Per-slot mnemonic allowlists: checked before prerequisites and check(),
    # like slot-specific prerequisites for mnemonic membership.
    a_mnemonic_set: Optional[frozenset] = None
    b_mnemonic_set: Optional[frozenset] = None

    # Properties that must be True on a for the rule to be applicable.
    a_prerequisites: list = field(default_factory=list)

    # Properties that must be True on b for the rule to be applicable.
    b_prerequisites: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# pairing failure exception
# ---------------------------------------------------------------------------

class NotPair(Exception):
    """Raised by a pairing rule when an encoding rejects a candidate pair."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(self.reason)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# The RSD set shared by rsd-alu-pair and arith-jump-pair — encoding.yaml
# `rsd_alu` / `rsd_alu_j`. Both slots write `alu rsd, rsd, ...`, so rd and rs1
# are the SAME field and li (rs1=x0) is NOT reachable by register choice the way
# it is in a chain slot: it needs its own codepoint, and being the commonest op
# in either frame it is what earns the wide immediate. Priced W² + 4W = 320 at
# W=16, exactly the two frames' reservations.
_RSD_ALU_MN = frozenset({
    "addi", "addiw", "andi",                 # immediate forms (li rides on addi)
    "add",  "and",  "or",   "xor",
    "slli", "srli",                          # shift-immediate forms
    })

# Signed immediate widths, derived from the yaml op contracts at import.
_RSD_IMM_BITS   = {mn: _w("rsd-alu-pair", "a", mn)
                   for mn in ("addi", "addiw", "andi")}
_RSD_JUMP_BITS  = {mn: _w("arith-jump-pair", "a", mn)
                   for mn in ("addi", "addiw", "andi")}
_RSD_SHIFT_MN = frozenset({"slli", "srli"})
# Shift-amount width is per-frame like the signed widths.
_RSD_SHIFT_BITS = _w("rsd-alu-pair", "a", "slli")
_RSD_JUMP_SHIFT_BITS = _w("arith-jump-pair", "a", "slli")

# The ALU set shared by the three CHAIN frames — encoding.yaml `alu_chain`.
# Chain slots write `alu tmp, rs1a, ...` / `alu rdb, tmp, ...` with rs1 an
# encoded field, so li/mv/addi4spn are register choices rather than opcodes and
# fold into `addi` -- which is why only addi earns extra range here.
# rsd-alu-pair and arith-jump-pair keep _RSD_ALU_MN (different population).
_CHAIN_ALU_MN = frozenset({"addi", "andi", "add", "addw", "and", "or", "sub",
                           "xor", "xori", "maxu", "sltu", "sltiu",
                           "slli", "srli", "srliw"})
_CHAIN_ALU_IMM_BITS = {mn: _w("load-alu-chain", "b", mn)          # signed
                   for mn in ("addi", "andi", "xori", "sltiu")}
# alu-alu-chain narrows to 11 weight per axis (addi counts 2 for its 6-bit
# immediate; 11^2 = 121 fits a 128 block), and the two axes differ: A produces
# a value, B consumes it.  Fitted on THREE corpora -- a set tuned without a
# C++ corpus drops srliw, which RV32 cannot even spell.  See encoding.yaml.  The three chain frames still SHARE
# _CHAIN_ALU_MN above; only this frame is cut.
# Two 8x8 blocks, each an (A-set, B-set) pair; a candidate must sit inside ONE
# of them, not in the cross product of the unions.  The population splits into
# two vocabularies that barely interact -- arithmetic/shift and
# logical/compare -- and one square would pay for every cross term between
# them (encoding.yaml carries the measurements).
_ALU_ALU_BLOCKS = (
    (frozenset({"add", "addi", "andi", "slli", "sltu", "srliw", "sub"}),
     frozenset({"add", "addi", "and", "or", "slli", "srli", "sub"})),
    (frozenset({"addi", "and", "or", "sltiu", "srli", "xor", "xori"}),
     frozenset({"add", "addi", "andi", "or", "slli", "sltiu", "xor"})),
)
_ALU_ALU_A_MN = frozenset().union(*(a for a, _ in _ALU_ALU_BLOCKS))
_ALU_ALU_B_MN = frozenset().union(*(b for _, b in _ALU_ALU_BLOCKS))

_CHAIN_ALU_SHIFT_MN = frozenset({"slli", "srli", "srliw"})
_CHAIN_ALU_SHIFT_HI = (1 << _w("load-alu-chain", "b", "slli")) - 1


def _chain_alu_imm_in_range(insn: Instruction) -> None:
    """Immediate / shift range for a chain-frame ALU op, per encoding.yaml's
    `alu_chain` op contracts. addi carries a signed 6-bit field (it is also the
    li/mv form, so zero is encodable, and it is where the wide constants are);
    andi the 5-bit base range; shifts an unsigned 5-bit amount."""
    bits = _CHAIN_ALU_IMM_BITS.get(insn.mnemonic)
    if bits is not None:
        imm = insn.imm
        if imm is None:
            raise NotPair("MALFORMED: missing-immediate")
        if not (-(1 << (bits - 1)) <= imm <= (1 << (bits - 1)) - 1):
            raise NotPair("big-imm")
    elif insn.mnemonic in _CHAIN_ALU_SHIFT_MN:
        imm = insn.imm
        if imm is None:
            raise NotPair("MALFORMED: missing-shift-amount")
        if not (0 <= imm <= _CHAIN_ALU_SHIFT_HI):
            raise NotPair("big-imm")


def a_chain_imm_ok(func: Callable):
    """A-slot immediate is in the chain-frame encodable range."""
    @wraps(func)
    def check_a_chain_imm(a: Instruction, b: Instruction):
        _chain_alu_imm_in_range(a)
        return func(a, b)
    return check_a_chain_imm


def b_chain_imm_ok(func: Callable):
    """B-slot immediate is in the chain-frame encodable range."""
    @wraps(func)
    def check_b_chain_imm(a: Instruction, b: Instruction):
        _chain_alu_imm_in_range(b)
        return func(a, b)
    return check_b_chain_imm


# ---------------------------------------------------------------------------
# Shared per-slot helpers (mnemonic already confirmed by rule.mnemonic_set)
# ---------------------------------------------------------------------------

def _imm_in_range(insn: Instruction, widths: dict = _RSD_IMM_BITS,
                  shift_bits: int = _RSD_SHIFT_BITS) -> None:
    """Immediate / shift-amount range check for an RSD ALU op (mnemonic already ok).

    `widths` selects the per-frame contract: rsd-alu-pair passes _RSD_IMM_BITS,
    arith-jump-pair the narrower _RSD_JUMP_BITS its row can actually draw.

    Register ranges are not checked anywhere: every frame draws its register
    operands in 5-bit columns, so there is no narrower class to enforce (gated
    by tests/test_conformance.py).
    """
    bits = widths.get(insn.mnemonic)
    if bits is not None:
        imm = insn.imm
        # imm==0 on addi/addiw encodes as add/addw rd, rs1, x0 — allow it through.
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        if imm is not None and imm != 0 and not (lo <= imm <= hi):
            raise NotPair("big-imm")
        if imm is None:
            raise NotPair("MALFORMED: missing-immediate")
    elif insn.mnemonic in _RSD_SHIFT_MN:
        imm = insn.imm
        if imm is None:
            raise NotPair("MALFORMED: missing-shift-amount")
        if not (0 <= imm <= (1 << shift_bits) - 1):
            raise NotPair("big-imm")


# ---------------------------------------------------------------------------
# decorators
# ---------------------------------------------------------------------------

def no_escape(func: Callable):
    @wraps(func)
    def check_escape(a: Instruction, b: Instruction):
        if a.rd != b.rd and a.rd in b.live_out and a.rd:
            raise NotPair("A-result-escapes")
        return func(a, b)
    return check_escape


def must_chain(func: Callable):
    @wraps(func)
    def check_chain(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd != b.rs1 and not (b.is_commutative and a.rd == b.rs2): raise NotPair("not-chain")
        return func(a, b)
    return check_chain


def must_chain_rs1(func: Callable):
    @wraps(func)
    def check_chain1(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd != b.rs1: raise NotPair("not-chain")
        return func(a, b)
    return check_chain1


# loads/stores use rs1 as base register.
must_chain_base: Final[Callable] = must_chain_rs1


def must_chain_rs2(func: Callable):
    @wraps(func)
    def check_chain2(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd != b.rs2: raise NotPair("not-chain")
        return func(a, b)
    return check_chain2


# stores use rs2 as source value
must_chain_stored: Final[Callable] = must_chain_rs2


def must_chain_either(func: Callable):
    @wraps(func)
    def check_chain3(a: Instruction, b: Instruction):
        if a.rd is None: raise NotPair("not-chain")
        if a.rd not in b.uses_regs: raise NotPair("not-chain")
        return func(a, b)
    return check_chain3


def must_not_chain(func: Callable):
    @wraps(func)
    def check_chain3(a: Instruction, b: Instruction):
        if a.rd and a.rd in b.uses_regs: raise NotPair("unwanted-chain")
        return func(a, b)
    return check_chain3


def a_base_not_from_auipc(func: Callable):
    @wraps(func)
    def check_a_base_from_auipc(a: Instruction, b: Instruction):
        if a.base_from_auipc:
            raise NotPair("A-relocatable-offset")
        return func(a, b)
    return check_a_base_from_auipc


def b_base_not_from_auipc(func: Callable):
    @wraps(func)
    def check_b_base_from_auipc(a: Instruction, b: Instruction):
        if b.base_from_auipc:
            raise NotPair("B-relocatable-offset")
        return func(a, b)
    return check_b_base_from_auipc


def a_is_rsd(func: Callable):
    @wraps(func)
    def check_a_is_rsd(a: Instruction, b: Instruction):
        if not a.is_rsd:
            raise NotPair("A-is-not-rsd")
        return func(a, b)
    return check_a_is_rsd


def a_is_rsd_or_li(func: Callable):
    @wraps(func)
    def check_a_is_rsd_or_li(a: Instruction, b: Instruction):
        if not a.is_rsd and not a.is_li:
            raise NotPair("A-is-not-rsd-or-li")
        return func(a, b)
    return check_a_is_rsd_or_li


def b_is_rsd(func: Callable):
    @wraps(func)
    def check_b_is_rsd(a: Instruction, b: Instruction):
        if not b.is_rsd:
            raise NotPair("B-is-not-rsd")
        return func(a, b)
    return check_b_is_rsd


def b_is_rsd_or_li(func: Callable):
    @wraps(func)
    def check_b_is_rsd_or_li(a: Instruction, b: Instruction):
        if not b.is_rsd and not b.is_li:
            raise NotPair("B-is-not-rsd-or-li")
        return func(a, b)
    return check_b_is_rsd_or_li


# is_rsd is structural ("rd is also a source"): it accepts rd==rs2 as well as
# rd==rs1.  But the compressed two-address encoding only realizes rd==rs2 by
# swapping operands, which is legal only for a commutative op.  These decorators
# refine the loose is_rsd gate down to what the encoding can actually express;
# li forms (is_rsd is False) pass through untouched.
def a_rsd_swappable(func: Callable):
    @wraps(func)
    def check_a_rsd_swappable(a: Instruction, b: Instruction):
        if a.is_rsd and a.rd != a.rs1 and not a.is_commutative:
            raise NotPair("A-rd==rs2-requires-commutative")
        return func(a, b)
    return check_a_rsd_swappable


def b_rsd_swappable(func: Callable):
    @wraps(func)
    def check_b_rsd_swappable(a: Instruction, b: Instruction):
        if b.is_rsd and b.rd != b.rs1 and not b.is_commutative:
            raise NotPair("B-rd==rs2-requires-commutative")
        return func(a, b)
    return check_b_rsd_swappable


def exclusive_rd(func: Callable):
    @wraps(func)
    def check_rd_exclusive(a: Instruction, b: Instruction):
        if a.rd and b.rd and a.rd == b.rd:  # zeroes and Nones aren't collisions
            raise NotPair("rd-collision")
        return func(a, b)
    return check_rd_exclusive


def a_imm_ok(func: Callable):
    """A-slot's immediate / shift amount is in the RSD-encodable range."""
    @wraps(func)
    def check_a_imm_ok(a: Instruction, b: Instruction):
        _imm_in_range(a)
        return func(a, b)
    return check_a_imm_ok


def b_imm_ok(func: Callable):
    """B-slot's immediate / shift amount is in the RSD-encodable range."""
    @wraps(func)
    def check_b_imm_ok(a: Instruction, b: Instruction):
        _imm_in_range(b)
        return func(a, b)
    return check_b_imm_ok


def a_jump_imm_ok(func: Callable):
    """A-slot immediate fits arith-jump-pair's declared widths (see
    _RSD_JUMP_BITS / _RSD_JUMP_SHIFT_BITS — every imm op carries a sixth bit)."""
    @wraps(func)
    def check_a_jump_imm_ok(a: Instruction, b: Instruction):
        _imm_in_range(a, _RSD_JUMP_BITS, _RSD_JUMP_SHIFT_BITS)
        return func(a, b)
    return check_a_jump_imm_ok


# ---------------------------------------------------------------------------
# rsd-alu-pair
# ---------------------------------------------------------------------------

# The two slots declare DIFFERENT op sets and different per-op immediate widths.
# Range past the row's five drawn bits is bought in opcode entries -- an op
# declaring N bits occupies 2^(N-5) -- so WEIGHT is the budget, not op count,
# and one more bit on `li` costs what four reg-reg opcodes cost.  A spends its
# sixteen on breadth (fifteen ops, nearly all weight 1); B spends twelve of its
# sixteen on two deep immediates (`li` at 8 bits, `addi` at 7).  Both from
# encoding.yaml, never restated here -- see results/corpus/RSD-RESIDUE.md for
# the weighted optimisation that chose them.
#
# Asymmetry is legitimate because the pair is ORDER-FREE in 87.1% of the
# corpus residue: two independent results, so unless one reads the other's
# destination either orientation may be emitted and only one need encode.  The
# list scheduler already tries both, via its tier-1 and tier-2 partner picks.
_RSD_A_W = _slot_widths("rsd-alu-pair", "a")
_RSD_B_W = _slot_widths("rsd-alu-pair", "b")
_RSD_A_MN = _slot_mnemonics("rsd-alu-pair", "a")
_RSD_B_MN = _slot_mnemonics("rsd-alu-pair", "b")
# Shift amounts are unsigned; every other immediate this frame carries is
# signed.  Kept local so the check does not depend on analysis/ (rules.py
# imports nothing from there).
_RSD_UNSIGNED_MN = frozenset({"slli", "srli", "srai",
                              "slliw", "srliw", "sraiw"})


def _rsd_slot_imm_ok(insn: Instruction, widths: dict, slot: str) -> None:
    """The immediate fits the width THIS SLOT declares for this op.

    A register-register form has no field to check.  `li` is `addi` with
    rs1 = x0 and is declared separately in the yaml (it breaks the RSD form the
    rest of the frame relies on), so it is looked up under its own name.
    """
    if insn.imm is None:
        return
    key = "li" if insn.is_li else insn.mnemonic
    bits = widths.get(key)
    if bits is None:
        raise NotPair(f"{slot}-op-not-in-slot-set")
    if key in _RSD_UNSIGNED_MN:
        lo, hi = 0, (1 << bits) - 1
    else:
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not (lo <= insn.imm <= hi):
        raise NotPair("big-imm")


@a_is_rsd_or_li
@b_is_rsd_or_li
@a_rsd_swappable
@b_rsd_swappable
@exclusive_rd
def _rsd_alu_pair(a: Instruction, b: Instruction) -> None:
    """Both instructions RSD or li form, immediates in range, and the two slots
    write distinct destination registers.

    Distinct destinations: rsd-alu-pair exists to pack two independent, both-live
    ALU results.  If a.rd == b.rd then either B consumes A (a producer/consumer
    chain — handled, more capably, by alu-alu-chain) or B does not (making A's
    write dead).  Either way this
    rule should not claim the pair; require distinct destinations.
    """
    _rsd_slot_imm_ok(a, _RSD_A_W, "A")
    _rsd_slot_imm_ok(b, _RSD_B_W, "B")


# ---------------------------------------------------------------------------
# alu-alu-chain
# ---------------------------------------------------------------------------

@must_chain
@no_escape
@a_chain_imm_ok
@b_chain_imm_ok
def _alu_alu_chain(a: Instruction, b: Instruction) -> None:
    """A computes a value that B immediately consumes; that value is dead after B.

    A has free choice of rd and rs1.  B must use A's rd as its rs1 input
    (or rs2 if B is commutative).  A's rd must be dead after B — either B
    overwrites it (b.rd == a.rd) or it is not live in b.live_out.

    Because a.rd is produced and consumed within the packet and dies there, it
    is not encoded at all.
    """
    if not any(a.mnemonic in A and b.mnemonic in B
               for A, B in _ALU_ALU_BLOCKS):
        raise NotPair("op-pair-outside-both-blocks")
    pass


# ---------------------------------------------------------------------------
# load-alu-chain / alu-store-chain
# ---------------------------------------------------------------------------
# Two variants of alu-alu-chain where one slot is an sp-relative memory access
# carrying an 8-bit scaled offset.  The ALU slot draws from the same table
# (_RSD_ALU_MN) and uses the same register/immediate checks as alu-alu-chain,
# so all three rules evolve together as the allowed-op set is tuned.
#
#   load-chain:  A = sp-relative load (8-bit scaled offset); B = ALU op that
#                consumes the loaded value as its input.  The value is dead
#                after B.
#   store-chain: A = ALU op; B = sp-relative store (8-bit scaled offset) that
#                writes A's result to the stack.  The result is dead after B.

_SP_LOAD_MN  = frozenset({"lw", "ld"})   # lwu dropped: see encoding.yaml load-alu-chain
_SP_STORE_MN = frozenset({"sw", "sd"})
_ALL_LOAD_MN = frozenset({"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld"})
_ZERO_BRANCH_MN = frozenset({"beqz", "bnez"})
ALL_BRANCH_MN = frozenset({"beq", "bne", "blt", "bge", "bltu", "bgeu", "beqz", "bnez"})


def _chain_mem_check(insn: Instruction, base_bits: int) -> None:
    """A chain-frame memory op: any base register (sp included, as x2 in the
    register column), width-scaled offset in the drawn `base_bits` field."""
    shift = insn.access_shift or 0
    if not insn.uimm_fits(base_bits, shift):
        raise NotPair("big-imm")


def a_chain_mem(bits: int):
    """A-slot memory op: any base register, `bits`-wide scaled offset."""
    def dec(func: Callable):
        @wraps(func)
        def check(a: Instruction, b: Instruction):
            _chain_mem_check(a, bits)
            return func(a, b)
        return check
    return dec


def b_chain_mem(bits: int):
    """B-slot memory op: any base register, `bits`-wide scaled offset."""
    def dec(func: Callable):
        @wraps(func)
        def check(a: Instruction, b: Instruction):
            _chain_mem_check(b, bits)
            return func(a, b)
        return check
    return dec


# a.rd (the chain register) is dead after B and not encoded in the packet, so it
# is exempt from range checks.  @must_chain / @must_chain_stored already reject
# a.rd is None, so the ALU/mem checks below never see a destination-less A.
@must_chain
@no_escape
@a_chain_mem(6)
@b_chain_imm_ok
def _load_alu_chain(a: Instruction, b: Instruction) -> None:
    """A loads from the stack; B (ALU) consumes the loaded value, which is then dead."""
    return None


# ---------------------------------------------------------------------------
# addi-store-chain
# ---------------------------------------------------------------------------
# encoding.yaml `addi-store-chain`: compute an addi, then store the result.
#
#     A: addi tmp, rs1a, imma      B: store tmp, k*immb(sp)  or  0(rbase)
#
# tmp is dead at the store, so it is never encoded -- which is what frees room
# for a 10-bit immediate alongside a full 5-bit rs1a. alu_chain cannot match
# that: widening its addi costs alu-alu-chain codepoints quadratically, while here
# no second register field competes for the space.
#
# A subsumes li (rs1a = x0), mv (imma = 0) and addi4spn (rs1a = sp) -- those are
# register/immediate choices, not separate opcodes.
#
# No register window applies: the frame encodes at most two registers, each in
# a full 5-bit field.
_ADDI_STORE_MN = frozenset({"sb", "sh", "sw", "sd"})
_ADDI_STORE_BITS = 10                        # signed immediate field
# The single row spends its bits on rbase, so B carries no offset at all.


def _addi_store_chain(a: Instruction, b: Instruction) -> None:
    """A computes an addi; B stores the result, after which it is dead."""
    if a.imm is None:
        raise NotPair("MALFORMED: missing-immediate")
    lo, hi = -(1 << (_ADDI_STORE_BITS - 1)), (1 << (_ADDI_STORE_BITS - 1)) - 1
    if not (lo <= a.imm <= hi):
        raise NotPair("A-big-imm")
    if a.rd is None or b.rs2 != a.rd:
        raise NotPair("not-chain")
    if a.rd in b.live_out:
        raise NotPair("A-result-escapes")
    if b.rs1 is None:
        raise NotPair("MALFORMED: missing register operand")
    if b.imm:
        raise NotPair("B-big-imm")           # the one row encodes no offset


@must_chain_stored
@no_escape
@a_chain_imm_ok
@b_chain_mem(5)
def _alu_store_chain(a: Instruction, b: Instruction) -> Optional[str]:
    """A (ALU) computes a value; B stores it to the stack, after which it is dead."""
    return None


# ---------------------------------------------------------------------------
# load-sp-branch-pair / load-base-branch-pair
# ---------------------------------------------------------------------------
# Load a value; branch on whether it is zero/nonzero; value kept alive.
# The two variants differ in base register and offset range:
#
#   load-sp-branch-pair:   A = any load with sp (x2) as base, 10-bit unsigned
#                     offset scaled by the access width.  Captures deep frames.
#   load-base-branch-pair: A = any load with any base register, 5-bit unsigned
#                     offset scaled by the access width.  Shallow struct fields.
#
# Offsets are width-scaled (a multiple of the access size, encoded shifted),
# matching every other memory rule here; unaligned offsets do not pair.
#
# rd is NOT required to be dead — the common case is a null-check where the
# pointer is tested and then used on the non-null path.  Dead-after cases
# are also matched as a subset.

def _load_branch_check(a: Instruction, b: Instruction,
                       imm_bits: int) -> None:
    if a.rbase is None:
        raise NotPair("load has no base register")
    if a.rd is None:
        raise NotPair("load has no destination")
    if not a.uimm_fits(imm_bits, a.access_shift or 0):
        raise NotPair(f"offset exceeds {imm_bits}-bit width-scaled range")
    return None


# Its own frame since the A9 split: sp implied, 10-bit offset, and the op set
# mirrors RVC's c.lwsp/c.ldsp precedent (ld simply never matches on rv32).
_SP_BRANCH_A_MN = frozenset({"lw", "ld", "lbu"})
_SP_BRANCH_OFF_BITS = _w("load-sp-branch-pair", "a", "lw")
_BASE_BRANCH_OFF_BITS = _w("load-base-branch-pair", "a", "lw")


@a_base_not_from_auipc
@must_chain
def _load_sp_branch(a: Instruction, b: Instruction) -> None:
    """sp-relative lw/ld/lbu (width-scaled uimm10 offset) -> beqz/bnez;
    rd kept alive."""
    if a.rbase != 2:
        raise NotPair("not-SP-base")
    _load_branch_check(a, b, _SP_BRANCH_OFF_BITS)


@a_base_not_from_auipc
@must_chain
def _load_base_branch(a: Instruction, b: Instruction) -> None:
    """Any-base load (width-scaled uimm5 offset) -> beqz/bnez; rd kept alive."""
    _load_branch_check(a, b, _BASE_BRANCH_OFF_BITS)


# ---------------------------------------------------------------------------
# load0-load10-chain / load5-load5-chain
# ---------------------------------------------------------------------------
# Two load+load pointer chases.  In both, A loads a pointer and B dereferences
# it: B's base register IS A's destination, which is dead after B.  They differ
# only in whether the FIRST load carries an offset.
#
#   base-chain:      A = lx rtmp, 0(rb);        B = load rd, imm10(rtmp)
#                    the common case -- a pointer at the head of a structure,
#                    then a reach into what it points at.  59.9% of all chases.
#   base-off-chain:  A = lx rtmp, imm5(rb);     B = load rd, imm5(rtmp)
#                    the pointer itself is at an offset, typically a stack slot.
#
# A IS ALWAYS THE NATURAL WORD.  must_chain_base makes A's loaded value B's base
# address, and a byte or a halfword is not an address.  Measured over every
# chain the pairer can form -- all 11583 across the suite, on and off the axes
# -- A is lw on RV32 and ld on RV64, 100.0%, no exceptions.  So the A slot
# spends one XLEN-switchable opcode (`lx`) rather than seven, and each block is
# 1x7 = 7 codepoints instead of 7x7 = 49.
#
# These two replace an earlier `deref-load-chain` (offset on the FIRST load,
# second at zero) and a wide `load0-load10-chain`.  Before that they were a single
# frame drawing both rows over one op-select with nothing selecting between
# them, so the offset in the word could not be attributed to a load at all.
# load5-load5-chain subsumes the deref population as its immb == 0 column,
# losing only the 28 chases needing more than five bits of imma.
#
# The two are disjoint by construction: load0-load10-chain demands A's offset be
# zero and load5-load5-chain demands it be nonzero, so no chase satisfies
# both and neither shadows the other.

# The union over both bases; the checks enforce which is the natural word for
# the base actually being scheduled, as _mem_sp_pair does.  A set rather than
# None so the rules are not eligible for -- and do not annotate -- unrelated
# instructions.
_CHAIN_A_MN = frozenset({"lw", "ld"})
_CHAIN_LOAD_MN = frozenset({"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld"})

_L0L10_IMMB_BITS = _w("load0-load10-chain", "b", "lw")          # immb, wide form
_L5L5_IMMA_BITS = _w("load5-load5-chain", "a", "lx")        # imma, split form
_L5L5_IMMB_BITS = _w("load5-load5-chain", "b", "lw")        # immb, split form


def _chain_a_ok(a: Instruction) -> None:
    """A must be a natural-word load: it is producing an ADDRESS."""
    if a.rbase is None or a.rd is None:
        raise NotPair("A missing base/dest register")
    if not is_xlen_width(a, XLEN):
        raise NotPair("not-xlen-width")


@must_chain_base
@no_escape
@a_base_not_from_auipc
def _load0_load10_chain(a: Instruction, b: Instruction) -> None:
    """A loads a pointer at 0(rb); B dereferences it at imm10(rtmp); rtmp dead."""
    _chain_a_ok(a)
    if a.imm != 0:
        raise NotPair("A offset must be zero")
    shift = b.access_shift or 0
    if not b.uimm_fits(_L0L10_IMMB_BITS, shift):
        raise NotPair("big-imm")
    return None


@must_chain_base
@no_escape
@a_base_not_from_auipc
def _load5_load5_chain(a: Instruction, b: Instruction) -> None:
    """A loads a pointer at imm5(rb); B dereferences it at imm5(rtmp).

    A's offset must be NONZERO: the zero case is load0-load10-chain's, which draws
    ten bits for B rather than five.  Keeping the two disjoint means a chase is
    never encodable both ways, so neither frame's count is an artefact of where
    it sits in RULES.
    """
    _chain_a_ok(a)
    if a.imm == 0:
        raise NotPair("A offset zero — load0-load10-chain's")
    if not a.uimm_fits(_L5L5_IMMA_BITS, a.access_shift or 0):
        raise NotPair("big-imm-A")
    if not b.uimm_fits(_L5L5_IMMB_BITS, b.access_shift or 0):
        raise NotPair("big-imm-B")
    return None


# ---------------------------------------------------------------------------
# dual-op-pair
# ---------------------------------------------------------------------------
# Two instructions drawn from the same canonical opcode tuple that share their
# input operands, perform different operations, and (where both have one) write
# distinct output registers.  The pair packs into a single 32-bit word.
#
# Match kinds (per tuple):
#   "arith2"      both R-type; share rs1, rs2 positionally; two distinct dests.
#                 Two halves of ONE computation (mul lo/hi, div quotient/rem,
#                 sum/difference, min/max), declared as a pair so hardware can
#                 fuse them instead of issuing twice — see encoding.yaml's
#                 macro-op-pair notes.  Kept despite a near-zero score.
#   "dual_setup_pair"  two independent small pseudo-ops (li / mv / addi4spn).
#
# Canonical order is (tuple[0], tuple[1]).  The reverse order is accepted only
# when the two instructions are fully independent (neither destination is a
# source operand of the pair).  In canonical order the B-slot instruction may
# write one of the shared source registers (a WAR that resolves correctly
# because B executes second); the reverse order may not.
#
# The former "mem_addi" / "mem_shadd" kinds are NOT dual-op families and have
# moved to the post-increment family below — see post_inc_family().

# ---------------------------------------------------------------------------
# post-inc-pair
# ---------------------------------------------------------------------------
# The `post-inc-pair` frame in encoding.yaml:
#
#     A: load  rda,  k*imma(rsda)      B: shXadd rsda, rsda, rs2b
#        store rs2a, k*imma(rsda)         addi   rsda, rsda, k*immb
#
# A reads memory through a base register; B then updates that base IN PLACE.
# Both slots name the base in the single `rsda` field, so the encoding requires
# b.rd == b.rs1 == a.rs1.
#
# This is deliberately NOT one of the dual-op families.  Those pack two
# INDEPENDENT operations; a post-increment is dependent by construction — B
# writes exactly the register A reads.  It is also strictly ORDER-SENSITIVE:
# reversing the two gives a pre-increment, which is a different frame
# (`pre-inc-pair`) with a different offset relationship.
#
# Width scaling: the memory op implies a data width k.  A's offset is a
# width-scaled unsigned 5-bit field (imma[4:0]).  The addi stride is likewise
# width-scaled, and nonzero — a zero stride is not an increment.  The shXadd
# shift is tied to the width by the tuple table (sh3add with 8-byte accesses,
# sh2add with 4-byte).

# No lb/lh/lwu: they accounted for 12 of 37816 scheduled slots.  arith-mem-pair
# reuses this set for its B slot.
_MEM_BASE_MN = frozenset({"lbu", "lhu", "lw", "ld", "sb", "sh", "sw", "sd"})
_MEM_L0L10_IMMB_BITS = _w("mem-base-pair", "a", "lw")
_MEM_SP_OFF_BITS = _w("mem-sp-pair", "a", "lx")


@exclusive_rd
def _mem_base_pair(a: Instruction, b: Instruction) -> None:
    """Adjacent same-width same-base loads or stores; offsets differ by one data width."""
    if a.mnemonic != b.mnemonic:
        raise NotPair("opcode-mismatch")
    if a.rbase != b.rbase or a.rbase is None:
        raise NotPair("base-reg-mismatch")
    if a.imm is None or b.imm is None:
        raise NotPair("MALFORMED: memory offset absent")
    width = a.access_width or (1 << (a.access_shift or 0))
    if abs(a.imm - b.imm) != width:
        raise NotPair(f"bad-delta")
    shift = a.access_shift or 0
    # Both rows draw imm[4:0] against `rbase`; the shared sixth bit is bought
    # once per op on the opcode list (encoding.yaml mem-base-pair).  The wide sp
    # form is its own frame (mem-sp-pair) -- an sp access too wide for this
    # field belongs to that frame or to neither.
    imm_bits = _MEM_L0L10_IMMB_BITS
    for insn in (a, b):
        if not insn.uimm_fits(imm_bits, shift):
            max_off = ((1 << imm_bits) - 1) << shift
            raise NotPair(f"offset exceeds {imm_bits}-bit scaled range (max {max_off})")
    return None


# ---------------------------------------------------------------------------
# arith-mem-pair
# ---------------------------------------------------------------------------
# Independent RSD arithmetic op (A) paired with a small-offset memory op (B).
# No producer-consumer relationship required — they share no operands.
# The dep graph prevents scheduling A before B when a true dependency exists.
#
# The union over both bases; `_mem_sp_pair` enforces which is the natural word
# for the base actually being scheduled.  A set rather than None so the rule is
# not eligible for -- and does not annotate -- unrelated instructions.
_MEM_SP_MN = frozenset({"lw", "sw", "ld", "sd"})


@exclusive_rd
def _mem_sp_pair(a: Instruction, b: Instruction) -> None:
    """Two adjacent sp-relative accesses of the NATURAL WORD, offsets one width
    apart.

    encoding.yaml's mem-sp-pair: the base is implicit, so the freed column pays
    for a 10-bit shared offset -- which sp traffic needs (only 41% of it fits
    five bits on rv32) and base traffic does not (97% fits).  The op is `lx`/`sx`,
    one XLEN-switchable opcode meaning lw/sw on RV32 and ld/sd on RV64, which
    covers 100% and 98% of measured sp pairs respectively."""
    if a.mnemonic != b.mnemonic:
        raise NotPair("opcode-mismatch")
    if not (is_xlen_width(a, XLEN) and is_xlen_width(b, XLEN)):
        raise NotPair("not-xlen-width")
    if a.rbase != 2 or b.rbase != 2:
        raise NotPair("not-SP-base")
    if a.imm is None or b.imm is None:
        raise NotPair("MALFORMED: memory offset absent")
    width = a.access_width or (1 << (a.access_shift or 0))
    if abs(a.imm - b.imm) != width:
        raise NotPair("bad-delta")
    shift = a.access_shift or 0
    for insn in (a, b):
        if not insn.uimm_fits(_MEM_SP_OFF_BITS, shift):
            raise NotPair("big-imm")


_DUAL_TUPLES: dict = {
    # arith2 — ONE unit pass yielding two results: mul hi/lo, div/rem.
    # sum/difference and min/max were cut: they share arguments but are two
    # separate computations, which is a different claim about the hardware.
    # High half first, per the M extension's fusion sequence -- see the
    # macro-op-pair notes in encoding.yaml.  The canonical direction is what
    # the encoding blesses; rules.py still accepts either order.
    ("mulh", "mul"):      "arith2",
    ("mulhu", "mul"):     "arith2",
    ("mulhsu", "mul"):    "arith2",
    ("div", "rem"):       "arith2",
    ("divu", "remu"):     "arith2",
    ("divw", "remw"):     "arith2",
    ("divuw", "remuw"):   "arith2",
    # (post-increment mem+addi / mem+shNadd tuples live in _POST_INC_TUPLES)
    # (adjacent load/store pairs are handled by the dedicated mem-base-pair rule)
    # independent single-output pairs — no shared operands required
    # ("addi", "addi") is overloaded: it covers three pseudo-ops (li, mv,
    # addi4spn) giving 6 order-insensitive combinations: li+li, mv+mv,
    # addi4spn+addi4spn, li+mv, li+addi4spn, mv+addi4spn.
    ("addi", "addi"):     "dual_setup_pair",
}

# post-inc-pair tuples, in strict (memory-op, base-update) order.  The op-sets
# mirror encoding.yaml's post-inc-pair clusters: addi strides any of the four
# 32/64-bit accesses.  The shXadd clusters were cut -- zero scheduled pairs on
# every corpus under both compilers, for half the frame's codepoints.
_POST_INC_TUPLES: dict = {
    ("ld",  "addi"):      "mem_addi",
    ("lw",  "addi"):      "mem_addi",
    ("sd",  "addi"):      "mem_addi",
    ("sw",  "addi"):      "mem_addi",
}


def _role_tuples(role: str) -> frozenset:
    """The (a.mnemonic, b.mnemonic) tuples belonging to one dual-op family."""
    src = _POST_INC_TUPLES if role == "mem_addi" else _DUAL_TUPLES
    return frozenset(k for k, v in src.items() if v == role)


def _role_mnems(role: str) -> frozenset:
    """The mnemonics appearing in a family (both slots, order-insensitive)."""
    return frozenset(m for k in _role_tuples(role) for m in k)


def _a_slot_mnems(role: str) -> frozenset:
    """The mnemonics legal in the A slot of an order-sensitive family."""
    return frozenset(k[0] for k in _role_tuples(role))


def _b_slot_mnems(role: str) -> frozenset:
    """The mnemonics legal in the B slot of an order-sensitive family."""
    return frozenset(k[1] for k in _role_tuples(role))


_POST_INC_STRIDE_BITS = _w("post-inc-pair", "b", "addi")
_POST_INC_OFF_BITS = _w("post-inc-pair", "a", "lw")


def _width_stride_ok(mem: Instruction, stride_insn: Instruction) -> bool:
    """stride_insn.imm is a nonzero width-scaled unsigned-with-remap stride."""
    shift = mem.access_shift if mem.access_shift is not None else 0
    return stride_insn.uimm_fits(_POST_INC_STRIDE_BITS, shift, nonzero='remap')


def _is_li_mv_addi4spn(insn: Instruction) -> bool:
    """True for the three addi pseudo-ops that qualify for dual_setup_pair."""
    return insn.is_li or insn.is_mv or insn.is_addi4spn

# The dual-op families below share one mechanism (distinct destinations, order-
# insensitive tuple match, mutual independence) but differ in how the two ops
# share operands.  Each family is its own PairingRule so its stats stand alone,
# rather than being hidden under a single "dual-op" tally.

def _canonical_dual(a: Instruction, b: Instruction, tuples: frozenset):
    """Order-insensitive tuple match: return (first, second, reversed_order) in
    canonical order, or raise if (a, b) is not one of `tuples`."""
    if (a.mnemonic, b.mnemonic) in tuples:
        return a, b, False
    if (b.mnemonic, a.mnemonic) in tuples:
        return b, a, True
    raise NotPair("bad-tuple")


def _reject_dependence(a: Instruction, b: Instruction, reversed_order: bool) -> None:
    """Every dual-op family packs two INDEPENDENT ops.  The A-slot op must not
    feed the B-slot op (a producer/consumer chain, or clobbering a shared source
    before B reads it).  A reversed (non-canonical) order is only legal when the
    B-slot op also does not feed A."""
    if a.rd is not None and a.rd in b.uses_regs:
        raise NotPair("unwanted-chain")
    if reversed_order and b.rd is not None and b.rd in a.uses_regs:
        raise NotPair("cannot-reorder")


def dual_family(role: str):
    """Turn a per-family operand-sharing check — written in canonical
    first/second terms — into a full rule check(a, b): distinct destinations
    (@exclusive_rd), order-insensitive tuple match, the family check, then mutual
    independence."""
    tuples = _role_tuples(role)
    def deco(shared_ok: Callable):
        @exclusive_rd
        @wraps(shared_ok)
        def check(a: Instruction, b: Instruction):
            first, second, reversed_order = _canonical_dual(a, b, tuples)
            shared_ok(first, second)
            _reject_dependence(a, b, reversed_order)
        return check
    return deco


@dual_family("arith2")
def _dual_arith2(a: Instruction, b: Instruction) -> None:
    """Two R-type ops sharing rs1 and rs2 positionally (sum/diff, min/max, ...).

    The pair is a fusion hint: both results of one computation are wanted, so an
    implementation can produce them in a single pass.  Low corpus yield is a
    fact about today's compilers, not about the frame."""
    if None in (a.rs1, a.rs2, b.rs1, b.rs2):
        raise NotPair("MALFORMED: missing register operand")
    if a.rs1 != b.rs1 or a.rs2 != b.rs2:
        raise NotPair("source-operand-mismatch")


def post_inc_family(role: str):
    """Turn a per-family base-update check into a full rule check(a, b).

    Shared by every post-increment tuple: strict (memory-op, base-update) order,
    the base named once in `rsda` (b.rd == b.rs1 == a.rs1), and A's own offset
    inside the width-scaled imma[4:0] field.  Order-insensitivity and the
    independence tests of dual_family are deliberately absent — see the header
    comment above."""
    tuples = _role_tuples(role)

    def deco(update_ok: Callable):
        @wraps(update_ok)
        def check(a: Instruction, b: Instruction):
            if (a.mnemonic, b.mnemonic) not in tuples:
                raise NotPair("bad-tuple")
            if None in (a.rs1, b.rd, b.rs1):
                raise NotPair("MALFORMED: missing register operand")
            # The base is a single encoded field, read by A and rewritten by B.
            if b.rs1 != a.rs1 or b.rd != a.rs1:
                raise NotPair("base-reg-mismatch")
            # A load must not land its result in the base register: B would then
            # increment the loaded value instead of the pointer.
            if a.rd is not None and a.rd == a.rs1:
                raise NotPair("load-clobbers-base")
            # A's offset rides the width-scaled imma[4:0] field.
            shift = a.access_shift if a.access_shift is not None else 0
            if not a.uimm_fits(_POST_INC_OFF_BITS, shift):
                raise NotPair("A-big-imm")
            update_ok(a, b)
        return check
    return deco


@post_inc_family("mem_addi")
def _post_inc_addi(a: Instruction, b: Instruction) -> None:
    """`addi rsda, rsda, k*immb` — stride a nonzero width-scaled uimm5."""
    if not _width_stride_ok(a, b):
        raise NotPair("B-addi-imm-mismatch")


_DUAL_ADDI4SPN_BITS = _w("dual-setup-pair", "a", "addi4spn")
_DUAL_LI_BITS = _w("dual-setup-pair", "a", "li")
# The un-extended field width: mv declares nothing, so its width IS the field.
_DUAL_FIELD_BITS = _w("dual-setup-pair", "a", "mv")


@dual_family("dual_setup_pair")
def _dual_indep(a: Instruction, b: Instruction) -> None:
    """Two fully independent small pseudo-ops (li / mv / addi4spn)."""
    li_lim = 1 << (_DUAL_LI_BITS - 1)
    for insn in (a, b):
        if not _is_li_mv_addi4spn(insn):
            raise NotPair("is-not-li_mv_addi4spn")
        if insn.is_addi4spn and not insn.uimm_fits(
                _DUAL_ADDI4SPN_BITS, 2, nonzero='remap'):
            raise NotPair(f"addi4spn immediate {insn.imm} out of range")
        # li's extra bit above the drawn field rides one opcode repeat.
        if insn.is_li and (insn.imm is None
                           or not (-li_lim <= insn.imm < li_lim)):
            raise NotPair("li-big-imm")
    # Only immb carries the extra bit, so at most one of the two may exceed the
    # narrow field.  Which SLOT it lands in does not matter: this frame requires
    # mutual independence, so the encoder may swap the pair to put the wide
    # operand in immb.
    nlim = 1 << (_DUAL_FIELD_BITS - 1)
    wide = sum(1 for i in (a, b)
               if i.is_li and i.imm is not None and not (-nlim <= i.imm < nlim))
    if wide > 1:
        raise NotPair("li-both-wide")
    # A→B independence is enforced by _reject_dependence; also require B↛A
    # (reversed_order is never set for this symmetric tuple).
    if b.rd is not None and b.rd in a.uses_regs:
        raise NotPair("B result feeds A")



# ---------------------------------------------------------------------------
# li-branch-chain
# ---------------------------------------------------------------------------
# A loads a small constant into a temporary; B is any conditional comparison
# branch that uses that temporary as one of its two operands (either slot),
# after which the temporary is dead.
#
# A slot: li rtmp, imm8  (addi rtmp, x0, imm  — 8-bit signed immediate)
# B slot: beq/bne/blt/bge/bltu/bgeu  rs, rtmp, off
#      or beq/bne/blt/bge/bltu/bgeu  rtmp, rs, off
#
# rtmp is dead after B — it carried only the comparison constant.
# rs (the non-constant operand) may be any register and survives.

_LI_BRANCH_A_MN = frozenset({"addi"})
_LI_BRANCH_B_MN = frozenset({"beq", "bne", "blt", "bge", "bltu", "bgeu"})
_CHAIN_LI_BITS = _w("li-branch-chain", "a", "li")


@must_chain
@no_escape
def _chain_li_branch(a: Instruction, b: Instruction) -> None:
    """A loads an 8-bit constant; B compares it against a register and branches."""
    if not a.is_li:
        raise NotPair("A not li form (must be addi rd, x0, imm)")
    # The 5-bit imma column plus the doublings the yaml's li op buys.
    if not a.imm_fits(_CHAIN_LI_BITS):
        raise NotPair(f"immediate out of {_CHAIN_LI_BITS}-bit signed range")
    return None


# ---------------------------------------------------------------------------
# inc-branch-pair
# ---------------------------------------------------------------------------
# Loop-counter idiom: bump a register in place by exactly one, then a
# comparison branch reads it.  The counter usually stays alive (no deadness
# requirement).  The allowed (branch, operand-position) set depends on the
# step direction -- see _INC_MODES/_DEC_MODES and the frame in encoding.yaml.
#
# A slot: addi/addiw  rd, rd, +/-1   (RSD form; step implied by opcode)
# B slot: comparison branch with rd as either operand (aliases included)

_INC_BRANCH_A_MN = frozenset({"addi", "addiw"})
_INC_BRANCH_B_MN = frozenset({"beq", "bne", "blt", "bge", "bltu", "bgeu",
                              "beqz", "bnez", "bltz", "bgez", "blez"})

# Alias spellings the parser leaves on the mnemonic (operands are already
# normalised into rs1/rs2, with x0 for the zero side).
_BRANCH_CANON = {"beqz": "beq", "bnez": "bne", "bltz": "blt",
                 "bgez": "bge", "blez": "bge", "bgtz": "blt"}

# The frame enumerates the best sixteen JOINT direction x mode cells of the
# adjacent-site census, not a mode product: down-loops are bltu/bgeu-heavy,
# up-loops beq/bne with bge/bgeu sum-first.  A mode is (canonical branch,
# position of the counter); eq/ne are operand-symmetric.
_INC_MODES = frozenset({("beq", "any"), ("bne", "any"),
                        ("bltu", "first"), ("bltu", "second"),
                        ("bge", "first"), ("bgeu", "first"),
                        ("bgeu", "second"), ("blt", "first")})
_DEC_MODES = frozenset({("beq", "any"), ("bne", "any"),
                        ("bltu", "first"), ("bltu", "second"),
                        ("bgeu", "first"), ("bgeu", "second"),
                        ("bge", "second"), ("blt", "first")})


@must_chain_either
def _inc_branch_pair(a: Instruction, b: Instruction) -> None:
    """inc/dec (addi rsd, rsd, +/-1) + comparison branch reading the counter.

    `addiw` is matched and billed as the full-width op: optimistic for
    unsigned int counters on rv64 (defined wrap), provable for signed ones
    (overflow is UB) -- see the frame note in encoding.yaml."""
    if not a.is_rsd:
        raise NotPair("A-is-not-rsd")
    if a.imm not in (1, -1):
        raise NotPair("A-not-unit-step")
    mn = _BRANCH_CANON.get(b.mnemonic, b.mnemonic)
    if mn in ("beq", "bne"):
        mode = (mn, "any")
    else:
        mode = (mn, "first" if b.rs1 == a.rd else "second")
    modes = _INC_MODES if a.imm == 1 else _DEC_MODES
    if mode not in modes:
        raise NotPair("mode-not-in-direction-set")
    return None


# ---------------------------------------------------------------------------
# bit-test-branch-chain
# ---------------------------------------------------------------------------
# A isolates a single bit (mask or shift); B branches on whether it is zero.
# A's result register is dead after B — it carries only the bit to the branch.
#
# A slot: andi rd, rs, pow2_imm   (any rd, including rd==rs RSD form)
#      or slli/srli/srai rd, rs, N
# B slot: beqz rd  or  bnez rd  (rs1 == A's rd; rd dead after B)
#
# The RSD case (rd==rs in A) is intentionally included — if the source is
# also dead it is valid, and the encoding can decide whether to compress it.

_BIT_BRANCH_A_MN = frozenset({"andi", "slli", "srli"})
_BIT_BRANCH_B_MN = frozenset({"beqz", "bnez", "beq", "bne"})
_BIT_BRANCH_IMM_HI = (1 << _w("bit-test-branch-chain", "a", "andi")) - 1


def _is_pow2_imm(v) -> bool:
    return v is not None and v > 0 and (v & (v - 1)) == 0


def _shift_for_zero_test(imm) -> Optional[tuple]:
    """If `andi rd, rs, imm; beqz/bnez` can be rewritten as `slli/srli rd, rs, N;
    beqz/bnez` (same branch type), return (shift_op, N).  Covers:
      2^N - 1  (low N bits all zero?)  → slli (64-N); beqz/bnez
      ~(2^N-1) (high bits all zero?)   → srli N;      beqz/bnez
    Does not cover plain pow2 (single bit) — those encode as andi directly."""
    if imm is None:
        return None
    if imm > 1 and (imm & (imm + 1)) == 0:          # 2^N - 1
        return ("slli", 64 - imm.bit_length())
    if imm < -1 and (-imm & (-imm - 1)) == 0:        # ~(2^N - 1) = -(2^N)
        return ("srli", (-imm).bit_length() - 1)
    return None


@must_chain
@no_escape
def _chain_bit_test_branch(a: Instruction, b: Instruction) -> None:
    """A isolates or masks bits; B branches on zero/nonzero; A's result is dead after B.

    andi with a pow2 immediate isolates a single bit and encodes directly.
    andi with a 2^N-1 or ~(2^N-1) immediate will be rewritten to slli/srli at
    emit time — accepted here because the zero/nonzero test is equivalent.
    slli/srli are accepted directly for any shift amount.
    All forms require a zero-test branch (beqz/bnez or beq/bne with rs2==x0).
    """
    if a.rd is None:
        raise NotPair("A has no destination")
    # beq/bne with zero are aliases for beqz/bnez; non-zero comparisons not supported
    if b.mnemonic in ("beq", "bne") and b.rs2 != 0:
        raise NotPair("beq/bne B slot requires rs2==zero")
    if a.mnemonic == "andi":
        rewrite = _shift_for_zero_test(a.imm)
        if _is_pow2_imm(a.imm):
            # Encoded as the MASK VALUE, so imma[5:0] reaches bit 5 only.
            if a.imm > _BIT_BRANCH_IMM_HI:
                raise NotPair("A-big-imm")
        elif rewrite is not None:
            # Rewritten to a shift at emit time; the shift amount is what the
            # field carries.
            if rewrite[1] > _BIT_BRANCH_IMM_HI:
                raise NotPair("A-big-imm")
        else:
            raise NotPair(f"andi immediate {a.imm} not pow2 or shift-expressible")
    elif a.imm is None or not (0 <= a.imm <= _BIT_BRANCH_IMM_HI):
        raise NotPair("A-big-imm")
    return None


# ---------------------------------------------------------------------------
# czero-or-chain
# ---------------------------------------------------------------------------
# The second half of a zicond select:
#     czero.eqz t1, x, c
#     czero.nez t2, y, c     <- A
#     or        r,  t1, t2   <- B
# A's result is the chain temporary, so only four registers are encoded and the
# shape is exactly alu-alu-chain's row. czero is not in *alu_chain because
# adding it there would take that 16x16 cross product to 18x18.
#
# 97-99% of czero's forward chains go to `or` on the four newer corpora.

_CZERO_MN = frozenset({"czero.eqz", "czero.nez"})
_LI_CZERO_BITS = 10          # the row draws imma[4:0|9:5]


@must_chain
@no_escape
def _li_czero_pair(a: Instruction, b: Instruction) -> None:
    """A materialises one arm's constant; B conditionally zeroes it."""
    if not a.is_li:
        raise NotPair("A-is-not-li")
    lim = 1 << (_LI_CZERO_BITS - 1)
    if a.imm is None or not (-lim <= a.imm < lim):
        raise NotPair("A-big-imm")


@must_chain
@no_escape
def _czero_select_pair(a: Instruction, b: Instruction) -> None:
    """Conditional-zero feeding the merge of a select; the arm is then dead."""
    return None


# ---------------------------------------------------------------------------
# index-mem-chain
# ---------------------------------------------------------------------------
# RISC-V has no register+register addressing, so `array[i]` costs two
# instructions: scale the index onto the base, then access through the result.
# That result is a pure temporary — it is the chain register, is dead after B,
# and is NOT encoded, which is what lets three register operands fit.
#
# A slot: add / sh1add / sh2add / sh3add  tmp, index, base
# B slot: a load or store through tmp whose width MATCHES the shift
#         (add=1, sh1add=2, sh2add=4, sh3add=8), offset a 5-bit scaled field.
#
# addi is deliberately excluded: with the sum as a temporary its addend is just
# the access's own offset, so `addi t,b,k; ld d,0(t)` folds to `ld d,k(b)`.
# The surviving-sum form (the pointer walk) belongs to pre-inc-pair, which is
# ordered after this rule.

_INDEX_MEM_TUPLES: frozenset = frozenset({
    ("add",    "lbu"), ("add",    "sb"),     # scale 1 — byte indexing, the bulk
    ("sh1add", "lhu"), ("sh1add", "sh"),     # scale 2
    ("sh2add", "lw"),  ("sh2add", "sw"),     # scale 4
    ("sh3add", "ld"),  ("sh3add", "sd"),     # scale 8
})
_INDEX_MEM_A_MN = frozenset(a for a, _ in _INDEX_MEM_TUPLES)
_INDEX_MEM_B_MN = frozenset(b for _, b in _INDEX_MEM_TUPLES)
_INDEX_MEM_OFF_BITS = _w("index-mem-chain", "b", "lw")


@must_chain_base
@no_escape
def _index_mem_chain(a: Instruction, b: Instruction) -> None:
    """A forms a scaled-index address into a temporary; B accesses through it."""
    if (a.mnemonic, b.mnemonic) not in _INDEX_MEM_TUPLES:
        raise NotPair("bad-tuple")
    # The address is the chain temporary and must not survive the pair.
    # @no_escape already enforces that, and correctly permits the very common
    # `add t,b,i; lbu t,0(t)` — B redefining the temporary also ends its life.
    # Both shXadd operands are encoded, so neither may be the temporary.
    if a.rd == a.rs1 or a.rd == a.rs2:
        raise NotPair("A-is-rsd")          # pre-inc-pair's shape, not this one
    if not b.uimm_fits(_INDEX_MEM_OFF_BITS, b.access_shift or 0):
        raise NotPair("B-big-imm")
    return None


# ---------------------------------------------------------------------------
# pre-inc-pair
# ---------------------------------------------------------------------------
# A is in RSD form: rd == rs1 (or commutative: rd == rs2).  A writes its
# result back to its own source register — a pre-increment or accumulate.
# B reads A's rd as its rs1 — the updated pointer (for loads/stores) or the
# left-hand side of a comparison.  For memory B, the offset must be zero.
#
# Canonical order only: B depends on A's result, so the pair cannot be
# reversed.  A's rd must not be destroyed by B (B.rd != A.rd) since
# the updated pointer or value typically survives the pair.

# encoding.yaml pre-inc-pair `ops`: addi against all four widths, and each
# shXadd against the width its scale matches. 8 tuples, budget 8.
_PRE_INC_TUPLES: frozenset = frozenset({
    ("addi",   "ld"),   # pre-increment pointer then load/store, any width
    ("addi",   "lw"),
    ("addi",   "sd"),
    ("addi",   "sw"),
    ("sh3add", "ld"),   # scaled-index update (8-byte stride) then load/store qword
    ("sh3add", "sd"),
    ("sh2add", "lw"),   # scaled-index update (4-byte stride) then load/store word
    ("sh2add", "sw"),
})

_PRE_INC_A_MN = frozenset(a for a, _ in _PRE_INC_TUPLES)
_PRE_INC_B_MN = frozenset(b for _, b in _PRE_INC_TUPLES)
_PRE_INC_BUMP_BITS = _w("pre-inc-pair", "a", "addi")
_PRE_INC_OFF_BITS = _w("pre-inc-pair", "b", "lw")


@a_is_rsd
@must_chain_base
@exclusive_rd
def _pre_inc_pair(a: Instruction, b: Instruction) -> None:
    """A (RSD form) updates a register; B reads that register as rs1."""
    if (a.mnemonic, b.mnemonic) not in _PRE_INC_TUPLES:
        raise NotPair("bad-tuple")
    if a.mnemonic == "addi":
        # The addi rows access AT the bumped pointer (offset structurally
        # zero) and spend the freed column on a 10-bit width-scaled bump —
        # `addi rsda, rsda, k*imma` then `access 0(rsda)`.
        if b.has_mem_operand and b.imm != 0:
            raise NotPair("B-imm-not-zero")
        shift = b.access_shift or 0
        if a.imm is None or not a.imm_multiple(shift):
            raise NotPair("A-stride-not-width-multiple")
        v = a.imm >> shift if a.imm >= 0 else -((-a.imm) >> shift)
        lim = 1 << (_PRE_INC_BUMP_BITS - 1)
        if not (-lim <= v < lim):
            raise NotPair("A-big-imm")
    else:
        # shXadd rows: the stride is the register, so B keeps the width-scaled
        # immb offset field.
        if b.has_mem_operand and not b.uimm_fits(
                _PRE_INC_OFF_BITS, b.access_shift or 0):
            raise NotPair("B-big-imm")
    return None


_EPILOGUE_A_MN = frozenset({"addi"})
_PROLOGUE_IMM_BITS = _w("prologue-pair", "a", "addi")
_EPILOGUE_IMM_BITS = _w("epilogue-pair", "a", "addi")
_EPILOGUE_B_MN = frozenset({"jalr", "ret"})


def _prologue_pair(a: Instruction, b: Instruction) -> None:
    """A reserves stack frame, B stores return address at top of frame
    A: addi sp, sp, -N  - width-scaled negative uimm x16, nonzero
    B: sw ra, N-4(sp)  - store return address
    """
    if a.rd != 2 or a.rs1 != 2:
        raise NotPair("A-not-addi-sp")
    if not a.nimm_fits(_PROLOGUE_IMM_BITS, 4, nonzero=True):
        raise NotPair("A-big-imm")
    if b.rs1 != 2:
        raise NotPair("B-not-SP-base")
    if b.rs2 != 1:
        raise NotPair("B-not-RA-src")
    if b.imm + b.access_width + a.imm != 0:
        raise NotPair("B-bad-delta")


def _epilogue_pair(a: Instruction, b: Instruction) -> None:
    """A restores sp; B is an unconditional return or jump.

    Order-sensitive: the packet runs A then B, and B is a control transfer, so
    the addi must be A (executes first) and the ret/jalr must be B (executes
    last).  The reverse would run the transfer first and skip the addi, so it is
    not a valid packet -- is_control_transfer also keeps ret/jalr out of A.

    A: addi sp, sp, +N  — width-scaled uimm×16, nonzero
    B: ret or jalr rd∈{0,1} with 12-bit signed offset (architectural, not a
       yaml field — the jump target register is what the row encodes)
    """
    if a.rd != 2 or a.rs1 != 2:
        raise NotPair("A-not-addi-sp")
    if not a.uimm_fits(_EPILOGUE_IMM_BITS, 4, nonzero=True):
        raise NotPair("A-big-imm")
    if b.rd not in (0, 1):
        raise NotPair("B rd must be x0 or x1")
    if not b.imm_fits(12):
        raise NotPair("B-big-imm")


# ---------------------------------------------------------------------------
# arith-jump-pair / setup-jump-pair
# ---------------------------------------------------------------------------
# Pack a productive instruction into the same packet as a trailing unconditional
# control transfer (the packet's B slot always executes last).  Ported from the
# legacy scheduler's arith_jump / mv_load_jump rules — its single largest
# advantage on real code, and offset-independent (unlike its memory rules).
#
#   arith-jump-pair:   A = RSD ALU op (or li), imm in range
#   setup-jump-pair:  A = mv, li (10-bit signed), or lbu/lw/ld with a
#                          non-negative offset fitting a 5-bit scaled field
#   B (both):          ret / jr / indirect jalr (imm 0) / direct j / jal x0
#
# Calls (which save a link register) are excluded from the B slot.  Direct jumps
# carry a target offset that is not range-checked here — the same optimism the
# *-branch rules apply (see CLAUDE.md); returns and register-indirect jumps need
# no offset field and are always encodable.

_SMALL_JUMP_MN = frozenset({"ret", "jalr", "j", "jal"})
_MVLOAD_JUMP_A_MN = frozenset({"addi", "lbu", "lw", "ld"})
_MVLOAD_JUMP_LI_BITS = _w("setup-jump-pair", "a", "li")
_MVLOAD_JUMP_OFF_BITS = 5        # imma[4:0], scaled by the access width
                                 # (a ONE-ROW narrowing — imm_contracts is
                                 # per-op and cannot express it)
# A DIRECT jump needs a displacement, and encoding.yaml pays for it out of the
# A slot: rows 3-4 give immb the rs2+rs1 span, leaving A only the funct5 column.
# So li narrows to 5 bits and a load has no offset field left at all.  (The
# displacement itself cannot be checked here — corpus jump operands are
# unresolved labels, so a pairwise rule has nothing to measure.)  Rows 6-7 draw
# the opposite trade: full-width A, 7-bit displacement.  Both are encodable, so
# this rule accepts the union and the encoder picks the row that fits.
_MVLOAD_JUMP_LI_J_BITS = 5


def _is_small_jump(insn: Instruction) -> bool:
    """B-slot control transfer: return, register-indirect jump (jr / jalr with
    zero offset), direct jump (j / jal x0), or an INDIRECT call.

    Direct calls are excluded because their target is a displacement with
    nowhere to live: resolved against in-file symbols, only 1.7% of cpp-rv32's
    calls fit a 10-bit packet displacement and 45% fit eighteen bits
    (FINDINGS.md).  An INDIRECT call is a different animal -- `jalr rs` reads
    its target from a register, encoding no target at all -- so the reason for
    the exclusion does not reach it.  The link value is well defined without a
    field: `ra = packet + 4`, exactly as for any other instruction, because
    packets are four bytes and 4-byte aligned."""
    m = insn.mnemonic
    if m == "ret":
        return True
    if m == "jalr":
        # covers jr (rd=x0) and the indirect call (rd=ra); a nonzero offset
        # would need a field this frame does not draw
        return insn.imm in (0, None) and insn.rd in (0, 1, None)
    if insn.is_call:
        return False                      # direct call: unencodable target
    if m in ("j", "jal"):
        return insn.rd in (0, None)
    return False


@a_is_rsd_or_li
@a_rsd_swappable
@a_jump_imm_ok
def _arith_jump_pair(a: Instruction, b: Instruction) -> None:
    """RSD ALU op (or li) followed by a small unconditional control transfer."""
    if not _is_small_jump(b):
        raise NotPair("B-not-small-jump")


def _is_direct_jump(insn: Instruction) -> bool:
    """`j label` / `jal x0, label` — a jump whose target is a displacement,
    not a register.  These take encoding.yaml's rows 3-4."""
    return insn.mnemonic == "j" or (insn.mnemonic == "jal" and insn.rd == 0)


def _mvload_jump_pair(a: Instruction, b: Instruction) -> None:
    """mv / li, or a small-offset load, followed by a small control transfer."""
    if not _is_small_jump(b):
        raise NotPair("B-not-small-jump")
    direct = _is_direct_jump(b)
    if a.is_mv:
        return None
    if a.is_li:
        # Row 4 narrows li to 5 bits to fund the 10-bit displacement.
        bits = _MVLOAD_JUMP_LI_J_BITS if direct else _MVLOAD_JUMP_LI_BITS
        lim = 1 << (bits - 1)
        if a.imm is None or not (-lim <= a.imm < lim):
            raise NotPair("A-big-imm")
        return None
    if a.reads_memory:
        off, width = a.imm, a.access_width
        if off is None or off < 0 or not width:
            raise NotPair("A-big-imm")
        if direct:
            # Row 3 spends the rs2+rs1 span on the displacement, so the load
            # keeps no offset field.  (The "near" alternative that kept one was
            # withdrawn as unfunded — see encoding.yaml.)
            if off:
                raise NotPair("A-offset-with-direct-jump")
            return None
        # imma[4:0] scaled by the access width: 0, 1×w, ... 31×w.
        if off % width or off > ((1 << _MVLOAD_JUMP_OFF_BITS) - 1) * width:
            raise NotPair("A-big-imm")
        return None
    raise NotPair("A is not mv/li or a small-offset load")

# ---------------------------------------------------------------------------
# load-store-chain
# ---------------------------------------------------------------------------
# A memory copy: A loads a value, B stores it straight back out, and the
# loaded value (the chain temporary) is dead afterwards.  Four operands -- two
# bases and two width-scaled offsets -- fill the 20-bit budget exactly.
#
# A slot: any load;  B slot: a store of the SAME access width whose stored
# value is A's result.  Signed loads are accepted and encoded as the unsigned
# form: feeding a same-width store, both write identical bytes.

_LOAD_STORE_A_MN = frozenset({"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld"})
_LOAD_STORE_B_MN = frozenset({"sb", "sh", "sw", "sd"})
_LOAD_STORE_OFF_BITS = _w("load-store-chain", "a", "lw")


@must_chain_stored
@no_escape
@a_base_not_from_auipc
def _load_store_chain(a: Instruction, b: Instruction) -> None:
    """Load a value and store it straight back out; the temporary dies."""
    if a.rd is None or a.rbase is None or b.rbase is None:
        raise NotPair("MALFORMED: missing base or destination")
    if a.access_width != b.access_width:
        raise NotPair("width-mismatch")
    # A load that lands its result in its own base would make B store the
    # loaded value through a clobbered pointer -- still well defined, but the
    # frame's temp must be dead, and @no_escape does not see this.
    if a.rd == a.rbase:
        raise NotPair("load-clobbers-base")
    for insn in (a, b):
        if not insn.uimm_fits(_LOAD_STORE_OFF_BITS, insn.access_shift or 0):
            raise NotPair("big-imm")
    return None


# ---------------------------------------------------------------------------
# load-call-chain
# ---------------------------------------------------------------------------
# C++ virtual dispatch: A loads a function pointer, B transfers through it, and
# the pointer dies there.  No target is encoded -- it is read from a register --
# so the direct call's unencodable-displacement problem does not arise, and the
# link value needs no field either (link = packet + 4).
#
# The shape was long believed absent: a census matching the explicit
# `jalr ra, rs, 0` spelling found none, but the corpus writes the one-operand
# pseudo-op `jalr rs` (2246 in cpp-rv32, 458 in this adjacency).
#
# WHICH LINK REGISTER.  The frame draws no rd field, so the link register is an
# op-select choice and the permitted set is exactly what the yaml spells in the
# B op list -- read through `_lr`, never repeated here.  It is currently ra and
# t1, and the distinction is not cosmetic: only ra is a CALL.  RISC-V treats x1
# and x5 as link registers (a `jalr` writing either pushes the return-address
# stack); x6 is not one, which is precisely why a PLT stub transfers with
# `jalr t1, rs` -- it must leave the caller's `ra` intact and must not unbalance
# the RAS, since the callee's own `ret` pops the entry the original call pushed.
# x5 was admitted here on the ISA's definition of a call, but never occurs: zero
# t0-linked jal/jalr across all 21 corpora.

_LOAD_CALL_A_MN = frozenset({"lw", "ld"})
_LOAD_CALL_B_MN = frozenset({"jalr"})
_LOAD_CALL_OFF_BITS = _w("load-call-chain", "a", "lw")   # 10: the whole word is free
_LOAD_CALL_LINK_REGS = _lr("load-call-chain", "b")       # {x1, x6} from the yaml
_LOAD_CALL_PCREL_LO = _yaml_pcrel_lo("load-call-chain")  # declared optimism


@must_chain_base
@no_escape
def _load_call_chain(a: Instruction, b: Instruction) -> None:
    """Load a function pointer, then transfer through it; the pointer dies."""
    if a.rd is None or a.rbase is None:
        raise NotPair("MALFORMED: missing base or destination")
    if b.imm not in (0, None):
        raise NotPair("B-nonzero-offset")
    if b.rd not in _LOAD_CALL_LINK_REGS:
        # Discards `jr` (rd=x0, which saves no link and is a tail call) and any
        # link register the frame has no codepoint for -- see _LOAD_CALL_LINK_REGS.
        raise NotPair("B-link-register-not-encodable")
    if a.base_from_auipc:
        # No `@a_base_not_from_auipc` here, by declaration: this frame's field
        # spans the whole pcrel-lo range, so the offset the link step computes
        # for the PACKED layout fits whatever it turns out to be.  The corpus
        # number is not that offset -- it belongs to the layout the binary was
        # linked for -- so it is not checked rather than checked wrongly.  The
        # frame states the reasoning and the residue; see `accepts_pcrel_lo` in
        # encoding.yaml and ACCOUNTING.md sec 8.
        if not _LOAD_CALL_PCREL_LO:
            raise NotPair("A-relocatable-offset")
        return None
    if not a.uimm_fits(_LOAD_CALL_OFF_BITS, a.access_shift or 0):
        raise NotPair("A-big-imm")
    return None


# ---------------------------------------------------------------------------
# addi-store-off-chain
# ---------------------------------------------------------------------------
# addi-store-chain's offset-bearing sibling: A computes a value from one base,
# B stores it at an offset from ANOTHER base, and the value dies there.  The
# zero-offset frame spends its whole row on a 10-bit addi immediate, so an
# offset can only be had by giving that width back.

_ASO_A_MN = frozenset({"addi"})
_ASO_B_MN = frozenset({"sb", "sh", "sw", "sd"})
_ASO_A_BITS = _w("addi-store-off-chain", "a", "addi")
_ASO_B_BITS = _w("addi-store-off-chain", "b", "sw")


@must_chain_stored
@no_escape
@a_base_not_from_auipc
def _addi_store_off_chain(a: Instruction, b: Instruction) -> None:
    """Compute a value, store it at an offset from another base."""
    if a.rd is None or a.rs1 is None or b.rbase is None:
        raise NotPair("MALFORMED: missing register")
    if a.rs1 == 0:
        raise NotPair("A-is-li")            # li belongs to the frames that draw it
    if a.imm is None or not a.imm_fits(_ASO_A_BITS):
        raise NotPair("A-big-imm")
    if not b.uimm_fits(_ASO_B_BITS, b.access_shift or 0):
        raise NotPair("B-big-imm")
    return None



# ---------------------------------------------------------------------------
# arg-call-pair
# ---------------------------------------------------------------------------
# Argument setup packed with a call made through a HARD-CODED base register.
#
# A direct call's displacement has nowhere to live in a packet (only 1.7% of
# cpp-rv32's calls fit ten bits, FINDINGS.md), so every call in the corpus is
# a solo today and so is the instruction beside it.  This frame makes the
# transfer ten bits by naming neither register:
#
#   jalr ra, 4*imm(ra)   a far CALL      -- link and base are both ra
#   jr       4*imm(t1)   a far TAIL call -- base is t1, link is x0
#
# Those are the two spellings the linker already emits when it cannot relax a
# call, so the rule matches code that exists rather than code we wish existed.
# The register choice is forced, not chosen: a call may clobber ra because the
# jalr overwrites it with the link anyway, and a TAIL call may not, because the
# auipc would destroy the return address the callee still needs.
#
# The displacement is in PACKETS.  Targets are packet-aligned in this ISA, so
# the low two bits are dead and ten bits reaches +-2 KiB -- which is what the
# high half leaves unresolved.  Corpus offsets are byte displacements against
# an auipc-computed base, so the check is `fits ten bits after scaling`.
#
# What this leaves behind: the high half is a 20-bit instruction that can never
# pair, and it stands as a solo word beside every packet this rule makes.  A
# table jump (Zcmt `cm.jalt`/`cm.jt`) deletes it -- the index IS the target --
# for one more word per call, with no pairing needed.  Converting this rule is
# a change of B mnemonic and nothing else: a 10-bit index and a 10-bit scaled
# displacement are the same field.

_ARG_CALL_A_MN = frozenset({"addi", "addiw", "mv", "li", "lw", "ld", "sw", "sd"})
_ARG_CALL_OFF_BITS = _w("arg-call-pair", "b", "jalr_ra")     # 10, scaled by 4
_ARG_CALL_LI_BITS = _w("arg-call-pair", "a", "li")           # 7, rd3 row
_ARG_CALL_SPN_BITS = _w("arg-call-pair", "a", "addi4spn")    # 7, rd3 row
_ARG_CALL_LOAD_BITS = _w("arg-call-pair", "a", "lw")         # 7, scaled, rd3
_ARG_CALL_STORE_BITS = _w("arg-call-pair", "a", "sw")        # 5, scaled, rs5
_ARG_CALL_RSD_BITS = _w("arg-call-pair", "a", "addi_rsd")    # 5
_ARG_REGS = frozenset(range(10, 18))          # a0-a7: the 3-bit rd column


def _fits_u(v, bits, scale=1):
    return v is not None and v >= 0 and v % scale == 0 and v // scale < (1 << bits)


def _fits_s(v, bits):
    return v is not None and -(1 << (bits - 1)) <= v < (1 << (bits - 1))


def _is_hardcoded_call(insn: Instruction) -> bool:
    """`jalr ra, imm(ra)` or `jr imm(t1)` -- the two forms whose registers this
    frame hard-codes, so the whole word is left for the displacement."""
    if insn.mnemonic != "jalr":
        return False
    if insn.rd == 1 and insn.rs1 == 1:
        return True
    return insn.rd == 0 and insn.rs1 == 6


def _arg_call_a_ok(a: Instruction) -> bool:
    """Whichever of the frame's A rows can hold `a`, if any."""
    m, rd, rs1, imm = a.mnemonic, a.rd, a.rs1, a.imm
    i = imm if imm is not None else 0
    if m in ("mv",) or (m in ("addi", "addiw") and i == 0 and rs1 not in (0, None)):
        return rd not in (0, None)                       # row 1: rda, rs1a
    if m == "li" or (m in ("addi", "addiw") and rs1 in (0, None)):
        return rd in _ARG_REGS and _fits_s(i, _ARG_CALL_LI_BITS)
    if m in ("addi", "addiw") and rs1 == 2 and rd not in (0, 2, None):
        # addi4spn: scaled by four AND biased by one, as c.addi4spn is
        # -- a zero offset is `mv rda, sp`, which row 1 already holds,
        # so the codepoint is spent on 512 instead of wasting it on 0
        return rd in _ARG_REGS and _fits_u(i - 4, _ARG_CALL_SPN_BITS, 4)
    if m in ("addi", "addiw") and rd == rs1 and rd not in (0, None):
        return _fits_s(i, _ARG_CALL_RSD_BITS)
    if m in ("lw", "ld") and rs1 == 2:
        return rd in _ARG_REGS and _fits_u(i, _ARG_CALL_LOAD_BITS, MEM_SCALE[m])
    if m in ("sw", "sd") and rs1 == 2:
        return _fits_u(i, _ARG_CALL_STORE_BITS, MEM_SCALE[m])
    return False


MEM_SCALE = {"lw": 4, "sw": 4, "ld": 8, "sd": 8}


def _arg_call_pair(a: Instruction, b: Instruction) -> None:
    """Argument setup followed by a call through a hard-coded base register."""
    if not _is_hardcoded_call(b):
        raise NotPair("B-not-hardcoded-call")
    # The displacement is measured in PACKETS, and every packet is four bytes,
    # so a target is 4-aligned by construction in this ISA. The corpus is not:
    # it was linked with RVC, so half its function entries land on a 2-byte
    # boundary and the byte displacement is odd*2. That is an artifact of the
    # build we are reading, not of the scheme being measured, so only the RANGE
    # is checked here -- the low two bits would be zero if our own linker had
    # placed the target. Without this, cpp-rv32's 2463 far calls all failed on
    # alignment alone and the frame measured zero on the corpus it was built
    # for, while godot's happened to be even and measured 1721.
    off = b.imm if b.imm is not None else 0
    reach = 1 << (_ARG_CALL_OFF_BITS - 1)          # packets each way
    if not -4 * reach <= off <= 4 * (reach - 1):
        raise NotPair("B-displacement-out-of-range")
    if not _arg_call_a_ok(a):
        raise NotPair("A-not-encodable-here")


RULES: list[PairingRule] = [
    PairingRule(
        name="rsd-alu-pair",
        a_mnemonic_set=_RSD_A_MN,
        b_mnemonic_set=_RSD_B_MN,
        check=_rsd_alu_pair,
    ),
    PairingRule(
        name="alu-alu-chain",
        a_mnemonic_set=_ALU_ALU_A_MN,
        b_mnemonic_set=_ALU_ALU_B_MN,
        check=_alu_alu_chain,
    ),
    PairingRule(
        name="load-alu-chain",
        a_mnemonic_set=_SP_LOAD_MN,
        b_mnemonic_set=_CHAIN_ALU_MN,
        check=_load_alu_chain,
    ),
    PairingRule(
        name="addi-store-chain",
        a_mnemonic_set=frozenset({"addi"}),
        b_mnemonic_set=_ADDI_STORE_MN,
        check=_addi_store_chain,
    ),
    PairingRule(
        name="alu-store-chain",
        a_mnemonic_set=_CHAIN_ALU_MN,
        b_mnemonic_set=_SP_STORE_MN,
        check=_alu_store_chain,
    ),
    PairingRule(
        name="load-sp-branch-pair",
        a_mnemonic_set=_SP_BRANCH_A_MN,
        b_mnemonic_set=_ZERO_BRANCH_MN,
        a_prerequisites=["reads_stack"],
        check=_load_sp_branch,
    ),
    PairingRule(
        name="load-base-branch-pair",
        a_mnemonic_set=_ALL_LOAD_MN,
        b_mnemonic_set=_ZERO_BRANCH_MN,
        check=_load_base_branch,
    ),
    PairingRule(
        name="load0-load10-chain",
        a_mnemonic_set=_CHAIN_A_MN,
        b_mnemonic_set=_CHAIN_LOAD_MN,
        check=_load0_load10_chain,
    ),
    PairingRule(
        name="load5-load5-chain",
        a_mnemonic_set=_CHAIN_A_MN,
        b_mnemonic_set=_CHAIN_LOAD_MN,
        check=_load5_load5_chain,
    ),
    PairingRule(
        name="mem-sp-pair",
        a_mnemonic_set=_MEM_SP_MN,
        b_mnemonic_set=_MEM_SP_MN,
        check=_mem_sp_pair,
    ),
    PairingRule(
        name="mem-base-pair",
        a_mnemonic_set=_MEM_BASE_MN,
        b_mnemonic_set=_MEM_BASE_MN,
        check=_mem_base_pair,
    ),
    PairingRule(
        name="load-store-chain",
        a_mnemonic_set=_LOAD_STORE_A_MN,
        b_mnemonic_set=_LOAD_STORE_B_MN,
        check=_load_store_chain,
    ),
    PairingRule(
        name="addi-store-off-chain",
        a_mnemonic_set=_ASO_A_MN,
        b_mnemonic_set=_ASO_B_MN,
        check=_addi_store_off_chain,
    ),
    PairingRule(
        name="load-call-chain",
        a_mnemonic_set=_LOAD_CALL_A_MN,
        b_mnemonic_set=_LOAD_CALL_B_MN,
        check=_load_call_chain,
    ),
    PairingRule(
        name="macro-op-pair",
        a_mnemonic_set=_role_mnems("arith2"),
        b_mnemonic_set=_role_mnems("arith2"),
        check=_dual_arith2,
    ),
    PairingRule(
        name="post-inc-pair",
        a_mnemonic_set=_a_slot_mnems("mem_addi"),
        b_mnemonic_set=_b_slot_mnems("mem_addi"),
        check=_post_inc_addi,
    ),
    PairingRule(
        name="dual-setup-pair",
        a_mnemonic_set=_role_mnems("dual_setup_pair"),
        b_mnemonic_set=_role_mnems("dual_setup_pair"),
        check=_dual_indep,
    ),
    PairingRule(
        name="li-branch-chain",
        a_mnemonic_set=_LI_BRANCH_A_MN,
        b_mnemonic_set=_LI_BRANCH_B_MN,
        a_prerequisites=["is_li"],
        check=_chain_li_branch,
    ),
    PairingRule(
        name="inc-branch-pair",
        a_mnemonic_set=_INC_BRANCH_A_MN,
        b_mnemonic_set=_INC_BRANCH_B_MN,
        a_prerequisites=["is_rsd"],
        check=_inc_branch_pair,
    ),
    PairingRule(
        name="bit-test-branch-chain",
        a_mnemonic_set=_BIT_BRANCH_A_MN,
        b_mnemonic_set=_BIT_BRANCH_B_MN,
        check=_chain_bit_test_branch,
    ),
    PairingRule(
        name="prologue-pair",
        a_mnemonic_set=frozenset({"addi"}),
        b_mnemonic_set=frozenset({"sw", "sd"}),
        check=_prologue_pair,
    ),
    PairingRule(
        name="epilogue-pair",
        a_mnemonic_set=_EPILOGUE_A_MN,
        b_mnemonic_set=_EPILOGUE_B_MN,
        check=_epilogue_pair,
    ),
    PairingRule(
        name="czero-or-chain",
        a_mnemonic_set=_CZERO_MN,
        b_mnemonic_set=frozenset({"or"}),
        check=_czero_select_pair,
    ),
    PairingRule(
        name="li-czero-chain",
        a_mnemonic_set=frozenset({"addi"}),
        b_mnemonic_set=_CZERO_MN,
        a_prerequisites=["is_li"],
        check=_li_czero_pair,
    ),
    PairingRule(
        name="index-mem-chain",
        a_mnemonic_set=_INDEX_MEM_A_MN,
        b_mnemonic_set=_INDEX_MEM_B_MN,
        check=_index_mem_chain,
    ),
    PairingRule(
        name="pre-inc-pair",
        a_mnemonic_set=_PRE_INC_A_MN,
        b_mnemonic_set=_PRE_INC_B_MN,
        a_prerequisites=["is_rsd"],
        check=_pre_inc_pair,
    ),
    PairingRule(
        name="arith-jump-pair",
        a_mnemonic_set=_RSD_ALU_MN,
        b_mnemonic_set=_SMALL_JUMP_MN,
        check=_arith_jump_pair,
    ),
    PairingRule(
        name="setup-jump-pair",
        a_mnemonic_set=_MVLOAD_JUMP_A_MN,
        b_mnemonic_set=_SMALL_JUMP_MN,
        check=_mvload_jump_pair,
    ),
    PairingRule(
        name="arg-call-pair",
        a_mnemonic_set=_ARG_CALL_A_MN,
        b_mnemonic_set=frozenset({"jalr"}),
        check=_arg_call_pair,
    ),
]



# ---------------------------------------------------------------------------
# The rd = x0/x2 sentinel (A1.11)
# ---------------------------------------------------------------------------
# encoding.yaml reserves those two bit patterns in the `rd` column: they select
# the prologue / epilogue / jump marker formats (drawn "0 0 0 1 0") instead of
# naming a register.  That is what lets those frames be identified without an
# opcode of their own, so every frame that writes a REAL register into that
# column owes the reservation -- a pair whose destination is x0 or x2 has no
# encoding there and must not be scheduled.
#
# Applied here, over the whole table, rather than written into each check: the
# frames that owe it are exactly the frames whose rows draw a destination in
# the rd column, which the yaml already says (imm_contracts.rd_column_slots).
# A rule need not know it is subject to this, and cannot forget it.
_SENTINEL_REGS = frozenset({0, 2})          # x0, x2 (sp)


def _guard_sentinel(rule: "PairingRule") -> None:
    slots = _rd_slots(rule.name)
    if not slots:
        return
    inner = rule.check
    def checked(a: Instruction, b: Instruction):
        for slot in slots:
            insn = a if slot == "a" else b
            if insn.rd in _SENTINEL_REGS:
                raise NotPair("rd-is-sentinel")
        return inner(a, b) if inner else None
    rule.check = wraps(inner)(checked) if inner else checked


for _rule in RULES:
    _guard_sentinel(_rule)

A_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
    # A control transfer can only be the B (last) slot — the hardware runs A
    # before B, so a transfer in A would never reach B.
    "is_control_transfer",
]

B_SLOT_DISQUALIFIERS: list[str] = [
    "is_unknown",
]
